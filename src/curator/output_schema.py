"""Build the per-call JSON schema for the Anthropic structured-output call.

The schema is constructed from the loaded ``PortfolioData`` at curate
time and injected via ``output_config.format`` on ``messages.stream``.
The grammar makes cross-parent ``highlight_id`` emission
decode-time-impossible (via per-property ``items.enum`` on
``work_highlights_by_id``).

Shape (top-level):

    summary                 string
    suggested_label         string
    company_name            string  (free-text; client slugifies)
    work_highlights_by_id   object[work_id -> array[items.enum]]
    skills                  array[items.enum]  (skill group IDs)
    projects                array[items.enum]

The 2026-05-18 hybrid skill design moves keyword selection out of
the AI: ``skills`` is an ordered list of portfolio skill group IDs
(judgment), and the client adapter fills each group's keywords from
portfolio data using JD-relevance scoring (see ``curator.jd_scorer``).
The group-ID enum surface is small (typically <30 IDs), well under
Anthropic's compiled-grammar budget; the 354-keyword surface that
forced the 2026-05-14 flat-array workaround is no longer on the wire.

The Pydantic models in ``models.py`` remain the single source of truth
for application-level shape and validation. This module produces only
the wire schema sent to the API. The client adapter converts the
response dict back to ``ResumeCuration`` before validation.

Constraints inherited from Anthropic's structured-output keyword
subset:

- ``enum`` arrays must be non-empty (empty enums return HTTP 400).
  Work entries with zero highlights are omitted from
  ``work_highlights_by_id``; the adapter synthesizes empty
  ``WorkHighlightRanking`` instances for omitted entries to keep the
  Pydantic "every portfolio work entry has a ranking" invariant.
  ``skills.items.enum`` falls back to an unconstrained string when
  the portfolio has zero skill groups.
- ``minLength`` / ``maxLength`` / ``pattern`` / ``minimum`` /
  ``maximum`` / ``maxItems`` are not enforced at decode time.
  Constraints survive only as post-hoc Pydantic re-validation and
  adapter-side trimming on the parsed dict.
- ``oneOf`` / ``dependentSchemas`` / ``if-then-else`` are
  unsupported; the design uses ``properties`` directly rather than
  any union construct.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any

from curator.page_caps import _caps_for_pages
from curator.rules import (
    COVER_LETTER_VALID_SIGN_OFFS,
    SKILL_GROUPS_MAX,
    SUMMARY_MANDATORY_MENTION,
    SUMMARY_WORD_TARGET_MAX,
    SUMMARY_WORD_TARGET_MIN,
)

if TYPE_CHECKING:
    from curator.models import PortfolioData


def _per_entry_emit_cap(work_position: int, max_pages: int) -> int:
    """Soft cap on highlight IDs the model should emit for one work entry.

    Anthropic's structured-output keyword subset does NOT include
    ``maxItems`` (verified empirically; see TestNoUnsupportedKeywords in
    test_output_schema.py and the API documentation). The cap is
    communicated to the model via the property's ``description`` text
    and enforced post-parse by the client adapter
    (``_trim_work_highlights_to_cap``); both layers exist so the model
    has a clear target and the adapter guarantees the renderer never
    sees over-emission.

    Formula: ``ceil(floor * 1.5)`` for the renderer floor at this
    position, clamped to a minimum of 2 so the model has room even on
    positions where the renderer's per-position floor is 0 (1-page mode
    positions 2..4). ``ceil`` is used (not ``round``) to avoid Python's
    banker's rounding edge cases and to always give the model a hair
    more headroom than the strict 1.5x scale.
    """
    caps = _caps_for_pages(max_pages)
    floor = caps.floor_for_position(work_position)
    return max(2, math.ceil(floor * 1.5))


_ID_PATTERN_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _check_id(value: str, kind: str) -> str:
    """Defensive ID validation before embedding as a schema property key.

    Portfolio data is trusted input (already passed Pydantic load-time
    validation against ``ID_PATTERN``), so this is defense-in-depth
    against a regression where an unvalidated value ends up in a
    schema key.
    """
    if not _ID_PATTERN_RE.fullmatch(value):
        msg = f"{kind} id {value!r} does not match ID_PATTERN"
        raise ValueError(msg)
    return value


def _build_summary_schema() -> dict[str, Any]:
    """Top-level ``summary`` field.

    Length bounds are advisory under constrained decoding; the
    post-parse Pydantic validator enforces them on the dict response.
    """
    return {
        "type": "string",
        "description": (
            f"Tailored 2-3 sentence professional summary, "
            f"{SUMMARY_WORD_TARGET_MIN}-{SUMMARY_WORD_TARGET_MAX} words "
            f"soft target. Open with title plus years of experience. "
            f"Close with capability value prop. Must include "
            f"'{SUMMARY_MANDATORY_MENTION}' verbatim. No first person; "
            f"no subjective filler. Generated first so tone and framing "
            f"are committed before downstream ranking under constrained "
            f"decoding."
        ),
    }


def _build_suggested_label_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "description": (
            "2-5 word professional title tailored to the JD "
            "(e.g. 'Staff DevOps Engineer'). Reflect actual portfolio "
            "seniority; never fabricate a level the candidate has not "
            "reached."
        ),
    }


def _build_company_name_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "description": (
            "Company display name extracted from the JD, written as it "
            "appears in the wild (e.g., 'DataDog', 'Anthropic, PBC', "
            "'Hugging Face'). Preserve the canonical capitalization and "
            "spacing the company uses for itself. Strip surrounding "
            "boilerplate ('Job at ...', 'Careers - ...') but not "
            "corporate suffixes. For subsidiaries like 'DataLabs (a "
            "Google company)' return the primary subsidiary name "
            "('DataLabs'). 200 characters max (soft cap; client slugifies "
            "and truncates downstream). The client converts this to a "
            "URL-safe slug; do not pre-slugify."
        ),
    }


def _build_work_highlights_by_id_schema(
    portfolio: PortfolioData, max_pages: int
) -> dict[str, Any]:
    """Object keyed by work entry ID; each value is enum-constrained items.

    Work entries with zero highlights are omitted (Anthropic rejects
    empty ``enum``). The client adapter synthesizes empty
    ``WorkHighlightRanking`` instances for omitted entries to satisfy
    the validator's "every portfolio work entry has a ranking"
    invariant.

    The per-entry description carries a soft cap on emitted IDs derived
    from the renderer's per-position floor and the current
    ``max_pages``. The cap is enforced post-parse by the client adapter
    (Anthropic's structured-output API does not honor ``maxItems``).
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for position, w in enumerate(portfolio.work):
        wid = _check_id(w.id, "work entry")
        if not w.highlights:
            continue
        highlight_ids = [_check_id(h.id, f"highlight in {wid}") for h in w.highlights]
        emit_cap = _per_entry_emit_cap(position, max_pages)
        properties[wid] = {
            "type": "array",
            "description": (
                f"Highlights belonging to work entry '{wid}', ordered "
                f"strongest-first for the JD. Every emitted string "
                f"must be one of this entry's highlight IDs. Emit at "
                f"most {emit_cap} IDs (your top picks). The renderer "
                f"keeps the top entries that fit the {max_pages}-page "
                f"budget; emitting more than {emit_cap} wastes tokens "
                f"on IDs the renderer will discard."
            ),
            "items": {"type": "string", "enum": highlight_ids},
        }
        required.append(wid)
    return {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "Highlight rankings keyed by portfolio work entry ID. The "
            "property key identifies the parent work entry; the value "
            "lists that entry's highlight IDs ordered strongest-first "
            "for the JD. Cross-parent attribution is grammar-impossible: "
            "each key's enum is scoped to that entry's children."
        ),
        "required": required,
        "properties": properties,
    }


