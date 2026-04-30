"""Tests for curator.eval.report."""

from __future__ import annotations

import pytest

from curator.eval.report import (
    EVAL_SCHEMA_VERSION,
    PORTFOLIO_FIT_METRIC_NAMES,
    CategoryScore,
    EvalMetricResult,
    EvalMetricStatus,
    EvalReport,
    build_portfolio_fit_report,
    build_report,
    score_category,
    status_from_score,
)

# ---------------------------------------------------------------------------
# EvalMetricStatus
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestEvalMetricStatus:
    def test_int_values(self) -> None:
        assert int(EvalMetricStatus.FAIL) == 0
        assert int(EvalMetricStatus.WARN) == 1
        assert int(EvalMetricStatus.PASS) == 2

    def test_ordering(self) -> None:
        assert EvalMetricStatus.FAIL < EvalMetricStatus.WARN < EvalMetricStatus.PASS

    def test_arithmetic(self) -> None:
        total = EvalMetricStatus.PASS + EvalMetricStatus.FAIL
        assert total == 2


# ---------------------------------------------------------------------------
# EvalMetricResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestEvalMetricResult:
    def test_construction(self) -> None:
        r = EvalMetricResult(
            name="word_count",
            category="content_density",
            status=EvalMetricStatus.PASS,
            value=500,
            detail="500 words",
        )
        assert r.name == "word_count"
        assert r.category == "content_density"
        assert r.status == EvalMetricStatus.PASS
        assert r.value == 500
        assert r.detail == "500 words"
        assert r.weight == 1.0

    def test_default_detail_and_weight(self) -> None:
        r = EvalMetricResult(
            name="test",
            category="cat",
            status=EvalMetricStatus.WARN,
            value=None,
        )
        assert r.detail == ""
        assert r.weight == 1.0

    def test_frozen(self) -> None:
        r = EvalMetricResult(
            name="test",
            category="cat",
            status=EvalMetricStatus.PASS,
            value=1,
        )
        with pytest.raises(AttributeError):
            r.name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# status_from_score — boundary tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestStatusFromScore:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (100, EvalMetricStatus.PASS),
            (85, EvalMetricStatus.PASS),
            (84, EvalMetricStatus.WARN),
            (75, EvalMetricStatus.WARN),
            (74, EvalMetricStatus.FAIL),
            (0, EvalMetricStatus.FAIL),
            (85.0, EvalMetricStatus.PASS),
            (84.99, EvalMetricStatus.WARN),
            (75.0, EvalMetricStatus.WARN),
            (74.99, EvalMetricStatus.FAIL),
        ],
    )
    def test_boundary(self, score: float, expected: EvalMetricStatus) -> None:
        assert status_from_score(score) == expected


