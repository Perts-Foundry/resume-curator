"""Tests for the --judge and --json CLI flags on the eval command."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

from curator.cli import _display_tier2_report, app
from curator.eval.judge import JUDGE_DIMENSIONS, Tier2DimensionResult, Tier2Report
from curator.eval.report import (
    CategoryScore,
    EvalMetricResult,
    EvalMetricStatus,
    EvalReport,
)


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
            detail="500 words",
        ),
    ]
    categories = [
        CategoryScore(
            name="content_density",
            score=100.0,
            status=EvalMetricStatus.PASS,
            weight=0.10,
            metrics=metrics,
        ),
    ]
    return EvalReport(
        metrics=metrics,
        categories=categories,
        aggregate_score=85.0,
        status=EvalMetricStatus.PASS,
    )


def _make_tier2_report() -> Tier2Report:
    return Tier2Report(
        dimensions=[
            Tier2DimensionResult(
                name=dim,
                group="selection_quality" if i < 4 else "output_quality",
                score=4,
                justification="Good performance on this dimension overall.",
                normalized_score=75.0,
            )
            for i, dim in enumerate(JUDGE_DIMENSIONS)
        ],
        aggregate_score=75.0,
        model="claude-sonnet-4-6",
        input_tokens=1500,
        output_tokens=900,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


def _patch_eval(
    tier2: Tier2Report | None = None,
) -> tuple[Any, ...]:
    """Create patches for eval functions and CuratorSettings."""
    return (
        patch("curator.eval.from_profile_dir", return_value=MagicMock()),
        patch("curator.eval.evaluate_tier1", return_value=_make_report()),
        patch(
            "curator.cli._run_judge",
            return_value=tier2 or _make_tier2_report(),
        ),
        # Mock CuratorSettings so --judge doesn't require ANTHROPIC_API_KEY in tests.
        patch("curator.config.CuratorSettings", return_value=MagicMock()),
    )


# ---------------------------------------------------------------------------
# --judge flag tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestJudgeFlag:
    def test_judge_flag_accepted(self, runner: CliRunner, tmp_path: Path) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        p_dir, p_t1, p_judge, p_cfg = _patch_eval()
        with p_dir, p_t1, p_judge, p_cfg:
            result = runner.invoke(app, ["eval", str(profile_dir), "--judge"])

        assert result.exit_code == 0

    def test_judge_flag_invokes_judge(self, runner: CliRunner, tmp_path: Path) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        p_dir, p_t1, p_judge, p_cfg = _patch_eval()
        with p_dir, p_t1, p_judge as mock_judge, p_cfg:
            runner.invoke(app, ["eval", str(profile_dir), "--judge"])

        mock_judge.assert_called_once()

    def test_judge_output_shows_dimensions(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        p_dir, p_t1, p_judge, p_cfg = _patch_eval()
        with p_dir, p_t1, p_judge, p_cfg:
            result = runner.invoke(app, ["eval", str(profile_dir), "--judge"])

        assert "Tier 2 LLM Judge" in result.output
        assert "relevance" in result.output
        assert "overall_impression" in result.output

    def test_no_judge_without_flag(self, runner: CliRunner, tmp_path: Path) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        p_dir, p_t1, p_judge, p_cfg = _patch_eval()
        with p_dir, p_t1, p_judge as mock_judge, p_cfg:
            runner.invoke(app, ["eval", str(profile_dir)])

        mock_judge.assert_not_called()


# ---------------------------------------------------------------------------
# --json flag tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestJsonFlag:
    def test_json_without_judge_emits_tier1_only(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        p_dir, p_t1, p_judge, p_cfg = _patch_eval()
        with p_dir, p_t1, p_judge, p_cfg:
            result = runner.invoke(app, ["eval", str(profile_dir), "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "tier1" in data
        assert "tier2" not in data

    def test_json_with_judge_emits_both(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        p_dir, p_t1, p_judge, p_cfg = _patch_eval()
        with p_dir, p_t1, p_judge, p_cfg:
            result = runner.invoke(app, ["eval", str(profile_dir), "--json", "--judge"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "tier1" in data
        assert "tier2" in data
        assert "dimensions" in data["tier2"]
        assert len(data["tier2"]["dimensions"]) == 8

    def test_json_is_valid_json(self, runner: CliRunner, tmp_path: Path) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        p_dir, p_t1, p_judge, p_cfg = _patch_eval()
        with p_dir, p_t1, p_judge, p_cfg:
            result = runner.invoke(app, ["eval", str(profile_dir), "--json", "--judge"])

        # Should not raise.
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_json_suppresses_rich_output(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        p_dir, p_t1, p_judge, p_cfg = _patch_eval()
        with p_dir, p_t1, p_judge, p_cfg:
            result = runner.invoke(app, ["eval", str(profile_dir), "--json"])

        # JSON output should not contain Rich table formatting.
        assert "Eval Report" not in result.output
        # Should be parseable JSON.
        json.loads(result.output)

    def test_json_tier2_contains_model(self, runner: CliRunner, tmp_path: Path) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        p_dir, p_t1, p_judge, p_cfg = _patch_eval()
        with p_dir, p_t1, p_judge, p_cfg:
            result = runner.invoke(app, ["eval", str(profile_dir), "--json", "--judge"])

        data = json.loads(result.output)
        assert data["tier2"]["model"] == "claude-sonnet-4-6"

    def test_json_tier2_aggregate_score(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        p_dir, p_t1, p_judge, p_cfg = _patch_eval()
        with p_dir, p_t1, p_judge, p_cfg:
            result = runner.invoke(app, ["eval", str(profile_dir), "--json", "--judge"])

        data = json.loads(result.output)
        assert data["tier2"]["aggregate_score"] == 75.0

    def test_json_tier2_dimension_fields(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        p_dir, p_t1, p_judge, p_cfg = _patch_eval()
        with p_dir, p_t1, p_judge, p_cfg:
            result = runner.invoke(app, ["eval", str(profile_dir), "--json", "--judge"])

        data = json.loads(result.output)
        dim = data["tier2"]["dimensions"][0]
        assert "name" in dim
        assert "group" in dim
        assert "score" in dim
        assert "justification" in dim
        assert "normalized_score" in dim


# ---------------------------------------------------------------------------
# _display_tier2_report tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestDisplayTier2Report:
    """Tests for _display_tier2_report Rich output."""

    def _capture_display(self, tier2: Tier2Report) -> str:
        """Render the tier2 report and capture console output."""
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=200)
        _display_tier2_report(console, tier2)
        return buf.getvalue()

    def test_title_contains_judge_label(self) -> None:
        tier2 = _make_tier2_report()
        output = self._capture_display(tier2)
        assert "Tier 2 LLM Judge" in output

    def test_title_contains_aggregate_score(self) -> None:
        tier2 = _make_tier2_report()
        output = self._capture_display(tier2)
        assert "75" in output

    def test_title_contains_model(self) -> None:
        tier2 = _make_tier2_report()
        output = self._capture_display(tier2)
        assert "claude-sonnet-4-6" in output

    def test_all_dimensions_displayed(self) -> None:
        tier2 = _make_tier2_report()
        output = self._capture_display(tier2)
        for dim in JUDGE_DIMENSIONS:
            assert dim in output

    def test_group_displayed_without_underscores(self) -> None:
        tier2 = _make_tier2_report()
        output = self._capture_display(tier2)
        assert "selection quality" in output
        assert "output quality" in output

    def test_summary_line_token_counts(self) -> None:
        tier2 = _make_tier2_report()
        output = self._capture_display(tier2)
        assert "8 dimensions" in output
        assert "1,500 in" in output
        assert "900 out" in output

    def test_score_coloring_high(self) -> None:
        """Scores 4-5 should get green markup."""
        tier2 = _make_tier2_report()  # all scores = 4
        output = self._capture_display(tier2)
        # In non-terminal mode, Rich strips color codes.
        # Just verify the score values appear.
        assert "4" in output

    def test_score_coloring_low_scores(self) -> None:
        """Verify display works with low scores (1-2)."""
        dims = [
            Tier2DimensionResult(
                name=dim,
                group="selection_quality" if i < 4 else "output_quality",
                score=1,
                justification="Poor performance observed here overall.",
                normalized_score=0.0,
            )
            for i, dim in enumerate(JUDGE_DIMENSIONS)
        ]
        tier2 = Tier2Report(
            dimensions=dims,
            aggregate_score=0.0,
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        output = self._capture_display(tier2)
        assert "1" in output

    def test_score_coloring_mid_scores(self) -> None:
        """Verify display works with mid scores (3)."""
        dims = [
            Tier2DimensionResult(
                name=dim,
                group="selection_quality" if i < 4 else "output_quality",
                score=3,
                justification="Adequate performance observed here.",
                normalized_score=50.0,
            )
            for i, dim in enumerate(JUDGE_DIMENSIONS)
        ]
        tier2 = Tier2Report(
            dimensions=dims,
            aggregate_score=50.0,
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        output = self._capture_display(tier2)
        assert "3" in output

    def test_long_justification_truncated(self) -> None:
        """Justifications longer than 80 chars are truncated in display."""
        long_just = "A" * 120
        dims = [
            Tier2DimensionResult(
                name=dim,
                group="selection_quality" if i < 4 else "output_quality",
                score=4,
                justification=long_just,
                normalized_score=75.0,
            )
            for i, dim in enumerate(JUDGE_DIMENSIONS)
        ]
        tier2 = Tier2Report(
            dimensions=dims,
            aggregate_score=75.0,
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        output = self._capture_display(tier2)
        # Should not contain the full 120-char string.
        assert long_just not in output
        # Should contain the truncated 80-char prefix.
        assert long_just[:80] in output

    def test_empty_justification_handled(self) -> None:
        """Empty justification does not crash display."""
        dims = [
            Tier2DimensionResult(
                name=dim,
                group="selection_quality" if i < 4 else "output_quality",
                score=4,
                justification="",
                normalized_score=75.0,
            )
            for i, dim in enumerate(JUDGE_DIMENSIONS)
        ]
        tier2 = Tier2Report(
            dimensions=dims,
            aggregate_score=75.0,
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        output = self._capture_display(tier2)
        assert "Tier 2 LLM Judge" in output


# ---------------------------------------------------------------------------
# _run_judge wrapper tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestRunJudgeWrapper:
    """Tests for the _run_judge thin wrapper in cli.py."""

    def test_run_judge_calls_evaluate_tier2(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        tier2 = _make_tier2_report()
        mock_ctx = MagicMock()

        with (
            patch("curator.eval.from_profile_dir", return_value=mock_ctx),
            patch("curator.eval.evaluate_tier1", return_value=_make_report()),
            patch("curator.eval.judge.evaluate_tier2", return_value=tier2) as mock_t2,
            patch("curator.config.CuratorSettings", return_value=MagicMock()),
        ):
            result = runner.invoke(app, ["eval", str(profile_dir), "--judge"])

        assert result.exit_code == 0
        mock_t2.assert_called_once()

    def test_run_judge_passes_client_kwarg(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """_run_judge passes client=None when called from single-profile eval."""
        profile_dir = tmp_path / "test-profile"
        profile_dir.mkdir()

        tier2 = _make_tier2_report()
        mock_ctx = MagicMock()

        with (
            patch("curator.eval.from_profile_dir", return_value=mock_ctx),
            patch("curator.eval.evaluate_tier1", return_value=_make_report()),
            patch("curator.eval.judge.evaluate_tier2", return_value=tier2) as mock_t2,
            patch("curator.config.CuratorSettings", return_value=MagicMock()),
        ):
            runner.invoke(app, ["eval", str(profile_dir), "--judge"])

        call_kwargs = mock_t2.call_args[1]
        assert call_kwargs["client"] is None
