"""Pydantic models for portfolio data and structured API output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self

from loguru import logger
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from curator.exceptions import CurationValidationError
from curator.rules import (
    COVER_LETTER_BODY_MAX_COUNT,
    COVER_LETTER_BODY_MIN_COUNT,
    COVER_LETTER_FORBIDDEN_PHRASES,
    COVER_LETTER_FORBIDDEN_WORDS,
    COVER_LETTER_PARAGRAPH_WORD_MAX,
    COVER_LETTER_PARAGRAPH_WORD_MIN,
    COVER_LETTER_PLACEHOLDER_PATTERN,
    COVER_LETTER_SALUTATION_FORBIDDEN_PHRASES,
    COVER_LETTER_VALID_SIGN_OFFS,
    COVER_LETTER_WORD_MAX,
    COVER_LETTER_WORD_MIN,
    SUMMARY_MANDATORY_MENTION,
    SUMMARY_WORD_HARD_MAX,
    SUMMARY_WORD_TARGET_MAX,
    SUMMARY_WORD_TARGET_MIN,
)

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

ResumeVariant = Literal["general", "devops", "security"]
CertificateType = Literal["professional", "course"]
PublicationType = Literal["blog", "talk", "paper", "presentation"]

# Sections the renderer writes as per-section YAML files.
RENDERER_SECTIONS: tuple[str, ...] = (
    "work",
    "skills",
    "projects",
    "certificates",
    "education",
)

# Subset of RENDERER_SECTIONS the AI produces rankings for.
# Education and certificates are renderer-managed (portfolio order + priority).
AI_RANKED_SECTIONS: tuple[str, ...] = (
    "work",
    "skills",
    "projects",
)

# Sections appended by the renderer after RENDERER_SECTIONS without any
# AI input or selection. Today this is just "interests"; kept as a tuple
# so the relationship to RENDERABLE_SECTIONS stays explicit.
RENDERER_MANAGED_SECTIONS: tuple[str, ...] = ("interests",)

# Sections that appear in the rendered PDF (renderer sections + renderer-managed).
RENDERABLE_SECTIONS: tuple[str, ...] = (
    *RENDERER_SECTIONS,
    *RENDERER_MANAGED_SECTIONS,
)

# Default empty-interests payload — the on-disk shape for an empty
# interests section. Centralized so renderer / golden materializer /
# tests stay in lockstep.
EMPTY_INTERESTS: dict[str, list[Any]] = {"hobbies": [], "fun_facts": []}

assert set(AI_RANKED_SECTIONS) <= set(RENDERER_SECTIONS), (
    "AI_RANKED_SECTIONS must be a subset of RENDERER_SECTIONS"
)

ID_PATTERN = r"^[a-z0-9][a-z0-9-]*$"


def _empty_to_none(v: str | None) -> str | None:
    """Normalize empty strings to None for optional date fields."""
    if v == "":
        return None
    return v


OptionalDate = Annotated[str | None, BeforeValidator(_empty_to_none)]


# ---------------------------------------------------------------------------
# Portfolio data models (YAML input boundary)
# ---------------------------------------------------------------------------


class Profile(BaseModel):
    """Social/professional profile link."""

    model_config = ConfigDict(frozen=True, extra="ignore", validate_default=True)

    network: str | None = None
    username: str | None = None
    url: str | None = None


class Location(BaseModel):
    """Physical location with optional detail fields."""

    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        validate_default=True,
        populate_by_name=True,
    )

    address: str | None = None
    postal_code: str | None = Field(default=None, validation_alias="postalCode")
    city: str | None = None
    country_code: str | None = Field(default=None, validation_alias="countryCode")
    region: str | None = None


class Basics(BaseModel):
    """Personal information and contact details (single object, not array)."""

    model_config = ConfigDict(frozen=True, extra="ignore", validate_default=True)

    name: str
    label: str | None = None
    email: str | None = None
    phone: str | None = None
    url: str | None = None
    summary: str | None = None
    location: Location | None = None
    profiles: list[Profile] = Field(default_factory=list)


class TaggedHighlight(BaseModel):
    """Accomplishment/bullet point with metadata for filtering."""

    model_config = ConfigDict(frozen=True, extra="ignore", validate_default=True)

    id: str = Field(pattern=ID_PATTERN)
    text: str
    tags: list[str] = Field(default_factory=list)
    resume_variants: list[ResumeVariant] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class WorkEntry(BaseModel):
    """Work experience entry with tagged highlights."""

    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        validate_default=True,
        populate_by_name=True,
    )

    id: str = Field(pattern=ID_PATTERN)
    name: str
    position: str
    url: str | None = None
    start_date: str = Field(validation_alias="startDate")
    end_date: OptionalDate = Field(default=None, validation_alias="endDate")
    location: str | None = None
    summary: str | None = None
    description: str | None = None
    highlights: list[TaggedHighlight] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    resume_variants: list[ResumeVariant] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class EducationEntry(BaseModel):
    """Education entry."""

    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        validate_default=True,
        populate_by_name=True,
    )

    id: str = Field(pattern=ID_PATTERN)
    institution: str
    url: str | None = None
    area: str | None = None
    minor: str | None = None
    study_type: str | None = Field(default=None, validation_alias="studyType")
    start_date: str | None = Field(default=None, validation_alias="startDate")
    end_date: OptionalDate = Field(default=None, validation_alias="endDate")
    score: str | None = None
    honors: str | None = None
    courses: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    resume_variants: list[ResumeVariant] = Field(default_factory=list)
    priority: int | None = Field(
        default=None,
        description="Renderer trim order. Lower = more important. Unset sorts last.",
    )


class SkillEntry(BaseModel):
    """Skill group with keywords."""

    model_config = ConfigDict(frozen=True, extra="ignore", validate_default=True)

    id: str = Field(pattern=ID_PATTERN)
    name: str
    level: str | None = None
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    resume_variants: list[ResumeVariant] = Field(default_factory=list)


class CertificateEntry(BaseModel):
    """Professional certification or course completion."""

    model_config = ConfigDict(frozen=True, extra="ignore", validate_default=True)

    id: str = Field(pattern=ID_PATTERN)
    name: str
    date: str
    type: CertificateType | None = None
    issuer: str | None = None
    tags: list[str] = Field(default_factory=list)
    resume_variants: list[ResumeVariant] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    priority: int | None = Field(
        default=None,
        description="Renderer trim order. Lower = more important. Unset sorts last.",
    )


class ProjectEntry(BaseModel):
    """Personal or open source project."""

    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        validate_default=True,
        populate_by_name=True,
    )

    id: str = Field(pattern=ID_PATTERN)
    name: str
    description: str | None = None
    url: str | None = None
    start_date: str | None = Field(default=None, validation_alias="startDate")
    end_date: OptionalDate = Field(default=None, validation_alias="endDate")
    type: str | None = None
    entity: str | None = None
    roles: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    highlights: list[TaggedHighlight] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    resume_variants: list[ResumeVariant] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    weight: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Portfolio preference (1 = highest). The AI blends this with JD "
            "fit when ranking projects: lower-weight projects are favored "
            "unless JD fit is dramatically worse."
        ),
    )


class VolunteerEntry(BaseModel):
    """Volunteer experience entry."""

    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        validate_default=True,
        populate_by_name=True,
    )

    id: str = Field(pattern=ID_PATTERN)
    organization: str
    position: str | None = None
    url: str | None = None
    start_date: str | None = Field(default=None, validation_alias="startDate")
    end_date: OptionalDate = Field(default=None, validation_alias="endDate")
    summary: str | None = None
    highlights: list[TaggedHighlight] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    resume_variants: list[ResumeVariant] = Field(default_factory=list)


class PublicationEntry(BaseModel):
    """Publication, talk, or presentation."""

    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        validate_default=True,
        populate_by_name=True,
    )

    id: str = Field(pattern=ID_PATTERN)
    name: str
    type: PublicationType | None = None
    publisher: str | None = None
    release_date: str | None = Field(default=None, validation_alias="releaseDate")
    url: str | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    resume_variants: list[ResumeVariant] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class LanguageEntry(BaseModel):
    """Language and fluency level."""

    model_config = ConfigDict(frozen=True, extra="ignore", validate_default=True)

    id: str = Field(pattern=ID_PATTERN)
    language: str
    fluency: str | None = None


class Hobby(BaseModel):
    """A hobby or interest with optional keywords."""

    model_config = ConfigDict(frozen=True, extra="ignore", validate_default=True)

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    keywords: list[str] = Field(default_factory=list)


class InterestData(BaseModel):
    """Hobbies and fun facts (single object, not array)."""

    model_config = ConfigDict(frozen=True, extra="ignore", validate_default=True)

    hobbies: list[Hobby] = Field(default_factory=list)
    fun_facts: list[str] = Field(default_factory=list)


class ServiceEntry(BaseModel):
    """Consulting service offering (context-only, not selectable)."""

    model_config = ConfigDict(frozen=True, extra="ignore", validate_default=True)

    id: str = Field(pattern=ID_PATTERN)
    name: str
    slug: str
    summary: str | None = None
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    weight: int | None = None


# ---------------------------------------------------------------------------
# Portfolio container (internal transfer object)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PortfolioData:
    """Assembled portfolio data from all YAML sections.

    Each section is individually validated via its Pydantic model before being
    collected here. This is an internal transfer object, not a validation
    boundary.
    """

    basics: Basics
    work: list[WorkEntry]
    education: list[EducationEntry]
    skills: list[SkillEntry]
    certificates: list[CertificateEntry]
    projects: list[ProjectEntry]
    volunteer: list[VolunteerEntry]
    publications: list[PublicationEntry]
    languages: list[LanguageEntry]
    interests: InterestData | None
    services: list[ServiceEntry]
    cover_letter: CoverLetterCuration | None = None


# ---------------------------------------------------------------------------
# Structured output models (API response boundary)
# ---------------------------------------------------------------------------

# C0 controls + DEL plus invisible formatting characters that web fonts
# render as .notdef boxes when pasted into application forms: U+00AD
# (SOFT HYPHEN), U+200B-U+200F (zero-width space, ZWNJ, ZWJ, LRM, RLM),
# U+FEFF (BOM / zero-width no-break space). Disabling Typst auto-
# hyphenation removes one source; this regex catches the rest if a
# contributor pastes them into YAML directly.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f­​-‏﻿]")


class WorkHighlightRanking(BaseModel):
    """Highlight ordering for a single work entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    work_id: str = Field(
        description=(
            "ID of a portfolio work entry. You MUST return one "
            "WorkHighlightRanking per portfolio work entry; the validator "
            "hard-rejects curations missing any portfolio work entry."
        ),
    )
    highlight_ids: list[str] = Field(
        description=(
            "Portfolio highlight IDs from this work entry, ordered "
            "strongest-first for the target JD. Return ALL highlights from "
            "the portfolio entry in ranked order; do not omit highlights. "
            "The renderer trims from the bottom based on page fit."
        ),
    )


