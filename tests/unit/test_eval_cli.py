"""Tests for the eval CLI command."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

from curator.cli import app
from curator.eval.golden import (
    GoldenCase,
    GoldenComparisonResult,
)
from curator.eval.report import (
    EVAL_SCHEMA_VERSION,
    CategoryScore,
    EvalMetricResult,
    EvalMetricStatus,
    EvalReport,
    build_report,
)
from tests.helpers import make_curation_dict


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_report() -> EvalReport:
    metrics = [
        EvalMetricResult(
            name="word_count",
            category="content_density",
            status=EvalMetricStatus.PASS,
            value=500,
            detail="500 words (target: 475-700)",
        ),
        EvalMetricResult(
            name="weak_phrase_count",
            category="writing_quality",
            status=EvalMetricStatus.FAIL,
            value=2,
            detail="Found: responsible for, helped",
        ),
    ]
    categories = [
        CategoryScore(
            name="content_density",
            score=100.0,
            status=EvalMetricStatus.PASS,
            weight=0.10,
            metrics=[metrics[0]],
        ),
        CategoryScore(
            name="writing_quality",
            score=0.0,
            status=EvalMetricStatus.FAIL,
            weight=0.25,
            metrics=[metrics[1]],
        ),
    ]
    return EvalReport(
        metrics=metrics,
        categories=categories,
        aggregate_score=71.4,
        status=EvalMetricStatus.FAIL,
    )


# ---------------------------------------------------------------------------
# Missing profile directory
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestEvalMissingDir:
    def test_missing_dir_exits_with_error(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["eval", "/nonexistent/path"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Valid profile directory
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestEvalValidDir:
    def test_successful_evaluation(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        mock_report = _make_report()
        mock_ctx = MagicMock()

        with (
            patch(
                "curator.eval.from_profile_dir",
                return_value=mock_ctx,
            ) as mock_from_dir,
            patch(
                "curator.eval.evaluate_tier1",
                return_value=mock_report,
            ) as mock_eval,
        ):
            result = runner.invoke(app, ["eval", str(profile_dir)])

        assert result.exit_code == 0
        mock_from_dir.assert_called_once()
        mock_eval.assert_called_once()

    def test_output_contains_score(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        mock_report = _make_report()
        mock_ctx = MagicMock()

        with (
            patch("curator.eval.from_profile_dir", return_value=mock_ctx),
            patch("curator.eval.evaluate_tier1", return_value=mock_report),
        ):
            result = runner.invoke(app, ["eval", str(profile_dir)])

        assert "71" in result.output or "71" in (result.stderr or "")
        assert "2 metrics evaluated" in result.output or "2 metrics" in (
            result.stderr or ""
        )

    def test_skip_metrics_passed_through(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        mock_report = _make_report()
        mock_ctx = MagicMock()

        with (
            patch("curator.eval.from_profile_dir", return_value=mock_ctx),
            patch(
                "curator.eval.evaluate_tier1",
                return_value=mock_report,
            ) as mock_eval,
        ):
            result = runner.invoke(
                app,
                ["eval", str(profile_dir), "--skip", "word_count"],
            )

        assert result.exit_code == 0
        call_kwargs = mock_eval.call_args[1]
        assert "word_count" in call_kwargs["skip_metrics"]

    def test_eval_error_exits_with_error(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        from curator.exceptions import EvalError

        with patch(
            "curator.eval.from_profile_dir",
            side_effect=EvalError("test error"),
        ):
            result = runner.invoke(app, ["eval", str(profile_dir)])

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Portfolio option
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestEvalPortfolioOption:
    def test_portfolio_loaded_when_provided(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()
        portfolio_dir = tmp_path / "portfolio"
        portfolio_dir.mkdir()

        mock_report = _make_report()
        mock_ctx = MagicMock()
        mock_portfolio = MagicMock()

        with (
            patch("curator.eval.from_profile_dir", return_value=mock_ctx),
            patch("curator.eval.evaluate_tier1", return_value=mock_report),
            patch(
                "curator.loader.load_portfolio",
                return_value=mock_portfolio,
            ) as mock_load,
        ):
            result = runner.invoke(
                app,
                ["eval", str(profile_dir), "--portfolio", str(portfolio_dir)],
            )

        assert result.exit_code == 0
        mock_load.assert_called_once_with(portfolio_dir)


# ---------------------------------------------------------------------------
# Helpers for golden CLI tests
# ---------------------------------------------------------------------------


def _minimal_curation_dict(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid ResumeCuration dict."""
    return make_curation_dict(**overrides)


