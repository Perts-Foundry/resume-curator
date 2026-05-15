"""Build the per-call JSON schema for the Anthropic structured-output call.

The schema is constructed from the loaded ``PortfolioData`` at curate
time and injected via ``output_config.format`` on ``messages.stream``.
The grammar makes cross-parent ``highlight_id`` emission
decode-time-impossible (via per-property ``items.enum`` on
``work_highlights_by_id``). Skill keyword verbatim-match is enforced
only post-hoc by ``client._adapt_curation_dict`` and
``validate_curation_ids``, NOT at decode time: the 2026-05-13
production schema (object-keyed-by-skill-group, 27 inner properties)
exceeded Anthropic's compiled-grammar budget; the 2026-05-14 Haiku
probe sequence localized the binding axis to inner-property count
under ``required`` + ``additionalProperties: false``, forcing the
collapse to a flat top-level array that shipped 2026-05-15.

Shape (top-level):

    summary                 string
    suggested_label         string
    company_slug            string
    work_highlights_by_id   object[work_id -> array[items.enum]]
    skills                  array[string]  (flat keyword list)
    projects                array[items.enum]

Only ``work_highlights_by_id`` carries nested per-property
``items.enum``; the adapter walks each emitted ``skills`` keyword back
to its parent portfolio group (first-match by portfolio order) to
reconstruct ``list[SkillRanking]`` for the domain model. Anthropic's
grammar compiles per-property constraints independently (verified
empirically 2026-05-13).

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
  ``skills`` is a flat unconstrained string array (no nested
  per-group structure on the wire), so the empty-enum rule has no
  surface here.
- ``minLength`` / ``maxLength`` / ``pattern`` / ``minimum`` /
  ``maximum`` are not enforced at decode time. Constraints survive
  only as post-hoc Pydantic re-validation on the parsed dict.
- ``oneOf`` / ``dependentSchemas`` / ``if-then-else`` are
  unsupported; the design uses ``properties`` directly rather than
  any union construct.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from curator.rules import (
    COVER_LETTER_VALID_SIGN_OFFS,
    SUMMARY_MANDATORY_MENTION,
    SUMMARY_WORD_TARGET_MAX,
    SUMMARY_WORD_TARGET_MIN,
)

if TYPE_CHECKING:
    from curator.models import PortfolioData

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


def _build_company_slug_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "description": (
            "Kebab-case company name extracted from the JD. Use only "
            "[a-z0-9-], starting with [a-z0-9]. For 'Acme Corp.' return "
            "'acme-corp'. For subsidiaries like 'DataLabs (a Google "
            "company)' return the primary subsidiary name ('datalabs'). "
            "Strip corporate suffixes (Inc, Ltd, LLC, GmbH)."
        ),
    }


def _build_work_highlights_by_id_schema(portfolio: PortfolioData) -> dict[str, Any]:
    """Object keyed by work entry ID; each value is enum-constrained items.

    Work entries with zero highlights are omitted (Anthropic rejects
    empty ``enum``). The client adapter synthesizes empty
    ``WorkHighlightRanking`` instances for omitted entries to satisfy
    the validator's "every portfolio work entry has a ranking"
    invariant.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for w in portfolio.work:
        wid = _check_id(w.id, "work entry")
        if not w.highlights:
            continue
        highlight_ids = [_check_id(h.id, f"highlight in {wid}") for h in w.highlights]
        properties[wid] = {
            "type": "array",
            "description": (
                f"Highlights belonging to work entry '{wid}', ordered "
                f"strongest-first for the JD. Every emitted string "
                f"must be one of this entry's highlight IDs. Return ALL "
                f"of this entry's highlight IDs in ranked order; do not "
                f"omit highlights. The renderer trims from the bottom "
                f"based on page fit."
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
    """Flat top-level array of skill keyword strings.

    Wire shape: ``{"skills": ["EC2", "S3", "Kubernetes", ...]}``. The
    model emits keywords ordered by JD fit across all relevant
    portfolio skill groups. The client adapter walks each emitted
    keyword back to its parent portfolio group (first-match by
    portfolio iteration order) to reconstruct ``list[SkillRanking]``.

    There is NO ``items.enum``: the 354-string production surface
    across 22 skill groups exceeded Anthropic's compiled-grammar
    budget (HTTP 400 "compiled grammar is too large" on 2026-05-13).
    Verbatim-match enforcement lives in
    ``client._adapt_curation_dict`` (drops + WARN logs unknown
    keywords) and ``validate_curation_ids`` (defense in depth).

    The argument is unused at present (the flat shape carries no
    portfolio-derived enum), but the signature mirrors the other
    ``_build_*_schema`` builders so future tightening can hook in
    without a call-site change.
    """
    del portfolio  # see docstring
    return {
        "type": "array",
        "description": (
            "Flat list of skill keywords ordered by JD fit, strongest "
            "first across all relevant portfolio skill groups. Every "
            "emitted string MUST be a verbatim (case-sensitive) match "
            "of a keyword already present in some portfolio skill "
            "group's ``keywords`` list. The schema does not enforce "
            "this at decode time (compile-budget constraint, see "
            "module docstring); non-verbatim keywords are dropped "
            "post-hoc by the client adapter and surface as WARN log "
            "lines. If a keyword appears in multiple portfolio groups, "
            "include it only once. If a portfolio group is irrelevant "
            "to the JD, omit all of its keywords; do NOT pad with "
            "weak keywords to keep a group represented. May be empty "
            "when no portfolio group is sufficiently JD-relevant."
        ),
        "items": {"type": "string"},
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


def _build_resume_schema(portfolio: PortfolioData) -> dict[str, Any]:
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
            "company_slug",
            "work_highlights_by_id",
            "skills",
            "projects",
        ],
        "properties": {
            "summary": _build_summary_schema(),
            "suggested_label": _build_suggested_label_schema(),
            "company_slug": _build_company_slug_schema(),
            "work_highlights_by_id": _build_work_highlights_by_id_schema(portfolio),
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
    portfolio: PortfolioData, *, with_cover_letter: bool = False
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

    Returns:
        A dict ready to pass as the ``schema`` field of
        ``output_config.format`` on ``messages.stream``. Construction
        is deterministic: ``build_curation_schema(p) ==
        build_curation_schema(p)`` byte-for-byte across fresh process
        invocations as long as the input portfolio is byte-stable.
    """
    resume = _build_resume_schema(portfolio)
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
