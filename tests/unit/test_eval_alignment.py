"""Tests for curator.eval.alignment."""

from __future__ import annotations

from typing import Any

import pytest

from curator.eval.alignment import evaluate_alignment, extract_keywords
from curator.eval.report import EvalMetricStatus
from curator.models import (
    Basics,
    PortfolioData,
    ResumeCuration,
    SkillEntry,
    WorkEntry,
)
from tests.helpers import find_metric


def _make_curation(**overrides: Any) -> ResumeCuration:
    from tests.helpers import make_curation_dict

    return ResumeCuration.model_validate(make_curation_dict(**overrides))


def _make_portfolio(
    skill_keywords: list[str] | None = None,
    work_technologies: list[str] | None = None,
) -> PortfolioData:
    skills = [
        SkillEntry(
            id="cloud-aws",
            name="AWS",
            keywords=skill_keywords or ["EKS", "Lambda", "S3"],
        ),
    ]
    work = [
        WorkEntry(
            id="acme-senior-engineer",
            name="Acme Corp",
            position="Senior Engineer",
            start_date="2023-06",
            technologies=work_technologies or ["Kubernetes", "Docker", "Terraform"],
        ),
    ]
    return PortfolioData(
        basics=Basics(name="Jane Doe"),
        work=work,
        education=[],
        skills=skills,
        certificates=[],
        projects=[],
        volunteer=[],
        publications=[],
        languages=[],
        interests=None,
        services=[],
    )


# ---------------------------------------------------------------------------
# extract_keywords
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestExtractKeywords:
    def test_empty_string(self) -> None:
        assert extract_keywords("") == set()

    def test_whitespace_only(self) -> None:
        assert extract_keywords("   ") == set()

    def test_compound_term_ci_cd(self) -> None:
        keywords = extract_keywords("CI/CD pipeline experience")
        assert "ci/cd" in keywords
        assert "ci" in keywords
        assert "cd" in keywords

    def test_case_normalization(self) -> None:
        keywords = extract_keywords("Kubernetes Docker AWS")
        assert "kubernetes" in keywords
        assert "docker" in keywords
        assert "aws" in keywords

    def test_stopword_removal(self) -> None:
        keywords = extract_keywords("the and or but in on at to for of with")
        assert len(keywords) == 0

    def test_slash_separated_terms(self) -> None:
        keywords = extract_keywords("DevOps/SRE role")
        assert "devops/sre" in keywords
        assert "devops" in keywords
        assert "sre" in keywords

    def test_single_char_tokens_excluded(self) -> None:
        keywords = extract_keywords("a b c Kubernetes")
        assert "a" not in keywords
        assert "b" not in keywords
        assert "kubernetes" in keywords

    def test_generates_bigrams(self) -> None:
        keywords = extract_keywords("site reliability engineering")
        assert "site reliability" in keywords
        assert "reliability engineering" in keywords
        # 3-grams removed to reduce keyword count inflation.
        assert "site reliability engineering" not in keywords

    def test_hyphenated_terms(self) -> None:
        keywords = extract_keywords("real-time processing")
        assert "real-time" in keywords


# ---------------------------------------------------------------------------
# keyword_coverage (portfolio-dependent)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestKeywordCoverage:
    def test_with_portfolio_and_matches_pass(self) -> None:
        jd = "Looking for Kubernetes and Docker and EKS experience"
        section_data: dict[str, Any] = {
            "skills": [
                {"name": "AWS", "keywords": ["EKS"]},
            ],
            "work": [
                {
                    "id": "acme",
                    "technologies": ["Kubernetes", "Docker"],
                    "highlights": [],
                },
            ],
        }
        basics: dict[str, Any] = {"summary": "Kubernetes and Docker expert"}
        portfolio = _make_portfolio()
        curation = _make_curation()
        results = evaluate_alignment(jd, section_data, basics, curation, portfolio)
        m = find_metric(results, "keyword_coverage")
        assert m.status in {EvalMetricStatus.PASS, EvalMetricStatus.WARN}

    def test_without_portfolio_returns_warn(self) -> None:
        jd = "Kubernetes experience required"
        curation = _make_curation()
        results = evaluate_alignment(jd, {}, {}, curation, None)
        m = find_metric(results, "keyword_coverage")
        assert m.status == EvalMetricStatus.WARN
        assert m.value is None