def _make_golden_case(**overrides: Any) -> GoldenCase:
    """Build a minimal validated GoldenCase for CLI tests."""
    data: dict[str, Any] = {
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
    data.update(overrides)
    return GoldenCase.model_validate(data)


def _make_golden_report() -> EvalReport:
    """Build a simple passing report for golden CLI tests."""
    metrics = [
        EvalMetricResult(
            name="word_count",
            category="content_density",
            status=EvalMetricStatus.PASS,
            value=500,
        ),
    ]
    return build_report(metrics)


def _make_golden_comparison(
    case: GoldenCase,
    report: EvalReport,
    *,
    passed: bool = True,
) -> GoldenComparisonResult:
    """Build a GoldenComparisonResult for CLI tests."""
    return GoldenComparisonResult(
        case_id=case.meta.id,
        passed=passed,
        report=report,
    )


# ---------------------------------------------------------------------------
# --calibrate / --apply flag validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestEvalCalibrateFlagValidation:
    def test_calibrate_without_golden_exits_with_error(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["eval", "--calibrate"])
        assert result.exit_code == 1
        assert "requires --golden" in result.output or "requires --golden" in (
            result.stderr or ""
        )

    def test_apply_without_calibrate_exits_with_error(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["eval", "--apply"])
        assert result.exit_code == 1
        assert "requires --calibrate" in result.output or "requires --calibrate" in (
            result.stderr or ""
        )


# ---------------------------------------------------------------------------
# --golden --calibrate
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestEvalGoldenCalibrate:
    def test_golden_calibrate_produces_calibration_table(
        self, runner: CliRunner
    ) -> None:
        case = _make_golden_case()
        report = _make_golden_report()
        comparison = _make_golden_comparison(case, report)
        mock_ctx = MagicMock()

        with (
            patch(
                "curator.eval.golden.discover_golden_cases",
                return_value=[case],
            ),
            patch("curator.eval.golden.materialize_profile"),
            patch("curator.eval.golden.render_golden_pdf"),
            patch("curator.eval.from_profile_dir", return_value=mock_ctx),
            patch("curator.eval.evaluate_tier1", return_value=report),
            patch(
                "curator.eval.golden.compare_against_golden",
                return_value=comparison,
            ),
        ):
            result = runner.invoke(app, ["eval", "--golden", "--calibrate"])

        assert result.exit_code == 0
        # Calibration output should contain the "Calibration" table title.
        output = result.output + (result.stderr or "")
        assert "Calibration" in output

    def test_golden_calibrate_shows_proposed_baselines(self, runner: CliRunner) -> None:
        case = _make_golden_case()
        report = _make_golden_report()
        comparison = _make_golden_comparison(case, report)
        mock_ctx = MagicMock()

        with (
            patch(
                "curator.eval.golden.discover_golden_cases",
                return_value=[case],
            ),
            patch("curator.eval.golden.materialize_profile"),
            patch("curator.eval.golden.render_golden_pdf"),
            patch("curator.eval.from_profile_dir", return_value=mock_ctx),
            patch("curator.eval.evaluate_tier1", return_value=report),
            patch(
                "curator.eval.golden.compare_against_golden",
                return_value=comparison,
            ),
        ):
            result = runner.invoke(app, ["eval", "--golden", "--calibrate"])

        output = result.output + (result.stderr or "")
        assert "Proposed baselines" in output
        assert "test-case-01" in output


# ---------------------------------------------------------------------------
# --golden --calibrate --apply
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestEvalGoldenCalibrateApply:
    def test_golden_calibrate_apply_updates_golden_files(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        # Create a golden YAML file with empty baselines.
        golden_dir = tmp_path / "golden"
        golden_dir.mkdir()
        case = _make_golden_case()
        golden_file = golden_dir / "test-case-01.yaml"
        golden_data = {
            "meta": {
                "id": "test-case-01",
                "name": "Test Case",
                "eval_schema_version": EVAL_SCHEMA_VERSION,
            },
            "job_description": "We are hiring a Senior DevOps Engineer.",
            "curation": _minimal_curation_dict(),
            "section_data": {},
            "basics": {"name": "Jane Doe"},
            "baselines": {},
        }
        golden_file.write_text(
            yaml.dump(golden_data, Dumper=yaml.SafeDumper, default_flow_style=False),
            encoding="utf-8",
        )

        report = _make_golden_report()
        comparison = _make_golden_comparison(case, report)
        mock_ctx = MagicMock()

        with (
            patch(
                "curator.eval.golden.discover_golden_cases",
                return_value=[case],
            ),
            patch("curator.eval.golden.materialize_profile"),
            patch("curator.eval.golden.render_golden_pdf"),
            patch("curator.eval.from_profile_dir", return_value=mock_ctx),
            patch("curator.eval.evaluate_tier1", return_value=report),
            patch(
                "curator.eval.golden.compare_against_golden",
                return_value=comparison,
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "eval",
                    "--golden",
                    "--golden-dir",
                    str(golden_dir),
                    "--calibrate",
                    "--apply",
                ],
            )

        assert result.exit_code == 0
        output = result.output + (result.stderr or "")
        assert "Updated" in output

        # Verify the golden file was updated with baselines.
        updated = golden_file.read_text(encoding="utf-8")
        assert "baselines:" in updated
        assert "aggregate_score:" in updated


# ---------------------------------------------------------------------------
# --golden with no cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestEvalGoldenNoCases:
    def test_no_golden_cases_found_exits_cleanly(self, runner: CliRunner) -> None:
        with patch(
            "curator.eval.golden.discover_golden_cases",
            return_value=[],
        ):
            result = runner.invoke(app, ["eval", "--golden"])

        assert result.exit_code == 0
        output = result.output + (result.stderr or "")
        assert "No golden cases found" in output


# ---------------------------------------------------------------------------
# --golden regression detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestEvalGoldenRegression:
    def test_golden_failure_exits_with_code_1(self, runner: CliRunner) -> None:
        case = _make_golden_case()
        report = _make_golden_report()
        comparison = _make_golden_comparison(case, report, passed=False)
        mock_ctx = MagicMock()

        with (
            patch(
                "curator.eval.golden.discover_golden_cases",
                return_value=[case],
            ),
            patch("curator.eval.golden.materialize_profile"),
            patch("curator.eval.golden.render_golden_pdf"),
            patch("curator.eval.from_profile_dir", return_value=mock_ctx),
            patch("curator.eval.evaluate_tier1", return_value=report),
            patch(
                "curator.eval.golden.compare_against_golden",
                return_value=comparison,
            ),
        ):
            result = runner.invoke(app, ["eval", "--golden"])

        assert result.exit_code == 1