# ---------------------------------------------------------------------------
# score_category
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestScoreCategory:
    def test_empty_metrics_returns_100(self) -> None:
        assert score_category([]) == 100.0

    def test_all_pass_returns_100(self) -> None:
        metrics = [
            EvalMetricResult(
                name=f"m{i}",
                category="cat",
                status=EvalMetricStatus.PASS,
                value=None,
            )
            for i in range(3)
        ]
        assert score_category(metrics) == 100.0

    def test_all_fail_returns_0(self) -> None:
        metrics = [
            EvalMetricResult(
                name=f"m{i}",
                category="cat",
                status=EvalMetricStatus.FAIL,
                value=None,
            )
            for i in range(3)
        ]
        assert score_category(metrics) == 0.0

    def test_all_warn_returns_50(self) -> None:
        metrics = [
            EvalMetricResult(
                name=f"m{i}",
                category="cat",
                status=EvalMetricStatus.WARN,
                value=None,
            )
            for i in range(3)
        ]
        assert score_category(metrics) == 50.0

    def test_mixed_statuses(self) -> None:
        metrics = [
            EvalMetricResult(
                name="pass",
                category="cat",
                status=EvalMetricStatus.PASS,
                value=None,
            ),
            EvalMetricResult(
                name="fail",
                category="cat",
                status=EvalMetricStatus.FAIL,
                value=None,
            ),
        ]
        # (2*1 + 0*1) / (2 * 2) * 100 = 50.0
        assert score_category(metrics) == 50.0

    def test_weighted_metrics(self) -> None:
        metrics = [
            EvalMetricResult(
                name="heavy",
                category="cat",
                status=EvalMetricStatus.PASS,
                value=None,
                weight=3.0,
            ),
            EvalMetricResult(
                name="light",
                category="cat",
                status=EvalMetricStatus.FAIL,
                value=None,
                weight=1.0,
            ),
        ]
        # (2*3 + 0*1) / (4 * 2) * 100 = 75.0
        assert score_category(metrics) == 75.0

    def test_zero_weight_returns_100(self) -> None:
        metrics = [
            EvalMetricResult(
                name="zero",
                category="cat",
                status=EvalMetricStatus.FAIL,
                value=None,
                weight=0.0,
            ),
        ]
        assert score_category(metrics) == 100.0


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestBuildReport:
    def test_groups_by_category(self) -> None:
        metrics = [
            EvalMetricResult(
                name="m1",
                category="cat_a",
                status=EvalMetricStatus.PASS,
                value=None,
            ),
            EvalMetricResult(
                name="m2",
                category="cat_b",
                status=EvalMetricStatus.PASS,
                value=None,
            ),
            EvalMetricResult(
                name="m3",
                category="cat_a",
                status=EvalMetricStatus.PASS,
                value=None,
            ),
        ]
        report = build_report(metrics)
        assert len(report.categories) == 2
        cat_names = {c.name for c in report.categories}
        assert cat_names == {"cat_a", "cat_b"}

    def test_all_pass_aggregate_100(self) -> None:
        metrics = [
            EvalMetricResult(
                name="m1",
                category="content_density",
                status=EvalMetricStatus.PASS,
                value=None,
            ),
        ]
        report = build_report(metrics)
        assert report.aggregate_score == 100.0
        assert report.status == EvalMetricStatus.PASS

    def test_uses_custom_category_weights(self) -> None:
        metrics = [
            EvalMetricResult(
                name="m1",
                category="alpha",
                status=EvalMetricStatus.PASS,
                value=None,
            ),
            EvalMetricResult(
                name="m2",
                category="beta",
                status=EvalMetricStatus.FAIL,
                value=None,
            ),
        ]
        weights = {"alpha": 0.8, "beta": 0.2}
        report = build_report(metrics, category_weights=weights)
        # alpha=100*0.8 + beta=0*0.2 => 80/1.0 = 80.0
        assert report.aggregate_score == 80.0

    def test_schema_version(self) -> None:
        report = build_report([])
        assert report.eval_schema_version == EVAL_SCHEMA_VERSION

    def test_empty_metrics(self) -> None:
        report = build_report([])
        assert report.metrics == []
        assert report.categories == []
        assert report.aggregate_score == 0.0

    def test_unknown_category_gets_zero_weight(self) -> None:
        metrics = [
            EvalMetricResult(
                name="m1",
                category="unknown_cat",
                status=EvalMetricStatus.FAIL,
                value=None,
            ),
        ]
        report = build_report(metrics)
        # Weight for unknown category is 0.0 => aggregate = 0.0
        assert report.aggregate_score == 0.0

    def test_report_is_frozen(self) -> None:
        report = build_report([])
        with pytest.raises(AttributeError):
            report.aggregate_score = 50.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EvalReport / CategoryScore frozen
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestFrozenDataclasses:
    def test_category_score_frozen(self) -> None:
        cs = CategoryScore(
            name="test",
            score=80.0,
            status=EvalMetricStatus.WARN,
            weight=0.1,
        )
        with pytest.raises(AttributeError):
            cs.score = 90.0  # type: ignore[misc]

    def test_eval_report_frozen(self) -> None:
        report = EvalReport(
            metrics=[],
            categories=[],
            aggregate_score=50.0,
            status=EvalMetricStatus.WARN,
        )
        with pytest.raises(AttributeError):
            report.status = EvalMetricStatus.PASS  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.eval
class TestEvalReportToDict:
    def test_basic_structure(self) -> None:
        metric = EvalMetricResult(
            name="word_count",
            category="content_density",
            status=EvalMetricStatus.PASS,
            value=500,
            detail="500 words",
        )
        report = build_report([metric])
        d = report.to_dict()
        assert d["eval_schema_version"] == EVAL_SCHEMA_VERSION
        assert isinstance(d["aggregate_score"], float)
        assert d["status"] == "PASS"
        assert len(d["categories"]) == 1
        assert len(d["metrics"]) == 1

    def test_status_serialized_as_names(self) -> None:
        metrics = [
            EvalMetricResult(
                name="m1",
                category="cat1",
                status=EvalMetricStatus.PASS,
                value=1,
            ),
            EvalMetricResult(
                name="m2",
                category="cat1",
                status=EvalMetricStatus.FAIL,
                value=0,
            ),
        ]
        report = build_report(metrics)
        d = report.to_dict()
        statuses = {m["status"] for m in d["metrics"]}
        assert statuses == {"PASS", "FAIL"}
        assert d["categories"][0]["status"] in {"PASS", "WARN", "FAIL"}

    def test_json_serializable(self) -> None:
        import json

        metric = EvalMetricResult(
            name="test",
            category="cat",
            status=EvalMetricStatus.WARN,
            value=42.5,
            detail="test detail",
            weight=2.0,
        )
        report = build_report([metric])
        # Should not raise.
        serialized = json.dumps(report.to_dict())
        deserialized = json.loads(serialized)
        assert deserialized["metrics"][0]["value"] == 42.5
        assert deserialized["metrics"][0]["weight"] == 2.0

    def test_metric_fields_complete(self) -> None:
        metric = EvalMetricResult(
            name="n",
            category="c",
            status=EvalMetricStatus.PASS,
            value=True,
            detail="d",
            weight=0.5,
        )
        report = build_report([metric])
        m = report.to_dict()["metrics"][0]
        expected_keys = {
            "name",
            "category",
            "status",
            "value",
            "detail",
            "weight",
            "informational",
        }
        assert set(m.keys()) == expected_keys