def _build_skills_schema(portfolio: PortfolioData) -> dict[str, Any]:
    """Top-level array of skill group IDs (hybrid AI/code skill selection).

    Wire shape: ``{"skills": ["skill-aws", "skill-devops", ...]}``. The
    model emits an *ordered* list of portfolio skill group IDs ranked
    by JD relevance (judgment). The client adapter then fills each
    group's keywords from portfolio data using a JD-relevance scorer
    (bookkeeping; see ``curator.jd_scorer``). The reconstructed
    ``list[SkillRanking]`` is what reaches the renderer.

    The group ID space is small enough (typically <30 groups) to
    encode as an ``items.enum`` without hitting Anthropic's
    compiled-grammar budget; the per-keyword 354-string surface that
    forced the earlier flat-array design (2026-05-13) is no longer on
    the wire. ``maxItems`` is not supported in structured output, so
    the cap on group count surfaces in the description text and is
    enforced post-parse by the adapter.
    """
    group_ids = [_check_id(g.id, "skill group") for g in portfolio.skills]
    items: dict[str, Any] = {"type": "string"}
    if group_ids:
        items["enum"] = group_ids
    return {
        "type": "array",
        "description": (
            f"Ordered list of portfolio skill group IDs, strongest "
            f"JD-fit first. Each ID must be the ID of a portfolio "
            f"skill group; the client fills in the keywords for each "
            f"group from portfolio data using JD-relevance scoring. "
            f"Emit at most {SKILL_GROUPS_MAX} groups. Omit any group "
            f"that is irrelevant to the JD; fewer well-targeted "
            f"groups beat broad coverage. May be empty when no group "
            f"is sufficiently JD-relevant. Order matters: the first "
            f"emitted group renders first in the skill section."
        ),
        "items": items,
    }


def _build_projects_schema(portfolio: PortfolioData) -> dict[str, Any]:
    """Array of project IDs, enum-constrained to portfolio.projects."""
    project_ids = [_check_id(p.id, "project") for p in portfolio.projects]
    items: dict[str, Any] = {"type": "string"}
    if project_ids:
        # Empty enum is a 400; if the portfolio has no projects, fall
        # back to an unconstrained string array. Validator still catches
        # any bogus ID post-parse.
        items["enum"] = project_ids
    return {
        "type": "array",
        "description": (
            "3-5 portfolio project IDs ordered by (JD fit x portfolio "
            "weight), strongest first. Weight-1 and weight-2 projects "
            "should appear unless they are genuinely unrelated. May be "
            "empty only when the portfolio has no projects or when "
            "nothing has any plausible JD connection."
        ),
        "items": items,
    }


