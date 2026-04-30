"""Writing Quality metrics (25% weight).

Evaluates bullet quality, prohibited content, action verbs, summary
composition, and skills appropriateness.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Any

from curator.eval._text_helpers import collect_highlight_texts
from curator.eval.report import EvalMetricResult, EvalMetricStatus
from curator.rules import (
    ACTION_VERBS,
    AI_RED_FLAG_PHRASES,
    AI_RED_FLAG_WORDS,
    PLACEHOLDER_PATTERNS,
    SOFT_SKILLS,
    TRIVIAL_SKILLS,
    WEAK_PHRASES,
)

if TYPE_CHECKING:
    from curator.models import ResumeCuration

_CATEGORY = "writing_quality"

# First-person pronoun detection — matches standalone "I" but not "I/O".
_FIRST_PERSON_PATTERNS = re.compile(
    r"(?<![A-Za-z/])(?:I(?!'m|'ve)|I'm|I've)(?![A-Za-z/])"
    r"|(?<![A-Za-z])\b(?:me|my)\b(?![A-Za-z])",
    re.IGNORECASE,
)

_THIRD_PERSON_RE = re.compile(r"^(?:He|She)\b", re.MULTILINE)

_REFERENCES_PHRASES = frozenset(
    {
        "references available upon request",
        "references available on request",
        "references furnished upon request",
    }
)

_QUANTIFICATION_RE = re.compile(r"\d+[%$kKmMbB]?|\$[\d,.]+|[\d,]+\+?")


def _build_weak_phrase_regex() -> re.Pattern[str]:
    """Build a word-boundary regex matching WEAK_PHRASES.

    Word boundaries prevent substring matches (e.g. "managed" not matching
    "management"). Hyphen guards prevent compound-adjective false positives
    (e.g. "managed" not matching "Atlantis-managed", "assisted" not matching
    "AI-assisted").
    """
    sorted_phrases = sorted(WEAK_PHRASES, key=len, reverse=True)
    alternation = "|".join(re.escape(p) for p in sorted_phrases)
    return re.compile(
        rf"(?<![\w-])(?:{alternation})(?![\w-])",
        re.IGNORECASE,
    )


_WEAK_PHRASE_RE = _build_weak_phrase_regex()


def _collect_all_text_fields(
    section_data: dict[str, Any],
    basics: dict[str, Any],
) -> list[str]:
    """Collect all visible text fields for prohibited content checks."""
    texts: list[str] = []
    if s := basics.get("summary"):
        texts.append(str(s))
    if lab := basics.get("label"):
        texts.append(str(lab))

    for entries in section_data.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for field in (
                "text",
                "summary",
                "description",
                "position",
                "name",
                "title",
                "reference",
                "honors",
            ):
                if val := entry.get(field):
                    texts.append(str(val))  # noqa: PERF401
            texts.extend(
                str(h["text"])
                for h in entry.get("highlights", [])
                if isinstance(h, dict) and h.get("text")
            )
    return texts


def _collect_prose_text_fields(
    section_data: dict[str, Any],
    basics: dict[str, Any],
) -> list[str]:
    """Collect prose-only text fields (excludes identifier fields).

    Unlike ``_collect_all_text_fields``, this skips ``position``, ``name``,
    ``title``, and ``reference`` — short identifier/label fields that are
    not prose and frequently contain Roman numerals ("Engineer I") or
    product names ("AWS Managed Services") that trigger false positives
    on first-person / weak-phrase checks.
    """
    texts: list[str] = []
    if s := basics.get("summary"):
        texts.append(str(s))

    for entries in section_data.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for field in ("text", "summary", "description", "honors"):
                if val := entry.get(field):
                    texts.append(str(val))  # noqa: PERF401
            texts.extend(
                str(h["text"])
                for h in entry.get("highlights", [])
                if isinstance(h, dict) and h.get("text")
            )
    return texts


def _collect_work_highlights_by_entry(
    section_data: dict[str, Any],
) -> list[tuple[str, list[str]]]:
    """Collect highlights grouped by work entry (entry_id, [texts])."""
    grouped: list[tuple[str, list[str]]] = []
    for entry in section_data.get("work", []):
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id", "unknown"))
        texts = [
            str(h["text"])
            for h in entry.get("highlights", [])
            if isinstance(h, dict) and h.get("text")
        ]
        if texts:
            grouped.append((entry_id, texts))
    return grouped


def _leading_verb_word(text: str) -> str:
    """Return the first verb-like word, skipping a leading -ly adverb.

    Treats constructions like "Proactively identified" as verb-initiated
    by returning "identified" rather than "proactively". If the first
    word is not an -ly adverb, returns it as-is. Returns an empty string
    for empty input.
    """
    words = text.split()
    if not words:
        return ""
    first = words[0].lower().rstrip(",.:;")
    if first.endswith("ly") and len(words) >= 2:
        return words[1].lower().rstrip(",.:;")
    return first


def evaluate_writing(
    section_data: dict[str, Any],
    basics: dict[str, Any],
    curation: ResumeCuration,
) -> list[EvalMetricResult]:
    """Evaluate Writing Quality metrics."""
    results: list[EvalMetricResult] = []
    all_texts = _collect_all_text_fields(section_data, basics)
    all_text_joined = " ".join(all_texts).lower()
    prose_texts = _collect_prose_text_fields(section_data, basics)
    prose_joined = " ".join(prose_texts)
    highlights = collect_highlight_texts(section_data)

    # --- Quantification & impact ---

    # quantification_rate — §4.5: >=50% bullets contain numbers.
    if highlights:
        quantified = sum(1 for h in highlights if _QUANTIFICATION_RE.search(h))
        rate = quantified / len(highlights)
        if rate >= 0.5:
            q_status = EvalMetricStatus.PASS
        elif rate >= 0.3:
            q_status = EvalMetricStatus.WARN
        else:
            q_status = EvalMetricStatus.FAIL
        results.append(
            EvalMetricResult(
                name="quantification_rate",
                category=_CATEGORY,
                status=q_status,
                value=round(rate, 2),
                detail=(
                    f"{quantified}/{len(highlights)} bullets quantified ({rate:.0%})"
                ),
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="quantification_rate",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=0.0,
                detail="No highlights to evaluate",
            )
        )

    # --- Prohibited content ---

    # weak_phrase_count — §4.6: 0 matches.
    # Word-boundary regex with hyphen guards prevents false positives from
    # substring matches ("managed" in "management") and compound adjectives
    # ("assisted" in "AI-assisted", "managed" in "Atlantis-managed").
    weak_matches = _WEAK_PHRASE_RE.findall(all_text_joined)
    weak_found = sorted({m.lower() for m in weak_matches})
    results.append(
        EvalMetricResult(
            name="weak_phrase_count",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if not weak_found else EvalMetricStatus.FAIL,
            value=len(weak_matches),
            detail=f"Found: {', '.join(weak_found)}" if weak_found else "None found",
        )
    )

    # ai_red_flag_count — §10.2: 0 matches.
    ai_words_found = [w for w in AI_RED_FLAG_WORDS if w in all_text_joined]
    ai_phrases_found = [p for p in AI_RED_FLAG_PHRASES if p in all_text_joined]
    ai_total = len(ai_words_found) + len(ai_phrases_found)
    ai_items = ai_words_found + ai_phrases_found
    results.append(
        EvalMetricResult(
            name="ai_red_flag_count",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if ai_total == 0 else EvalMetricStatus.FAIL,
            value=ai_total,
            detail=f"Found: {', '.join(ai_items)}" if ai_items else "None found",
        )
    )

    # first_person_count — §4.2, §7: 0 matches.
    # Scans prose only (not position/name/title) so Roman-numeral job
    # titles like "Engineer I" are not counted as a pronoun.
    fp_matches = _FIRST_PERSON_PATTERNS.findall(prose_joined)
    # Filter out "I/O" false positives.
    fp_count = len([m for m in fp_matches if m.upper() != "I/O"])
    results.append(
        EvalMetricResult(
            name="first_person_count",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if fp_count == 0 else EvalMetricStatus.FAIL,
            value=fp_count,
            detail=f"{fp_count} first-person pronoun(s) found",
        )
    )

    # third_person_count — §9.1: 0 matches.
    tp_count = len(_THIRD_PERSON_RE.findall(" ".join(all_texts)))
    results.append(
        EvalMetricResult(
            name="third_person_count",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if tp_count == 0 else EvalMetricStatus.FAIL,
            value=tp_count,
            detail=f"{tp_count} third-person sentence starter(s) found",
        )
    )

    # no_periods_on_bullets — §2.10: 0 bullets ending with period.
    period_bullets = sum(1 for h in highlights if h.rstrip().endswith("."))
    results.append(
        EvalMetricResult(
            name="no_periods_on_bullets",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS
            if period_bullets == 0
            else EvalMetricStatus.FAIL,
            value=period_bullets,
            detail=f"{period_bullets} bullet(s) end with period",
        )
    )

    # references_available_phrase — §7: absent.
    has_ref_phrase = any(phrase in all_text_joined for phrase in _REFERENCES_PHRASES)
    results.append(
        EvalMetricResult(
            name="references_available_phrase",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS
            if not has_ref_phrase
            else EvalMetricStatus.FAIL,
            value=has_ref_phrase,
            detail="'References available' phrase found"
            if has_ref_phrase
            else "No 'references available' phrase",
        )
    )

    # placeholder_text_count — §10.2: 0 matches.
    placeholders_found = [p for p in PLACEHOLDER_PATTERNS if p in all_text_joined]
    results.append(
        EvalMetricResult(
            name="placeholder_text_count",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS
            if not placeholders_found
            else EvalMetricStatus.FAIL,
            value=len(placeholders_found),
            detail=f"Found: {', '.join(placeholders_found)}"
            if placeholders_found
            else "None found",
        )
    )

    # --- Action verbs ---

    action_verbs_lower = {v.lower() for v in ACTION_VERBS}

    # action_verb_start_rate — §2.10, §4.6: 100%.
    # A leading -ly adverb ("Proactively identified...") is treated as
    # verb-initiated; the check uses the first non-adverb word.
    if highlights:
        starts_with_verb = 0
        non_verb_starts: list[str] = []
        for h in highlights:
            verb_word = _leading_verb_word(h)
            if verb_word in action_verbs_lower:
                starts_with_verb += 1
            elif verb_word:
                non_verb_starts.append(verb_word)
        rate = starts_with_verb / len(highlights) if highlights else 0
        if rate == 1.0:
            avsr_status = EvalMetricStatus.PASS
        elif rate >= 0.8:
            avsr_status = EvalMetricStatus.WARN
        else:
            avsr_status = EvalMetricStatus.FAIL
        results.append(
            EvalMetricResult(
                name="action_verb_start_rate",
                category=_CATEGORY,
                status=avsr_status,
                value=round(rate, 2),
                detail=f"{starts_with_verb}/{len(highlights)} ({rate:.0%})"
                + (
                    f"; non-verbs: {', '.join(non_verb_starts[:5])}"
                    if non_verb_starts
                    else ""
                ),
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="action_verb_start_rate",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=0.0,
                detail="No highlights to evaluate",
            )
        )

    # action_verb_diversity_per_entry — no repeated leading verbs within entry.
    grouped = _collect_work_highlights_by_entry(section_data)
    per_entry_dupes: list[str] = []
    for entry_id, texts in grouped:
        leading_verbs = [
            t.split()[0].lower().rstrip(",.:;") for t in texts if t.split()
        ]
        counts = Counter(leading_verbs)
        dupes = [v for v, c in counts.items() if c > 1 and v in action_verbs_lower]
        if dupes:
            per_entry_dupes.append(f"{entry_id}: {', '.join(dupes)}")
    results.append(
        EvalMetricResult(
            name="action_verb_diversity_per_entry",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS
            if not per_entry_dupes
            else EvalMetricStatus.FAIL,
            value=len(per_entry_dupes),
            detail="; ".join(per_entry_dupes)
            if per_entry_dupes
            else "No repeated leading verbs within entries",
        )
    )

    # action_verb_diversity_global — no verb used more than twice.
    all_leading = [h.split()[0].lower().rstrip(",.:;") for h in highlights if h.split()]
    global_counts = Counter(v for v in all_leading if v in action_verbs_lower)
    overused = [f"{v} ({c}x)" for v, c in global_counts.items() if c > 2]
    results.append(
        EvalMetricResult(
            name="action_verb_diversity_global",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS
            if not overused
            else EvalMetricStatus.WARN
            if len(overused) <= 1
            else EvalMetricStatus.FAIL,
            value=len(overused),
            detail="; ".join(overused) if overused else "No verb used more than twice",
        )
    )

    # --- Summary quality ---

    summary_text = str(basics.get("summary", ""))

    # summary_sentence_count — §4.2: 2-4 sentences.
    sentences = [s.strip() for s in re.split(r"[.!?]+", summary_text) if s.strip()]
    sent_count = len(sentences)
    results.append(
        EvalMetricResult(
            name="summary_sentence_count",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS
            if 2 <= sent_count <= 4
            else EvalMetricStatus.WARN
            if 1 <= sent_count <= 5
            else EvalMetricStatus.FAIL,
            value=sent_count,
            detail=f"{sent_count} sentences (target: 2-4)",
        )
    )

    # summary_has_years_experience — §4.2: "N years" or "N+ years" pattern.
    years_pattern = re.search(r"\d+\+?\s*years?", summary_text, re.IGNORECASE)
    results.append(
        EvalMetricResult(
            name="summary_has_years_experience",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if years_pattern else EvalMetricStatus.FAIL,
            value=bool(years_pattern),
            detail=f"Found: '{years_pattern.group()}'"
            if years_pattern
            else "No years of experience mentioned",
        )
    )

    # summary_has_title — §4.2: summary references the professional label.
    label = curation.suggested_label.lower()
    summary_lower = summary_text.lower()
    # Check if label or any significant part (>=2 words) appears in summary.
    label_words = label.split()
    has_title = label in summary_lower
    if not has_title and len(label_words) >= 2:
        # Check for partial matches (e.g., "DevOps Engineer" in summary).
        for i in range(len(label_words) - 1):
            bigram = f"{label_words[i]} {label_words[i + 1]}"
            if bigram in summary_lower:
                has_title = True
                break
    results.append(
        EvalMetricResult(
            name="summary_has_title",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if has_title else EvalMetricStatus.WARN,
            value=has_title,
            detail=f"Label '{curation.suggested_label}' "
            + ("found in summary" if has_title else "not found in summary"),
        )
    )

    # --- Skills quality ---

    selected_keywords_lower = [
        kw.lower() for skill in curation.skills for kw in skill.keywords
    ]

    # no_trivial_skills — §4.7: 0 exact matches.
    trivial_found = [kw for kw in selected_keywords_lower if kw in TRIVIAL_SKILLS]
    results.append(
        EvalMetricResult(
            name="no_trivial_skills",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS
            if not trivial_found
            else EvalMetricStatus.FAIL,
            value=len(trivial_found),
            detail=f"Found: {', '.join(trivial_found)}"
            if trivial_found
            else "No trivial skills found",
        )
    )

    # no_soft_skills_listed — §4.7: 0 exact matches.
    soft_found = [kw for kw in selected_keywords_lower if kw in SOFT_SKILLS]
    results.append(
        EvalMetricResult(
            name="no_soft_skills_listed",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if not soft_found else EvalMetricStatus.FAIL,
            value=len(soft_found),
            detail=f"Found: {', '.join(soft_found)}"
            if soft_found
            else "No soft skills listed",
        )
    )

    return results
