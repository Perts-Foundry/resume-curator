"""Parametrized golden dataset regression tests.

Each golden YAML in ``tests/eval/golden/`` is loaded and used to:
1. Materialize a profile directory (no API calls).
2. Render a PDF via Typst (exercises PDF output metrics).
3. Run the full Tier 1 evaluation suite (skipping portfolio-dependent metrics).
4. Compare results against golden expectations.
5. Assert no ERROR-severity regressions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from curator.eval import evaluate_tier1, from_profile_dir
from curator.eval.golden import (
    GOLDEN_SKIP_METRICS,
    GoldenCase,
    RegressionSeverity,
    compare_against_golden,
    materialize_profile,
    render_golden_pdf,
)
from tests.conftest import TYPST_AVAILABLE

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [
    pytest.mark.golden,
    pytest.mark.eval,
    pytest.mark.skipif(not TYPST_AVAILABLE, reason="Typst not installed"),
]


def test_golden_no_error_regressions(
    golden_case: GoldenCase,
    typst_safe_dir: Path,
) -> None:
    """Golden case must produce no ERROR-severity findings."""
    work_dir = typst_safe_dir / golden_case.meta.id

    # Materialize profile directory from golden data.
    profile_dir = materialize_profile(golden_case, work_dir)

    # Render PDF via Typst (exercises all 11 PDF output metrics).
    render_golden_pdf(profile_dir)

    # Load the materialized profile and run Tier 1 evaluation.
    # Skip portfolio-dependent alignment metrics (no portfolio in golden cases).
    ctx = from_profile_dir(profile_dir)
    report = evaluate_tier1(ctx, skip_metrics=GOLDEN_SKIP_METRICS)

    # Compare against golden expectations.
    result = compare_against_golden(report, golden_case)

    # Collect ERROR findings for assertion message.
    errors = [f for f in result.findings if f.severity == RegressionSeverity.ERROR]
    error_msgs = "\n".join(f"  [{f.category}] {f.message}" for f in errors)

    assert result.passed, (
        f"Golden case '{golden_case.meta.id}' has {len(errors)} ERROR finding(s):\n"
        f"{error_msgs}\n"
        f"Aggregate score: {report.aggregate_score:.1f} ({report.status.name})"
    )
