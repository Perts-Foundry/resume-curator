"""Content Density metrics (10% weight).

Measures word counts across resume sections at densities appropriate to
the rendered page budget. Bands are page-budget-aware via ``EvalBands``;
``SHORT_FORM_BANDS`` (1-page) and ``LONG_FORM_BANDS`` (2+-page) are
selected by ``bands_for_pages(max_pages)`` upstream in
``evaluate_tier1``.
"""

from __future__ import annotations

from typing import Any

from curator.eval._text_helpers import collect_highlight_texts
from curator.eval.report import (
    SHORT_FORM_BANDS,
    EvalBands,
    EvalMetricResult,
    EvalMetricStatus,
)
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
    *,
    bands: EvalBands,
) -> list[EvalMetricResult]:
    """Evaluate Content Density metrics against a page-budget-aware rubric.

    ``bands`` is keyword-only with no default to force the call site to
    decide between ``SHORT_FORM_BANDS`` and ``LONG_FORM_BANDS``.
    Production callers via ``evaluate_tier1`` resolve
    ``bands_for_pages(ctx.max_pages)`` explicitly; tests pass
    ``bands=SHORT_FORM_BANDS`` (or ``LONG_FORM_BANDS``) by name. A
    silent default would mis-score any future direct caller that
    omitted the kwarg on a 2-page profile.
    """
    results: list[EvalMetricResult] = []

    # word_count — bands selected by page budget; short-form 475-700,
    # long-form 900-1400 (per EvalBands.word_count_pass/warn).
    wc_pass_lo, wc_pass_hi = bands.word_count_pass
    wc_warn_lo, wc_warn_hi = bands.word_count_warn
    all_text = _collect_all_text(section_data, basics)
    total_words = _count_words(all_text)
    if wc_pass_lo <= total_words <= wc_pass_hi:
        wc_status = EvalMetricStatus.PASS
    elif wc_warn_lo <= total_words <= wc_warn_hi:
        wc_status = EvalMetricStatus.WARN
    else:
        wc_status = EvalMetricStatus.FAIL
    results.append(
        EvalMetricResult(
            name="word_count",
            category=_CATEGORY,
            status=wc_status,
            value=total_words,
            detail=f"{total_words} words (target: {wc_pass_lo}-{wc_pass_hi})",
        )
    )

    # bullet_word_count — bands equal across SHORT_FORM and LONG_FORM by
    # design (bullet length is a per-bullet quality signal, not per-page
    # volume). PASS 8-35, WARN 5-40, FAIL outside on both rubrics today.
    bw_pass_lo, bw_pass_hi = bands.bullet_word_count_pass
    bw_warn_lo, bw_warn_hi = bands.bullet_word_count_warn
    highlights = collect_highlight_texts(section_data)
    if highlights:
        out_of_range = [
            (i, _count_words(h))
            for i, h in enumerate(highlights)
            if not bw_pass_lo <= _count_words(h) <= bw_pass_hi
        ]
        if not out_of_range:
            bwc_status = EvalMetricStatus.PASS
            bwc_detail = (
                f"All {len(highlights)} bullets in "
                f"{bw_pass_lo}-{bw_pass_hi} word range"
            )
        elif all(bw_warn_lo <= wc <= bw_warn_hi for _, wc in out_of_range):
            bwc_status = EvalMetricStatus.WARN
            bwc_detail = (
                f"{len(out_of_range)}/{len(highlights)} bullets outside "
                f"{bw_pass_lo}-{bw_pass_hi} range"
            )
        else:
            bwc_status = EvalMetricStatus.FAIL
            bwc_detail = (
                f"{len(out_of_range)}/{len(highlights)} bullets outside "
                f"{bw_pass_lo}-{bw_pass_hi} range"
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
