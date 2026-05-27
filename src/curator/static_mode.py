"""Static (zero-API) curation synthesis.

Builds a deterministic ``ResumeCuration`` from portfolio data without any
Anthropic API call. The synthesized curation is validated with the same
checks as an API response and fed into the existing renderer path.

Selection rules:

- **Summary**: ``portfolio.basics.summary`` verbatim (truncated to 750 if
  oversized). Fallback is the portfolio ``label`` plus the mandatory
  mention when the portfolio has no summary.
- **Label**: ``portfolio.basics.label`` verbatim (truncated to 60);
  fallback ``"Professional"``.
- **Work highlights**: all highlights per work entry in portfolio order,
  optionally capped per-entry by ``max_highlights_per_work``.
- **Skills**: all portfolio skill groups, all keywords in portfolio order.
  Groups with zero keywords are skipped.
- **Projects**: all project IDs sorted by ``weight`` ascending (stable;
  ties and unset values fall back to portfolio order).
- **Cover letter**: verbatim pass-through of
  ``portfolio.cover_letter`` (loaded from
  ``<portfolio>/data/cover-letter.yaml``). Missing content raises
  :class:`StaticModeError` pointing the candidate at the
  ``COVER_LETTER_*`` constants in :mod:`curator.rules`. Validator
  failures are wrapped with the source file path so the candidate can
  find and fix the offending YAML.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

from curator.client import CurationResult
from curator.exceptions import CurationValidationError, StaticModeError
from curator.io_utils import priority_sort_key, slugify
from curator.models import (
    CoverLetterCuration,
    ResumeCuration,
    SkillRanking,
    WorkHighlightRanking,
    validate_cover_letter,
    validate_curation_ids,
)
from curator.rules import SUMMARY_MANDATORY_MENTION

if TYPE_CHECKING:
    from curator.models import PortfolioData

# Field length ceilings mirror ResumeCuration schema constraints.
_SUMMARY_MAX_LEN: int = 750
_LABEL_MAX_LEN: int = 60
_DEFAULT_LABEL: str = "Professional"

#: Default ``--name`` value. Public so CLI callers can compare without
#: duplicating the literal string.
DEFAULT_NAME: str = "general"
_DEFAULT_NAME: str = DEFAULT_NAME  # internal alias; prefer DEFAULT_NAME

_WORD_RE = re.compile(r"\b\w+\b")


def _derive_summary(portfolio: PortfolioData) -> str:
    """Return a valid ``ResumeCuration.summary`` from portfolio data.

    Uses ``basics.summary`` verbatim when present, truncating with an
    ellipsis when it exceeds the 750-char schema max. Falls back to a
    short label + mandatory-mention string so the schema's
    ``min_length=1`` is always satisfied.
    """
    raw = portfolio.basics.summary
    if raw is not None and raw.strip():
        if SUMMARY_MANDATORY_MENTION not in raw:
            logger.warning(
                "Portfolio basics.summary does not contain the mandatory "
                "mention '{}'. Static mode renders the summary verbatim; the "
                "API path would re-write it to include this phrase. Consider "
                "updating the portfolio summary to preserve attribution.",
                SUMMARY_MANDATORY_MENTION,
            )
        if len(raw) > _SUMMARY_MAX_LEN:
            logger.warning(
                "Portfolio basics.summary is {} chars; truncating to {} for "
                "schema compliance. Consider shortening the portfolio summary.",
                len(raw),
                _SUMMARY_MAX_LEN,
            )
            return raw[: _SUMMARY_MAX_LEN - 3] + "..."
        return raw
    label = portfolio.basics.label or _DEFAULT_LABEL
    return f"{label}. {SUMMARY_MANDATORY_MENTION}."


def _derive_label(portfolio: PortfolioData) -> str:
    """Return a valid ``ResumeCuration.suggested_label`` from portfolio data."""
    raw = portfolio.basics.label
    if raw is not None and raw.strip():
        return raw[:_LABEL_MAX_LEN]
    return _DEFAULT_LABEL


def synthesize_curation(
    portfolio: PortfolioData,
    *,
    name: str = _DEFAULT_NAME,
    max_highlights_per_work: int | None = None,
) -> ResumeCuration:
    """Deterministically build a ``ResumeCuration`` from portfolio data.

    Args:
        portfolio: Loaded portfolio.
        name: Free-text name used to derive ``company_slug`` (default
            ``"general"``). Passed through ``slugify`` so any input is safe.
        max_highlights_per_work: Optional per-entry highlight cap. When set,
            each work entry's highlight list is truncated to this length
            (in portfolio order) before rendering.

    Returns:
        A validated ``ResumeCuration`` that passes both Pydantic validation
        and ``validate_curation_ids`` against *portfolio*.

    Raises:
        StaticModeError: If the portfolio has zero work entries (the schema
            requires at least one ``WorkHighlightRanking``).
    """
    if not portfolio.work:
        msg = (
            "Static mode requires at least one work entry in the portfolio "
            "(ResumeCuration.work_highlights has min_length=1)."
        )
        raise StaticModeError(msg)

    summary = _derive_summary(portfolio)
    label = _derive_label(portfolio)
    company_slug = slugify(name)

    # Work highlights: one ranking per entry, all highlights in portfolio order.
    cap_engaged = False
    work_rankings: list[WorkHighlightRanking] = []
    for entry in portfolio.work:
        highlight_ids = [h.id for h in entry.highlights]
        if not highlight_ids:
            logger.warning(
                "Work entry '{}' has zero highlights; it will render with an "
                "empty bullet list.",
                entry.id,
            )
        if max_highlights_per_work is not None:
            capped = highlight_ids[:max_highlights_per_work]
            if len(capped) < len(highlight_ids):
                cap_engaged = True
                logger.debug(
                    "Work entry '{}': capped {} -> {} highlights",
                    entry.id,
                    len(highlight_ids),
                    len(capped),
                )
            highlight_ids = capped
        work_rankings.append(
            WorkHighlightRanking(work_id=entry.id, highlight_ids=highlight_ids)
        )
    if cap_engaged:
        logger.info(
            "Applied max_highlights={} cap to one or more work entries.",
            max_highlights_per_work,
        )

    # Skills: all groups with all keywords. Skip groups drained to zero.
    skill_rankings: list[SkillRanking] = []
    for group in portfolio.skills:
        if not group.keywords:
            logger.warning("Skill group '{}' has zero keywords; skipping.", group.id)
            continue
        skill_rankings.append(
            SkillRanking(skill_id=group.id, keywords=list(group.keywords))
        )

    # Projects: sort by weight ascending (stable; None sorts last).
    sorted_projects = sorted(
        portfolio.projects, key=lambda p: priority_sort_key(p, "weight")
    )
    project_ids = [p.id for p in sorted_projects]

    curation = ResumeCuration(
        summary=summary,
        suggested_label=label,
        company_slug=company_slug,
        work_highlights=work_rankings,
        skills=skill_rankings,
        projects=project_ids,
    )

    # Defense in depth: round-trip validators in case portfolio data drifts.
    # The static path constructs IDs from the portfolio itself, so the
    # sanitization is a no-op here, but we use the return value uniformly.
    return validate_curation_ids(curation, portfolio)


def synthesize_cover_letter(portfolio: PortfolioData) -> CoverLetterCuration:
    """Return the portfolio-authored cover letter verbatim.

    The letter is loaded by the portfolio loader from
    ``<portfolio>/data/cover-letter.yaml`` and validated at load time.
    This function performs no assembly and no tailoring; it simply
    returns the authored prose.

    Args:
        portfolio: Loaded portfolio.

    Returns:
        The portfolio's cover letter, unmodified.

    Raises:
        StaticModeError: When the portfolio has no cover-letter content.
            The message points the candidate at the authoring guide.
    """
    if portfolio.cover_letter is None:
        msg = (
            "Static cover letter requires data/cover-letter.yaml in the "
            "portfolio source directory. See curator.rules.COVER_LETTER_* "
            "constants (word counts, sign-off enum, forbidden words and "
            "phrases) for the authoring constraints, then re-run."
        )
        raise StaticModeError(msg)
    return portfolio.cover_letter


def build_static_result(
    portfolio: PortfolioData,
    *,
    name: str = _DEFAULT_NAME,
    max_highlights_per_work: int | None = None,
    with_cover_letter: bool = False,
) -> CurationResult:
    """Wrap a synthesized ``ResumeCuration`` in a ``CurationResult``.

    The returned object has ``source="static"``, ``model="n/a"``, and all
    token counts zero; it is distinguishable from API-sourced results in
    ``curation_log.json`` and downstream consumers.

    When ``with_cover_letter`` is True, the portfolio-authored cover
    letter (from ``data/cover-letter.yaml``) is attached to
    ``CurationResult.cover_letter`` and the standard
    :func:`validate_cover_letter` check is applied. Validator failures
    are wrapped as :class:`StaticModeError` with a pointer to the source
    file so the candidate can find and fix the offending YAML.

    Raises:
        StaticModeError: When ``with_cover_letter`` is True and either
            (a) the portfolio has no cover-letter content, or (b) the
            authored letter fails the policy validator.
    """
    curation = synthesize_curation(
        portfolio,
        name=name,
        max_highlights_per_work=max_highlights_per_work,
    )

    cover_letter: CoverLetterCuration | None = None
    if with_cover_letter:
        cover_letter = synthesize_cover_letter(portfolio)
        try:
            # Static path is strict: hand-authored YAMLs that overshoot
            # the band should fail loudly with a pointer to the authoring
            # guide, not silently ship under the API-path soft-warn. AR-6.
            validate_cover_letter(cover_letter, portfolio, strict=True)
        except CurationValidationError as exc:
            # Strip the inner "Cover letter failed N policy check(s): "
            # prefix so the wrapped message does not duplicate it.
            inner = re.sub(
                r"^Cover letter failed \d+ policy check\(s\): ", "", str(exc)
            )
            msg = (
                "Cover letter in <portfolio>/data/cover-letter.yaml failed "
                f"validation: {inner}. See curator.rules.COVER_LETTER_* "
                "constants for the machine-enforced constraints."
            )
            raise StaticModeError(msg) from exc
        logger.info(
            "Static cover letter loaded from portfolio ({} words).",
            _cover_letter_word_count(cover_letter),
        )

    return CurationResult(
        curation=curation,
        model="n/a",
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        source="static",
        cover_letter=cover_letter,
    )


def _cover_letter_word_count(letter: CoverLetterCuration) -> int:
    """Count words across opening + body + closing for diagnostic logging."""
    total = len(_WORD_RE.findall(letter.opening))
    total += sum(len(_WORD_RE.findall(p)) for p in letter.body_paragraphs)
    total += len(_WORD_RE.findall(letter.closing))
    return total
