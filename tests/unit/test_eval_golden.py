"""Tests for curator.eval.golden."""

from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from curator.eval import from_golden_case
from curator.eval.golden import (
    GOLDEN_SKIP_METRICS,
    BaselineRange,
    GoldenCase,
    GoldenComparisonResult,
    GoldenExpected,
    GoldenMeta,
    RegressionCategory,
    RegressionFinding,
    RegressionSeverity,
    compare_against_golden,
    discover_golden_cases,
    load_golden_case,
    materialize_profile,
    render_golden_pdf,
)
from curator.eval.report import (
    EVAL_SCHEMA_VERSION,
    EvalMetricResult,
    EvalMetricStatus,
    EvalReport,
    build_report,
)
from curator.exceptions import CuratorError, EvalError
from curator.io_utils import compile_typst

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_curation_dict(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid ResumeCuration dict."""
    from tests.helpers import make_curation_dict

    return make_curation_dict(**overrides)


def _minimal_golden_dict(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid GoldenCase dict."""
    base: dict[str, Any] = {
        "meta": {
            "id": "test-case-01",
            "name": "Test Case",
            "eval_schema_version": EVAL_SCHEMA_VERSION,
            "tier": "good",
        },
        "job_description": "We are hiring a Senior DevOps Engineer.",
        "curation": _minimal_curation_dict(),
        "section_data": {},
        "basics": {"name": "Jane Doe"},
    }
    base.update(overrides)
    return base


def _make_golden(**overrides: Any) -> GoldenCase:
    """Construct a validated GoldenCase."""
    return GoldenCase.model_validate(_minimal_golden_dict(**overrides))


def _make_report(
    metrics: list[EvalMetricResult] | None = None,
    *,
    schema_version: int = EVAL_SCHEMA_VERSION,
) -> EvalReport:
    """Build an EvalReport with optional overrides."""
    if metrics is None:
        metrics = [
            EvalMetricResult(
                name="word_count",
                category="content_density",
                status=EvalMetricStatus.PASS,
                value=500,
            ),
        ]
    report = build_report(metrics)
    # Rebuild with custom schema version if needed.
    if schema_version != EVAL_SCHEMA_VERSION:
        return EvalReport(
            metrics=report.metrics,
            categories=report.categories,
            aggregate_score=report.aggregate_score,
            status=report.status,
            eval_schema_version=schema_version,
        )
    return report


def _write_golden_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write a golden case dict as a YAML file."""
    path.write_text(
        yaml.dump(data, Dumper=yaml.SafeDumper, default_flow_style=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# GoldenMeta
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestGoldenMeta:
    def test_construction(self) -> None:
        meta = GoldenMeta(
            id="test-case", name="Test", eval_schema_version=1, tier="good"
        )
        assert meta.id == "test-case"
        assert meta.name == "Test"
        assert meta.eval_schema_version == 1

    def test_frozen(self) -> None:
        meta = GoldenMeta(
            id="test-case", name="Test", eval_schema_version=1, tier="good"
        )
        with pytest.raises(ValidationError):
            meta.id = "changed"

    def test_extra_ignore(self) -> None:
        meta = GoldenMeta(
            id="test-case",
            name="Test",
            eval_schema_version=1,
            tier="good",
            unknown_field="ignored",  # type: ignore[call-arg]
        )
        assert not hasattr(meta, "unknown_field")

    def test_id_pattern_valid_kebab(self) -> None:
        meta = GoldenMeta(
            id="abc-123-def", name="Test", eval_schema_version=1, tier="good"
        )
        assert meta.id == "abc-123-def"

    def test_id_pattern_valid_single_char(self) -> None:
        meta = GoldenMeta(id="a", name="Test", eval_schema_version=1, tier="good")
        assert meta.id == "a"

    def test_id_pattern_rejects_uppercase(self) -> None:
        with pytest.raises(ValidationError):
            GoldenMeta(id="Test-Case", name="Test", eval_schema_version=1, tier="good")

    def test_id_pattern_rejects_leading_hyphen(self) -> None:
        with pytest.raises(ValidationError):
            GoldenMeta(id="-test", name="Test", eval_schema_version=1, tier="good")

    def test_id_pattern_rejects_spaces(self) -> None:
        with pytest.raises(ValidationError):
            GoldenMeta(id="test case", name="Test", eval_schema_version=1, tier="good")


# ---------------------------------------------------------------------------
# BaselineRange
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestBaselineRange:
    def test_construction_both_bounds(self) -> None:
        br = BaselineRange(min=0.5, max=1.0)
        assert br.min == 0.5
        assert br.max == 1.0

    def test_construction_min_only(self) -> None:
        br = BaselineRange(min=0.5)
        assert br.min == 0.5
        assert br.max is None

    def test_construction_max_only(self) -> None:
        br = BaselineRange(max=1.0)
        assert br.min is None
        assert br.max == 1.0

    def test_construction_no_bounds(self) -> None:
        br = BaselineRange()
        assert br.min is None
        assert br.max is None

    def test_equal_min_max_valid(self) -> None:
        br = BaselineRange(min=0.75, max=0.75)
        assert br.min == br.max == 0.75

    def test_min_greater_than_max_raises(self) -> None:
        with pytest.raises(ValueError, match=r"min.*must be <= max"):
            BaselineRange(min=1.0, max=0.5)

    def test_frozen(self) -> None:
        br = BaselineRange(min=0.5, max=1.0)
        with pytest.raises(ValidationError):
            br.min = 0.0

    def test_extra_ignore(self) -> None:
        br = BaselineRange(min=0.5, extra_field="ignored")  # type: ignore[call-arg]
        assert not hasattr(br, "extra_field")


# ---------------------------------------------------------------------------
# GoldenExpected
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestGoldenExpected:
    def test_defaults(self) -> None:
        expected = GoldenExpected()
        assert expected.must_include == {}
        assert expected.must_exclude == {}

    def test_construction_with_values(self) -> None:
        expected = GoldenExpected(
            must_include={"work": ["job-1"]},
            must_exclude={"skills": ["old-skill"]},
        )
        assert expected.must_include == {"work": ["job-1"]}
        assert expected.must_exclude == {"skills": ["old-skill"]}

    def test_frozen(self) -> None:
        expected = GoldenExpected()
        with pytest.raises(ValidationError):
            expected.must_include = {"work": ["job"]}

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            GoldenExpected(
                bonus_field="ignored",  # type: ignore[call-arg]
            )


# ---------------------------------------------------------------------------
# GoldenCase
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestGoldenCase:
    def test_construction(self) -> None:
        golden = _make_golden()
        assert golden.meta.id == "test-case-01"
        assert golden.job_description == "We are hiring a Senior DevOps Engineer."
        assert golden.curation["company_slug"] == "test-co"
        assert golden.basics == {"name": "Jane Doe"}

    def test_defaults(self) -> None:
        golden = _make_golden()
        assert golden.expected.must_include == {}
        assert golden.expected.must_exclude == {}
        assert golden.baselines == {}
        assert golden.human_scores == {}

    def test_human_scores_defaults_to_empty(self) -> None:
        data = _minimal_golden_dict()
        assert "human_scores" not in data
        golden = GoldenCase.model_validate(data)
        assert golden.human_scores == {}

    def test_frozen(self) -> None:
        golden = _make_golden()
        with pytest.raises(ValidationError):
            golden.job_description = "changed"

    def test_extra_ignore(self) -> None:
        data = _minimal_golden_dict()
        data["unknown_top_level"] = "should be ignored"
        golden = GoldenCase.model_validate(data)
        assert not hasattr(golden, "unknown_top_level")

    def test_with_baselines(self) -> None:
        golden = _make_golden(
            baselines={
                "aggregate_score": {"min": 70.0, "max": 100.0},
                "word_count": {"min": 400.0},
            },
        )
        assert golden.baselines["aggregate_score"].min == 70.0
        assert golden.baselines["word_count"].max is None

    def test_with_expected(self) -> None:
        golden = _make_golden(
            expected={
                "must_include": {"work": ["acme-senior-engineer"]},
                "must_exclude": {"projects": ["old-project"]},
            },
        )
        assert golden.expected.must_include == {"work": ["acme-senior-engineer"]}

    def test_with_human_scores(self) -> None:
        golden = _make_golden(human_scores={"overall": 4.5, "relevance": 3.8})
        assert golden.human_scores == {"overall": 4.5, "relevance": 3.8}


# ---------------------------------------------------------------------------
# RegressionSeverity / RegressionCategory
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestRegressionEnums:
    def test_severity_values(self) -> None:
        assert RegressionSeverity.ERROR.value == "error"
        assert RegressionSeverity.WARNING.value == "warning"

    def test_category_values(self) -> None:
        assert RegressionCategory.SCORE_DROP.value == "score_drop"
        assert RegressionCategory.STATUS_FLIP.value == "status_flip"
        assert RegressionCategory.MISSING_ENTRY.value == "missing_entry"
        assert RegressionCategory.EXCLUDED_PRESENT.value == "excluded_present"
        assert RegressionCategory.BASELINE_VIOLATION.value == "baseline_violation"
        assert RegressionCategory.SCHEMA_MISMATCH.value == "schema_mismatch"
        assert RegressionCategory.METRIC_COUNT_MISMATCH.value == "metric_count_mismatch"

    def test_category_count(self) -> None:
        assert len(RegressionCategory) == 7


# ---------------------------------------------------------------------------
# RegressionFinding
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestRegressionFinding:
    def test_construction(self) -> None:
        finding = RegressionFinding(
            severity=RegressionSeverity.ERROR,
            category=RegressionCategory.SCORE_DROP,
            message="Score dropped below threshold",
        )
        assert finding.severity == RegressionSeverity.ERROR
        assert finding.category == RegressionCategory.SCORE_DROP
        assert finding.message == "Score dropped below threshold"

    def test_frozen(self) -> None:
        finding = RegressionFinding(
            severity=RegressionSeverity.WARNING,
            category=RegressionCategory.BASELINE_VIOLATION,
            message="test",
        )
        with pytest.raises(FrozenInstanceError):
            finding.message = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# GoldenComparisonResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestGoldenComparisonResult:
    def test_construction(self) -> None:
        report = _make_report()
        result = GoldenComparisonResult(
            case_id="test-case-01",
            passed=True,
            report=report,
        )
        assert result.case_id == "test-case-01"
        assert result.passed is True
        assert result.findings == []

    def test_with_findings(self) -> None:
        report = _make_report()
        findings = [
            RegressionFinding(
                severity=RegressionSeverity.WARNING,
                category=RegressionCategory.SCHEMA_MISMATCH,
                message="version mismatch",
            ),
        ]
        result = GoldenComparisonResult(
            case_id="test-case-01",
            passed=True,
            report=report,
            findings=findings,
        )
        assert len(result.findings) == 1

    def test_frozen(self) -> None:
        report = _make_report()
        result = GoldenComparisonResult(
            case_id="test-case-01",
            passed=True,
            report=report,
        )
        with pytest.raises(FrozenInstanceError):
            result.passed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# load_golden_case
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestLoadGoldenCase:
    def test_valid_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "case.yaml"
        _write_golden_yaml(path, _minimal_golden_dict())
        case = load_golden_case(path)
        assert case.meta.id == "test-case-01"
        assert case.meta.name == "Test Case"

    def test_malformed_yaml_raises_eval_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("{{invalid: yaml: content", encoding="utf-8")
        with pytest.raises(EvalError, match="Failed to load golden case"):
            load_golden_case(path)

    def test_invalid_schema_raises_eval_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad-schema.yaml"
        _write_golden_yaml(path, {"meta": {"id": "test"}})
        with pytest.raises(EvalError, match="Failed to load golden case"):
            load_golden_case(path)

    def test_nonexistent_file_raises_eval_error(self, tmp_path: Path) -> None:
        path = tmp_path / "does-not-exist.yaml"
        with pytest.raises(EvalError, match="Failed to load golden case"):
            load_golden_case(path)


# ---------------------------------------------------------------------------
# discover_golden_cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestDiscoverGoldenCases:
    def test_discovers_and_sorts_by_id(self, tmp_path: Path) -> None:
        for case_id in ("charlie-case", "alpha-case", "bravo-case"):
            data = _minimal_golden_dict()
            data["meta"]["id"] = case_id
            data["meta"]["name"] = case_id
            _write_golden_yaml(tmp_path / f"{case_id}.yaml", data)
        cases = discover_golden_cases(tmp_path)
        assert len(cases) == 3
        assert [c.meta.id for c in cases] == [
            "alpha-case",
            "bravo-case",
            "charlie-case",
        ]

    def test_empty_dir_returns_empty_list(self, tmp_path: Path) -> None:
        cases = discover_golden_cases(tmp_path)
        assert cases == []

    def test_missing_dir_returns_empty_list(self, tmp_path: Path) -> None:
        cases = discover_golden_cases(tmp_path / "nonexistent")
        assert cases == []

    def test_skips_non_yaml_files(self, tmp_path: Path) -> None:
        _write_golden_yaml(tmp_path / "valid.yaml", _minimal_golden_dict())
        (tmp_path / "readme.md").write_text("Not a YAML file", encoding="utf-8")
        (tmp_path / "data.json").write_text("{}", encoding="utf-8")
        cases = discover_golden_cases(tmp_path)
        assert len(cases) == 1
        assert cases[0].meta.id == "test-case-01"

    def test_uses_default_dir_when_none(self) -> None:
        # With None, discover uses the default dir which likely does not
        # exist in the test environment. Should return empty list gracefully.
        cases = discover_golden_cases(None)
        assert isinstance(cases, list)


# ---------------------------------------------------------------------------
# materialize_profile
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestMaterializeProfile:
    def test_creates_correct_structure(self, tmp_path: Path) -> None:
        golden = _make_golden()
        target = tmp_path / "profile"
        result = materialize_profile(golden, target)
        assert result == target
        assert (target / "curated.yaml").is_file()
        assert (target / "job_description.txt").is_file()
        assert (target / "curation_log.json").is_file()
        assert (target / "data" / "basics.yaml").is_file()

    def test_creates_all_data_section_files(self, tmp_path: Path) -> None:
        golden = _make_golden()
        target = tmp_path / "profile"
        materialize_profile(golden, target)
        expected_sections = (
            "work",
            "skills",
            "projects",
            "certificates",
            "education",
            "interests",
        )
        for section in expected_sections:
            assert (target / "data" / f"{section}.yaml").is_file()

    def test_job_description_content(self, tmp_path: Path) -> None:
        golden = _make_golden(
            job_description="Looking for a Python developer.",
        )
        target = tmp_path / "profile"
        materialize_profile(golden, target)
        jd_text = (target / "job_description.txt").read_text(encoding="utf-8")
        assert jd_text == "Looking for a Python developer."

    def test_curation_log_has_format_version(self, tmp_path: Path) -> None:
        import json

        golden = _make_golden()
        target = tmp_path / "profile"
        materialize_profile(golden, target)
        log_data = json.loads(
            (target / "curation_log.json").read_text(encoding="utf-8"),
        )
        assert log_data["format_version"] == "2.7"
        assert log_data["max_pages"] == 1

    def test_validates_resume_curation_schema(self, tmp_path: Path) -> None:
        golden = _make_golden()
        target = tmp_path / "profile"
        materialize_profile(golden, target)
        # curated.yaml should be loadable and valid.
        raw = yaml.safe_load(
            (target / "curated.yaml").read_text(encoding="utf-8"),
        )
        from curator.models import ResumeCuration

        ResumeCuration.model_validate(raw)

    def test_empty_sections_get_empty_lists(self, tmp_path: Path) -> None:
        golden = _make_golden(section_data={})
        target = tmp_path / "profile"
        materialize_profile(golden, target)
        for section in ("work", "skills", "projects"):
            content = yaml.safe_load(
                (target / "data" / f"{section}.yaml").read_text(encoding="utf-8"),
            )
            assert content == []

    def test_section_data_written_correctly(self, tmp_path: Path) -> None:
        section_data = {
            "work": [
                {"id": "job-1", "name": "Acme", "position": "Engineer"},
            ],
            "skills": [
                {"id": "python", "name": "Python", "keywords": ["flask"]},
            ],
        }
        golden = _make_golden(section_data=section_data)
        target = tmp_path / "profile"
        materialize_profile(golden, target)
        work_data = yaml.safe_load(
            (target / "data" / "work.yaml").read_text(encoding="utf-8"),
        )
        assert len(work_data) == 1
        assert work_data[0]["id"] == "job-1"

    def test_stale_schema_raises_eval_error(self, tmp_path: Path) -> None:
        data = _minimal_golden_dict()
        # Corrupt the curation dict so it fails ResumeCuration validation.
        data["curation"] = {"invalid_field": "no reasoning, etc."}
        golden = GoldenCase.model_validate(data)
        target = tmp_path / "profile"
        with pytest.raises(EvalError, match="stale curation schema"):
            materialize_profile(golden, target)

    def test_basics_written_correctly(self, tmp_path: Path) -> None:
        golden = _make_golden(basics={"name": "Jane Doe", "email": "jane@test.com"})
        target = tmp_path / "profile"
        materialize_profile(golden, target)
        basics_data = yaml.safe_load(
            (target / "data" / "basics.yaml").read_text(encoding="utf-8"),
        )
        assert basics_data["name"] == "Jane Doe"
        assert basics_data["email"] == "jane@test.com"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        golden = _make_golden()
        target = tmp_path / "nested" / "deep" / "profile"
        result = materialize_profile(golden, target)
        assert result.is_dir()
        assert (target / "curated.yaml").is_file()


# ---------------------------------------------------------------------------
# compare_against_golden — baseline checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestCompareBaselines:
    def test_all_baselines_met_passes(self) -> None:
        golden = _make_golden(
            baselines={
                "aggregate_score": {"min": 50.0, "max": 100.0},
                "word_count": {"min": 400.0, "max": 600.0},
            },
        )
        metrics = [
            EvalMetricResult(
                name="word_count",
                category="content_density",
                status=EvalMetricStatus.PASS,
                value=500,
            ),
        ]
        report = _make_report(metrics)
        result = compare_against_golden(report, golden)
        assert result.passed is True
        errors = [f for f in result.findings if f.severity == RegressionSeverity.ERROR]
        assert len(errors) == 0

    def test_aggregate_below_min_is_error(self) -> None:
        golden = _make_golden(
            baselines={"aggregate_score": {"min": 90.0}},
        )
        metrics = [
            EvalMetricResult(
                name="word_count",
                category="content_density",
                status=EvalMetricStatus.FAIL,
                value=100,
            ),
        ]
        report = _make_report(metrics)
        result = compare_against_golden(report, golden)
        assert result.passed is False
        score_drops = [
            f for f in result.findings if f.category == RegressionCategory.SCORE_DROP
        ]
        assert len(score_drops) >= 1
        assert score_drops[0].severity == RegressionSeverity.ERROR

    def test_metric_below_baseline_min_is_warning(self) -> None:
        golden = _make_golden(
            baselines={"word_count": {"min": 600.0}},
        )
        metrics = [
            EvalMetricResult(
                name="word_count",
                category="content_density",
                status=EvalMetricStatus.PASS,
                value=500,
            ),
        ]
        report = _make_report(metrics)
        result = compare_against_golden(report, golden)
        violations = [
            f
            for f in result.findings
            if f.category == RegressionCategory.BASELINE_VIOLATION
        ]
        assert len(violations) == 1
        assert violations[0].severity == RegressionSeverity.WARNING
        # Warnings alone do not cause failure.
        assert result.passed is True

    def test_empty_baselines_no_violations(self) -> None:
        golden = _make_golden(baselines={})
        report = _make_report()
        result = compare_against_golden(report, golden)
        assert result.passed is True
        assert result.findings == []

    def test_boundary_inclusive_at_min(self) -> None:
        golden = _make_golden(
            baselines={"word_count": {"min": 500.0}},
        )
        metrics = [
            EvalMetricResult(
                name="word_count",
                category="content_density",
                status=EvalMetricStatus.PASS,
                value=500.0,
            ),
        ]
        report = _make_report(metrics)
        result = compare_against_golden(report, golden)
        violations = [
            f
            for f in result.findings
            if f.category == RegressionCategory.BASELINE_VIOLATION
        ]
        assert len(violations) == 0

    def test_boundary_just_below_min(self) -> None:
        golden = _make_golden(
            baselines={"word_count": {"min": 500.0}},
        )
        metrics = [
            EvalMetricResult(
                name="word_count",
                category="content_density",
                status=EvalMetricStatus.PASS,
                value=499.99,
            ),
        ]
        report = _make_report(metrics)
        result = compare_against_golden(report, golden)
        violations = [
            f
            for f in result.findings
            if f.category == RegressionCategory.BASELINE_VIOLATION
        ]
        assert len(violations) == 1

    def test_exceeding_max_emits_warning(self) -> None:
        golden = _make_golden(
            baselines={"word_count": {"max": 500.0}},
        )
        metrics = [
            EvalMetricResult(
                name="word_count",
                category="content_density",
                status=EvalMetricStatus.PASS,
                value=600.0,
            ),
        ]
        report = _make_report(metrics)
        result = compare_against_golden(report, golden)
        # Exceeding max is a calibration cue (warning), not an error.
        assert result.passed is True
        violations = [
            f
            for f in result.findings
            if f.category == RegressionCategory.BASELINE_VIOLATION
        ]
        assert len(violations) == 1
        assert violations[0].severity == RegressionSeverity.WARNING
        assert "above maximum" in violations[0].message


@pytest.mark.unit
@pytest.mark.eval
class TestCompareStatusFlip:
    def test_matching_status_no_finding(self) -> None:
        golden = _make_golden(
            baselines={"word_count": {"min": 0.0, "status": "PASS"}},
        )
        metrics = [
            EvalMetricResult(
                name="word_count",
                category="content_density",
                status=EvalMetricStatus.PASS,
                value=500,
            ),
        ]
        report = _make_report(metrics)
        result = compare_against_golden(report, golden)
        flips = [
            f for f in result.findings if f.category == RegressionCategory.STATUS_FLIP
        ]
        assert flips == []
        assert result.passed is True

    def test_status_flip_pass_to_fail_is_error(self) -> None:
        golden = _make_golden(
            baselines={"word_count": {"status": "PASS"}},
        )
        metrics = [
            EvalMetricResult(
                name="word_count",
                category="content_density",
                status=EvalMetricStatus.FAIL,
                value=500,
            ),
        ]
        report = _make_report(metrics)
        result = compare_against_golden(report, golden)
        flips = [
            f for f in result.findings if f.category == RegressionCategory.STATUS_FLIP
        ]
        assert len(flips) == 1
        assert flips[0].severity == RegressionSeverity.ERROR
        assert "PASS" in flips[0].message
        assert "FAIL" in flips[0].message
        assert result.passed is False

    def test_no_baseline_status_no_check(self) -> None:
        # Baseline without `status` field opts out of flip detection entirely.
        golden = _make_golden(
            baselines={"word_count": {"min": 0.0}},
        )
        metrics = [
            EvalMetricResult(
                name="word_count",
                category="content_density",
                status=EvalMetricStatus.FAIL,
                value=500,
            ),
        ]
        report = _make_report(metrics)
        result = compare_against_golden(report, golden)
        flips = [
            f for f in result.findings if f.category == RegressionCategory.STATUS_FLIP
        ]
        assert flips == []


# ---------------------------------------------------------------------------
# compare_against_golden — schema / metric count checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestCompareSchemaAndMetricCount:
    def test_schema_mismatch_is_error(self) -> None:
        # SA-3 (2026-04-26): schema-version mismatch is now ERROR, not
        # WARNING. The shape may have shifted; baselines may not be
        # comparable; downstream tooling that reads the JSON keys may
        # break. Re-stamping the golden YAML's eval_schema_version once
        # baselines are confirmed compatible is the explicit remediation.
        golden = _make_golden()
        report = _make_report(schema_version=EVAL_SCHEMA_VERSION + 1)
        result = compare_against_golden(report, golden)
        schema_findings = [
            f
            for f in result.findings
            if f.category == RegressionCategory.SCHEMA_MISMATCH
        ]
        assert len(schema_findings) == 1
        assert schema_findings[0].severity == RegressionSeverity.ERROR
        # ERRORs cause failure.
        assert result.passed is False

    def test_matching_schema_no_warning(self) -> None:
        golden = _make_golden()
        report = _make_report()
        result = compare_against_golden(report, golden)
        schema_findings = [
            f
            for f in result.findings
            if f.category == RegressionCategory.SCHEMA_MISMATCH
        ]
        assert len(schema_findings) == 0

    def test_baseline_metric_not_in_report_is_error(self) -> None:
        golden = _make_golden(
            baselines={"nonexistent_metric": {"min": 0.5}},
        )
        report = _make_report()
        result = compare_against_golden(report, golden)
        assert result.passed is False
        mismatch_findings = [
            f
            for f in result.findings
            if f.category == RegressionCategory.METRIC_COUNT_MISMATCH
            and f.severity == RegressionSeverity.ERROR
        ]
        assert len(mismatch_findings) >= 1
        assert "nonexistent_metric" in mismatch_findings[0].message

    def test_new_metric_not_in_baselines_is_warning(self) -> None:
        # Need >=5 non-aggregate baselines to trigger new-metric warnings.
        golden = _make_golden(
            baselines={
                "known_metric": {"min": 0.5},
                "metric_b": {"min": 0.1},
                "metric_c": {"min": 0.1},
                "metric_d": {"min": 0.1},
                "metric_e": {"min": 0.1},
            },
        )
        metrics = [
            EvalMetricResult(
                name="known_metric",
                category="content_density",
                status=EvalMetricStatus.PASS,
                value=0.8,
            ),
            EvalMetricResult(
                name="brand_new_metric",
                category="content_density",
                status=EvalMetricStatus.PASS,
                value=1.0,
            ),
        ]
        report = _make_report(metrics)
        result = compare_against_golden(report, golden)
        new_metric_findings = [
            f
            for f in result.findings
            if f.category == RegressionCategory.METRIC_COUNT_MISMATCH
            and f.severity == RegressionSeverity.WARNING
            and "brand_new_metric" in f.message
        ]
        assert len(new_metric_findings) == 1

    def test_no_new_metric_warnings_when_baselines_empty(self) -> None:
        golden = _make_golden(baselines={})
        metrics = [
            EvalMetricResult(
                name="anything",
                category="cat",
                status=EvalMetricStatus.PASS,
                value=1.0,
            ),
        ]
        report = _make_report(metrics)
        result = compare_against_golden(report, golden)
        new_metric_findings = [
            f
            for f in result.findings
            if f.category == RegressionCategory.METRIC_COUNT_MISMATCH
        ]
        assert len(new_metric_findings) == 0


# ---------------------------------------------------------------------------
# compare_against_golden — selection expectations
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestCompareSelectionExpectations:
    def test_must_include_present_no_error(self) -> None:
        golden = _make_golden(
            expected={"must_include": {"skills": ["cloud-aws"]}},
        )
        report = _make_report()
        result = compare_against_golden(report, golden)
        missing = [
            f for f in result.findings if f.category == RegressionCategory.MISSING_ENTRY
        ]
        assert len(missing) == 0

    def test_must_exclude_present_is_error(self) -> None:
        golden = _make_golden(
            expected={"must_exclude": {"projects": ["infra-toolkit"]}},
        )
        report = _make_report()
        result = compare_against_golden(report, golden)
        assert result.passed is False
        excluded = [
            f
            for f in result.findings
            if f.category == RegressionCategory.EXCLUDED_PRESENT
        ]
        assert len(excluded) == 1

    def test_skills_section_must_include(self) -> None:
        golden = _make_golden(
            expected={"must_include": {"skills": ["cloud-aws"]}},
        )
        report = _make_report()
        result = compare_against_golden(report, golden)
        missing = [
            f for f in result.findings if f.category == RegressionCategory.MISSING_ENTRY
        ]
        assert len(missing) == 0

    def test_simple_section_must_include_missing(self) -> None:
        golden = _make_golden(
            expected={"must_include": {"projects": ["nonexistent-project"]}},
        )
        report = _make_report()
        result = compare_against_golden(report, golden)
        assert result.passed is False
        missing = [
            f for f in result.findings if f.category == RegressionCategory.MISSING_ENTRY
        ]
        assert len(missing) == 1
        assert "nonexistent-project" in missing[0].message

    def test_work_must_include_skipped(self) -> None:
        golden = _make_golden(
            expected={"must_include": {"work": ["nonexistent-job"]}},
        )
        report = _make_report()
        result = compare_against_golden(report, golden)
        missing = [
            f for f in result.findings if f.category == RegressionCategory.MISSING_ENTRY
        ]
        assert len(missing) == 0

    def test_education_must_include_skipped(self) -> None:
        golden = _make_golden(
            expected={"must_include": {"education": ["mit-ms-cs"]}},
        )
        report = _make_report()
        result = compare_against_golden(report, golden)
        missing = [
            f for f in result.findings if f.category == RegressionCategory.MISSING_ENTRY
        ]
        assert len(missing) == 0


# ---------------------------------------------------------------------------
# compare_against_golden — section order prefix
# ---------------------------------------------------------------------------


@pytest.mark.unit
# ---------------------------------------------------------------------------
# compare_against_golden — pass/fail determination
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestComparePassFail:
    def test_warnings_only_still_passes(self) -> None:
        # Schema-mismatch was previously WARNING; SA-3 (2026-04-26)
        # promoted it to ERROR. To keep this assertion meaningful, the
        # case now matches schema versions and only exercises the
        # baseline-violation WARNING path.
        golden = _make_golden(
            baselines={"word_count": {"min": 600.0}},
        )
        metrics = [
            EvalMetricResult(
                name="word_count",
                category="content_density",
                status=EvalMetricStatus.PASS,
                value=500,
            ),
        ]
        report = _make_report(metrics, schema_version=EVAL_SCHEMA_VERSION)
        result = compare_against_golden(report, golden)
        warnings = [
            f for f in result.findings if f.severity == RegressionSeverity.WARNING
        ]
        assert len(warnings) >= 1
        assert result.passed is True

    def test_any_error_causes_failure(self) -> None:
        golden = _make_golden(
            baselines={"aggregate_score": {"min": 99.0}},
        )
        metrics = [
            EvalMetricResult(
                name="word_count",
                category="content_density",
                status=EvalMetricStatus.FAIL,
                value=0,
            ),
        ]
        report = _make_report(metrics)
        result = compare_against_golden(report, golden)
        assert result.passed is False

    def test_case_id_from_golden_meta(self) -> None:
        golden = _make_golden()
        report = _make_report()
        result = compare_against_golden(report, golden)
        assert result.case_id == "test-case-01"

    def test_report_preserved_in_result(self) -> None:
        golden = _make_golden()
        report = _make_report()
        result = compare_against_golden(report, golden)
        assert result.report is report


# ---------------------------------------------------------------------------
# compare_against_golden — multiple findings collected
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestCompareMultipleFindings:
    def test_collects_findings_from_multiple_sources(self) -> None:
        golden_data = _minimal_golden_dict(
            baselines={
                "aggregate_score": {"min": 99.0},
                "missing_metric": {"min": 0.5},
                "word_count": {"min": 100.0},
            },
            expected={
                "must_include": {"skills": ["nonexistent-skill"]},
                "must_exclude": {"projects": ["infra-toolkit"]},
            },
        )
        golden_data["meta"]["eval_schema_version"] = EVAL_SCHEMA_VERSION + 1
        golden = GoldenCase.model_validate(golden_data)
        metrics = [
            EvalMetricResult(
                name="word_count",
                category="content_density",
                status=EvalMetricStatus.FAIL,
                value=0,
            ),
        ]
        report = _make_report(metrics, schema_version=EVAL_SCHEMA_VERSION)
        result = compare_against_golden(report, golden)
        assert result.passed is False
        # Should have findings from multiple categories.
        categories_found = {f.category for f in result.findings}
        assert RegressionCategory.SCHEMA_MISMATCH in categories_found
        assert RegressionCategory.SCORE_DROP in categories_found
        assert RegressionCategory.METRIC_COUNT_MISMATCH in categories_found
        assert RegressionCategory.MISSING_ENTRY in categories_found
        assert RegressionCategory.EXCLUDED_PRESENT in categories_found
        assert RegressionCategory.BASELINE_VIOLATION in categories_found
        assert len(result.findings) >= 6

    def test_non_numeric_metric_value_skips_range_check(self) -> None:
        golden = _make_golden(
            baselines={"bool_metric": {"min": 0.5}},
        )
        metrics = [
            EvalMetricResult(
                name="bool_metric",
                category="content_density",
                status=EvalMetricStatus.PASS,
                value=True,
            ),
        ]
        report = _make_report(metrics)
        result = compare_against_golden(report, golden)
        # Non-numeric value should not trigger a baseline violation.
        violations = [
            f
            for f in result.findings
            if f.category == RegressionCategory.BASELINE_VIOLATION
        ]
        assert len(violations) == 0


# ---------------------------------------------------------------------------
# GOLDEN_SKIP_METRICS
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestGoldenSkipMetrics:
    def test_is_frozenset(self) -> None:
        assert isinstance(GOLDEN_SKIP_METRICS, frozenset)

    def test_contains_expected_metrics(self) -> None:
        # jd_match_rate was removed from GOLDEN_SKIP_METRICS in the
        # 2026-04-24 recalibration: it is now informational=True, and
        # the informational filter in score_category already excludes
        # it from golden comparisons. keyword_coverage stays here because
        # it is a SCORED metric that nonetheless needs full portfolio
        # data which golden cases lack.
        assert "keyword_coverage" in GOLDEN_SKIP_METRICS
        assert "jd_match_rate" not in GOLDEN_SKIP_METRICS
        assert len(GOLDEN_SKIP_METRICS) == 1


# ---------------------------------------------------------------------------
# render_golden_pdf
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestRenderGoldenPdf:
    def test_happy_path_returns_pdf_path(self, tmp_path: Path) -> None:
        golden = _make_golden()
        profile_dir = tmp_path / "profile"
        materialize_profile(golden, profile_dir)

        template = tmp_path / "curated.typ"
        template.write_text("// fake template", encoding="utf-8")

        with patch("curator.eval.golden.compile_typst") as mock_typst:
            result = render_golden_pdf(profile_dir, template_path=template)

        assert result == profile_dir / "resume.pdf"
        mock_typst.assert_called_once()

    def test_layout_yaml_created_with_default_section_order(
        self, tmp_path: Path
    ) -> None:
        golden = _make_golden()
        profile_dir = tmp_path / "profile"
        materialize_profile(golden, profile_dir)

        template = tmp_path / "curated.typ"
        template.write_text("// fake template", encoding="utf-8")

        with patch("curator.eval.golden.compile_typst"):
            render_golden_pdf(profile_dir, template_path=template)

        layout = yaml.safe_load(
            (profile_dir / "layout.yaml").read_text(encoding="utf-8"),
        )
        from curator.models import RENDERER_SECTIONS

        assert layout["section_order"] == [*RENDERER_SECTIONS, "interests"]

    def test_template_copied_into_profile_dir(self, tmp_path: Path) -> None:
        golden = _make_golden()
        profile_dir = tmp_path / "profile"
        materialize_profile(golden, profile_dir)

        template = tmp_path / "curated.typ"
        template.write_text("// template content", encoding="utf-8")

        with patch("curator.eval.golden.compile_typst"):
            render_golden_pdf(profile_dir, template_path=template)

        copied = profile_dir / "curated.typ"
        assert copied.is_file()
        assert copied.read_text(encoding="utf-8") == "// template content"

    def test_compile_typst_called_with_correct_args(self, tmp_path: Path) -> None:
        golden = _make_golden()
        profile_dir = tmp_path / "profile"
        materialize_profile(golden, profile_dir)

        template = tmp_path / "curated.typ"
        template.write_text("// fake template", encoding="utf-8")

        with patch("curator.eval.golden.compile_typst") as mock_typst:
            render_golden_pdf(profile_dir, template_path=template)

        mock_typst.assert_called_once_with(
            root_dir=profile_dir,
            template_path=profile_dir / "curated.typ",
            output_path=profile_dir / "resume.pdf",
        )

    def test_missing_template_raises_eval_error(self, tmp_path: Path) -> None:
        golden = _make_golden()
        profile_dir = tmp_path / "profile"
        materialize_profile(golden, profile_dir)

        missing_template = tmp_path / "nonexistent.typ"

        with pytest.raises(EvalError, match="Template not found"):
            render_golden_pdf(profile_dir, template_path=missing_template)

    def test_typst_not_installed_propagates_file_not_found(
        self, tmp_path: Path
    ) -> None:
        golden = _make_golden()
        profile_dir = tmp_path / "profile"
        materialize_profile(golden, profile_dir)

        template = tmp_path / "curated.typ"
        template.write_text("// fake template", encoding="utf-8")

        with (
            patch(
                "curator.eval.golden.compile_typst",
                side_effect=FileNotFoundError("typst not found"),
            ),
            pytest.raises(FileNotFoundError, match="typst not found"),
        ):
            render_golden_pdf(profile_dir, template_path=template)

    def test_subprocess_timeout_propagates(self, tmp_path: Path) -> None:
        golden = _make_golden()
        profile_dir = tmp_path / "profile"
        materialize_profile(golden, profile_dir)

        template = tmp_path / "curated.typ"
        template.write_text("// fake template", encoding="utf-8")

        timeout_exc = subprocess.TimeoutExpired(cmd=["typst"], timeout=30)
        with (
            patch(
                "curator.eval.golden.compile_typst",
                side_effect=timeout_exc,
            ),
            pytest.raises(subprocess.TimeoutExpired),
        ):
            render_golden_pdf(profile_dir, template_path=template)

    def test_typst_compilation_failure_raises_eval_error(self, tmp_path: Path) -> None:
        golden = _make_golden()
        profile_dir = tmp_path / "profile"
        materialize_profile(golden, profile_dir)

        template = tmp_path / "curated.typ"
        template.write_text("// fake template", encoding="utf-8")

        with (
            patch(
                "curator.eval.golden.compile_typst",
                side_effect=CuratorError("Typst compilation failed (exit 1): error"),
            ),
            pytest.raises(EvalError, match="Golden PDF rendering failed"),
        ):
            render_golden_pdf(profile_dir, template_path=template)


# ---------------------------------------------------------------------------
# compile_typst (io_utils)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestCompileTypst:
    def test_success_no_exception(self, tmp_path: Path) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["typst"], returncode=0, stdout="", stderr=""
        )
        with patch("curator.io_utils.subprocess.run", return_value=mock_result):
            compile_typst(
                root_dir=tmp_path,
                template_path=tmp_path / "template.typ",
                output_path=tmp_path / "output.pdf",
            )

    def test_nonzero_exit_raises_curator_error(self, tmp_path: Path) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["typst"],
            returncode=1,
            stdout="",
            stderr="error: file not found",
        )
        with (
            patch("curator.io_utils.subprocess.run", return_value=mock_result),
            pytest.raises(
                CuratorError, match=r"Typst compilation failed.*file not found"
            ),
        ):
            compile_typst(
                root_dir=tmp_path,
                template_path=tmp_path / "template.typ",
                output_path=tmp_path / "output.pdf",
            )

    def test_file_not_found_propagates(self, tmp_path: Path) -> None:
        with (
            patch(
                "curator.io_utils.subprocess.run",
                side_effect=FileNotFoundError("typst: command not found"),
            ),
            pytest.raises(FileNotFoundError, match="typst"),
        ):
            compile_typst(
                root_dir=tmp_path,
                template_path=tmp_path / "template.typ",
                output_path=tmp_path / "output.pdf",
            )

    def test_timeout_propagates(self, tmp_path: Path) -> None:
        with (
            patch(
                "curator.io_utils.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="typst", timeout=30),
            ),
            pytest.raises(subprocess.TimeoutExpired),
        ):
            compile_typst(
                root_dir=tmp_path,
                template_path=tmp_path / "template.typ",
                output_path=tmp_path / "output.pdf",
            )

    def test_correct_command_constructed(self, tmp_path: Path) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["typst"], returncode=0, stdout="", stderr=""
        )
        with patch(
            "curator.io_utils.subprocess.run", return_value=mock_result
        ) as mock_run:
            template = tmp_path / "curated.typ"
            output = tmp_path / "resume.pdf"
            compile_typst(
                root_dir=tmp_path,
                template_path=template,
                output_path=output,
            )

        mock_run.assert_called_once_with(
            [
                "typst",
                "compile",
                "--root",
                str(tmp_path),
                str(template),
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_custom_timeout_passed_through(self, tmp_path: Path) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["typst"], returncode=0, stdout="", stderr=""
        )
        with patch(
            "curator.io_utils.subprocess.run", return_value=mock_result
        ) as mock_run:
            compile_typst(
                root_dir=tmp_path,
                template_path=tmp_path / "template.typ",
                output_path=tmp_path / "output.pdf",
                timeout=60,
            )

        assert mock_run.call_args[1]["timeout"] == 60


# ---------------------------------------------------------------------------
# from_golden_case
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestFromGoldenCase:
    def test_returns_eval_context_with_curation(self) -> None:
        case = _make_golden()
        ctx = from_golden_case(case)
        assert ctx.curation is not None
        assert ctx.basics == case.basics
        assert ctx.section_data == case.section_data
        assert ctx.jd_text == case.job_description

    def test_no_pdf_path_by_default(self) -> None:
        case = _make_golden()
        ctx = from_golden_case(case)
        assert ctx.pdf_path is None

    def test_pdf_path_threaded_through(self, tmp_path: Path) -> None:
        case = _make_golden()
        pdf = tmp_path / "resume.pdf"
        pdf.write_bytes(b"fake")
        ctx = from_golden_case(case, pdf_path=pdf)
        assert ctx.pdf_path == pdf

    def test_stale_curation_raises_eval_error(self) -> None:
        # Strip required fields from the curation dict to simulate drift.
        case = _make_golden(curation={"summary": "too short"})
        with pytest.raises(EvalError, match="stale curation schema"):
            from_golden_case(case)

    def test_meta_max_pages_default_threaded_through(self) -> None:
        """Default ``meta.max_pages: 1`` lands on EvalContext.

        Without this round-trip, a future regression that drops the
        ``case.meta.max_pages`` read in ``from_golden_case`` would
        silently re-rate every long-form golden against SHORT_FORM_BANDS.
        """
        case = _make_golden()
        ctx = from_golden_case(case)
        assert case.meta.max_pages == 1  # default value pinned
        assert ctx.max_pages == 1

    def test_meta_max_pages_long_form_threaded_through(self) -> None:
        """Explicit ``meta.max_pages: 2`` lands on EvalContext."""
        meta = _minimal_golden_dict()["meta"] | {"max_pages": 2}
        case = _make_golden(meta=meta)
        ctx = from_golden_case(case)
        assert case.meta.max_pages == 2
        assert ctx.max_pages == 2

    @pytest.mark.parametrize("max_pages", [1, 2, 3, 4, 5])
    def test_meta_max_pages_round_trip(self, max_pages: int) -> None:
        """Every supported page budget round-trips correctly."""
        meta = _minimal_golden_dict()["meta"] | {"max_pages": max_pages}
        case = _make_golden(meta=meta)
        ctx = from_golden_case(case)
        assert ctx.max_pages == max_pages