class SkillRanking(BaseModel):
    """A skill group with keyword-level filtering and ordering."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    skill_id: str = Field(description="ID of a portfolio skill group")
    keywords: list[str] = Field(
        min_length=1,
        description=(
            "Verbatim subset of portfolio keywords in this skill group, "
            "ordered by relevance. Every string MUST be a case-sensitive "
            "match of a keyword already present in the portfolio skill "
            "group. Never add keywords from the job description that are "
            "not already in the portfolio."
        ),
    )


class ResumeCuration(BaseModel):
    """Complete curation result from Claude.

    Used as the output_format for messages.stream(). Field order matters:
    summary is first so Claude commits to tone and framing before ranking
    (constrained decoding produces fields in schema order).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(
        min_length=1,
        max_length=600,
        description=(
            f"2-3 sentence tailored professional summary, "
            f"{SUMMARY_WORD_TARGET_MIN}-{SUMMARY_WORD_TARGET_MAX} words soft "
            f"target / {SUMMARY_WORD_HARD_MAX} word hard max. No first "
            f"person, no subjective filler. Must include "
            f"'{SUMMARY_MANDATORY_MENTION}' verbatim. Open with title + "
            "years of experience, close with capability value prop."
        ),
    )
    suggested_label: str = Field(
        min_length=1,
        max_length=60,
        description=(
            "2-5 word professional title tailored to the JD "
            "(e.g. 'Staff DevOps Engineer'). Reflect actual portfolio "
            "seniority; never fabricate a level the candidate has not "
            "reached."
        ),
    )
    company_slug: str = Field(
        pattern=ID_PATTERN,
        max_length=64,
        description=(
            "Kebab-case company name extracted from the job description. "
            "Use only [a-z0-9-], start with [a-z0-9]. For 'Acme Corp.' "
            "return 'acme-corp'. For subsidiaries like 'DataLabs (a Google "
            "company)' return the primary subsidiary name ('datalabs'). "
            "Strip corporate suffixes (Inc, Ltd, LLC, GmbH)."
        ),
    )
    work_highlights: list[WorkHighlightRanking] = Field(
        min_length=1,
        description=(
            "One ranking per portfolio work entry. You MUST return a "
            "ranking for every work entry in the portfolio; omission is "
            "rejected by the validator. Entry order does not matter "
            "(the renderer sorts reverse-chronologically)."
        ),
    )
    skills: list[SkillRanking] = Field(
        description=(
            "Relevant skill groups ordered by JD fit, each with filtered "
            "keywords. May be any non-negative length."
        ),
    )
    projects: list[str] = Field(
        description=(
            "3-5 portfolio project IDs ordered by (JD fit x portfolio "
            "weight), strongest first. Weight-1 and weight-2 projects "
            "should appear unless they are genuinely unrelated to the "
            "role. MAY be an empty list only when the portfolio has no "
            "projects at all, or when nothing has any plausible "
            "connection to the JD."
        ),
    )

    @field_validator("summary", "suggested_label")
    @classmethod
    def _no_control_chars(cls, v: str) -> str:
        if _CONTROL_CHAR_RE.search(v):
            msg = "contains control characters"
            raise ValueError(msg)
        return v


