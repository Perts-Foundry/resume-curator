"""Date & Format Consistency metrics (5% weight).

Validates date formatting in rendered section data matches
expected Mon YYYY or "Present"/"Current" patterns.
"""

from __future__ import annotations

import re
from typing import Any

from curator.eval.report import EvalMetricResult, EvalMetricStatus

_CATEGORY = "date_consistency"

# Expected date formats in rendered output.
_MONTH_YEAR_RE = re.compile(
    r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}$"
)
_YEAR_ONLY_RE = re.compile(r"^\d{4}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?$")
_PRESENT_VALUES = frozenset({"present", "current"})


def _extract_dates(section_data: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Extract (entry_id, field_name, date_value) tuples from work + education."""
    dates: list[tuple[str, str, str]] = []
    for section in ("work", "education"):
        for entry in section_data.get(section, []):
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("id", "unknown"))
            for field in ("start_date", "end_date", "startDate", "endDate"):
                val = entry.get(field)
                if val is not None and str(val).strip():
                    dates.append((entry_id, field, str(val).strip()))
    return dates


def evaluate_dates(section_data: dict[str, Any]) -> list[EvalMetricResult]:
    """Evaluate Date & Format Consistency metrics."""
    results: list[EvalMetricResult] = []

    dates = _extract_dates(section_data)

    # date_format_consistency — all dates match Mon YYYY, ISO, or Present/Current.
    inconsistent: list[str] = []
    for entry_id, field, value in dates:
        value_lower = value.lower()
        if value_lower in _PRESENT_VALUES:
            continue
        if _MONTH_YEAR_RE.match(value):
            continue
        if _ISO_DATE_RE.match(value):
            continue
        if _YEAR_ONLY_RE.match(value):
            continue  # Year-only is checked separately.
        inconsistent.append(f"{entry_id}.{field}: '{value}'")

    results.append(
        EvalMetricResult(
            name="date_format_consistency",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if not inconsistent else EvalMetricStatus.FAIL,
            value=len(inconsistent),
            detail="; ".join(inconsistent)
            if inconsistent
            else "All dates in expected format",
        )
    )

    # dates_include_months — no year-only dates.
    year_only: list[str] = []
    for entry_id, field, value in dates:
        if value.lower() in _PRESENT_VALUES:
            continue
        if _YEAR_ONLY_RE.match(value):
            year_only.append(f"{entry_id}.{field}: '{value}'")

    results.append(
        EvalMetricResult(
            name="dates_include_months",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if not year_only else EvalMetricStatus.WARN,
            value=len(year_only),
            detail="; ".join(year_only) if year_only else "All dates include month",
        )
    )

    # current_role_has_present — entry with no end_date renders as Present/Current.
    work_entries = section_data.get("work", [])
    has_current = any(
        isinstance(e, dict)
        and (
            not (e.get("end_date") or e.get("endDate"))
            or str(e.get("end_date") or e.get("endDate", "")).strip() == ""
        )
        for e in work_entries
    )
    results.append(
        EvalMetricResult(
            name="current_role_has_present",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if has_current else EvalMetricStatus.WARN,
            value=has_current,
            detail="Current role found (no end_date)"
            if has_current
            else "No current role detected — all entries have end dates",
        )
    )

    return results