# ---------------------------------------------------------------------------
# keyword_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestKeywordCount:
    def test_with_overlapping_keywords(self) -> None:
        jd = "Kubernetes Docker Terraform AWS EKS Lambda S3"
        section_data: dict[str, Any] = {
            "skills": [
                {"name": "Cloud", "keywords": ["EKS", "Lambda", "S3"]},
            ],
            "work": [
                {
                    "id": "acme",
                    "technologies": ["Kubernetes", "Docker", "Terraform"],
                    "highlights": [],
                },
            ],
        }
        curation = _make_curation()
        results = evaluate_alignment(jd, section_data, {}, curation)
        m = find_metric(results, "keyword_count")
        assert m.value >= 0
        assert m.status in {
            EvalMetricStatus.PASS,
            EvalMetricStatus.WARN,
            EvalMetricStatus.FAIL,
        }


# ---------------------------------------------------------------------------
# keyword_distribution
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestKeywordDistribution:
    def test_distributed_keywords_pass(self) -> None:
        jd = "Kubernetes Kubernetes Kubernetes Docker Docker Docker"
        section_data: dict[str, Any] = {
            "skills": [{"name": "Cloud", "keywords": ["Kubernetes"]}],
            "work": [
                {
                    "id": "acme",
                    "position": "Kubernetes Engineer",
                    "technologies": ["Docker"],
                    "highlights": [
                        {"text": "Deployed Kubernetes clusters"},
                    ],
                },
            ],
        }
        basics: dict[str, Any] = {
            "summary": "Expert in Kubernetes and Docker container orchestration",
            "label": "DevOps Engineer",
        }
        curation = _make_curation()
        results = evaluate_alignment(jd, section_data, basics, curation)
        m = find_metric(results, "keyword_distribution")
        assert m.value >= 0


# ---------------------------------------------------------------------------
# job_title_present
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestJobTitlePresent:
    def test_exact_match_pass(self) -> None:
        jd = "We are hiring a Senior DevOps Engineer to join our team."
        curation = _make_curation(suggested_label="Senior DevOps Engineer")
        results = evaluate_alignment(jd, {}, {}, curation)
        m = find_metric(results, "job_title_present")
        assert m.status == EvalMetricStatus.PASS

    def test_bigram_match_pass(self) -> None:
        jd = "Looking for a DevOps Engineer with Kubernetes experience."
        curation = _make_curation(suggested_label="Senior DevOps Engineer")
        results = evaluate_alignment(jd, {}, {}, curation)
        m = find_metric(results, "job_title_present")
        assert m.status == EvalMetricStatus.PASS

    def test_no_match_warn(self) -> None:
        jd = "Looking for a backend developer."
        curation = _make_curation(suggested_label="Senior DevOps Engineer")
        results = evaluate_alignment(jd, {}, {}, curation)
        m = find_metric(results, "job_title_present")
        assert m.status == EvalMetricStatus.WARN


# ---------------------------------------------------------------------------
# acronym_expansion_pairs
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestAcronymExpansionPairs:
    def test_acronym_present_in_resume_pass(self) -> None:
        jd = "CI/CD pipeline experience required. AWS knowledge preferred."
        section_data: dict[str, Any] = {
            "skills": [{"name": "CI/CD", "keywords": ["Jenkins"]}],
            "work": [
                {
                    "id": "acme",
                    "position": "Engineer",
                    "technologies": ["AWS"],
                    "highlights": [
                        {"text": "Built CI/CD pipelines"},
                    ],
                },
            ],
        }
        curation = _make_curation()
        results = evaluate_alignment(jd, section_data, {}, curation)
        m = find_metric(results, "acronym_expansion_pairs")
        # If acronyms found in resume, should pass or warn (not fail).
        assert m.status in {EvalMetricStatus.PASS, EvalMetricStatus.WARN}