# ---------------------------------------------------------------------------
# Curation ID validation (Layer 3)
# ---------------------------------------------------------------------------


def validate_curation_ids(
    curation: ResumeCuration, portfolio: PortfolioData
) -> ResumeCuration:
    """Verify curation IDs against the portfolio; return a sanitized copy.

    Applies the same checks to both API-sourced and statically-synthesized
    curations. Accumulates hard errors into a single message for
    debuggability. Returns a new ``ResumeCuration`` with hallucinated
    skill keywords dropped (soft warn) so callers don't silently keep
    invalid output.

    Work highlights: one ranking per portfolio work entry is required
    (hard fail on missing or unknown entries, hard fail on unknown
    highlight IDs). Partial highlight lists are allowed and logged as
    WARNING; the renderer safety-net appends omitted IDs in portfolio order.

    Skills: unknown group IDs are still a hard failure (model invented a
    section that doesn't exist). Non-verbatim keywords inside a known
    group are a SOFT warning: the bogus keyword is dropped from the
    returned curation, the rest of the group is preserved, and a
    WARNING line names every drop. This was the right trade after
    repeated runs where the model emitted JD-listed AWS services
    (e.g., RDS, Route53) under ``cloud-aws`` despite the verbatim-only
    rule; hard-rejecting on hallucinated keywords burned paid calls
    without producing a usable resume on the next attempt.

    Projects: unknown IDs are hard failures; empty list is valid.

    Args:
        curation: Structured curation output to validate.
        portfolio: Loaded portfolio to validate against.

    Returns:
        A ``ResumeCuration`` with hallucinated skill keywords removed.
        Other fields are unchanged. Callers MUST use this return value
        instead of the original ``curation`` so dropped keywords don't
        leak into the renderer.

    Raises:
        CurationValidationError: On any HARD ID mismatch (unknown
            work/highlight/skill_group/project IDs, missing rankings,
            duplicates). Callers that want the historical
            ``APIResponseError`` behavior (e.g., ``CuratorClient``)
            should catch this and re-wrap.
    """
    valid_work_ids = {w.id for w in portfolio.work}
    valid_highlight_ids: dict[str, set[str]] = {
        w.id: {h.id for h in w.highlights} for w in portfolio.work
    }

    errors: list[str] = []

    # --- work_highlights: unknown, duplicate, missing, highlight IDs ---

    seen_work_ids: set[str] = set()
    duplicate_work_ids: list[str] = []
    for wh in curation.work_highlights:
        if wh.work_id in seen_work_ids:
            duplicate_work_ids.append(wh.work_id)
        seen_work_ids.add(wh.work_id)

        if wh.work_id not in valid_work_ids:
            errors.append(f"unknown work_id: '{wh.work_id}'")
        else:
            entry_highlights = valid_highlight_ids[wh.work_id]
            errors.extend(
                f"unknown highlight_id '{hid}' in work entry '{wh.work_id}'"
                for hid in wh.highlight_ids
                if hid not in entry_highlights
            )
            omitted = entry_highlights - set(wh.highlight_ids)
            if omitted:
                logger.warning(
                    "Work entry '{}': {}/{} highlights ranked, "
                    "{} will be appended by safety net",
                    wh.work_id,
                    len(wh.highlight_ids),
                    len(entry_highlights),
                    len(omitted),
                )

    if duplicate_work_ids:
        errors.append(f"duplicate work_ids: {sorted(set(duplicate_work_ids))}")

    missing_work_ids = valid_work_ids - seen_work_ids
    if missing_work_ids:
        errors.append(f"missing ranking for work entries: {sorted(missing_work_ids)}")

    # --- skills: unknown group IDs are hard; hallucinated keywords are soft ---

    valid_skill_ids = {s.id: set(s.keywords) for s in portfolio.skills}
    sanitized_skills: list[SkillRanking] = []
    for sr in curation.skills:
        if sr.skill_id not in valid_skill_ids:
            errors.append(f"unknown skill group id: '{sr.skill_id}'")
            sanitized_skills.append(sr)  # preserve for error context only
            continue
        valid_keywords = valid_skill_ids[sr.skill_id]
        kept = [kw for kw in sr.keywords if kw in valid_keywords]
        dropped = [kw for kw in sr.keywords if kw not in valid_keywords]
        if dropped:
            logger.warning(
                "Skill group '{}': dropped {} hallucinated keyword(s) not in "
                "portfolio: {}. Kept {}/{}.",
                sr.skill_id,
                len(dropped),
                dropped,
                len(kept),
                len(sr.keywords),
            )
        sanitized_skills.append(
            sr.model_copy(update={"keywords": kept}) if dropped else sr
        )

    # --- projects: unknown IDs (empty list is valid) ---

    valid_project_ids = {p.id for p in portfolio.projects}
    errors.extend(
        f"unknown project id: '{pid}'"
        for pid in curation.projects
        if pid not in valid_project_ids
    )

    if errors:
        msg = f"Curation contains {len(errors)} invalid ID(s): {'; '.join(errors)}"
        raise CurationValidationError(msg)

    # All hard checks passed. Return the original instance unchanged when
    # no keywords were dropped (preserves object identity for callers that
    # rely on it); otherwise return a copy with sanitized skills.
    any_dropped = any(
        new is not old
        for new, old in zip(sanitized_skills, curation.skills, strict=False)
    )
    if not any_dropped:
        return curation
    return curation.model_copy(update={"skills": sanitized_skills})


