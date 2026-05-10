"""Tests for curator.eval.selection."""

from __future__ import annotations

from typing import Any

import pytest

from curator.eval.report import SHORT_FORM_BANDS, EvalMetricResult, EvalMetricStatus
from curator.eval.selection import evaluate_selection as _evaluate_selection
from curator.models import ResumeCuration
from tests.helpers import find_metric, make_curation_dict


def evaluate_selection(
    curation: ResumeCuration,
    basics: dict[str, Any],
    **kwargs: Any,
) -> list[EvalMetricResult]:
    """Test wrapper that binds ``bands=SHORT_FORM_BANDS`` by default.

    Production ``evaluate_selection`` requires an explicit ``bands``
    kwarg; these short-form tests opt in once.
    """
    kwargs.setdefault("bands", SHORT_FORM_BANDS)
    return _evaluate_selection(curation, basics, **kwargs)


def _authored_counts(*entries: tuple[str, int]) -> dict[str, int]:
    """Build a work_authored_highlight_counts projection mapping for tests."""
    return dict(entries)


def _make_curation(**overrides: Any) -> ResumeCuration:
    return ResumeCuration.model_validate(make_curation_dict(**overrides))


def _basics_with_profiles(*networks: str) -> dict[str, Any]:
    return {
        "profiles": [
            {"network": network, "url": f"https://{network.lower()}.com/user"}
            for network in networks
        ],
    }


def _section_data_from_curation(curation: ResumeCuration) -> dict[str, Any]:
    return {
        "work": [
            {
                "id": wh.work_id,
                "highlights": [{"id": hid} for hid in wh.highlight_ids],
            }
            for wh in curation.work_highlights
        ],
    }


