"""Tests for curator.eval.writing."""

from __future__ import annotations

from typing import Any

import pytest

from curator.eval.report import EvalMetricStatus
from curator.eval.writing import evaluate_writing
from curator.models import ResumeCuration
from tests.helpers import find_metric


def _make_curation(**overrides: Any) -> ResumeCuration:
    from tests.helpers import make_curation_dict

    return ResumeCuration.model_validate(make_curation_dict(**overrides))


def _make_section_data(
    highlights: list[str] | None = None,
    *,
    end_date: str | None = None,
) -> dict[str, Any]:
    texts = highlights or [
        "Deployed Kubernetes cluster serving 10k RPS with 99.9% uptime"
    ]
    return {
        "work": [
            {
                "id": "acme-senior-engineer",
                "position": "Senior Engineer",
                "name": "Acme Corp",
                "end_date": end_date,
                "highlights": [
                    {"id": f"h-{i}", "text": text} for i, text in enumerate(texts)
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# quantification_rate
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestQuantificationRate:
    def test_all_quantified_pass(self) -> None:
        section_data = _make_section_data(
            [
                "Reduced latency by 40% across 3 services",
                "Saved $50k annually through automation of 12 processes",
            ]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "quantification_rate")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 1.0

    def test_none_quantified_fail(self) -> None:
        section_data = _make_section_data(
            [
                "Improved system performance significantly",
                "Enhanced team collaboration across departments",
            ]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "quantification_rate")
        assert m.status == EvalMetricStatus.FAIL
        assert m.value == 0.0

    def test_partial_quantification_warn(self) -> None:
        section_data = _make_section_data(
            [
                "Reduced latency by 40%",
                "Improved system performance significantly",
                "Enhanced collaboration across teams",
            ]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "quantification_rate")
        # 1/3 = 33% => WARN
        assert m.status == EvalMetricStatus.WARN

    def test_no_highlights_warn(self) -> None:
        curation = _make_curation()
        results = evaluate_writing({}, {}, curation)
        m = find_metric(results, "quantification_rate")
        assert m.status == EvalMetricStatus.WARN


# ---------------------------------------------------------------------------
# weak_phrase_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestWeakPhraseCount:
    def test_none_found_pass(self) -> None:
        section_data = _make_section_data(
            ["Deployed Kubernetes cluster serving 10k RPS"]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "weak_phrase_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 0

    def test_weak_phrase_found_fail(self) -> None:
        section_data = _make_section_data(
            [
                "Responsible for deploying Kubernetes cluster",
            ]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "weak_phrase_count")
        assert m.status == EvalMetricStatus.FAIL
        assert m.value >= 1

    def test_substring_of_longer_word_not_matched(self) -> None:
        # "managed" must not match "management" (different word).
        section_data = _make_section_data(
            ["Built automated credential lifecycle management for SCIM"]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "weak_phrase_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 0

    def test_compound_adjective_not_matched(self) -> None:
        # "managed" in "Atlantis-managed" and "assisted" in "AI-assisted"
        # are compound adjectives, not weak verbs. Must not match.
        section_data = _make_section_data(
            [
                "Spearheaded redesign through Atlantis-managed workflows",
                "Delivered features using AI-assisted development tools",
            ]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "weak_phrase_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 0

    def test_weak_verb_still_caught_after_boundary_fix(self) -> None:
        # Guard: the regex fix must still catch legitimate weak phrases.
        section_data = _make_section_data(["Managed the entire deployment pipeline"])
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "weak_phrase_count")
        assert m.status == EvalMetricStatus.FAIL
        assert m.value >= 1


# ---------------------------------------------------------------------------
# ai_red_flag_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestAIRedFlagCount:
    def test_none_found_pass(self) -> None:
        section_data = _make_section_data(
            ["Deployed Kubernetes cluster serving 10k RPS"]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "ai_red_flag_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 0

    def test_red_flag_word_fail(self) -> None:
        section_data = _make_section_data(
            ["Delve into infrastructure to leverage synergy"]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "ai_red_flag_count")
        assert m.status == EvalMetricStatus.FAIL
        assert m.value >= 1


# ---------------------------------------------------------------------------
# first_person_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestFirstPersonCount:
    def test_none_found_pass(self) -> None:
        section_data = _make_section_data(
            ["Deployed Kubernetes cluster serving 10k RPS"]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "first_person_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 0

    def test_i_pronoun_fail(self) -> None:
        section_data = _make_section_data(["I deployed the Kubernetes cluster"])
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "first_person_count")
        assert m.status == EvalMetricStatus.FAIL
        assert m.value >= 1

    def test_io_excluded(self) -> None:
        section_data = _make_section_data(
            ["Optimized I/O performance across storage layer"]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "first_person_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 0

    def test_my_pronoun_fail(self) -> None:
        basics: dict[str, Any] = {"summary": "my experience includes DevOps"}
        curation = _make_curation()
        results = evaluate_writing({}, basics, curation)
        m = find_metric(results, "first_person_count")
        assert m.status == EvalMetricStatus.FAIL

    def test_roman_numeral_in_position_not_counted(self) -> None:
        # "Cloud Support Engineer I" — Roman numeral in a title is not a
        # first-person pronoun. Position field is identifier-only and
        # excluded from prose scanning.
        section_data: dict[str, Any] = {
            "work": [
                {
                    "id": "aws-cse",
                    "position": "Cloud Support Engineer I - Container Services",
                    "name": "Amazon Web Services",
                    "end_date": "2023-06",
                    "highlights": [
                        {"id": "h-0", "text": "Led response to outage on EKS cluster"},
                    ],
                },
            ],
        }
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "first_person_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 0


# ---------------------------------------------------------------------------
# no_periods_on_bullets
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestNoPeriodsOnBullets:
    def test_no_periods_pass(self) -> None:
        section_data = _make_section_data(
            ["Deployed Kubernetes cluster serving 10k RPS"]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "no_periods_on_bullets")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 0

    def test_period_on_bullet_fail(self) -> None:
        section_data = _make_section_data(
            ["Deployed Kubernetes cluster serving 10k RPS."]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "no_periods_on_bullets")
        assert m.status == EvalMetricStatus.FAIL
        assert m.value == 1


# ---------------------------------------------------------------------------
# action_verb_start_rate
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestActionVerbStartRate:
    def test_all_start_with_verbs_pass(self) -> None:
        section_data = _make_section_data(
            [
                "Deployed Kubernetes cluster serving 10k RPS",
                "Built automated CI pipeline reducing deploy time",
            ]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "action_verb_start_rate")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 1.0

    def test_no_verbs_fail(self) -> None:
        section_data = _make_section_data(
            [
                "The system was improved significantly",
                "Many services were updated across infrastructure",
            ]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "action_verb_start_rate")
        assert m.status == EvalMetricStatus.FAIL

    def test_no_highlights_warn(self) -> None:
        curation = _make_curation()
        results = evaluate_writing({}, {}, curation)
        m = find_metric(results, "action_verb_start_rate")
        assert m.status == EvalMetricStatus.WARN

    def test_adverb_then_verb_accepted(self) -> None:
        # "Proactively identified" and "Successfully delivered" are valid
        # verb-initiated bullets; the -ly adverb prefix is skipped.
        section_data = _make_section_data(
            [
                "Proactively identified and resolved a critical pipeline bug",
                "Successfully delivered the migration ahead of schedule",
            ]
        )
        curation = _make_curation()
        results = evaluate_writing(section_data, {}, curation)
        m = find_metric(results, "action_verb_start_rate")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 1.0


# ---------------------------------------------------------------------------
# Summary quality metrics
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestSummaryQuality:
    def test_summary_sentence_count_in_range_pass(self) -> None:
        basics: dict[str, Any] = {
            "summary": (
                "Experienced SRE with 8 years of experience. "
                "Focused on reliability and automation. "
                "Passionate about scalable systems."
            ),
        }
        curation = _make_curation()
        results = evaluate_writing({}, basics, curation)
        m = find_metric(results, "summary_sentence_count")
        assert m.status == EvalMetricStatus.PASS

    def test_summary_has_years_experience_pass(self) -> None:
        basics: dict[str, Any] = {
            "summary": "Senior SRE with 8+ years of experience in DevOps.",
        }
        curation = _make_curation()
        results = evaluate_writing({}, basics, curation)
        m = find_metric(results, "summary_has_years_experience")
        assert m.status == EvalMetricStatus.PASS

    def test_summary_has_years_experience_missing_fail(self) -> None:
        basics: dict[str, Any] = {
            "summary": "Senior SRE focused on reliability and automation.",
        }
        curation = _make_curation()
        results = evaluate_writing({}, basics, curation)
        m = find_metric(results, "summary_has_years_experience")
        assert m.status == EvalMetricStatus.FAIL

    def test_summary_has_title_pass(self) -> None:
        basics: dict[str, Any] = {
            "summary": "Senior DevOps Engineer with extensive experience.",
        }
        curation = _make_curation(suggested_label="Senior DevOps Engineer")
        results = evaluate_writing({}, basics, curation)
        m = find_metric(results, "summary_has_title")
        assert m.status == EvalMetricStatus.PASS

    def test_summary_has_title_bigram_match_pass(self) -> None:
        basics: dict[str, Any] = {
            "summary": "Experienced DevOps Engineer focused on automation.",
        }
        curation = _make_curation(suggested_label="Senior DevOps Engineer")
        results = evaluate_writing({}, basics, curation)
        m = find_metric(results, "summary_has_title")
        assert m.status == EvalMetricStatus.PASS

    def test_summary_has_title_missing_warn(self) -> None:
        basics: dict[str, Any] = {
            "summary": "Experienced professional focused on cloud infrastructure.",
        }
        curation = _make_curation(suggested_label="Senior DevOps Engineer")
        results = evaluate_writing({}, basics, curation)
        m = find_metric(results, "summary_has_title")
        assert m.status == EvalMetricStatus.WARN


# ---------------------------------------------------------------------------
# Trivial / soft skills detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestSkillsQuality:
    def test_no_trivial_skills_pass(self) -> None:
        curation = _make_curation(
            skills=[
                {"skill_id": "cloud-aws", "keywords": ["EKS", "Lambda"]},
            ],
        )
        results = evaluate_writing({}, {}, curation)
        m = find_metric(results, "no_trivial_skills")
        assert m.status == EvalMetricStatus.PASS

    def test_trivial_skill_detected_fail(self) -> None:
        curation = _make_curation(
            skills=[
                {"skill_id": "office", "keywords": ["Microsoft Office"]},
            ],
        )
        results = evaluate_writing({}, {}, curation)
        m = find_metric(results, "no_trivial_skills")
        assert m.status == EvalMetricStatus.FAIL

    def test_no_soft_skills_pass(self) -> None:
        curation = _make_curation(
            skills=[
                {"skill_id": "cloud-aws", "keywords": ["EKS", "Lambda"]},
            ],
        )
        results = evaluate_writing({}, {}, curation)
        m = find_metric(results, "no_soft_skills_listed")
        assert m.status == EvalMetricStatus.PASS

    def test_soft_skill_detected_fail(self) -> None:
        curation = _make_curation(
            skills=[
                {"skill_id": "soft", "keywords": ["Communication"]},
            ],
        )
        results = evaluate_writing({}, {}, curation)
        m = find_metric(results, "no_soft_skills_listed")
        assert m.status == EvalMetricStatus.FAIL


# ---------------------------------------------------------------------------
# Return structure
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestReturnStructure:
    def test_all_metrics_present(self) -> None:
        curation = _make_curation()
        results = evaluate_writing(
            _make_section_data(),
            {},
            curation,
        )
        names = {r.name for r in results}
        expected = {
            "quantification_rate",
            "weak_phrase_count",
            "ai_red_flag_count",
            "first_person_count",
            "third_person_count",
            "no_periods_on_bullets",
            "references_available_phrase",
            "placeholder_text_count",
            "action_verb_start_rate",
            "action_verb_diversity_per_entry",
            "action_verb_diversity_global",
            "summary_sentence_count",
            "summary_has_years_experience",
            "summary_has_title",
            "no_trivial_skills",
            "no_soft_skills_listed",
        }
        assert names == expected

    def test_all_category_is_writing_quality(self) -> None:
        curation = _make_curation()
        results = evaluate_writing({}, {}, curation)
        for r in results:
            assert r.category == "writing_quality"
