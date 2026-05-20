"""Selection Quality metrics (15% weight).

Evaluates the quality of curation decisions: highlight distribution,
section coverage, and profile completeness. Under the minimized AI scope,
the AI no longer selects work entries (all are rendered) or
education/certificates; those are renderer-managed.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from curator.eval.report import (
    EvalBands,
    EvalMetricResult,
    EvalMetricStatus,
)
from curator.rules import (
    SUMMARY_WORD_HARD_MAX,
    SUMMARY_WORD_PASS_MIN,
    SUMMARY_WORD_TARGET_MIN,
    SUMMARY_WORD_WARN_MAX,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from curator.models import ResumeCuration

_CATEGORY = "selection_quality"

#: Headroom above the per-position floor for ``highlight_counts`` PASS
#: bands. The renderer cascade trims TO the floor, so the rendered
#: output typically lands at exactly the floor; ``floor + margin``
#: leaves room for cases where the cascade converged before tier 6
#: fired (no trim needed). The lower bound stays at the floor; going
#: below the floor only happens via the last-resort below-floor tier 8
#: which logs a WARNING, so flagging it in the eval is correct.
_HIGHLIGHT_BAND_HEADROOM = 2


def evaluate_selection(
    curation: ResumeCuration,
    basics: dict[str, Any],
    *,
    section_data: dict[str, Any] | None = None,
    work_authored_highlight_counts: Mapping[str, int] | None = None,
    bands: EvalBands,
) -> list[EvalMetricResult]:
    """Evaluate Selection Quality metrics.

    ``work_authored_highlight_counts`` is the
    ``EvalContext.work_authored_highlight_counts`` projection (work
    entry id -> count of authored highlights in the portfolio). Used
    by highlight_counts to clamp the position-based expected band so
    entries with fewer authored highlights than the position target
    (e.g., a recently-started role with only 3 highlights so far) are
    not penalized when the curator selected everything available.
    Defaults to an empty mapping; clamping then no-ops and the metric
    falls back to position-only bands.
    """
    results: list[EvalMetricResult] = []

    authored_highlight_counts: Mapping[str, int] = (
        work_authored_highlight_counts
        if work_authored_highlight_counts is not None
        else {}
    )

    # work_entry_count -- count rendered work entries with >= 1 highlight.
    rendered_work = section_data.get("work", []) if section_data is not None else []
    wec = sum(1 for e in rendered_work if isinstance(e, dict) and e.get("highlights"))
    results.append(
        EvalMetricResult(
            name="work_entry_count",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if wec >= 1 else EvalMetricStatus.FAIL,
            value=wec,
            detail=f"{wec} work entries with highlights after render",
        )
    )

    # highlight_counts -- evaluates POST-RENDER (post-trim) output quality,
    # not AI ranking quality. The AI ranks all highlights; the renderer trims
    # from the bottom for page fit. This metric checks whether the rendered
    # output has appropriate highlight density per position.
    #
    # Philosophy: every portfolio work entry renders in the output (preserving
    # the full employment timeline). Per-position bullet expectations are
    # page-budget-aware via ``bands.work_position_floors`` (sourced from the
    # renderer cascade): on 1-page profiles the floor is ``(3, 3, 0, 0, 0)``,
    # so older positions whose floor is 0 may render as header-only rows
    # ("ghost rows") and the metric treats that as acceptable (lower bound
    # 0). On 2+-page profiles the older floors are non-zero (`(8, 6, 6, 2,
    # 2)` on 2-page), so the metric expects positions 2+ to carry content;
    # an unexpectedly empty older role on 2-page output flags as below-band.
    # The renderer's tier 8 per-entry floor (RENDERER_BEHAVIOR_INVARIANT in
    # ``src/curator/renderer.py``) further guarantees at least one bullet
    # per rendered entry whenever ``base_floor > 0`` for that position, so
    # the eval band (which uses ``lo = floor``, already >= 2 on 2-page) is
    # strictly tighter than the renderer floor; the per-entry floor surfaces
    # here only as a redundant safety net.
    if not rendered_work:
        results.append(
            EvalMetricResult(
                name="highlight_counts",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=0,
                detail=(
                    "Rendered section_data required for position-based highlight counts"
                ),
            )
        )
    else:
        highlight_issues: list[str] = []
        entries_for_check: list[tuple[str, int]] = [
            (
                str(e.get("id", "unknown")),
                len(e.get("highlights", [])),
            )
            for e in rendered_work
            if isinstance(e, dict)
        ]
        floors = bands.work_position_floors
        last_floor = floors[-1] if floors else 0
        for position, (entry_id, count) in enumerate(entries_for_check):
            # Per-position floor sourced from the renderer cascade
            # (``work_position_floors``). Lower bound is the floor
            # itself (cascade trims TO the floor; the only way to land
            # below is the last-resort tier 8 which logs a WARNING).
            # Upper bound is ``floor + _HIGHLIGHT_BAND_HEADROOM`` to
            # accommodate renders that converged before tier 6 fired.
            # Positions beyond the tuple length receive the last value,
            # matching renderer behavior.
            floor = floors[position] if position < len(floors) else last_floor
            lo = floor
            hi = floor + _HIGHLIGHT_BAND_HEADROOM
            # Clamp the expected band against the entry's authored highlight
            # count. A recent role with only 3 authored highlights cannot
            # render 4-5 no matter how the curator ranks it; treat
            # selecting-all-available as PASS rather than penalizing the
            # portfolio gap as a curation defect. Authored count of 0 (or
            # entry not present in the portfolio map) falls back to the
            # position-based band, since clamping to 0 would force-FAIL
            # every position-0 role.
            authored = authored_highlight_counts.get(entry_id)
            clamped = False
            if authored is not None and authored > 0:
                # lo <= hi is invariant in this code path, so authored < hi
                # subsumes authored < lo as the clamp-fired condition.
                if authored < hi:
                    clamped = True
                lo = min(lo, authored)
                hi = min(hi, authored)
            if not lo <= count <= hi:
                detail = (
                    f"{entry_id}: {count} highlights "
                    f"(expected {lo}-{hi} at position {position})"
                )
                if clamped:
                    detail += f" [authored: {authored}]"
                highlight_issues.append(detail)
        if not highlight_issues:
            hc_status = EvalMetricStatus.PASS
            hc_detail = "All entries have appropriate highlight counts"
        elif len(highlight_issues) <= 1:
            hc_status = EvalMetricStatus.WARN
            hc_detail = "; ".join(highlight_issues)
        else:
            hc_status = EvalMetricStatus.FAIL
            hc_detail = "; ".join(highlight_issues)
        results.append(
            EvalMetricResult(
                name="highlight_counts",
                category=_CATEGORY,
                status=hc_status,
                value=len(highlight_issues),
                detail=hc_detail,
            )
        )

    # skill_group_count.
    sgc = len(curation.skills)
    if 6 <= sgc <= 10:
        sgc_status = EvalMetricStatus.PASS
    elif 4 <= sgc <= 12:
        sgc_status = EvalMetricStatus.WARN
    else:
        sgc_status = EvalMetricStatus.FAIL
    results.append(
        EvalMetricResult(
            name="skill_group_count",
            category=_CATEGORY,
            status=sgc_status,
            value=sgc,
            detail=f"{sgc} groups (target: 6-10)",
        )
    )

    # section_count -- count renderable sections with content (post-render).
    if section_data is not None:
        sc = sum(
            1
            for section_name in (
                "work",
                "skills",
                "projects",
                "certificates",
                "education",
            )
            if section_data.get(section_name)
        )
    else:
        sc = sum(
            1
            for attr in ("work_highlights", "skills", "projects")
            if getattr(curation, attr)
        )
    results.append(
        EvalMetricResult(
            name="section_count",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS
            if 3 <= sc <= 5
            else EvalMetricStatus.WARN
            if sc == 2
            else EvalMetricStatus.FAIL,
            value=sc,
            detail=f"{sc} sections with content (target: 3-5)",
        )
    )

    # label_word_count.
    label_words = len(curation.suggested_label.split())
    results.append(
        EvalMetricResult(
            name="label_word_count",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS
            if 2 <= label_words <= 5
            else EvalMetricStatus.FAIL,
            value=label_words,
            detail=f"'{curation.suggested_label}' ({label_words} words, target: 2-5)",
        )
    )

    # website_present.
    has_website = bool(basics.get("url", ""))
    results.append(
        EvalMetricResult(
            name="website_present",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if has_website else EvalMetricStatus.FAIL,
            value=has_website,
            detail="Personal website present"
            if has_website
            else "No personal website found",
        )
    )

    # github_present.
    profiles = basics.get("profiles", [])
    has_github = any(
        isinstance(p, dict) and str(p.get("network", "")).lower() == "github"
        for p in profiles
    )
    results.append(
        EvalMetricResult(
            name="github_present",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if has_github else EvalMetricStatus.FAIL,
            value=has_github,
            detail="GitHub profile present"
            if has_github
            else "No GitHub profile found",
        )
    )

    # summary_word_count_in_range -- replaces has_reasoning.
    # PASS band tracks the prompt's soft-target floor up to the prompt's
    # hard max; WARN band uses rules.py WARN bounds. Sourced from the same
    # SUMMARY_WORD_* constants as eval/content.py:summary_word_count so
    # the two metrics never disagree on the same input.
    summary_words = len(curation.summary.split())
    results.append(
        EvalMetricResult(
            name="summary_word_count_in_range",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS
            if SUMMARY_WORD_TARGET_MIN <= summary_words <= SUMMARY_WORD_HARD_MAX
            else EvalMetricStatus.WARN
            if SUMMARY_WORD_PASS_MIN <= summary_words <= SUMMARY_WORD_WARN_MAX
            else EvalMetricStatus.FAIL,
            value=summary_words,
            detail=(
                f"Summary: {summary_words} words "
                f"(target: {SUMMARY_WORD_TARGET_MIN}-{SUMMARY_WORD_HARD_MAX})"
            ),
        )
    )

    # total_highlight_count -- from section_data (post-render).
    #
    # Philosophy: the two most recent roles carry the weight of the resume.
    # On short-form the total can be as low as ~6 (positions 0-1 at floor 3
    # plus header-only older rows); on long-form positions 0-1 carry more
    # depth and positions 2+ may carry up to 4 bullets each, raising the
    # expected total. Bands selected via ``bands.total_highlight_count_*``.
    thc_pass_lo, thc_pass_hi = bands.total_highlight_count_pass
    thc_warn_lo, thc_warn_hi = bands.total_highlight_count_warn
    if rendered_work:
        thc = sum(
            len(e.get("highlights", [])) for e in rendered_work if isinstance(e, dict)
        )
    else:
        thc = sum(len(wh.highlight_ids) for wh in curation.work_highlights)
    if thc_pass_lo <= thc <= thc_pass_hi:
        thc_status = EvalMetricStatus.PASS
    elif thc_warn_lo <= thc <= thc_warn_hi:
        thc_status = EvalMetricStatus.WARN
    else:
        thc_status = EvalMetricStatus.FAIL
    results.append(
        EvalMetricResult(
            name="total_highlight_count",
            category=_CATEGORY,
            status=thc_status,
            value=thc,
            detail=f"{thc} total highlights (target: {thc_pass_lo}-{thc_pass_hi})",
        )
    )

    # skills_keyword_count.
    #
    # Philosophy: skill groups are preserved as a signal of breadth; the
    # renderer trims keywords one-by-one (never whole groups). A full skill
    # matrix with 8-10 groups at 4-7 keywords each lands in the 30-70 range
    # on short-form; long-form accommodates broader breadth (35-110). Bands
    # selected via ``bands.skills_keyword_count_*``.
    skc_pass_lo, skc_pass_hi = bands.skills_keyword_count_pass
    skc_warn_lo, skc_warn_hi = bands.skills_keyword_count_warn
    skc = sum(len(s.keywords) for s in curation.skills)
    if skc_pass_lo <= skc <= skc_pass_hi:
        skc_status = EvalMetricStatus.PASS
    elif skc_warn_lo <= skc <= skc_warn_hi:
        skc_status = EvalMetricStatus.WARN
    else:
        skc_status = EvalMetricStatus.FAIL
    results.append(
        EvalMetricResult(
            name="skills_keyword_count",
            category=_CATEGORY,
            status=skc_status,
            value=skc,
            detail=f"{skc} total keywords (target: {skc_pass_lo}-{skc_pass_hi})",
        )
    )

    # resume_experience_years.
    max_years = _compute_experience_years(section_data or {})
    results.append(
        EvalMetricResult(
            name="resume_experience_years",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if max_years > 0 else EvalMetricStatus.WARN,
            value=max_years,
            detail=f"{max_years:.1f} years of experience",
        )
    )

    return results


def _compute_experience_years(section_data: dict[str, Any]) -> float:
    """Estimate total years of experience from work entry start_date fields."""
    from datetime import UTC, datetime

    years: list[int] = []
    for entry in section_data.get("work", []):
        if not isinstance(entry, dict):
            continue
        for field in ("start_date", "startDate", "end_date", "endDate"):
            val = entry.get(field)
            if val and str(val).strip():
                match = re.match(r"((?:19|20)\d{2})", str(val).strip())
                if match:
                    years.append(int(match.group(1)))

    if not years:
        return 0.0

    earliest = min(years)
    latest = max(max(years), datetime.now(tz=UTC).year)
    return float(latest - earliest)