# ---------------------------------------------------------------------------
# Cover letter structured output
# ---------------------------------------------------------------------------
#
# Class validators here are deliberately minimal (em-dash, control chars,
# paragraph count, salutation/sign-off surface structure). Policy-level
# checks (word counts, forbidden phrases, placeholder handling) live in
# ``validate_cover_letter`` so both static-mode (placeholder-bearing) and
# API-mode (final) letters share one matcher and the forbidden-phrase list
# cannot drift between prompt and validator.
#
# Anthropic structured outputs strip JSON Schema string-length / array-count
# constraints from the compiled grammar. Length / count rules are enforced
# at Pydantic parse time after the SDK returns, not at generation time.

_EM_DASH_RE = re.compile(r"[\u2013\u2014]")
_WORD_RE = re.compile(r"\b\w+\b")
_PLACEHOLDER_RE = re.compile(COVER_LETTER_PLACEHOLDER_PATTERN)
_FORBIDDEN_WORDS_RE = re.compile(
    r"\b("
    + "|".join(re.escape(w) for w in sorted(COVER_LETTER_FORBIDDEN_WORDS))
    + r")\b",
)
# Phrases use word-bounded substring match (lookarounds), so a forbidden
# phrase tucked inside a larger token (e.g., 'supper my resume') does not
# false-positive while legitimate hyphenated compounds are still caught.
_FORBIDDEN_PHRASES_RES = tuple(
    re.compile(r"(?<!\w)" + re.escape(p) + r"(?!\w)")
    for p in sorted(COVER_LETTER_FORBIDDEN_PHRASES)
)


