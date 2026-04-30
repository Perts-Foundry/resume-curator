"""Shared constants for resume quality rules.

Single source of truth for word lists, thresholds, and quality constants
used by both the curation prompt (``prompt.py``) and the evaluation
framework (``eval/``). Detection forms are canonical lowercase for
matching; rendering functions format them as prompt-ready prose.

Sources: external resume-best-practices reference (Appendix A Action
Verbs, Appendix B Weak Phrases, Appendix C AI Red Flags). Word lists are
maintained here in-repo; update both the constant and any review tests
in lockstep.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Weak phrases (Appendix B) — passive/vague language to never use
# ---------------------------------------------------------------------------

WEAK_PHRASES: frozenset[str] = frozenset(
    {
        "responsible for",
        "helped",
        "assisted",
        "worked on",
        "worked with",
        "participated in",
        "was involved in",
        "duties included",
        "supported",
        "managed",  # overused per Appendix B
        "handled",
        "tried",
        "utilized",
    }
)

# ---------------------------------------------------------------------------
# AI red-flag words (Appendix C) — signal low-effort AI output
# ---------------------------------------------------------------------------

AI_RED_FLAG_WORDS: frozenset[str] = frozenset(
    {
        "delve",
        "leverage",
        "leveraged",
        "synergy",
        "synergies",
        "pivotal",
        "realm",
        "showcasing",
        # "spearheaded" deliberately excluded — also in ACTION_VERBS.
        # Contextual judgment ("inflating minor contributions") deferred
        # to Phase C LLM judge; deterministic eval can't distinguish.
        "instrumentalized",
        "facilitate",
        "facilitated",
        "holistic",
    }
)

AI_RED_FLAG_PHRASES: frozenset[str] = frozenset(
    {
        "results-driven professional with a proven track record",
        "cross-functional synergies",
        "facilitated knowledge transfer",
        "dynamic and innovative leader",
        "passionate about driving excellence",
        "seeking a challenging position where i can utilize my skills",
        "proven ability to",
        "strong communication skills",
        "motivated team player",
        "leveraged cross-functional collaboration to drive strategic initiatives",
    }
)

# ---------------------------------------------------------------------------
# Action verbs (Appendix A) — valid bullet-starting verbs
# ---------------------------------------------------------------------------

ACTION_VERBS: frozenset[str] = frozenset(
    {
        # Leadership / Management
        "directed",
        "guided",
        "headed",
        "oversaw",
        "supervised",
        "mentored",
        "coached",
        "established",
        "chaired",
        "administered",
        "assigned",
        "consolidated",
        "coordinated",
        "delegated",
        "executed",
        "organized",
        "planned",
        "prioritized",
        "produced",
        "recommended",
        "reorganized",
        "scheduled",
        "mobilized",
        "streamlined",
        "championed",
        "drove",
        "founded",
        "initiated",
        "led",
        "pioneered",
        "spearheaded",
        # Technical / Engineering
        "architected",
        "engineered",
        "developed",
        "implemented",
        "optimized",
        "scaled",
        "debugged",
        "deployed",
        "automated",
        "configured",
        "integrated",
        "migrated",
        "refactored",
        "standardized",
        "programmed",
        "designed",
        "built",
        "tested",
        "validated",
        "monitored",
        # Achievement / Impact
        "increased",
        "decreased",
        "reduced",
        "accelerated",
        "boosted",
        "generated",
        "exceeded",
        "secured",
        "launched",
        "revitalized",
        "transformed",
        "solved",
        "introduced",
        "delivered",
        "achieved",
        "surpassed",
        "maximized",
        "minimized",
        "strengthened",
        "eliminated",
        "enhanced",
        "expanded",
        "saved",
        "resolved",
        # Communication / Interpersonal
        "addressed",
        "advised",
        "arbitrated",
        "authored",
        "collaborated",
        "corresponded",
        "counseled",
        "demonstrated",
        "explained",
        "influenced",
        "interpreted",
        "mediated",
        "moderated",
        "negotiated",
        "persuaded",
        "presented",
        "promoted",
        "proposed",
        "publicized",
        "recruited",
        "reported",
        "conveyed",
        "convinced",
        "translated",
        # Research / Analysis
        "analyzed",
        "assessed",
        "audited",
        "calculated",
        "diagnosed",
        "evaluated",
        "examined",
        "explored",
        "forecasted",
        "identified",
        "investigated",
        "mapped",
        "measured",
        "modeled",
        "quantified",
        "researched",
        "studied",
        "surveyed",
        # Creative / Design
        "conceptualized",
        "created",
        "customized",
        "devised",
        "formulated",
        "illustrated",
        "innovated",
        "invented",
        "originated",
        "revamped",
        "shaped",
        "visualized",
        # Financial
        "allocated",
        "appraised",
        "balanced",
        "budgeted",
        "computed",
        "projected",
        # Additional engineering verbs
        "upgraded",
        "completed",
        "modernized",
        "onboarded",
        "tuned",
        # Present tense forms (for current roles)
        "direct",
        "guide",
        "head",
        "oversee",
        "supervise",
        "mentor",
        "coach",
        "establish",
        "chair",
        "administer",
        "assign",
        "consolidate",
        "coordinate",
        "delegate",
        "execute",
        "organize",
        "plan",
        "prioritize",
        "produce",
        "recommend",
        "reorganize",
        "schedule",
        "mobilize",
        "streamline",
        "champion",
        "drive",
        # "found" excluded — ambiguous (past of "find" vs present of "found").
        "initiate",
        "lead",
        "pioneer",
        "architect",
        "engineer",
        "develop",
        "implement",
        "optimize",
        "scale",
        "debug",
        "deploy",
        "automate",
        "configure",
        "integrate",
        "migrate",
        "refactor",
        "standardize",
        "program",
        "design",
        "build",
        "test",
        "validate",
        "monitor",
        "increase",
        "decrease",
        "reduce",
        "accelerate",
        "boost",
        "generate",
        "exceed",
        "secure",
        "launch",
        "revitalize",
        "transform",
        "solve",
        "introduce",
        "deliver",
        "achieve",
        "surpass",
        "maximize",
        "minimize",
        "strengthen",
        "eliminate",
        "enhance",
        "expand",
        "save",
        "resolve",
        "address",
        "advise",
        "collaborate",
        "negotiate",
        "present",
        "promote",
        "propose",
        "analyze",
        "assess",
        "audit",
        "calculate",
        "diagnose",
        "evaluate",
        "examine",
        "explore",
        "forecast",
        "identify",
        "investigate",
        "map",
        "measure",
        "model",
        "quantify",
        "research",
        "study",
        "survey",
        "create",
        "customize",
        "devise",
        "formulate",
        "innovate",
        "invent",
        "allocate",
        "budget",
        "compute",
        "project",
        # Additional engineering verbs (present tense)
        "upgrade",
        "complete",
        "modernize",
        "onboard",
        "tune",
    }
)

# ---------------------------------------------------------------------------
# Trivial and soft skills (§4.7) — should not appear in skills section
# ---------------------------------------------------------------------------

TRIVIAL_SKILLS: frozenset[str] = frozenset(
    {
        "microsoft office",
        "ms office",
        "email",
        "typing",
        "word",
        "excel basics",
        "powerpoint",
        "internet",
        "google docs",
    }
)

SOFT_SKILLS: frozenset[str] = frozenset(
    {
        "communication",
        "teamwork",
        "problem solving",
        "problem-solving",
        "leadership",
        "time management",
        "critical thinking",
        "work ethic",
        "adaptability",
        "interpersonal skills",
        "attention to detail",
        "organizational skills",
    }
)

# ---------------------------------------------------------------------------
# Common tech acronym expansions (§3.4, §4.7)
# ---------------------------------------------------------------------------

ACRONYM_EXPANSIONS: dict[str, str] = {
    "CI/CD": "Continuous Integration/Continuous Deployment",
    "IaC": "Infrastructure as Code",
    "AWS": "Amazon Web Services",
    "GCP": "Google Cloud Platform",
    "K8s": "Kubernetes",
    "SRE": "Site Reliability Engineering",
    "MTTR": "Mean Time to Recovery",
    "MTTD": "Mean Time to Detection",
    "API": "Application Programming Interface",
    "REST": "Representational State Transfer",
    "SQL": "Structured Query Language",
    "NoSQL": "Not Only SQL",
    "DNS": "Domain Name System",
    "TLS": "Transport Layer Security",
    "SSL": "Secure Sockets Layer",
    "VPN": "Virtual Private Network",
    "IAM": "Identity and Access Management",
    "RBAC": "Role-Based Access Control",
    "SIEM": "Security Information and Event Management",
    "SOC": "Security Operations Center",
}

# ---------------------------------------------------------------------------
# Placeholder patterns (§10.2) — leftover AI artifacts
# ---------------------------------------------------------------------------

PLACEHOLDER_PATTERNS: frozenset[str] = frozenset(
    {
        "[company]",
        "[your name]",
        "[company name]",
        "[job title]",
        "lorem ipsum",
        "tbd",
        "todo",
        "[insert",
    }
)

# ---------------------------------------------------------------------------
# Scoring thresholds and category weights
# ---------------------------------------------------------------------------

SCORE_PASS_THRESHOLD: int = 85
SCORE_WARN_THRESHOLD: int = 75
BASELINE_MARGIN: int = 5  # Points below actual score for golden baselines
MAX_JD_LENGTH: int = 50_000  # Maximum job description length in characters

# Summary word-count thresholds — shared by prompt.py (AI instruction) and
# eval/content.py (scoring). Prompt target is tighter than eval PASS range
# to give Claude a clear anchor inside the acceptable band.
SUMMARY_WORD_TARGET_MIN: int = 50  # soft target for prompt guidance
SUMMARY_WORD_TARGET_MAX: int = 65  # soft target for prompt guidance
SUMMARY_WORD_HARD_MAX: int = 70  # hard max in prompt; eval PASS upper
SUMMARY_WORD_PASS_MIN: int = 30  # eval PASS lower bound (below target, still usable)
SUMMARY_WORD_WARN_MIN: int = 20  # eval WARN lower bound
SUMMARY_WORD_WARN_MAX: int = 100  # eval WARN upper bound

# Mandatory phrase to include in every generated summary.
#
# Resolved at module import time from the ``CURATOR_SUMMARY_MANDATORY_MENTION``
# environment variable, falling back to the project author's value as the
# default. Forks should set the env var to either their own mandatory mention
# (e.g. ``CURATOR_SUMMARY_MANDATORY_MENTION="founder of YourCo"``) or to an
# empty string to disable. When set to an empty string the prompt rule still
# fires with an empty interpolation (cosmetic awkwardness; Claude treats it
# as a no-op); a follow-up cleanup will gate emission on truthiness so the
# rule is omitted entirely when no mention is configured.
#
# Referenced in prompt.py at two locations via a shared format() slot, in
# models.py via a Pydantic Field description, and in static_mode.py for the
# zero-API path summary derivation.
SUMMARY_MANDATORY_MENTION: str = os.environ.get(
    "CURATOR_SUMMARY_MANDATORY_MENTION",
    "founder of Perts Foundry LLC (a consulting company)",
)

CATEGORY_WEIGHTS: dict[str, float] = {
    "jd_alignment": 0.25,
    "writing_quality": 0.25,
    "pdf_output": 0.15,
    "selection_quality": 0.15,
    "content_density": 0.10,
    "template_correctness": 0.05,
    "date_consistency": 0.05,
}

if abs(sum(CATEGORY_WEIGHTS.values()) - 1.0) > 1e-9:
    msg = f"CATEGORY_WEIGHTS must sum to 1.0, got {sum(CATEGORY_WEIGHTS.values())}"
    raise RuntimeError(msg)

# ---------------------------------------------------------------------------
# Cover letter rules
# ---------------------------------------------------------------------------
#
# This block is the source of truth for cover-letter authoring constraints
# enforced at runtime by ``models.validate_cover_letter`` (word counts,
# forbidden words, forbidden phrases, sign-off enum) and surfaced in the
# system prompt by ``prompt.py:_COVER_LETTER_PROMPT_BLOCK``. The two must
# be edited in lockstep when adding or relaxing a rule.
#
# Matching semantics (enforced in models.validate_cover_letter):
# - Words: whole-word, case-insensitive (\b{word}\b on lowered text).
# - Phrases: lowercase substring match.
# - Salutation-scoped phrases: checked against the salutation field only.
# - Placeholder tokens matching COVER_LETTER_PLACEHOLDER_PATTERN are rejected
#   anywhere in the letter (salutation + opening + body + closing).
#   Both the static path (portfolio-authored) and the API path (generated)
#   must ship letters with no unfilled [UPPERCASE] tokens.

COVER_LETTER_WORD_TARGET: int = 275
COVER_LETTER_WORD_MIN: int = 250
COVER_LETTER_WORD_MAX: int = 300
COVER_LETTER_PARAGRAPH_WORD_MIN: int = 40
COVER_LETTER_PARAGRAPH_WORD_MAX: int = 90
# Body paragraphs fixed at exactly 2. Allowing a 2-or-3 choice gave the model
# a discrete variance source that let total word count drift past the cap even
# when the per-paragraph cap held. With exactly 2, the arithmetic is bounded:
# 2 * PARAGRAPH_WORD_MAX + opening + closing leaves ~120 words for the latter
# two, which comfortably fits a natural hook + value-recap closing.
COVER_LETTER_BODY_MIN_COUNT: int = 2
COVER_LETTER_BODY_MAX_COUNT: int = 2

# Added to settings.max_tokens only when --cover-letter is on. A 300-word
# letter is ~450 output tokens plus structured-output scaffolding; 1024
# leaves comfortable headroom and keeps the single-call invariant intact.
COVER_LETTER_MAX_TOKENS_HEADROOM: int = 1024

# PDF minimum font size thresholds (Tier 1 `actual_min_font_size`).
# Contact-line and footer runs at 8.5pt are standard design practice and
# remain readable; body text renders above this. Anything below 7.5pt is
# genuinely borderline.
MIN_FONT_SIZE_PASS_PT: float = 8.5
MIN_FONT_SIZE_WARN_PT: float = 7.5

# Single-word AI-tell and cliché tokens (whole-word, case-insensitive).
COVER_LETTER_FORBIDDEN_WORDS: frozenset[str] = frozenset(
    {
        # Classic AI-tell words from §17.2
        "delve",
        "pivotal",
        "intricate",
        "realm",
        "showcasing",
        "adept",
        "cutting-edge",
        "seamless",
        "holistic",
        "meticulous",
        "vibrant",
        "testament",
        "unparalleled",
        "unwavering",
        "robust",
        "beacon",
        "tapestry",
        "synergize",
        "tech-savvy",
        # Formal legalese forbidden by §17.2
        "herein",
        "hereby",
    }
)

# Multi-word phrases matched as lowercase substrings against body fields
# (opening + body_paragraphs + closing). Placeholder tokens are not
# stripped; any [UPPERCASE] token is rejected outright by the validator.
COVER_LETTER_FORBIDDEN_PHRASES: frozenset[str] = frozenset(
    {
        # Cliché phrases §17.2
        "proven track record",
        "results-driven",
        "go-getter",
        "self-starter",
        "detail-oriented",
        "hard worker",
        "team player",
        "think outside the box",
        "leverage my skills",
        "bring my expertise to bear",
        # AI-tell phrases §17.2
        "in today's fast-paced",
        "rapidly evolving landscape",
        "leverage synergies",
        "it's important to remember",
        "i hope this letter finds you well",
        "paradigm shift",
        # Weak opener §17.2
        "i am writing to apply for",
        # Generic praise §17.2
        "per my resume",
        "as attached",
        "your amazing culture",
        "your innovative team",
        "your exciting mission",
    }
)

# Salutation-specific forbidden phrases. Never cross-matched against body.
COVER_LETTER_SALUTATION_FORBIDDEN_PHRASES: frozenset[str] = frozenset(
    {
        "to whom it may concern",
    }
)

# Allowed sign-off values (exact, no trailing comma; renderer adds comma).
COVER_LETTER_VALID_SIGN_OFFS: frozenset[str] = frozenset(
    {
        "Sincerely",
        "Best regards",
        "Kind regards",
        "Regards",
        "Best",
    }
)

# Matches [COMPANY], [ROLE], [HIRING_MANAGER_NAME], [TAILOR: ...] etc.
# Detected (not stripped) by ``validate_cover_letter``; any occurrence in
# salutation + opening + body + closing is a hard failure, because the
# renderer-facing contract is that letters are fully filled in before
# render time. No runtime substitution.
#
# The inner group is written `[^\]]+` rather than `\s*[^\]]+` to avoid
# overlapping quantifiers (which would give the regex superlinear
# complexity on malformed input like ``[FOO:`` followed by many spaces).
COVER_LETTER_PLACEHOLDER_PATTERN: str = r"\[[A-Z_]+(?::[^\]]+)?\]"

assert COVER_LETTER_WORD_MIN <= COVER_LETTER_WORD_TARGET <= COVER_LETTER_WORD_MAX, (
    "COVER_LETTER_WORD_TARGET must lie in [MIN, MAX]"
)
assert COVER_LETTER_PARAGRAPH_WORD_MIN < COVER_LETTER_PARAGRAPH_WORD_MAX, (
    "Per-paragraph word band must be non-empty"
)
assert (
    SUMMARY_WORD_WARN_MIN
    <= SUMMARY_WORD_PASS_MIN
    <= SUMMARY_WORD_TARGET_MIN
    <= SUMMARY_WORD_TARGET_MAX
    <= SUMMARY_WORD_HARD_MAX
    <= SUMMARY_WORD_WARN_MAX
), "Summary word-count thresholds must be monotonic non-decreasing"

# ---------------------------------------------------------------------------
# Prompt rendering functions
# ---------------------------------------------------------------------------


def render_summary_length_guidance_for_prompt() -> str:
    """Format summary word-count guidance for prompt text."""
    return (
        f"{SUMMARY_WORD_TARGET_MIN}-{SUMMARY_WORD_TARGET_MAX} words soft "
        f"target, {SUMMARY_WORD_HARD_MAX} word hard maximum"
    )


def render_weak_phrases_for_prompt() -> str:
    """Format weak phrases as comma-separated quoted list for prompt text."""
    # Canonical list for the prompt (subset — prompt uses specific phrasing)
    prompt_phrases = [
        '"Responsible for"',
        '"Helped"',
        '"Assisted"',
        '"Worked on"',
        '"Participated in"',
        '"Was involved in"',
        '"Duties included"',
        '"Supported"',
    ]
    return ", ".join(prompt_phrases)


def render_ai_red_flag_words_for_prompt() -> str:
    """Format AI red-flag words as prompt-ready prose with qualifiers."""
    return (
        "Delve, Leverage/leveraged, Synergy/synergies, Pivotal, Realm, "
        "Showcasing, Spearheaded (when inflating minor contributions), "
        "Instrumentalized, Facilitate/facilitated (when overused), Holistic"
    )


def render_ai_red_flag_phrases_for_prompt() -> str:
    """Format AI red-flag phrases as prompt-ready prose with qualifiers."""
    prompt_phrases = [
        '"Results-driven professional with a proven track record"',
        '"Cross-functional synergies"',
        '"Facilitated knowledge transfer"',
        '"Dynamic and innovative leader"',
        '"Passionate about driving excellence"',
        '"Seeking a challenging position where I can utilize my skills"',
        '"Proven ability to" (without specific evidence)',
        '"Strong communication skills" (without demonstration)',
        '"Motivated team player"',
        '"Leveraged cross-functional collaboration to drive strategic initiatives"',
    ]
    return ", ".join(prompt_phrases)


def render_cover_letter_forbidden_words_for_prompt() -> str:
    """Format cover-letter forbidden words as prompt-ready prose."""
    return ", ".join(sorted(COVER_LETTER_FORBIDDEN_WORDS))


def render_cover_letter_forbidden_phrases_for_prompt() -> str:
    """Format cover-letter forbidden phrases as prompt-ready prose."""
    return ", ".join(f'"{p}"' for p in sorted(COVER_LETTER_FORBIDDEN_PHRASES))


def render_cover_letter_valid_sign_offs_for_prompt() -> str:
    """Format allowed sign-off values as prompt-ready prose."""
    return ", ".join(sorted(COVER_LETTER_VALID_SIGN_OFFS))
