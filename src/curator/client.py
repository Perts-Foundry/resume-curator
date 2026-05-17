"""Anthropic API wrapper for resume curation.

Wraps the Claude Messages API with streaming, structured output parsing,
refusal handling, and application-level ID validation. Returns a
``CurationResult`` containing the validated curation alongside API metadata
for cost tracking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Any, Literal, Self

import anthropic
import httpx
from loguru import logger
from pydantic import ValidationError

from curator.exceptions import (
    APIAuthError,
    APIError,
    APIRateLimitError,
    APIRefusalError,
    APIResponseError,
    APISpendGuardError,
    CurationValidationError,
)
from curator.io_utils import slugify, sort_work_chronologically
from curator.jd_scorer import score_keywords_for_jd
from curator.models import (
    CoverLetterCuration,
    PortfolioData,
    ResumeCuration,
    validate_cover_letter,
    validate_curation_ids,
)
from curator.output_schema import build_curation_schema
from curator.page_caps import per_entry_emit_cap
from curator.prompt import build_system_prompt, build_user_message
from curator.rules import (
    COVER_LETTER_MAX_TOKENS_HEADROOM,
    SKILL_GROUPS_MAX,
    SKILL_KEYWORDS_PER_GROUP_MAX,
    SKILL_KEYWORDS_TOTAL_MAX,
)

if TYPE_CHECKING:
    from curator.config import CuratorSettings


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CurationResult:
    """Validated curation response with provenance and metadata.

    Returned by ``CuratorClient.curate()`` (API path, ``source="api"``) and by
    ``static_mode.build_static_result()`` (deterministic path, ``source="static"``).

    The ``model`` field reflects the model that actually served an API request;
    for static runs it is the sentinel ``"n/a"``. The authoritative provenance
    signal is ``source``.

    The optional ``cover_letter`` field is populated when the caller asks
    for one (``--cover-letter`` / ``with_cover_letter=True``). When absent,
    every existing consumer continues to work without modification.
    """

    curation: ResumeCuration
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    source: Literal["api", "static"] = "api"
    cover_letter: CoverLetterCuration | None = None


# ---------------------------------------------------------------------------
# ID validation
# ---------------------------------------------------------------------------


def _validate_curation_ids(
    curation: ResumeCuration, portfolio: PortfolioData
) -> ResumeCuration:
    """API-path adapter around ``models.validate_curation_ids``.

    Returns the sanitized curation (with hallucinated highlight IDs
    inside known work entries AND hallucinated skill keywords inside
    known skill groups dropped + WARN-logged).

    Hard ID-mismatch failures (unknown ``work_id``, missing rankings,
    duplicate ``work_id`` across rankings, unknown ``skill_group_id``,
    unknown ``project_id``) still surface as ``APIResponseError`` to
    keep the API call path's exception taxonomy intact.
    """
    try:
        return validate_curation_ids(curation, portfolio)
    except CurationValidationError as e:
        raise APIResponseError(str(e)) from e


def _extract_curation_dict(message: anthropic.types.Message) -> dict[str, Any]:
    """Pull and parse the JSON object from a structured-output response.

    When the structured-output call uses a raw-dict schema (rather than a
    Pydantic class), ``message.parsed_output`` is None and the JSON sits
    in the first text block of ``message.content``. The grammar
    guarantees the text is schema-valid JSON; this function raises
    :class:`APIResponseError` if extraction or parsing fails (e.g.,
    truncation mid-object, no text block at all).
    """
    if not message.content:
        msg = (
            f"No content in API response (stop_reason={message.stop_reason}, "
            f"request_id={message.id})"
        )
        raise APIResponseError(msg)
    text_blocks = [
        b for b in message.content if isinstance(b, anthropic.types.TextBlock)
    ]
    if not text_blocks:
        msg = (
            f"No text block in API response content "
            f"(stop_reason={message.stop_reason}, request_id={message.id})"
        )
        raise APIResponseError(msg)
    # Concatenate all text blocks in order. With `effort` enabled or future
    # SDK changes, the structured JSON could split across multiple text
    # blocks (or trail a reasoning block); reading only `text_blocks[0]`
    # would silently truncate the response.
    raw = "".join(b.text for b in text_blocks)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = (
            f"API response is not valid JSON "
            f"(stop_reason={message.stop_reason}, request_id={message.id}): "
            f"{exc}"
        )
        raise APIResponseError(msg) from exc
    if not isinstance(parsed, dict):
        msg = (
            f"API response JSON is not an object "
            f"(got {type(parsed).__name__}, request_id={message.id})"
        )
        raise APIResponseError(msg)
    return parsed


def _adapt_curation_dict(
    parsed: dict[str, Any],
    portfolio: PortfolioData,
    *,
    with_cover_letter: bool,
    request_id: str,
    max_pages: int,
    jd_text: str,
) -> tuple[ResumeCuration, CoverLetterCuration | None]:
    """Convert the wire dict to ``ResumeCuration`` (+ optional cover letter).

    The wire schema encodes ``work_highlights_by_id`` as an object keyed
    by portfolio work entry IDs (for grammar enforcement of cross-parent
    highlight attribution) and ``skills`` as a top-level array of
    portfolio skill group IDs. The domain model uses
    ``list[WorkHighlightRanking]`` and ``list[SkillRanking]``. This
    adapter converts shapes:

    - Work entries omitted from the schema (zero-highlight portfolios)
      are synthesized as empty ``WorkHighlightRanking`` instances so
      the validator's "every portfolio entry has a ranking" invariant
      continues to hold.
    - Work-entry highlight emissions are trimmed to the per-position
      cap (``page_caps.per_entry_emit_cap``) when the model exceeds the soft
      cap surfaced in the schema description (Anthropic does not
      enforce ``maxItems``). Over-emission is WARN-logged.
    - Each emitted ``skills`` group ID is looked up in the portfolio;
      ``score_keywords_for_jd`` fills the group's keywords from
      portfolio data ranked by JD relevance. Three caps apply:
      ``SKILL_GROUPS_MAX`` (per-resume), ``SKILL_KEYWORDS_PER_GROUP_MAX``
      (per-group), and ``SKILL_KEYWORDS_TOTAL_MAX`` (absolute total).
      Unknown group IDs are dropped with WARN; repeated emissions are
      de-duped with INFO; over-cap emissions are dropped with WARN.
    - JSON validation failures surface as :class:`APIResponseError` with
      the request_id and a brief shape description for debuggability.

    See ``docs/architecture.md`` "Dynamic schema construction (API
    path)" for the design rationale behind moving keyword selection
    from the AI to the client adapter (2026-05-18 hybrid).
    """
    if with_cover_letter:
        resume_dict = parsed.get("resume")
        cover_letter_dict = parsed.get("cover_letter")
        if not isinstance(resume_dict, dict) or not isinstance(cover_letter_dict, dict):
            msg = (
                f"Expected resume + cover_letter objects in response "
                f"(request_id={request_id})"
            )
            raise APIResponseError(msg)
    else:
        resume_dict = parsed
        cover_letter_dict = None

    # Resume: convert object-keyed work_highlights and flat skills array
    # into domain-shape ranking lists.
    raw_work = resume_dict.get("work_highlights_by_id", {})
    raw_skills = resume_dict.get("skills", [])
    if not isinstance(raw_work, dict):
        msg = (
            f"work_highlights_by_id must be an object in API response "
            f"(request_id={request_id})"
        )
        raise APIResponseError(msg)
    if not isinstance(raw_skills, list):
        msg = f"skills must be an array in API response (request_id={request_id})"
        raise APIResponseError(msg)

    work_highlights: list[dict[str, Any]] = [
        {"work_id": wid, "highlight_ids": hids} for wid, hids in raw_work.items()
    ]
    # Synthesize empty rankings for portfolio entries the schema omitted
    # (zero-highlight entries cannot be encoded as an empty enum).
    present_work_ids = {wh["work_id"] for wh in work_highlights}
    work_highlights.extend(
        {"work_id": w.id, "highlight_ids": []}
        for w in portfolio.work
        if w.id not in present_work_ids
    )

    # Enforce the per-entry highlight emission cap surfaced in the
    # schema's description text. Anthropic does not honor ``maxItems``
    # at decode time, so the cap reaches the model as guidance only.
    # The renderer's page-fit cascade discards anything beyond
    # ``floor[i]`` anyway; trimming here at ``floor[i] * 1.5`` keeps a
    # small headroom margin while preventing run-away over-emission
    # from reaching the validator or audit log.
    #
    # Position is chronological (index 0 = most recent role) via the
    # same ``sort_work_chronologically`` helper the renderer uses. A
    # non-chronologically-ordered ``portfolio.work`` would otherwise
    # have the adapter's cap disagree with the renderer's cascade
    # floor on which entry is "pos 0."
    sorted_work_lite = sort_work_chronologically(
        [
            {
                "id": w.id,
                "start_date": w.start_date,
                "end_date": w.end_date,
            }
            for w in portfolio.work
        ]
    )
    work_id_to_position = {w["id"]: i for i, w in enumerate(sorted_work_lite)}
    over_emit_counts: dict[str, int] = {}
    for wh in work_highlights:
        position = work_id_to_position.get(wh["work_id"])
        if position is None:
            continue  # unknown work_id is caught downstream by validator
        cap = per_entry_emit_cap(position, max_pages)
        original = wh["highlight_ids"]
        if isinstance(original, list) and len(original) > cap:
            over_emit_counts[wh["work_id"]] = len(original) - cap
            wh["highlight_ids"] = original[:cap]
    if over_emit_counts:
        logger.warning(
            "Adapter trimmed work-highlight over-emission to per-position "
            "caps (max_pages={}): {} (request_id={})",
            max_pages,
            over_emit_counts,
            request_id,
        )

    # Skills: AI emits an ordered list of skill group IDs (judgment).
    # Code fills each group's keywords from portfolio data using the
    # JD-relevance scorer (bookkeeping). Three caps apply:
    #   - SKILL_GROUPS_MAX: max number of groups (excess groups dropped)
    #   - SKILL_KEYWORDS_PER_GROUP_MAX: max keywords per group
    #   - SKILL_KEYWORDS_TOTAL_MAX: absolute total across all groups
    portfolio_groups = {g.id: g for g in portfolio.skills}
    seen_groups: set[str] = set()
    dedup_emit_count = 0
    unknown_group_ids: list[str] = []
    skills: list[dict[str, Any]] = []
    total_keywords = 0
    over_groups_cap_count = 0
    for gid in raw_skills:
        # Defense-in-depth: the schema declares ``items: {"type":
        # "string", "enum": [<portfolio group IDs>]}`` so a non-string
        # or unknown-ID emission would already be a grammar regression
        # on the API path. Coerce non-strings and route unknowns
        # through the dropped-IDs log so the operator sees what the
        # model produced.
        if not isinstance(gid, str):
            unknown_group_ids.append(repr(gid))
            continue
        if gid not in portfolio_groups:
            unknown_group_ids.append(gid)
            continue
        if gid in seen_groups:
            dedup_emit_count += 1
            continue
        if len(skills) >= SKILL_GROUPS_MAX:
            over_groups_cap_count += 1
            continue
        seen_groups.add(gid)
        group = portfolio_groups[gid]
        # Invariant: with the current rules.py constants
        # (SKILL_GROUPS_MAX=12, SKILL_KEYWORDS_PER_GROUP_MAX=10,
        # SKILL_KEYWORDS_TOTAL_MAX=140), the per-group cap upper bound
        # is 10 and total_keywords cannot exceed 140 by loop
        # construction, so (TOTAL_MAX - total_keywords) is always
        # >= 0 and the min() never produces a negative. If a future
        # constant change pushes 12 * 10 = 120 above TOTAL_MAX,
        # score_keywords_for_jd's top_n <= 0 guard would still return
        # [] safely; consider failing loud here instead.
        per_group_cap = min(
            SKILL_KEYWORDS_PER_GROUP_MAX,
            SKILL_KEYWORDS_TOTAL_MAX - total_keywords,
        )
        keywords = score_keywords_for_jd(jd_text, group.keywords, top_n=per_group_cap)
        if not keywords:
            # Empty SkillRanking would fail the model's min_length=1
            # constraint. Skip groups with zero keywords (unusual:
            # would require a portfolio group with no keywords at
            # all).
            continue
        skills.append({"skill_id": gid, "keywords": keywords})
        total_keywords += len(keywords)

    if unknown_group_ids:
        logger.warning(
            "Adapter dropped {} skill group ID(s) not in portfolio: {} (request_id={})",
            len(unknown_group_ids),
            unknown_group_ids,
            request_id,
        )
    if dedup_emit_count:
        logger.info(
            "Adapter de-duplicated {} repeated skill group emission(s) (request_id={})",
            dedup_emit_count,
            request_id,
        )
    if over_groups_cap_count:
        logger.warning(
            "Adapter dropped {} skill group emission(s) over the "
            "per-resume cap of {} (request_id={})",
            over_groups_cap_count,
            SKILL_GROUPS_MAX,
            request_id,
        )

    company_name = resume_dict.get("company_name") or ""
    resume_payload = {
        "summary": resume_dict.get("summary"),
        "suggested_label": resume_dict.get("suggested_label"),
        "company_slug": slugify(company_name),
        "work_highlights": work_highlights,
        "skills": skills,
        "projects": resume_dict.get("projects", []),
        "work_highlight_weights": resume_dict.get("work_highlight_weights") or {},
        "trim_priority": resume_dict.get("trim_priority") or [],
    }
    try:
        curation = ResumeCuration.model_validate(resume_payload)
    except ValidationError as exc:
        msg = (
            f"API response failed ResumeCuration validation "
            f"(request_id={request_id}): {exc}"
        )
        raise APIResponseError(msg) from exc

    cover_letter: CoverLetterCuration | None = None
    if cover_letter_dict is not None:
        try:
            cover_letter = CoverLetterCuration.model_validate(cover_letter_dict)
        except ValidationError as exc:
            msg = (
                f"API response failed CoverLetterCuration validation "
                f"(request_id={request_id}): {exc}"
            )
            raise APIResponseError(msg) from exc

    return curation, cover_letter


def _persist_partial_resume(
    curation: ResumeCuration,
    *,
    output_dir: Path,
    request_id: str | None,
) -> Path:
    """Write a successful resume curation to a side file for recovery.

    Used when the cover-letter validation step fails for a curate call
    that otherwise returned a valid ``ResumeCuration``. The partial YAML
    lets :mod:`scripts.rerender` rebuild the resume PDF without making a
    second paid API call.

    Returns the absolute path written. Uses ``atomic_yaml_write`` so a
    crash mid-write cannot leave a truncated file.
    """
    from curator.io_utils import atomic_yaml_write

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    safe_id = _sanitize_request_id(request_id)
    filename = f"curation_partial-{timestamp}-{curation.company_slug}-{safe_id}.yaml"
    path = output_dir / filename
    atomic_yaml_write(path, curation.model_dump())
    return path


def _sanitize_request_id(request_id: str | None) -> str:
    """Return a filesystem-safe slice of the Anthropic request id.

    Cap is defensive: today's Anthropic ``msg_*`` ids are well under 40
    chars, but the bound prevents pathological filenames if the SDK id
    format changes. Any non ``[A-Za-z0-9_-]`` input collapses to ``_`` to
    block path traversal and shell metacharacters in the filename.
    """
    import re as _re

    if request_id is None:
        return "unknown"
    return _re.sub(r"[^A-Za-z0-9_-]", "_", request_id)[:40] or "unknown"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class CuratorClient:
    """Anthropic API client for resume curation.

    Wraps ``anthropic.Anthropic`` with streaming, structured output parsing,
    and three-layer response validation. Use as a context manager for
    automatic resource cleanup.

    Args:
        settings: Validated application configuration.

    Example::

        with CuratorClient(settings) as client:
            result = client.curate(portfolio, job_description)
    """

    def __init__(self, settings: CuratorSettings) -> None:
        """Initialise the client from validated application settings."""
        if not settings.allow_api_spend:
            msg = (
                "API spending is not authorized. "
                "Set CURATOR_ALLOW_API_SPEND=true to allow Anthropic API calls."
            )
            raise APISpendGuardError(msg)

        self._client = anthropic.Anthropic(
            api_key=settings.require_api_key(),
            max_retries=settings.api_max_retries,
            # 120s total prevents timeout on slow responses;
            # 5s connect catches network issues quickly.
            timeout=httpx.Timeout(120.0, connect=5.0),
        )
        self._settings = settings
        self._model = settings.model
        self._max_tokens = settings.max_tokens
        self._effort = settings.effort

    # -- Context manager --------------------------------------------------

    def __enter__(self) -> Self:
        """Enter the context manager, returning the client."""
        return self

    def __exit__(self, *args: object) -> None:
        """Exit the context manager, releasing HTTP resources."""
        self.close()

    def close(self) -> None:
        """Release the underlying HTTP client resources."""
        self._client.close()

    # -- Public API -------------------------------------------------------

    def curate(
        self,
        portfolio: PortfolioData,
        job_description: str,
        *,
        with_cover_letter: bool = False,
    ) -> CurationResult:
        """Send portfolio and job description to Claude for curation.

        Builds prompts internally, calls the Claude API with streaming and
        structured output, validates the response, and returns a
        ``CurationResult`` with the curation and usage metadata.

        When ``with_cover_letter`` is True, ``build_curation_schema`` wraps
        the resume schema with a sibling ``cover_letter`` property and the
        system prompt gains a cover-letter rulebook block. The call itself
        remains a single ``messages.stream(...)`` invocation; there is never
        a second paid call.

        Args:
            portfolio: Validated portfolio data from the loader.
            job_description: Raw text of the job posting.
            with_cover_letter: When True, also produce a cover letter in
                the same structured-output call. Adds
                ``COVER_LETTER_MAX_TOKENS_HEADROOM`` tokens to the output
                budget for headroom.

        Returns:
            Validated curation result with API metadata. The optional
            ``cover_letter`` field is populated only when requested.

        Raises:
            APIRefusalError: Claude refused due to safety filters.
            APIAuthError: Invalid or missing API key, or insufficient
                permissions.
            APIRateLimitError: Rate limit exceeded after SDK retries.
            APIResponseError: Invalid response, truncation, or curation
                IDs not found in portfolio.
            APIError: Other Anthropic API errors (network, timeout, etc.).
            JobDescriptionError: Empty or oversized job description
                (propagated from ``build_user_message``).
        """
        # 1. Build prompts (delegates to prompt.py)
        system = build_system_prompt(portfolio, with_cover_letter=with_cover_letter)
        messages = build_user_message(
            job_description, with_cover_letter=with_cover_letter
        )

        # 2. Effective max_tokens: local variable, never mutate self._max_tokens.
        effective_max_tokens = self._max_tokens + (
            COVER_LETTER_MAX_TOKENS_HEADROOM if with_cover_letter else 0
        )

        # 3. Build the per-call JSON schema from portfolio data. Object-
        # with-fixed-keys shape: each work entry / skill group gets its
        # own property whose value carries items.enum scoped to that
        # parent's children. The grammar then makes cross-parent ID
        # emission decode-time impossible (verified 2026-05-13).
        schema = build_curation_schema(
            portfolio,
            with_cover_letter=with_cover_letter,
            max_pages=self._settings.max_pages,
        )
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": schema},
        }
        if self._effort is not None:
            output_config["effort"] = self._effort
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": effective_max_tokens,
            "output_config": output_config,
            "system": system,
            "messages": messages,
        }

        # Log input sizes and request config.
        prompt_chars = sum(len(b["text"]) for b in system)
        logger.info(
            "API request: model={}, prompt={}chars, jd={}chars, max_tokens={}, "
            "cover_letter={}{}",
            self._model,
            prompt_chars,
            len(job_description),
            effective_max_tokens,
            with_cover_letter,
            f", effort={self._effort}" if self._effort else "",
        )

        try:
            # 4. Streaming API call (exactly one; locked by test).
            with self._client.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()

            # 5. Check stop_reason.
            was_truncated = False
            if message.stop_reason == "refusal":
                msg = (
                    "Claude refused to process this request. "
                    "Check the job description for content that "
                    "may trigger safety filters."
                )
                raise APIRefusalError(msg)
            if message.stop_reason == "max_tokens":
                # When cover letter is on, bundled responses are larger and
                # truncation is more likely. If the JSON closed cleanly
                # before max_tokens hit, log a WARNING and continue rather
                # than raising, so the user gets a usable result without
                # re-paying for a retry. JSON parse failure below converts
                # the soft path back to a hard raise via APIResponseError.
                if with_cover_letter:
                    was_truncated = True
                    logger.warning(
                        "Response truncated at max_tokens={} (request_id={}); "
                        "attempting to parse partial result. Do not retry; "
                        "increase CURATOR_MAX_TOKENS if this recurs.",
                        effective_max_tokens,
                        message.id,
                    )
                else:
                    msg = (
                        "Response was truncated (max_tokens reached). "
                        "Increase CURATOR_MAX_TOKENS (current: "
                        f"{self._max_tokens}) and retry."
                    )
                    raise APIResponseError(msg)

            # 6. Extract the JSON object from the response and adapt the
            # wire shape (object-keyed) to the domain shape (list-of-
            # rankings) so existing validators and downstream code
            # continue to work unchanged.
            try:
                parsed_dict = _extract_curation_dict(message)
            except APIResponseError as exc:
                if was_truncated:
                    # Add the truncation hint so the user can act on it.
                    msg = (
                        f"Response truncated at max_tokens="
                        f"{effective_max_tokens} and the partial JSON is "
                        f"unparseable. Increase CURATOR_MAX_TOKENS "
                        f"(current: {self._max_tokens}) and retry "
                        f"(request_id={message.id}): {exc}"
                    )
                    raise APIResponseError(msg) from exc
                raise
            curation, cover_letter = _adapt_curation_dict(
                parsed_dict,
                portfolio,
                with_cover_letter=with_cover_letter,
                request_id=message.id,
                max_pages=self._settings.max_pages,
                jd_text=job_description,
            )

            # 7. Application-level ID validation (Layer 3) for the resume.
            # Returns a sanitized curation with hallucinated keywords
            # dropped; hard ID failures still raise.
            try:
                curation = _validate_curation_ids(curation, portfolio)
            except APIResponseError:
                logger.error(
                    "Curation ID validation failed (request_id={})",
                    message.id,
                )
                raise

            # 8. Cover-letter policy validation. On failure, persist the
            # successful resume curation to a side file so the user can
            # recover via scripts/rerender.py without re-paying for the API
            # call. Catch only the documented validator exception so genuine
            # bugs (AttributeError etc.) surface unobscured.
            #
            # Note: the validator's error string must not embed letter body
            # or portfolio values, since this message flows into logs.
            if cover_letter is not None:
                try:
                    validate_cover_letter(cover_letter, portfolio)
                except CurationValidationError as exc:
                    partial_path: Path | None = None
                    try:
                        partial_path = _persist_partial_resume(
                            curation,
                            output_dir=self._settings.output_dir,
                            request_id=message.id,
                        )
                    except (OSError, ValueError) as persist_exc:
                        logger.error(
                            "Failed to persist partial resume after cover-"
                            "letter validation failure: {}",
                            persist_exc,
                        )
                    logger.error(
                        "Cover letter validation failed (request_id={}). "
                        "Resume saved to {} for recovery via "
                        "scripts/rerender.py. Original error: {}",
                        message.id,
                        partial_path,
                        exc,
                    )
                    truncation_hint = (
                        " (response was truncated at max_tokens)"
                        if was_truncated
                        else ""
                    )
                    persist_hint = (
                        f" Resume persisted to {partial_path}."
                        if partial_path is not None
                        else " Resume not persisted (see logs)."
                    )
                    msg = (
                        f"Cover letter validation failed{truncation_hint}: "
                        f"{exc}.{persist_hint} (request_id={message.id})"
                    )
                    raise APIResponseError(msg) from exc

            # 7. Log usage, token counts, and cache stats at INFO for
            # cost tracking and troubleshooting.
            usage = message.usage
            cache_create = getattr(usage, "cache_creation_input_tokens", 0)
            cache_read = getattr(usage, "cache_read_input_tokens", 0)
            logger.info(
                "API response: model={}, stop={}, "
                "tokens(in={}, out={}, cache_create={}, cache_read={})",
                message.model,
                message.stop_reason,
                usage.input_tokens,
                usage.output_tokens,
                cache_create,
                cache_read,
            )

            # 8. Log curation summary at INFO.
            total_highlights = sum(
                len(wh.highlight_ids) for wh in curation.work_highlights
            )
            logger.info(
                "Curation: company={}, work={} ({} highlights ranked), "
                "skills={}, projects={}",
                curation.company_slug,
                len(curation.work_highlights),
                total_highlights,
                len(curation.skills),
                len(curation.projects),
            )

            # 10. Return result (includes optional cover letter).
            return CurationResult(
                curation=curation,
                model=message.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_input_tokens=getattr(
                    usage, "cache_creation_input_tokens", 0
                ),
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0),
                cover_letter=cover_letter,
            )

        except (APIRefusalError, APIResponseError):
            # Already our exceptions — re-raise without wrapping
            raise

        except anthropic.AuthenticationError as e:
            logger.error(
                "Authentication failed (request_id={})",
                getattr(e, "request_id", "unknown"),
            )
            msg = "Invalid or missing Anthropic API key"
            raise APIAuthError(msg) from e

        except anthropic.PermissionDeniedError as e:
            logger.error(
                "Permission denied (request_id={})",
                getattr(e, "request_id", "unknown"),
            )
            msg = "API key is valid but lacks required permissions"
            raise APIAuthError(msg) from e

        except anthropic.RateLimitError as e:
            logger.error(
                "Rate limit exceeded (request_id={})",
                getattr(e, "request_id", "unknown"),
            )
            msg = "Anthropic API rate limit exceeded; retry later"
            raise APIRateLimitError(msg) from e

        except anthropic.BadRequestError as e:
            logger.error(
                "Bad request (request_id={}): {}",
                getattr(e, "request_id", "unknown"),
                e,
            )
            msg = "Anthropic API rejected the request"
            raise APIResponseError(msg) from e

        except anthropic.APITimeoutError as e:
            # APITimeoutError is a subclass of APIConnectionError —
            # must be caught first.
            logger.error("API timeout: {}", e)
            msg = "Anthropic API request timed out — try again"
            raise APIError(msg) from e

        except anthropic.APIConnectionError as e:
            logger.error("API connection error: {}", e)
            msg = "Could not connect to Anthropic API — check network"
            raise APIError(msg) from e

        except anthropic.APIError as e:
            logger.error(
                "API error (request_id={}): {}",
                getattr(e, "request_id", "unknown"),
                e,
            )
            msg = "Anthropic API error"
            raise APIError(msg) from e