class CoverLetterCuration(BaseModel):
    """Structured cover letter content.

    Field order is chosen for schema-based reasoning under constrained
    decoding: salutation first (commits the addressing decision), then
    opening (sets tone), body (substantive STAR paragraphs), closing
    (value recap + CTA), sign_off last.

    Length, per-paragraph word counts, forbidden phrases, and grounding
    rules live in ``validate_cover_letter`` and are enforced after parse.

    Body paragraphs are split into two distinct fields rather than a
    ``list[str]`` of length 2 because Anthropic's structured-output
    grammar strips array length constraints (``min_length`` /
    ``max_length`` are advisory, not enforced at decode time). The
    tuple shape forces the model to emit exactly two paragraphs at
    grammar level, eliminating the "got 4 paragraphs" failure mode
    observed in the 2026-05-09 Haiku 4.5 cross-model evaluation.
    Downstream consumers (renderer, validator, Typst template) keep
    accessing ``cover_letter.body_paragraphs`` via the computed_field
    below; the tuple shape is purely an API-surface decision.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    salutation: str = Field(
        max_length=200,
        description=(
            "Greeting line. 'Dear [Name],' when the hiring manager name is "
            "known, otherwise 'Dear Hiring Manager,'. Never "
            "'To Whom It May Concern'. Must end with a comma."
        ),
    )
    opening: str = Field(
        max_length=2000,
        description=(
            "2 sentence hook, 55-65 words (prompt-steering band; not "
            "directly enforced by the post-parse validator). Contains a "
            "company-specific reference. Use an achievement lead, a "
            "specific origin story, or a company-product hook. Never open "
            "with 'I am writing to apply for' or any other boilerplate "
            "opener."
        ),
    )
    body_paragraph_1: str = Field(
        max_length=2000,
        description=(
            "First STAR-shaped body paragraph, ordered by JD relevance "
            f"(the strongest match goes here). 80 to {COVER_LETTER_PARAGRAPH_WORD_MAX} "
            "words, 3-4 sentences, single topic. Every claim must trace "
            "to portfolio data; include at least one number, specific "
            "name, or concrete artifact."
        ),
    )
    body_paragraph_2: str = Field(
        max_length=2000,
        description=(
            "Second STAR-shaped body paragraph, ordered by JD relevance "
            "(the second-strongest match goes here). Same shape as "
            "body_paragraph_1: 80 to "
            f"{COVER_LETTER_PARAGRAPH_WORD_MAX} words, 3-4 sentences, "
            "single topic, every claim grounded in portfolio data. Cover "
            "a different topic than body_paragraph_1."
        ),
    )
    closing: str = Field(
        max_length=2000,
        description=(
            "2 sentence value recap plus subtle CTA "
            "(e.g., 'I would welcome a conversation'), 35-45 words "
            "(prompt-steering band; not directly enforced by the "
            "post-parse validator). No moral-reminder closers. No generic "
            "praise of the company."
        ),
    )
    sign_off: str = Field(
        max_length=50,
        description=(
            "Exactly one of: Sincerely, Best regards, Kind regards, Regards, "
            "Best. No trailing comma; the renderer adds it."
        ),
    )

    # -- Structural class validators ---------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_body_paragraphs(cls, data: Any) -> Any:
        """Accept legacy ``body_paragraphs: [p1, p2]`` and convert to the
        tuple shape.

        On-disk artifacts (``data/cover_letter.yaml`` in profile dirs and
        portfolio cover-letter files) and existing tests construct the
        model with ``body_paragraphs`` as a list. The schema-facing API
        uses the tuple shape (``body_paragraph_1`` / ``body_paragraph_2``)
        for grammar-level enforcement of the exactly-2 constraint. This
        validator bridges the two: legacy callers stay valid, the AI
        sees the tuple shape.
        """
        if not isinstance(data, dict):
            return data
        if "body_paragraphs" not in data:
            return data
        paras = data["body_paragraphs"]
        if not isinstance(paras, list):
            msg = (
                f"legacy body_paragraphs must be a list; got "
                f"{type(paras).__name__}"
            )
            raise ValueError(msg)
        if len(paras) != COVER_LETTER_BODY_MAX_COUNT:
            msg = (
                f"legacy body_paragraphs must have exactly "
                f"{COVER_LETTER_BODY_MAX_COUNT} entries; got {len(paras)}"
            )
            raise ValueError(msg)
        new_data = {k: v for k, v in data.items() if k != "body_paragraphs"}
        new_data["body_paragraph_1"] = paras[0]
        new_data["body_paragraph_2"] = paras[1]
        return new_data

    @field_validator(
        "salutation",
        "opening",
        "body_paragraph_1",
        "body_paragraph_2",
        "closing",
        "sign_off",
    )
    @classmethod
    def _no_control_chars(cls, v: str) -> str:
        if _CONTROL_CHAR_RE.search(v):
            msg = "contains control characters"
            raise ValueError(msg)
        return v

    @field_validator(
        "opening", "body_paragraph_1", "body_paragraph_2", "closing"
    )
    @classmethod
    def _no_em_dashes_in_prose(cls, v: str) -> str:
        if _EM_DASH_RE.search(v):
            msg = (
                "contains em dash or en dash; use commas, semicolons, "
                "parentheses, or periods"
            )
            raise ValueError(msg)
        return v

    @field_validator("salutation")
    @classmethod
    def _salutation_shape(cls, v: str) -> str:
        if not v.rstrip().endswith(","):
            msg = "salutation must end with a comma"
            raise ValueError(msg)
        return v

    @field_validator("sign_off")
    @classmethod
    def _sign_off_shape(cls, v: str) -> str:
        if v.rstrip().endswith(","):
            msg = "sign_off has a trailing comma; the renderer adds it"
            raise ValueError(msg)
        if v not in COVER_LETTER_VALID_SIGN_OFFS:
            msg = (
                f"sign_off must be one of {sorted(COVER_LETTER_VALID_SIGN_OFFS)}; "
                f"got '{v}'"
            )
            raise ValueError(msg)
        return v

    # Tuple shape forces exactly 2 body paragraphs; with that fixed, the
    # total is always 4 (opening + body_1 + body_2 + closing). Kept as a
    # narrative invariant rather than a runtime check.

    @computed_field  # type: ignore[prop-decorator]
    @property
    def body_paragraphs(self) -> list[str]:
        """Backwards-compat list view used by validator, renderer, Typst.

        The schema-facing fields are ``body_paragraph_1`` and
        ``body_paragraph_2`` (tuple shape, grammar-enforced count). Every
        downstream consumer (``validate_cover_letter``, ``renderer.py``
        cover-letter writer, the Typst template's
        ``letter.body_paragraphs``) reads from this property.
        """
        return [self.body_paragraph_1, self.body_paragraph_2]


class ResumeCurationWithCoverLetter(BaseModel):
    """Wrapper output schema used only when ``--cover-letter`` is on.

    Composition, not inheritance: ``ResumeCuration`` is untouched so every
    existing consumer continues to work. The wrapper is used as
    ``output_format`` for the Anthropic structured-output call; the client
    then splits the parsed response into resume and cover letter.

    Field order places ``resume`` before ``cover_letter`` so the model
    commits to curation decisions before writing the letter, letting the
    letter reference those selections under constrained decoding.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    resume: ResumeCuration
    cover_letter: CoverLetterCuration