# ---------------------------------------------------------------------------
# work_entry_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestWorkEntryCount:
    def test_with_section_data_pass(self) -> None:
        curation = _make_curation()
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(curation, {}, section_data=section_data)
        m = find_metric(results, "work_entry_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 1

    def test_empty_section_data_fail(self) -> None:
        curation = _make_curation()
        results = evaluate_selection(curation, {}, section_data={"work": []})
        m = find_metric(results, "work_entry_count")
        assert m.status == EvalMetricStatus.FAIL


# ---------------------------------------------------------------------------
# highlight_counts
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestHighlightCounts:
    def test_position_0_pass(self) -> None:
        curation = _make_curation(
            work_highlights=[
                {
                    "work_id": "acme-senior-engineer",
                    "highlight_ids": [f"h-{i}" for i in range(5)],
                },
            ],
        )
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(curation, {}, section_data=section_data)
        m = find_metric(results, "highlight_counts")
        assert m.status == EvalMetricStatus.PASS

    def test_position_0_too_few_highlights_issues(self) -> None:
        curation = _make_curation(
            work_highlights=[
                {"work_id": "acme-senior-engineer", "highlight_ids": ["h-0"]},
            ],
        )
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(curation, {}, section_data=section_data)
        m = find_metric(results, "highlight_counts")
        assert m.value >= 1

    def test_multiple_issues_fail(self) -> None:
        work = [
            {"work_id": f"job-{i}", "highlight_ids": [f"h-{i}-0"]} for i in range(3)
        ]
        curation = _make_curation(work_highlights=work)
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(curation, {}, section_data=section_data)
        m = find_metric(results, "highlight_counts")
        assert m.status == EvalMetricStatus.FAIL

    def test_missing_section_data_warns(self) -> None:
        curation = _make_curation()
        results = evaluate_selection(curation, {})
        m = find_metric(results, "highlight_counts")
        assert m.status == EvalMetricStatus.WARN
        assert "section_data" in m.detail

    def test_position_0_clamped_to_authored_count_pass(self) -> None:
        """Entry at position 0 with only 3 authored highlights should PASS
        when the curator selected all 3, rather than WARN against a
        position-based 4-5 band the portfolio cannot satisfy."""
        curation = _make_curation(
            work_highlights=[
                {
                    "work_id": "newish-role",
                    "highlight_ids": [f"newish-role-h-{i}" for i in range(3)],
                },
            ],
        )
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(
            curation,
            {},
            section_data=section_data,
            work_authored_highlight_counts=_authored_counts(("newish-role", 3)),
        )
        m = find_metric(results, "highlight_counts")
        assert m.status == EvalMetricStatus.PASS, m.detail

    def test_position_0_at_floor_passes_regardless_of_authored(self) -> None:
        """Position 0 with 3 selected (== short-form floor) passes even when
        the portfolio authored more.

        Semantic shift from the old ``primary_role_highlight_target=5``
        model: under the new floor-derived bands, the metric checks
        whether the rendered output landed at the per-position floor.
        Position 0 at floor 3 with 3 selected == band lower bound →
        PASS. The "rich-portfolio under-selection" signal moved out of
        ``highlight_counts``; if it's needed as a separate eval, a
        dedicated metric can be added without disturbing the
        floor-derived band semantics.
        """
        curation = _make_curation(
            work_highlights=[
                {
                    "work_id": "deep-role",
                    "highlight_ids": [f"deep-role-h-{i}" for i in range(3)],
                },
            ],
        )
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(
            curation,
            {},
            section_data=section_data,
            work_authored_highlight_counts=_authored_counts(("deep-role", 6)),
        )
        m = find_metric(results, "highlight_counts")
        assert m.status == EvalMetricStatus.PASS, m.detail
        # No clamp fired (authored=6 >= hi=5 at position 0), so the
        # detail should NOT carry the [authored: ...] decoration. Pins
        # CR-1's invariant that the decoration only appears on clamped
        # mismatches.
        assert "[authored:" not in m.detail

    def test_position_1_clamped_to_authored_band_pass(self) -> None:
        """TE-1 repurpose: Entry at position 1 with only 2 authored
        highlights (position-1 band is 2-3, clamped to 2-2) and 2 selected
        should PASS. Exercises the clamp branch that the prior
        full-authored-selection test no-op'd through."""
        curation = _make_curation(
            work_highlights=[
                {
                    "work_id": "current-role",
                    "highlight_ids": [f"current-h-{i}" for i in range(5)],
                },
                {
                    "work_id": "prior-role",
                    "highlight_ids": [f"prior-h-{i}" for i in range(2)],
                },
            ],
        )
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(
            curation,
            {},
            section_data=section_data,
            work_authored_highlight_counts=_authored_counts(
                ("current-role", 5), ("prior-role", 2)
            ),
        )
        m = find_metric(results, "highlight_counts")
        assert m.status == EvalMetricStatus.PASS, m.detail

    def test_clamped_still_underselects_flags(self) -> None:
        """TE-2: Portfolio has 3 authored at position 0; band clamps to
        3-3; curator selected 2. Should WARN, expected band reads 3-3,
        and detail carries the [authored: 3] decoration."""
        curation = _make_curation(
            work_highlights=[
                {
                    "work_id": "shallow-role",
                    "highlight_ids": [f"shallow-h-{i}" for i in range(2)],
                },
            ],
        )
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(
            curation,
            {},
            section_data=section_data,
            work_authored_highlight_counts=_authored_counts(("shallow-role", 3)),
        )
        m = find_metric(results, "highlight_counts")
        assert m.status == EvalMetricStatus.WARN, m.detail
        assert "expected 3-3" in m.detail
        assert "[authored: 3]" in m.detail

    def test_entry_id_missing_from_portfolio_falls_back_to_position_band(
        self,
    ) -> None:
        """TE-3: When a curated entry id is not present in the projection
        (e.g., entry was renamed/deleted in the portfolio), the metric
        falls back to the unclamped position band silently. Behavior
        matches the no-projection case."""
        curation = _make_curation(
            work_highlights=[
                {
                    "work_id": "acme-senior-engineer",
                    "highlight_ids": [f"h-{i}" for i in range(5)],
                },
            ],
        )
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(
            curation,
            {},
            section_data=section_data,
            # Projection contains a different entry; lookup misses.
            work_authored_highlight_counts=_authored_counts(("unrelated-role", 9)),
        )
        m = find_metric(results, "highlight_counts")
        assert m.status == EvalMetricStatus.PASS, m.detail

    def test_authored_zero_falls_back_to_position_band(self) -> None:
        """TE-4 + CR-2: Entry exists in the portfolio with zero authored
        highlights. The clamp guard skips this case (clamping to 0 would
        force-FAIL every position-0 role); the metric uses the unclamped
        position band. Selecting 5 at position 0 PASSes."""
        curation = _make_curation(
            work_highlights=[
                {
                    "work_id": "empty-role",
                    "highlight_ids": [f"h-{i}" for i in range(5)],
                },
            ],
        )
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(
            curation,
            {},
            section_data=section_data,
            work_authored_highlight_counts=_authored_counts(("empty-role", 0)),
        )
        m = find_metric(results, "highlight_counts")
        assert m.status == EvalMetricStatus.PASS, m.detail

    def test_authored_equals_hi_no_clamp(self) -> None:
        """CR-3: authored=hi exactly; clamp condition (authored < hi) is
        false; band stays unchanged; selecting hi PASSes without the
        clamped decoration."""
        curation = _make_curation(
            work_highlights=[
                {
                    "work_id": "exact-fit-role",
                    "highlight_ids": [f"h-{i}" for i in range(5)],
                },
            ],
        )
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(
            curation,
            {},
            section_data=section_data,
            work_authored_highlight_counts=_authored_counts(("exact-fit-role", 5)),
        )
        m = find_metric(results, "highlight_counts")
        assert m.status == EvalMetricStatus.PASS, m.detail

    def test_authored_equals_lo_band_collapses_pass(self) -> None:
        """CR-3: authored=lo exactly (4 at position 0); band collapses
        to 4-4 (lo=min(4,4)=4, hi=min(5,4)=4); selecting 4 PASSes."""
        curation = _make_curation(
            work_highlights=[
                {
                    "work_id": "lo-fit-role",
                    "highlight_ids": [f"h-{i}" for i in range(4)],
                },
            ],
        )
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(
            curation,
            {},
            section_data=section_data,
            work_authored_highlight_counts=_authored_counts(("lo-fit-role", 4)),
        )
        m = find_metric(results, "highlight_counts")
        assert m.status == EvalMetricStatus.PASS, m.detail

    def test_older_roles_with_zero_highlights_pass(self) -> None:
        """Positions 2+ may render as header-only rows (zero highlights)
        without triggering an issue. The cascade preserves the full
        employment timeline by keeping every work entry visible, and the
        metric treats header-only older roles as acceptable."""
        work = [
            {
                "work_id": "acme-senior-engineer",
                "highlight_ids": [f"h0-{i}" for i in range(5)],
            },
            {
                "work_id": "beta-engineer",
                "highlight_ids": [f"h1-{i}" for i in range(3)],
            },
            {"work_id": "gamma-engineer", "highlight_ids": []},
            {"work_id": "delta-engineer", "highlight_ids": []},
        ]
        curation = _make_curation(work_highlights=work)
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(curation, {}, section_data=section_data)
        m = find_metric(results, "highlight_counts")
        assert m.status == EvalMetricStatus.PASS, m.detail


# ---------------------------------------------------------------------------
# skill_group_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestSkillGroupCount:
    def test_in_range_pass(self) -> None:
        skills = [{"skill_id": f"skill-{i}", "keywords": ["kw"]} for i in range(7)]
        curation = _make_curation(skills=skills)
        results = evaluate_selection(curation, {})
        m = find_metric(results, "skill_group_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 7

    def test_below_4_fail(self) -> None:
        skills = [{"skill_id": f"skill-{i}", "keywords": ["kw"]} for i in range(2)]
        curation = _make_curation(skills=skills)
        results = evaluate_selection(curation, {})
        m = find_metric(results, "skill_group_count")
        assert m.status == EvalMetricStatus.FAIL

    def test_4_to_5_warn(self) -> None:
        skills = [{"skill_id": f"skill-{i}", "keywords": ["kw"]} for i in range(5)]
        curation = _make_curation(skills=skills)
        results = evaluate_selection(curation, {})
        m = find_metric(results, "skill_group_count")
        assert m.status == EvalMetricStatus.WARN


# ---------------------------------------------------------------------------
# section_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestSectionCount:
    def test_in_range_with_section_data(self) -> None:
        curation = _make_curation()
        section_data = {
            "work": [{"id": "w"}],
            "skills": [{"id": "s"}],
            "projects": [{"id": "p"}],
            "certificates": [{"id": "c"}],
            "education": [{"id": "e"}],
        }
        results = evaluate_selection(curation, {}, section_data=section_data)
        m = find_metric(results, "section_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 5

    def test_few_sections_warn(self) -> None:
        curation = _make_curation(skills=[], projects=[])
        results = evaluate_selection(curation, {})
        m = find_metric(results, "section_count")
        assert m.status in (EvalMetricStatus.WARN, EvalMetricStatus.FAIL)

    def test_one_section_fail(self) -> None:
        curation = _make_curation(skills=[], projects=[])
        results = evaluate_selection(curation, {})
        m = find_metric(results, "section_count")
        assert m.value <= 2


# ---------------------------------------------------------------------------
# label_word_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestLabelWordCount:
    def test_in_range_pass(self) -> None:
        curation = _make_curation(suggested_label="Senior DevOps Engineer")
        results = evaluate_selection(curation, {})
        m = find_metric(results, "label_word_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 3

    def test_one_word_fail(self) -> None:
        curation = _make_curation(suggested_label="Engineer")
        results = evaluate_selection(curation, {})
        m = find_metric(results, "label_word_count")
        assert m.status == EvalMetricStatus.FAIL

    def test_six_words_fail(self) -> None:
        curation = _make_curation(
            suggested_label="Very Senior Distinguished Staff DevOps Eng",
        )
        results = evaluate_selection(curation, {})
        m = find_metric(results, "label_word_count")
        assert m.status == EvalMetricStatus.FAIL

    def test_five_words_pass(self) -> None:
        curation = _make_curation(
            suggested_label="Senior Staff DevOps SRE Engineer",
        )
        results = evaluate_selection(curation, {})
        m = find_metric(results, "label_word_count")
        assert m.status == EvalMetricStatus.PASS


# ---------------------------------------------------------------------------
# website_present / github_present
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestProfilePresence:
    def test_website_present_pass(self) -> None:
        basics = {"url": "https://example.com"}
        curation = _make_curation()
        results = evaluate_selection(curation, basics)
        m = find_metric(results, "website_present")
        assert m.status == EvalMetricStatus.PASS

    def test_website_missing_fail(self) -> None:
        curation = _make_curation()
        results = evaluate_selection(curation, {})
        m = find_metric(results, "website_present")
        assert m.status == EvalMetricStatus.FAIL

    def test_website_empty_string_fail(self) -> None:
        basics = {"url": ""}
        curation = _make_curation()
        results = evaluate_selection(curation, basics)
        m = find_metric(results, "website_present")
        assert m.status == EvalMetricStatus.FAIL

    def test_github_present_pass(self) -> None:
        basics = _basics_with_profiles("GitHub")
        curation = _make_curation()
        results = evaluate_selection(curation, basics)
        m = find_metric(results, "github_present")
        assert m.status == EvalMetricStatus.PASS

    def test_github_missing_fail(self) -> None:
        curation = _make_curation()
        results = evaluate_selection(curation, {})
        m = find_metric(results, "github_present")
        assert m.status == EvalMetricStatus.FAIL

    def test_github_case_insensitive(self) -> None:
        basics = {**_basics_with_profiles("GITHUB"), "url": "https://example.com"}
        curation = _make_curation()
        results = evaluate_selection(curation, basics)
        assert find_metric(results, "website_present").status == EvalMetricStatus.PASS
        assert find_metric(results, "github_present").status == EvalMetricStatus.PASS


# ---------------------------------------------------------------------------
# summary_word_count_in_range (replaces has_reasoning)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestSummaryWordCountInRange:
    def test_in_range_pass(self) -> None:
        curation = _make_curation()
        results = evaluate_selection(curation, {})
        m = find_metric(results, "summary_word_count_in_range")
        assert m.status in (EvalMetricStatus.PASS, EvalMetricStatus.WARN)

    def test_short_summary_fail(self) -> None:
        curation = _make_curation(summary="Too short.")
        results = evaluate_selection(curation, {})
        m = find_metric(results, "summary_word_count_in_range")
        assert m.status == EvalMetricStatus.FAIL


# ---------------------------------------------------------------------------
# total_highlight_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestTotalHighlightCount:
    def test_in_range_pass(self) -> None:
        work = [
            {
                "work_id": f"job-{i}",
                "highlight_ids": [f"h-{i}-{j}" for j in range(5)],
            }
            for i in range(2)
        ]
        curation = _make_curation(work_highlights=work)
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(curation, {}, section_data=section_data)
        m = find_metric(results, "total_highlight_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 10

    def test_below_5_fail(self) -> None:
        curation = _make_curation(
            work_highlights=[
                {"work_id": "acme-senior-engineer", "highlight_ids": ["h-0", "h-1"]},
            ],
        )
        section_data = _section_data_from_curation(curation)
        results = evaluate_selection(curation, {}, section_data=section_data)
        m = find_metric(results, "total_highlight_count")
        assert m.status == EvalMetricStatus.FAIL


# ---------------------------------------------------------------------------
# skills_keyword_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestSkillsKeywordCount:
    def test_in_range_pass(self) -> None:
        skills = [
            {"skill_id": f"skill-{i}", "keywords": [f"kw-{j}" for j in range(3)]}
            for i in range(7)
        ]
        curation = _make_curation(skills=skills)
        results = evaluate_selection(curation, {})
        m = find_metric(results, "skills_keyword_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 21

    def test_below_10_fail(self) -> None:
        skills = [{"skill_id": "skill-0", "keywords": ["kw"]}]
        curation = _make_curation(skills=skills)
        results = evaluate_selection(curation, {})
        m = find_metric(results, "skills_keyword_count")
        assert m.status == EvalMetricStatus.FAIL

    def test_full_skill_matrix_passes(self) -> None:
        """The renderer preserves skill group diversity by trimming
        keywords one-by-one rather than removing whole groups. A full
        matrix with 8-10 groups at 5-7 keywords each (40-70 total) must
        score PASS under the expanded target band."""
        skills = [
            {"skill_id": f"skill-{i}", "keywords": [f"kw-{j}" for j in range(7)]}
            for i in range(9)
        ]
        curation = _make_curation(skills=skills)
        results = evaluate_selection(curation, {})
        m = find_metric(results, "skills_keyword_count")
        assert m.value == 63
        assert m.status == EvalMetricStatus.PASS


# ---------------------------------------------------------------------------
# resume_experience_years
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestResumeExperienceYears:
    def test_dates_in_section_data_pass(self) -> None:
        curation = _make_curation()
        section_data = {
            "work": [
                {"id": "job-1", "start_date": "2018-06", "end_date": "2022-01"},
                {"id": "job-2", "start_date": "2022-02"},
            ],
        }
        results = evaluate_selection(curation, {}, section_data=section_data)
        m = find_metric(results, "resume_experience_years")
        assert m.status == EvalMetricStatus.PASS
        assert m.value > 0

    def test_no_dates_warn(self) -> None:
        curation = _make_curation()
        results = evaluate_selection(curation, {}, section_data={})
        m = find_metric(results, "resume_experience_years")
        assert m.status == EvalMetricStatus.WARN
        assert m.value == 0.0


# ---------------------------------------------------------------------------
# Return structure
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestReturnStructure:
    def test_all_metrics_present(self) -> None:
        curation = _make_curation()
        results = evaluate_selection(curation, {})
        names = {r.name for r in results}
        expected = {
            "work_entry_count",
            "highlight_counts",
            "skill_group_count",
            "section_count",
            "label_word_count",
            "website_present",
            "github_present",
            "summary_word_count_in_range",
            "total_highlight_count",
            "skills_keyword_count",
            "resume_experience_years",
        }
        assert names == expected

    def test_all_category_is_selection_quality(self) -> None:
        curation = _make_curation()
        results = evaluate_selection(curation, {})
        for r in results:
            assert r.category == "selection_quality"