# ---------------------------------------------------------------------------
# jd_match_rate (portfolio-dependent)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestJDMatchRate:
    def test_without_portfolio_returns_warn(self) -> None:
        # No-portfolio path is the one remaining WARN: it signals the audit
        # input is incomplete, not a portfolio-fit gap. Kept distinct from
        # the uniform-PASS rate path.
        jd = "Kubernetes experience required"
        curation = _make_curation()
        results = evaluate_alignment(jd, {}, {}, curation, None)
        m = find_metric(results, "jd_match_rate")
        assert m.status == EvalMetricStatus.WARN
        assert m.value is None

    def test_with_matching_portfolio(self) -> None:
        # Status is uniformly PASS post-2026-04-27: the metric is
        # informational and the value carries the signal. Empirical
        # 0-5% match rates across all 10 Phase-1 JDs made the prior
        # 15%/8% bands FAIL noise rather than actionable signal.
        jd = "Kubernetes Docker EKS Lambda"
        portfolio = _make_portfolio()
        curation = _make_curation()
        results = evaluate_alignment(jd, {}, {}, curation, portfolio)
        m = find_metric(results, "jd_match_rate")
        assert m.status == EvalMetricStatus.PASS
        assert m.value is not None

    # The synthetic JD below produces 15 keywords (8 unigrams + 7 bigrams);
    # picking non-adjacent unigrams in the portfolio avoids bigram matches.
    # Status is uniformly PASS regardless of rate; tests assert the
    # numeric value continues to reflect coverage accurately.
    _BOUNDARY_JD = "alphax bravox charliex deltax echox foxtrotx golfx hotelx"

    def test_match_rate_high_coverage_value(self) -> None:
        # 3 of 15 keywords match -> 0.20.
        portfolio = _make_portfolio(skill_keywords=["alphax", "charliex", "echox"])
        curation = _make_curation()
        results = evaluate_alignment(self._BOUNDARY_JD, {}, {}, curation, portfolio)
        m = find_metric(results, "jd_match_rate")
        assert m.status == EvalMetricStatus.PASS
        assert m.value is not None
        assert m.value >= 0.15

    def test_match_rate_mid_coverage_value(self) -> None:
        # 2 of 15 keywords match -> 0.13.
        portfolio = _make_portfolio(skill_keywords=["alphax", "charliex"])
        curation = _make_curation()
        results = evaluate_alignment(self._BOUNDARY_JD, {}, {}, curation, portfolio)
        m = find_metric(results, "jd_match_rate")
        assert m.status == EvalMetricStatus.PASS
        assert m.value is not None
        assert 0.08 <= m.value < 0.15

    def test_match_rate_low_coverage_value(self) -> None:
        # 1 of 15 keywords match -> 0.067; status still PASS, value is
        # the signal. Mirrors empirical 0-5% Phase-1 results.
        portfolio = _make_portfolio(skill_keywords=["alphax"])
        curation = _make_curation()
        results = evaluate_alignment(self._BOUNDARY_JD, {}, {}, curation, portfolio)
        m = find_metric(results, "jd_match_rate")
        assert m.status == EvalMetricStatus.PASS
        assert m.value is not None
        assert m.value < 0.08

    def test_no_jd_keywords_with_portfolio_passes(self) -> None:
        """TE-6: empty/stopword-only JD with a portfolio present.
        Post-2026-04-27 this branch emits PASS (was WARN) and the
        detail carries the 'informational, portfolio-JD fit signal'
        framing. Pins the status flip and the rewritten detail
        string."""
        portfolio = _make_portfolio()
        curation = _make_curation()
        # All-stopword JD yields no extractable keywords.
        results = evaluate_alignment(
            "the and or but in on at to for of with",
            {},
            {},
            curation,
            portfolio,
        )
        m = find_metric(results, "jd_match_rate")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 0.0
        assert "informational" in m.detail
        assert "portfolio-JD fit signal" in m.detail


# ---------------------------------------------------------------------------
# Return structure
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestReturnStructure:
    def test_all_metrics_present(self) -> None:
        jd = "Senior DevOps Engineer with Kubernetes experience"
        curation = _make_curation()
        results = evaluate_alignment(jd, {}, {}, curation)
        names = {r.name for r in results}
        expected = {
            "keyword_coverage",
            "keyword_count",
            "keyword_distribution",
            "job_title_present",
            "acronym_expansion_pairs",
            "jd_match_rate",
        }
        assert names == expected

    def test_all_category_is_jd_alignment(self) -> None:
        jd = "DevOps engineer role"
        curation = _make_curation()
        results = evaluate_alignment(jd, {}, {}, curation)
        for r in results:
            assert r.category == "jd_alignment"

    def test_portfolio_fit_metrics_are_informational(self) -> None:
        # jd_match_rate and acronym_expansion_pairs measure portfolio-JD
        # fit, not curation quality. They MUST carry informational=True
        # so they surface as visible signals without dragging the
        # category score (score_category excludes informational metrics).
        jd = "Senior DevOps Engineer with Kubernetes, SRE, and IAM needs"
        curation = _make_curation()
        results = evaluate_alignment(jd, {}, {}, curation)
        by_name = {r.name: r for r in results}
        assert by_name["jd_match_rate"].informational is True
        assert by_name["acronym_expansion_pairs"].informational is True
        # Scoring metrics retain their default (non-informational) state.
        assert by_name["keyword_count"].informational is False
        assert by_name["keyword_coverage"].informational is False