def _count_words(text: str) -> int:
    """Count word tokens in text using a simple word-boundary regex."""
    return len(_WORD_RE.findall(text))


def _forbidden_words_hits(text: str) -> list[str]:
    """Return forbidden cliche words present in *text*, lowercase-only.

    Match is case-sensitive against the lowercase patterns, so capitalized
    proper-noun occurrences (e.g., a target company whose name happens to
    be one of the forbidden metaphor words) are exempted while lowercase
    metaphor uses ("a rich beacon of experience") still trip. Trade-off:
    a metaphor that happens to start a sentence ("Beacon of skills...")
    will slip through, but well-structured STAR body paragraphs rarely
    open with abstract metaphors. See [TEST-4] in TODO.md for the
    rationale.
    """
    return sorted(set(_FORBIDDEN_WORDS_RE.findall(text)))


def _forbidden_phrases_hits(text: str) -> list[str]:
    """Return forbidden phrases present in *text*, word-bounded.

    Match is case-sensitive against the lowercase patterns; same
    rationale as ``_forbidden_words_hits`` (capitalized proper-noun
    occurrences are exempted, lowercase cliche uses still trip).

    Phrases match only at word boundaries on both ends, so 'go-getter'
    inside 'go-getter-like' would still match (hyphen is a non-word char
    so the right boundary is satisfied) but inside a single word like
    'gogetterland' it would not.
    """
    sorted_phrases = sorted(COVER_LETTER_FORBIDDEN_PHRASES)
    return [
        sorted_phrases[i]
        for i, regex in enumerate(_FORBIDDEN_PHRASES_RES)
        if regex.search(text)
    ]


