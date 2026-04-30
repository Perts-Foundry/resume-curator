"""Content Density metrics (10% weight).

Measures word counts across resume sections to ensure adequate content
density without exceeding single-page limits.
"""

from __future__ import annotations

from typing import Any

from curator.eval._text_helpers import collect_highlight_texts
from curator.eval.report import EvalMetricResult, EvalMetricStatus
from curator.rules import (
    SUMMARY_WORD_HARD_MAX,
    SUMMARY_WORD_PASS_MIN,
    SUMMARY_WORD_WARN_MAX,
    SUMMARY_WORD_WARN_MIN,
)

_CATEGORY = "content_density"


def _count_words(text: str) -> int:
    """Count words in a string."""
    return len(text.split())


def _collect_all_text(
    section_data: dict[str, Any],
    basics: dict[str, Any],
) -> str:
    """Collect all visible text from curated section data."""
    parts: list[str] = []

    # Basics: summary and label.
    if summary := basics.get("summary"):
        parts.append(str(summary))
    if label := basics.get("label"):
        parts.append(str(label))
    if name := basics.get("name"):
        parts.append(str(name))

    for section_name, entries in section_data.items():
        if section_name == "interests" and isinstance(entries, dict):
            for hobby in entries.get("hobbies", []):
                if isinstance(hobby, dict):
                    if name := hobby.get("name"):
                        parts.append(str(name))
                    if desc := hobby.get("description"):
                        parts.append(str(desc))
                    parts.extend(str(kw) for kw in hobby.get("keywords", []))
            parts.extend(str(f) for f in entries.get("fun_facts", []))
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            # Text fields common across entry types.
            for field in (
                "text",
                "summary",
                "description",
                "position",
                "name",
                "title",
                "area",
                "institution",
                "organization",
                "reference",
                "language",
                "honors",
                "minor",
            ):
                if val := entry.get(field):
                    parts.append(str(val))  # noqa: PERF401
            # Highlights (work, projects).
            for h in entry.get("highlights", []):
                if isinstance(h, dict) and (t := h.get("text")):
                    parts.append(str(t))  # noqa: PERF401
            # Skill keywords.
            parts.extend(str(kw) for kw in entry.get("keywords", []))

    return " ".join(parts)


def evaluate_content(
    section_data: dict[str, Any],
    basics: dict[str, Any],
) -> list[EvalMetricResult]:
    """Evaluate Content Density metrics."""
    results: list[EvalMetricResult] = []

    # word_count — §2.1, §13.1: total 475-700 words.
    all_text = _collect_all_text(section_data, basics)
    total_words = _count_words(all_text)
    if 475 <= total_words <= 700:
        wc_status = EvalMetricStatus.PASS
    elif 400 <= total_words <= 800:
        wc_status = EvalMetricStatus.WARN
    else:
        wc_status = EvalMetricStatus.FAIL
    results.append(
        EvalMetricResult(
            name="word_count",
            category=_CATEGORY,
            status=wc_status,
            value=total_words,
            detail=f"{total_words} words (target: 475-700)",
        )
    )

    # bullet_word_count — relaxed thresholds: PASS 8-35, WARN 5-40, FAIL outside.
    # Reflects reality that detailed portfolio bullets routinely run 25-35 words
    # with specific technologies and metrics; only truly egregious bullets fail.
    highlights = collect_highlight_texts(section_data)
    if highlights:
        out_of_range = [
            (i, _count_words(h))
            for i, h in enumerate(highlights)
            if not 8 <= _count_words(h) <= 35
        ]
        if not out_of_range:
            bwc_status = EvalMetricStatus.PASS
            bwc_detail = f"All {len(highlights)} bullets in 8-35 word range"
        elif all(5 <= wc <= 40 for _, wc in out_of_range):
            bwc_status = EvalMetricStatus.WARN
            bwc_detail = (
                f"{len(out_of_range)}/{len(highlights)} bullets outside 8-35 range"
            )
        else:
            bwc_status = EvalMetricStatus.FAIL
            bwc_detail = (
                f"{len(out_of_range)}/{len(highlights)} bullets outside 8-35 range"
            )
        results.append(
            EvalMetricResult(
                name="bullet_word_count",
                category=_CATEGORY,
                status=bwc_status,
                value=len(out_of_range),
                detail=bwc_detail,
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="bullet_word_count",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=0,
                detail="No highlights found",
            )
        )

    # summary_word_count — thresholds sourced from rules.py so the prompt
    # target (SUMMARY_WORD_TARGET_*) and eval PASS range stay in lockstep.
    # PASS upper deliberately tracks SUMMARY_WORD_HARD_MAX, not
    # SUMMARY_WORD_TARGET_MAX: the prompt soft target sits strictly inside
    # the PASS band so summaries that hover at the soft-target ceiling
    # don't get downgraded for being one or two words over the soft cap.
    # If empirical runs show too many high-but-passing summaries, retighten
    # the PASS upper to SUMMARY_WORD_TARGET_MAX.
    summary = basics.get("summary", "")
    summary_words = _count_words(str(summary)) if summary else 0
    if SUMMARY_WORD_PASS_MIN <= summary_words <= SUMMARY_WORD_HARD_MAX:
        swc_status = EvalMetricStatus.PASS
    elif SUMMARY_WORD_WARN_MIN <= summary_words <= SUMMARY_WORD_WARN_MAX:
        swc_status = EvalMetricStatus.WARN
    else:
        swc_status = EvalMetricStatus.FAIL
    results.append(
        EvalMetricResult(
            name="summary_word_count",
            category=_CATEGORY,
            status=swc_status,
            value=summary_words,
            detail=(
                f"{summary_words} words "
                f"(target: {SUMMARY_WORD_PASS_MIN}-{SUMMARY_WORD_HARD_MAX})"
            ),
        )
    )

    return results
