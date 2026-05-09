"""Tests for curator.eval.content."""

from __future__ import annotations

from typing import Any

import pytest

from curator.eval.content import evaluate_content as _evaluate_content
from curator.eval.report import SHORT_FORM_BANDS, EvalMetricResult, EvalMetricStatus
from tests.helpers import find_metric


def evaluate_content(
    section_data: dict[str, Any],
    basics: dict[str, Any],
    **kwargs: Any,
) -> list[EvalMetricResult]:
    """Test wrapper that binds ``bands=SHORT_FORM_BANDS`` by default.

    The production ``evaluate_content`` requires an explicit ``bands``
    kwarg (no default; see ``test_eval_bands.py``). These short-form
    tests opt in to ``SHORT_FORM_BANDS`` once at module load rather
    than threading the kwarg through every call site.
    """
    kwargs.setdefault("bands", SHORT_FORM_BANDS)
    return _evaluate_content(section_data, basics, **kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_work_with_highlights(texts: list[str]) -> dict[str, Any]:
    return {
        "work": [
            {
                "id": "test-co",
                "position": "Engineer",
                "name": "Test",
                "highlights": [
                    {"id": f"h-{i}", "text": text} for i, text in enumerate(texts)
                ],
            },
        ],
    }


def _make_section_data_with_word_count(target_words: int) -> dict[str, Any]:
    words = ["word"] * target_words
    # Split across highlights and summary to hit the target.
    highlights = []
    chunk_size = 15
    used = 0
    while used < target_words:
        chunk = words[used : used + chunk_size]
        highlights.append(" ".join(chunk))
        used += chunk_size
    return _make_work_with_highlights(highlights)


# ---------------------------------------------------------------------------
# word_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestWordCount:
    def test_in_range_pass(self) -> None:
        section_data = _make_section_data_with_word_count(500)
        basics: dict[str, Any] = {"summary": "A short summary here."}
        results = evaluate_content(section_data, basics)
        m = find_metric(results, "word_count")
        assert m.status == EvalMetricStatus.PASS

    def test_below_400_fail(self) -> None:
        section_data = _make_section_data_with_word_count(50)
        basics: dict[str, Any] = {}
        results = evaluate_content(section_data, basics)
        m = find_metric(results, "word_count")
        assert m.status == EvalMetricStatus.FAIL

    def test_400_to_474_warn(self) -> None:
        section_data = _make_section_data_with_word_count(420)
        basics: dict[str, Any] = {}
        results = evaluate_content(section_data, basics)
        m = find_metric(results, "word_count")
        assert m.status == EvalMetricStatus.WARN

    def test_701_to_800_warn(self) -> None:
        section_data = _make_section_data_with_word_count(750)
        basics: dict[str, Any] = {}
        results = evaluate_content(section_data, basics)
        m = find_metric(results, "word_count")
        assert m.status == EvalMetricStatus.WARN

    def test_above_800_fail(self) -> None:
        section_data = _make_section_data_with_word_count(850)
        basics: dict[str, Any] = {}
        results = evaluate_content(section_data, basics)
        m = find_metric(results, "word_count")
        assert m.status == EvalMetricStatus.FAIL

    def test_empty_sections_zero_words(self) -> None:
        results = evaluate_content({}, {})
        m = find_metric(results, "word_count")
        assert m.value == 0
        assert m.status == EvalMetricStatus.FAIL

    def test_interests_dict_contributes_to_word_count(self) -> None:
        # Interests is a dict, not a list — historically skipped by the
        # word_count walker. Ensure hobbies/fun_facts text now counts.
        section_data: dict[str, Any] = {
            "interests": {
                "hobbies": [
                    {
                        "name": "Mountain biking",
                        "description": "Trail riding around Marin County",
                        "keywords": ["trails", "endurance"],
                    },
                ],
                "fun_facts": ["Once cycled across France in eight days"],
            },
        }
        results = evaluate_content(section_data, {})
        m = find_metric(results, "word_count")
        # Six tokens from name + description, two keywords, eight from fact.
        assert m.value > 10


# ---------------------------------------------------------------------------
# bullet_word_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestBulletWordCount:
    def test_all_bullets_in_range_pass(self) -> None:
        texts = [
            "Deployed Kubernetes cluster serving ten thousand"
            " requests per second daily",
            "Built automated CI pipeline reducing deployment"
            " time by fifty percent total",
        ]
        section_data = _make_work_with_highlights(texts)
        results = evaluate_content(section_data, {})
        m = find_metric(results, "bullet_word_count")
        assert m.status == EvalMetricStatus.PASS

    def test_bullet_too_short_outside_warn_range_fail(self) -> None:
        texts = ["Short", "Another short"]
        section_data = _make_work_with_highlights(texts)
        results = evaluate_content(section_data, {})
        m = find_metric(results, "bullet_word_count")
        assert m.status == EvalMetricStatus.FAIL

    def test_bullet_slightly_out_of_range_warn(self) -> None:
        texts = [
            # 6 words: in 5-40 WARN fallback but not 8-35 PASS range
            "Built system with five components total",
            "Deployed Kubernetes cluster serving ten thousand"
            " requests per second daily",
        ]
        section_data = _make_work_with_highlights(texts)
        results = evaluate_content(section_data, {})
        m = find_metric(results, "bullet_word_count")
        assert m.status == EvalMetricStatus.WARN

    def test_no_highlights_returns_warn(self) -> None:
        results = evaluate_content({}, {})
        m = find_metric(results, "bullet_word_count")
        assert m.status == EvalMetricStatus.WARN

    # PR #43 widened the band to PASS 8-35, WARN 5-40, FAIL outside.
    # Exercise the boundaries explicitly so future tweaks don't regress.

    def test_eight_words_is_pass_lower_boundary(self) -> None:
        # exactly 8 words on each bullet -> PASS lower bound.
        eight = "alpha beta gamma delta epsilon zeta eta theta"
        section_data = _make_work_with_highlights([eight, eight])
        results = evaluate_content(section_data, {})
        m = find_metric(results, "bullet_word_count")
        assert m.status == EvalMetricStatus.PASS

    def test_thirty_five_words_is_pass_upper_boundary(self) -> None:
        thirty_five = " ".join(f"word{i}" for i in range(35))
        section_data = _make_work_with_highlights([thirty_five, thirty_five])
        results = evaluate_content(section_data, {})
        m = find_metric(results, "bullet_word_count")
        assert m.status == EvalMetricStatus.PASS

    def test_thirty_six_words_warns(self) -> None:
        thirty_six = " ".join(f"word{i}" for i in range(36))
        baseline = " ".join(f"baseline{i}" for i in range(20))
        section_data = _make_work_with_highlights([thirty_six, baseline])
        results = evaluate_content(section_data, {})
        m = find_metric(results, "bullet_word_count")
        assert m.status == EvalMetricStatus.WARN

    def test_forty_one_words_fails(self) -> None:
        forty_one = " ".join(f"word{i}" for i in range(41))
        baseline = " ".join(f"baseline{i}" for i in range(20))
        section_data = _make_work_with_highlights([forty_one, baseline])
        results = evaluate_content(section_data, {})
        m = find_metric(results, "bullet_word_count")
        assert m.status == EvalMetricStatus.FAIL


# ---------------------------------------------------------------------------
# summary_word_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestSummaryWordCount:
    def test_in_range_pass(self) -> None:
        summary = " ".join(["word"] * 50)
        results = evaluate_content({}, {"summary": summary})
        m = find_metric(results, "summary_word_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 50

    def test_below_20_fail(self) -> None:
        results = evaluate_content({}, {"summary": "Too short."})
        m = find_metric(results, "summary_word_count")
        assert m.status == EvalMetricStatus.FAIL

    def test_20_to_29_warn(self) -> None:
        summary = " ".join(["word"] * 25)
        results = evaluate_content({}, {"summary": summary})
        m = find_metric(results, "summary_word_count")
        assert m.status == EvalMetricStatus.WARN

    def test_81_to_100_warn(self) -> None:
        summary = " ".join(["word"] * 90)
        results = evaluate_content({}, {"summary": summary})
        m = find_metric(results, "summary_word_count")
        assert m.status == EvalMetricStatus.WARN

    def test_over_100_fail(self) -> None:
        summary = " ".join(["word"] * 120)
        results = evaluate_content({}, {"summary": summary})
        m = find_metric(results, "summary_word_count")
        assert m.status == EvalMetricStatus.FAIL

    def test_empty_summary_fail(self) -> None:
        results = evaluate_content({}, {})
        m = find_metric(results, "summary_word_count")
        assert m.value == 0
        assert m.status == EvalMetricStatus.FAIL

    def test_none_summary_fail(self) -> None:
        results = evaluate_content({}, {"summary": None})
        m = find_metric(results, "summary_word_count")
        assert m.value == 0
        assert m.status == EvalMetricStatus.FAIL


# ---------------------------------------------------------------------------
# Return structure
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestReturnStructure:
    def test_returns_three_metrics(self) -> None:
        results = evaluate_content({}, {})
        names = {r.name for r in results}
        assert names == {"word_count", "bullet_word_count", "summary_word_count"}

    def test_all_metrics_have_content_density_category(self) -> None:
        results = evaluate_content({}, {})
        for r in results:
            assert r.category == "content_density"