# ---------------------------------------------------------------------------
# PortfolioFitReport
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestPortfolioFitReport:
    def test_informational_metrics_excluded_from_category_aggregate(self) -> None:
        # Scored PASS + informational FAIL → category 100 (informational
        # excluded); portfolio_fit picks up the FAIL signal.
        # Uses acronym_expansion_pairs (which still produces real
        # PASS/WARN/FAIL bands in production) rather than jd_match_rate
        # (which uniformly emits PASS post-2026-04-27); the synthetic
        # FAIL stays aligned with a state production can actually emit.
        metrics = [
            EvalMetricResult(
                name="keyword_count",
                category="jd_alignment",
                status=EvalMetricStatus.PASS,
                value=20,
            ),
            EvalMetricResult(
                name="acronym_expansion_pairs",
                category="jd_alignment",
                status=EvalMetricStatus.FAIL,
                value=4,
                informational=True,
            ),
        ]
        report = build_report(metrics)
        assert report.categories[0].score == 100.0
        assert report.portfolio_fit is not None
        # acronym_expansion_pairs is FAIL, score 0/2 → 0.0.
        assert report.portfolio_fit.aggregate_score == 0.0
        assert report.portfolio_fit.status == EvalMetricStatus.FAIL
        assert [m.name for m in report.portfolio_fit.metrics] == [
            "acronym_expansion_pairs"
        ]

    def test_informational_stub_not_in_portfolio_fit(self) -> None:
        # font_embedding_valid is informational but NOT a portfolio-fit
        # signal — it must not land in PortfolioFitReport.
        metrics = [
            EvalMetricResult(
                name="font_embedding_valid",
                category="pdf_output",
                status=EvalMetricStatus.PASS,
                value=True,
                informational=True,
            ),
        ]
        pf = build_portfolio_fit_report(metrics)
        assert pf.metrics == []
        assert pf.aggregate_score == 100.0

    def test_portfolio_fit_metric_names_pinned(self) -> None:
        assert (
            frozenset({"jd_match_rate", "acronym_expansion_pairs"})
            == PORTFOLIO_FIT_METRIC_NAMES
        )

    def test_to_dict_emits_portfolio_fit(self) -> None:
        # acronym_expansion_pairs stands in for the WARN signal (real
        # production state); jd_match_rate uniformly emits PASS
        # post-2026-04-27 and would not represent a WARN aggregate.
        metrics = [
            EvalMetricResult(
                name="keyword_count",
                category="jd_alignment",
                status=EvalMetricStatus.PASS,
                value=20,
            ),
            EvalMetricResult(
                name="acronym_expansion_pairs",
                category="jd_alignment",
                status=EvalMetricStatus.WARN,
                value=2,
                informational=True,
            ),
        ]
        report = build_report(metrics)
        d = report.to_dict()
        assert "portfolio_fit" in d
        # Single WARN metric: weighted_sum=1, max_possible=2 → 50.0.
        # Under the sidecar's looser thresholds (PASS=100, WARN=50) this
        # maps to WARN. CR-9 (2026-04-26): rebalanced from FAIL because
        # 1-of-2 WARN is real signal but not a stamp-everything-FAIL case.
        assert d["portfolio_fit"]["aggregate_score"] == 50.0
        assert d["portfolio_fit"]["status"] == "WARN"
        assert d["portfolio_fit"]["metrics"][0]["name"] == "acronym_expansion_pairs"

    def test_to_dict_portfolio_fit_none_when_no_signals(self) -> None:
        # A report with no informational portfolio-fit metrics still
        # emits a PortfolioFitReport (default 100 PASS), so the key is
        # always present.
        metrics = [
            EvalMetricResult(
                name="keyword_count",
                category="jd_alignment",
                status=EvalMetricStatus.PASS,
                value=20,
            ),
        ]
        report = build_report(metrics)
        d = report.to_dict()
        assert d["portfolio_fit"] is not None
        assert d["portfolio_fit"]["metrics"] == []
        assert d["portfolio_fit"]["aggregate_score"] == 100.0
