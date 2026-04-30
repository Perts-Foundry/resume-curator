"""Calibration tests for the Tier 2 LLM judge.

Requires a real Anthropic API key and costs ~$1.20 per full run (24 cases).
Not included in default CI — the ``llm`` marker is excluded by ``addopts``
in ``pyproject.toml``, and the ``skipif`` guard handles accidental inclusion
when CI overrides ``addopts`` without providing an API key.

Run manually::

    uv run pytest tests/eval/test_judge_calibration.py -m llm
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anthropic
import httpx
import pytest

from curator.config import CuratorSettings
from curator.eval import from_profile_dir
from curator.eval.golden import (
    GOLDEN_SKIP_METRICS,
    compare_judge_against_golden,
    discover_golden_cases,
    materialize_profile,
    render_golden_pdf,
)
from curator.eval.judge import (
    JUDGE_DIMENSIONS,
    JUDGE_SCORE_MAX,
    JUDGE_SCORE_MIN,
    evaluate_tier2,
)

if TYPE_CHECKING:
    from collections.abc import Generator

pytestmark = [
    pytest.mark.llm,
    pytest.mark.eval,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY")
        and not os.environ.get("CURATOR_ANTHROPIC_API_KEY"),
        reason="No Anthropic API key set",
    ),
    pytest.mark.skipif(
        os.environ.get("CURATOR_ALLOW_API_SPEND", "").lower()
        not in ("true", "1", "yes"),
        reason="CURATOR_ALLOW_API_SPEND not set (prevents surprise charges)",
    ),
]


# Suppress GOLDEN_SKIP_METRICS unused warning — kept for future use.
_ = GOLDEN_SKIP_METRICS


@pytest.fixture(scope="module")
def settings() -> CuratorSettings:
    return CuratorSettings()


@pytest.fixture(scope="module")
def judge_client(
    settings: CuratorSettings,
) -> Generator[anthropic.Anthropic, None, None]:
    """Shared Anthropic client for the calibration test module."""
    client = anthropic.Anthropic(
        api_key=settings.require_api_key(),
        max_retries=settings.api_max_retries,
        timeout=httpx.Timeout(120.0, connect=5.0),
    )
    yield client
    client.close()


@pytest.fixture(scope="module")
def typst_safe_dir() -> Generator[Path, None, None]:
    """Temp directory in home cache for Typst snap compatibility."""
    cache_base = Path.home() / ".cache" / "curator-judge-calibration"
    cache_base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="judge-cal-", dir=cache_base) as tmp:
        yield Path(tmp)


def _materialize_with_pdf(case: Any, base_dir: Path, prefix: str) -> Path:
    """Materialize a golden case and attempt PDF rendering."""
    profile_path = base_dir / f"{prefix}-{case.meta.id}"
    materialize_profile(case, profile_path)
    with contextlib.suppress(FileNotFoundError):
        render_golden_pdf(profile_path)
    return profile_path


class TestJudgeSingleCase:
    """Basic sanity tests on a single golden case."""

    def test_judge_scores_valid_range(
        self,
        settings: CuratorSettings,
        judge_client: anthropic.Anthropic,
        typst_safe_dir: Path,
    ) -> None:
        """All 8 dimension scores are in [1, 5]."""
        cases = discover_golden_cases()
        case = cases[0]

        profile_path = _materialize_with_pdf(case, typst_safe_dir, "single")
        ctx = from_profile_dir(profile_path)
        tier2 = evaluate_tier2(ctx, settings=settings, client=judge_client)

        for d in tier2.dimensions:
            assert JUDGE_SCORE_MIN <= d.score <= JUDGE_SCORE_MAX, (
                f"{d.name}: score {d.score} out of range"
            )

    def test_judge_returns_all_dimensions(
        self,
        settings: CuratorSettings,
        judge_client: anthropic.Anthropic,
        typst_safe_dir: Path,
    ) -> None:
        """Report contains exactly 8 dimensions matching JUDGE_DIMENSIONS."""
        cases = discover_golden_cases()
        case = cases[0]

        profile_path = _materialize_with_pdf(case, typst_safe_dir, "dims")
        ctx = from_profile_dir(profile_path)
        tier2 = evaluate_tier2(ctx, settings=settings, client=judge_client)

        dim_names = tuple(d.name for d in tier2.dimensions)
        assert dim_names == JUDGE_DIMENSIONS


class TestJudgeGoldenBatch:
    """Batch sanity tests across all 24 golden cases."""

    def test_judge_golden_batch_sanity(
        self,
        settings: CuratorSettings,
        judge_client: anthropic.Anthropic,
        typst_safe_dir: Path,
    ) -> None:
        """All 24 cases score without API errors. Tier means sensible."""
        cases = discover_golden_cases()

        tier_scores: dict[str, list[float]] = {
            "strong": [],
            "good": [],
            "moderate": [],
            "poor": [],
        }

        for case in cases:
            profile_path = _materialize_with_pdf(case, typst_safe_dir, "batch")
            ctx = from_profile_dir(profile_path)
            tier2 = evaluate_tier2(ctx, settings=settings, client=judge_client)

            for d in tier2.dimensions:
                assert JUDGE_SCORE_MIN <= d.score <= JUDGE_SCORE_MAX

            tier = case.meta.id.split("-")[0]
            if tier in tier_scores:
                tier_scores[tier].append(tier2.aggregate_score)

        # Loose sanity bounds.
        if tier_scores["strong"]:
            strong_mean = sum(tier_scores["strong"]) / len(tier_scores["strong"])
            assert strong_mean > 50, f"Strong-fit mean {strong_mean:.1f} too low"

        if tier_scores["poor"]:
            poor_mean = sum(tier_scores["poor"]) / len(tier_scores["poor"])
            assert poor_mean < 85, f"Poor-fit mean {poor_mean:.1f} too high"

    def test_human_scores_within_tolerance(
        self,
        settings: CuratorSettings,
        judge_client: anthropic.Anthropic,
        typst_safe_dir: Path,
    ) -> None:
        """No ERROR findings comparing judge against human_scores.

        AR-5 fail-fast: if every case in the calibration set short-circuits
        on judge_version mismatch, the suite has nothing to assert (every
        comparison returns a single skip-WARNING). Treat that state as a
        hard error rather than passing silently.
        """
        from curator.eval.golden import RegressionSeverity
        from curator.eval.judge import JUDGE_VERSION

        cases = discover_golden_cases()

        if all(not c.human_scores for c in cases):
            pytest.skip("human_scores not yet populated")

        errors: list[tuple[str, list[Any]]] = []
        version_mismatches = 0
        compared_cases = 0
        for case in cases:
            if not case.human_scores:
                continue

            profile_path = _materialize_with_pdf(case, typst_safe_dir, "tol")
            ctx = from_profile_dir(profile_path)
            tier2 = evaluate_tier2(ctx, settings=settings, client=judge_client)
            findings = compare_judge_against_golden(tier2, case)
            compared_cases += 1

            case_errors = [
                f for f in findings if f.severity == RegressionSeverity.ERROR
            ]
            if case_errors:
                errors.append((case.meta.id, case_errors))

            # AR-5: count cases that short-circuited on rubric drift so we
            # can detect the all-skipped state below. The short-circuit
            # emits a single WARNING with "judge_version" in the message.
            if any("judge_version" in f.message for f in findings):
                version_mismatches += 1

        if compared_cases > 0 and version_mismatches == compared_cases:
            pytest.fail(
                f"All {compared_cases} calibration cases short-circuited on "
                f"judge_version mismatch (current={JUDGE_VERSION!r}). The "
                "calibration suite has nothing to assert. Recalibrate "
                "tests/eval/golden/*.yaml human_scores under the current "
                "judge_version, then re-stamp meta.judge_version on each "
                "case before running this suite."
            )

        assert not errors, f"{len(errors)} case(s) with ERROR findings: " + ", ".join(
            f"{cid}: {[f.message for f in fs]}" for cid, fs in errors
        )
