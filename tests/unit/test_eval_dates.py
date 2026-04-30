"""Tests for curator.eval.dates."""

from __future__ import annotations

from typing import Any

import pytest

from curator.eval.dates import evaluate_dates
from curator.eval.report import EvalMetricStatus
from tests.helpers import find_metric


def _make_section_data(
    work_entries: list[dict[str, Any]] | None = None,
    education_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if work_entries is not None:
        data["work"] = work_entries
    if education_entries is not None:
        data["education"] = education_entries
    return data


# ---------------------------------------------------------------------------
# date_format_consistency
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestDateFormatConsistency:
    def test_iso_dates_pass(self) -> None:
        section_data = _make_section_data(
            work_entries=[
                {
                    "id": "acme",
                    "start_date": "2023-06",
                    "end_date": "2025-01",
                },
            ],
        )
        results = evaluate_dates(section_data)
        m = find_metric(results, "date_format_consistency")
        assert m.status == EvalMetricStatus.PASS

    def test_mon_yyyy_format_pass(self) -> None:
        section_data = _make_section_data(
            work_entries=[
                {
                    "id": "acme",
                    "startDate": "Jun 2023",
                    "endDate": "Jan 2025",
                },
            ],
        )
        results = evaluate_dates(section_data)
        m = find_metric(results, "date_format_consistency")
        assert m.status == EvalMetricStatus.PASS

    def test_year_only_passes_format_check(self) -> None:
        section_data = _make_section_data(
            education_entries=[
                {
                    "id": "umw",
                    "startDate": "2014",
                    "endDate": "2018",
                },
            ],
        )
        results = evaluate_dates(section_data)
        m = find_metric(results, "date_format_consistency")
        assert m.status == EvalMetricStatus.PASS

    def test_present_value_passes(self) -> None:
        section_data = _make_section_data(
            work_entries=[
                {
                    "id": "acme",
                    "start_date": "2023-06",
                    "end_date": "Present",
                },
            ],
        )
        results = evaluate_dates(section_data)
        m = find_metric(results, "date_format_consistency")
        assert m.status == EvalMetricStatus.PASS

    def test_current_value_passes(self) -> None:
        section_data = _make_section_data(
            work_entries=[
                {
                    "id": "acme",
                    "start_date": "2023-06",
                    "end_date": "Current",
                },
            ],
        )
        results = evaluate_dates(section_data)
        m = find_metric(results, "date_format_consistency")
        assert m.status == EvalMetricStatus.PASS

    def test_invalid_format_fail(self) -> None:
        section_data = _make_section_data(
            work_entries=[
                {
                    "id": "acme",
                    "start_date": "June 2023",
                    "end_date": "January 2025",
                },
            ],
        )
        results = evaluate_dates(section_data)
        m = find_metric(results, "date_format_consistency")
        assert m.status == EvalMetricStatus.FAIL
        assert m.value >= 1

    def test_empty_section_data_pass(self) -> None:
        results = evaluate_dates({})
        m = find_metric(results, "date_format_consistency")
        assert m.status == EvalMetricStatus.PASS


# ---------------------------------------------------------------------------
# dates_include_months
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestDatesIncludeMonths:
    def test_all_have_months_pass(self) -> None:
        section_data = _make_section_data(
            work_entries=[
                {
                    "id": "acme",
                    "start_date": "2023-06",
                    "end_date": "2025-01",
                },
            ],
        )
        results = evaluate_dates(section_data)
        m = find_metric(results, "dates_include_months")
        assert m.status == EvalMetricStatus.PASS

    def test_year_only_warns(self) -> None:
        section_data = _make_section_data(
            work_entries=[
                {
                    "id": "acme",
                    "start_date": "2023",
                    "end_date": "2025",
                },
            ],
        )
        results = evaluate_dates(section_data)
        m = find_metric(results, "dates_include_months")
        assert m.status == EvalMetricStatus.WARN
        assert m.value >= 1

    def test_present_value_excluded(self) -> None:
        section_data = _make_section_data(
            work_entries=[
                {
                    "id": "acme",
                    "start_date": "2023-06",
                    "end_date": "Present",
                },
            ],
        )
        results = evaluate_dates(section_data)
        m = find_metric(results, "dates_include_months")
        assert m.status == EvalMetricStatus.PASS


# ---------------------------------------------------------------------------
# current_role_has_present
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestCurrentRoleHasPresent:
    def test_no_end_date_detected_as_current(self) -> None:
        section_data = _make_section_data(
            work_entries=[
                {
                    "id": "acme",
                    "start_date": "2023-06",
                },
            ],
        )
        results = evaluate_dates(section_data)
        m = find_metric(results, "current_role_has_present")
        assert m.status == EvalMetricStatus.PASS

    def test_empty_end_date_detected_as_current(self) -> None:
        section_data = _make_section_data(
            work_entries=[
                {
                    "id": "acme",
                    "start_date": "2023-06",
                    "end_date": "",
                },
            ],
        )
        results = evaluate_dates(section_data)
        m = find_metric(results, "current_role_has_present")
        assert m.status == EvalMetricStatus.PASS

    def test_null_end_date_detected_as_current(self) -> None:
        section_data = _make_section_data(
            work_entries=[
                {
                    "id": "acme",
                    "start_date": "2023-06",
                    "end_date": None,
                },
            ],
        )
        results = evaluate_dates(section_data)
        m = find_metric(results, "current_role_has_present")
        assert m.status == EvalMetricStatus.PASS

    def test_all_entries_have_end_dates_warn(self) -> None:
        section_data = _make_section_data(
            work_entries=[
                {
                    "id": "acme",
                    "start_date": "2020-01",
                    "end_date": "2023-06",
                },
                {
                    "id": "beta",
                    "start_date": "2023-07",
                    "end_date": "2025-01",
                },
            ],
        )
        results = evaluate_dates(section_data)
        m = find_metric(results, "current_role_has_present")
        assert m.status == EvalMetricStatus.WARN

    def test_no_work_entries_warn(self) -> None:
        results = evaluate_dates({})
        m = find_metric(results, "current_role_has_present")
        assert m.status == EvalMetricStatus.WARN


# ---------------------------------------------------------------------------
# Return structure
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestReturnStructure:
    def test_all_metrics_present(self) -> None:
        results = evaluate_dates({})
        names = {r.name for r in results}
        expected = {
            "date_format_consistency",
            "dates_include_months",
            "current_role_has_present",
        }
        assert names == expected

    def test_all_category_is_date_consistency(self) -> None:
        results = evaluate_dates({})
        for r in results:
            assert r.category == "date_consistency"