def _placeholder_hits(text: str) -> list[str]:
    """Return any ``[UPPERCASE]`` / ``[TAILOR: ...]`` tokens present."""
    return sorted(set(_PLACEHOLDER_RE.findall(text)))


#: Per-section prompt-steering word bands. The prompt's Structure block
#: tells the model these ranges, but only ``opening`` and ``closing``
#: under-min cases are enforced as hard rejects; over-max is a soft
#: warning paralleling the total-cap behavior. Body bands are enforced
#: separately via the ``COVER_LETTER_PARAGRAPH_WORD_*`` constants in
#: ``rules.py`` (which the prompt also references).
_OPENING_WORD_MIN: int = 55
_OPENING_WORD_MAX: int = 65
_CLOSING_WORD_MIN: int = 35
_CLOSING_WORD_MAX: int = 45


def validate_cover_letter(
    letter: CoverLetterCuration,
    portfolio: PortfolioData,
    *,
    strict: bool = False,
) -> None:
    """Policy-level validation of a cover letter.

    Runs after Pydantic parse. Enforces word counts, per-paragraph word
    bounds, forbidden-word / forbidden-phrase rules, bracketed-placeholder
    rejection (no ``[COMPANY]`` / ``[ROLE]`` / ``[TAILOR: ...]`` tokens
    anywhere), and salutation-specific rules. Applies uniformly to
    portfolio-authored static letters and API-generated letters.

    ``portfolio`` is reserved for a future grounding check that traces
    numeric and named claims back to portfolio entries. Do not remove.

    Args:
        letter: Parsed cover letter.
        portfolio: Reserved for future grounding check (unused today).
        strict: When True, over-max word counts (total + per-section) are
            promoted from soft warnings to hard rejects. Used by the
            static path so hand-authored YAMLs that overshoot the bands
            fail loudly with a pointer to the authoring guide; the API
            path keeps the soft-warn so paid-call output ships even at
            +5%-10% overshoot. Defaults to False (API behavior).

    Raises:
        CurationValidationError: On any policy violation.
    """
    errors: list[str] = []

    # -- Word counts ------------------------------------------------------

    opening_words = _count_words(letter.opening)
    body_words_per_paragraph = [_count_words(p) for p in letter.body_paragraphs]
    closing_words = _count_words(letter.closing)
    total_words = opening_words + sum(body_words_per_paragraph) + closing_words

    # Under-min is always a hard reject (stunted letters fail the product
    # spec). Over-max behavior depends on ``strict``: API path soft-warns
    # (model tends to overshoot 5-10%, paid calls are expensive); static
    # path hard-rejects (hand-authored YAMLs should be fixed locally).
    if total_words < COVER_LETTER_WORD_MIN:
        errors.append(
            f"total word count {total_words} below minimum {COVER_LETTER_WORD_MIN}"
        )
    elif total_words > COVER_LETTER_WORD_MAX:
        over = total_words - COVER_LETTER_WORD_MAX
        if strict:
            errors.append(
                f"total word count {total_words} exceeds maximum "
                f"{COVER_LETTER_WORD_MAX} (over by {over})"
            )
        else:
            logger.warning(
                f"Cover letter total word count {total_words} exceeds target "
                f"cap {COVER_LETTER_WORD_MAX} (over by {over}). Letter will "
                "still be written; trim manually if required by submission "
                "rules."
            )

    # Per-paragraph body bands: under-min hard, over-max strict-aware.
    for i, words in enumerate(body_words_per_paragraph):
        if words < COVER_LETTER_PARAGRAPH_WORD_MIN:
            errors.append(
                f"body paragraph {i + 1} word count {words} below "
                f"per-paragraph minimum {COVER_LETTER_PARAGRAPH_WORD_MIN}"
            )
        elif words > COVER_LETTER_PARAGRAPH_WORD_MAX:
            over = words - COVER_LETTER_PARAGRAPH_WORD_MAX
            if strict:
                errors.append(
                    f"body paragraph {i + 1} word count {words} exceeds "
                    f"per-paragraph maximum "
                    f"{COVER_LETTER_PARAGRAPH_WORD_MAX} (over by {over})"
                )
            else:
                logger.warning(
                    f"Cover letter body paragraph {i + 1} word count {words} "
                    f"exceeds per-paragraph cap "
                    f"{COVER_LETTER_PARAGRAPH_WORD_MAX} (over by {over}). "
                    "Letter will still be written; trim manually if required "
                    "by submission rules."
                )

    # Per-section opening / closing bands (_OPENING_WORD_*, _CLOSING_WORD_*)
    # are intentionally NOT enforced here today. The prompt steers the
    # model toward 55-65 / 35-45 in its Structure block, but real-world
    # generated letters routinely land at 70-90 word openings without
    # being lower-quality, and rejecting on those would force false-
    # positive partial-recovery flows. Keep the constants as
    # documentation of the prompt-steering target; revisit enforcement
    # if a future testing rerun shows the band drift becoming a quality
    # signal worth catching at validation time. PR-7 (deferred).

    # -- Placeholders / forbidden words / forbidden phrases ---------------

    body_scope = " ".join([letter.opening, *letter.body_paragraphs, letter.closing])

    # Placeholder scan covers salutation too; a bare "Dear [HIRING_MANAGER_NAME],"
    # is the easiest authoring mistake and must not slip through.
    placeholder_scope = letter.salutation + " " + body_scope
    placeholder_hits = _placeholder_hits(placeholder_scope)
    if placeholder_hits:
        errors.append(f"unfilled placeholder tokens: {placeholder_hits}")

    # Forbidden-word / forbidden-phrase matches run against original-case
    # text so capitalized proper-noun occurrences (target company names
    # that happen to collide with a forbidden metaphor word, e.g. "Beacon")
    # are exempted while lowercase metaphor uses still trip. See [TEST-4]
    # in TODO.md.
    word_hits = _forbidden_words_hits(body_scope)
    if word_hits:
        errors.append(f"forbidden words: {sorted(word_hits)}")

    phrase_hits = _forbidden_phrases_hits(body_scope)
    if phrase_hits:
        errors.append(f"forbidden phrases: {phrase_hits}")

    # -- Salutation-specific -----------------------------------------------

    salutation_lower = letter.salutation.lower()
    errors.extend(
        f"salutation contains forbidden phrase: '{phrase}'"
        for phrase in COVER_LETTER_SALUTATION_FORBIDDEN_PHRASES
        if phrase in salutation_lower
    )

    if errors:
        msg = f"Cover letter failed {len(errors)} policy check(s): {'; '.join(errors)}"
        raise CurationValidationError(msg)