def _build_resume_schema(portfolio: PortfolioData, max_pages: int) -> dict[str, Any]:
    """The resume-only schema body.

    Top-level field declaration order is load-bearing under constrained
    decoding (CLAUDE.md "Claude API & AI Best Practices"): the model
    emits fields in declared order, so ``summary`` first commits tone
    and framing before ``work_highlights_by_id`` ranking decisions.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "summary",
            "suggested_label",
            "company_name",
            "work_highlights_by_id",
            "skills",
            "projects",
        ],
        "properties": {
            "summary": _build_summary_schema(),
            "suggested_label": _build_suggested_label_schema(),
            "company_name": _build_company_name_schema(),
            "work_highlights_by_id": _build_work_highlights_by_id_schema(
                portfolio, max_pages
            ),
            "skills": _build_skills_schema(portfolio),
            "projects": _build_projects_schema(portfolio),
        },
    }


def _build_cover_letter_schema() -> dict[str, Any]:
    """Cover-letter sub-schema (used only when --cover-letter is on).

    Mirrors ``CoverLetterCuration``: salutation -> opening ->
    body_paragraph_1 -> body_paragraph_2 -> closing -> sign_off.
    Field order is preserved for the same constrained-decoding reason
    as the resume schema (commit salutation before opening, opening
    before body, etc.). Length constraints survive only as post-hoc
    Pydantic checks.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "salutation",
            "opening",
            "body_paragraph_1",
            "body_paragraph_2",
            "closing",
            "sign_off",
        ],
        "properties": {
            "salutation": {
                "type": "string",
                "description": (
                    "Greeting line. 'Dear [Name],' when the hiring "
                    "manager name is known, otherwise 'Dear Hiring "
                    "Manager,'. Never 'To Whom It May Concern'. Must "
                    "end with a comma."
                ),
            },
            "opening": {
                "type": "string",
                "description": (
                    "2-sentence hook with a company-specific reference. "
                    "Use an achievement lead, a specific origin story, "
                    "or a company-product hook. Never open with 'I am "
                    "writing to apply for' or any other boilerplate."
                ),
            },
            "body_paragraph_1": {
                "type": "string",
                "description": (
                    "First STAR-shaped body paragraph; strongest JD "
                    "match. 3-4 sentences, single topic. Every claim "
                    "must trace to portfolio data; include at least one "
                    "number, specific name, or concrete artifact."
                ),
            },
            "body_paragraph_2": {
                "type": "string",
                "description": (
                    "Second STAR-shaped body paragraph; second-strongest "
                    "JD match. Same shape as body_paragraph_1; cover a "
                    "different topic."
                ),
            },
            "closing": {
                "type": "string",
                "description": (
                    "2-sentence value recap plus subtle CTA "
                    "(e.g., 'I would welcome a conversation'). No "
                    "moral-reminder closers; no generic praise of the "
                    "company."
                ),
            },
            "sign_off": {
                "type": "string",
                "description": (
                    "Exactly one of the allowed sign-offs. No trailing "
                    "comma; the renderer adds it."
                ),
                "enum": sorted(COVER_LETTER_VALID_SIGN_OFFS),
            },
        },
    }


def build_curation_schema(
    portfolio: PortfolioData,
    *,
    with_cover_letter: bool = False,
    max_pages: int = 2,
) -> dict[str, Any]:
    """Build the JSON schema sent to Anthropic for a single ``curate()`` call.

    Args:
        portfolio: Loaded portfolio data. Schema is built from
            ``portfolio.work``, ``portfolio.skills``, and
            ``portfolio.projects`` in their existing deterministic
            order (YAML file order via ``loader.py``).
        with_cover_letter: When True, wrap the resume schema with a
            sibling ``cover_letter`` property mirroring
            ``CoverLetterCuration``.
        max_pages: Page budget for the current call. Drives the
            per-work-entry highlight-emission cap surfaced in each
            property's ``description`` text (Anthropic does not enforce
            ``maxItems``; the cap is a soft hint to the model and a
            hard limit applied post-parse by the client adapter).
            Defaults to 2 to match the project-wide default page
            budget.

    Returns:
        A dict ready to pass as the ``schema`` field of
        ``output_config.format`` on ``messages.stream``. Construction
        is deterministic: ``build_curation_schema(p, max_pages=N) ==
        build_curation_schema(p, max_pages=N)`` byte-for-byte across
        fresh process invocations as long as the input portfolio is
        byte-stable.
    """
    resume = _build_resume_schema(portfolio, max_pages)
    if not with_cover_letter:
        return resume
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["resume", "cover_letter"],
        "properties": {
            "resume": resume,
            "cover_letter": _build_cover_letter_schema(),
        },
    }
