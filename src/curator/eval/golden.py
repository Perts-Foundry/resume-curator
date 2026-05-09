"""Golden dataset loading, comparison, and profile materialization.

Provides ``GoldenCase`` Pydantic models for regression testing,
a YAML loader with size guards, comparison logic that detects
regressions, and a profile materializer that writes golden data
to disk for ``from_profile_dir()`` to consume.
"""

from __future__ import annotations

import copy
import enum
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from curator import default_template_path
from curator.exceptions import EvalError
from curator.io_utils import (
    atomic_json_write,
    atomic_text_write,
    atomic_yaml_write,
    compile_typst,
    load_yaml_safe,
)
from curator.models import (
    EMPTY_INTERESTS,
    RENDERABLE_SECTIONS,
    RENDERER_MANAGED_SECTIONS,
)

if TYPE_CHECKING:
    from curator.eval.judge import Tier2Report
    from curator.eval.report import EvalReport

# ---------------------------------------------------------------------------
# Default golden directory — relative to repo root
# ---------------------------------------------------------------------------

GOLDEN_DIR_DEFAULT = (
    Path(__file__).resolve().parent.parent.parent.parent / "tests" / "eval" / "golden"
)

# Default template path — resolved via importlib.resources so editable
# installs and built wheels both find the bundled template.
_DEFAULT_TEMPLATE = default_template_path()

# Metrics that require full portfolio data — not available in golden cases.
# ``keyword_coverage`` scores curation quality (what the curator picked)
# but needs the whole portfolio to compute. ``jd_match_rate`` is now
# ``informational=True`` and automatically excluded from golden baselines
# by the informational filter, so it is not listed here; kept only for
# scored metrics that must be skipped despite their scoring intent.
GOLDEN_SKIP_METRICS: frozenset[str] = frozenset({"keyword_coverage"})

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


GoldenTier = Literal["strong", "good", "moderate", "poor"]
"""Curation-quality tier for a golden case.

Recorded explicitly on each case so callers do not need to parse the
``id`` prefix to group cases by tier.
"""


class GoldenMeta(BaseModel):
    """Metadata for a golden case.

    ``judge_version`` records the ``JUDGE_VERSION`` that was active when
    ``human_scores`` was calibrated. ``compare_judge_against_golden``
    skips comparisons against calibration data from a prior rubric so a
    rubric rewrite does not produce spurious ERROR findings. Legacy
    golden cases without the field are treated as "version unknown" and
    their comparisons also skip (loud WARNING, not a silent pass).
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    eval_schema_version: int
    tier: GoldenTier
    judge_version: str | None = None
    max_pages: int = Field(default=1, ge=1, le=5)
    """Page budget the case was authored against. Selects SHORT_FORM_BANDS
    (1) or LONG_FORM_BANDS (>=2) when the case is loaded via
    ``from_golden_case``. Threaded into ``materialize_profile`` so the
    audit log carries the same value the case meta declares."""


BaselineStatus = Literal["PASS", "WARN", "FAIL"]


class BaselineRange(BaseModel):
    """Numeric range for baseline comparisons (inclusive).

    Optional ``status`` opts a metric in to STATUS_FLIP regression
    detection: when set, the current report's metric status must equal
    the expected status or comparison emits an ERROR finding.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    min: float | None = None
    max: float | None = None
    status: BaselineStatus | None = None

    @model_validator(mode="after")
    def _validate_ordering(self) -> BaselineRange:
        if self.min is not None and self.max is not None and self.min > self.max:
            msg = f"BaselineRange min ({self.min}) must be <= max ({self.max})"
            raise ValueError(msg)
        return self


class GoldenExpected(BaseModel):
    """Expected selection outcomes.

    Uses generic ``must_include``/``must_exclude`` dicts keyed by section
    name (e.g. ``{"work": ["entry-id"], "skills": ["group-id"]}``).
    Scales to all section types without per-section fields.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    must_include: dict[str, list[str]] = Field(default_factory=dict)
    must_exclude: dict[str, list[str]] = Field(default_factory=dict)


class GoldenCase(BaseModel):
    """A golden test case for regression testing.

    Self-contained: embeds the synthetic JD, curation selections,
    section data, and basics so tests can materialize a complete
    profile directory without API calls.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    meta: GoldenMeta
    job_description: str
    expected: GoldenExpected = Field(default_factory=GoldenExpected)
    baselines: dict[str, BaselineRange] = Field(default_factory=dict)
    human_scores: dict[str, float] = Field(default_factory=dict)
    calibration_source: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Provenance for ``human_scores`` (e.g., 'judge_calibration_2026-04-13', "
            "'human-rated', 'mean_of_3_judge_runs'). Audit trail only; not "
            "consumed by comparison logic."
        ),
    )
    curation: dict[str, Any]
    section_data: dict[str, Any]
    basics: dict[str, Any]


# ---------------------------------------------------------------------------
# Comparison result types
# ---------------------------------------------------------------------------


class RegressionSeverity(enum.StrEnum):
    """Severity of a regression finding."""

    ERROR = "error"
    WARNING = "warning"


class RegressionCategory(enum.StrEnum):
    """Category of a regression finding."""

    SCORE_DROP = "score_drop"
    STATUS_FLIP = "status_flip"
    MISSING_ENTRY = "missing_entry"
    EXCLUDED_PRESENT = "excluded_present"
    BASELINE_VIOLATION = "baseline_violation"
    SCHEMA_MISMATCH = "schema_mismatch"
    METRIC_COUNT_MISMATCH = "metric_count_mismatch"


@dataclass(frozen=True)
class RegressionFinding:
    """A single regression finding from golden comparison."""

    severity: RegressionSeverity
    category: RegressionCategory
    message: str


@dataclass(frozen=True)
class GoldenComparisonResult:
    """Result of comparing an EvalReport against a golden case."""

    case_id: str
    passed: bool
    report: EvalReport
    findings: list[RegressionFinding] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_golden_case(path: Path) -> GoldenCase:
    """Load and validate a single golden YAML file.

    Args:
        path: Path to the golden YAML file.

    Returns:
        Validated GoldenCase.

    Raises:
        EvalError: If the file is malformed, oversized, or invalid.
    """
    try:
        raw = load_yaml_safe(path)
        return GoldenCase.model_validate(raw)
    except Exception as e:
        msg = f"Failed to load golden case {path.name}: {e}"
        raise EvalError(msg) from e


def discover_golden_cases(
    golden_dir: Path | None = None,
) -> list[GoldenCase]:
    """Discover and load all golden YAML files from a directory.

    Args:
        golden_dir: Directory containing golden YAML files.
            Defaults to ``tests/eval/golden/``.

    Returns:
        List of validated GoldenCase objects, sorted by meta.id.
        Empty list if the directory does not exist or contains no YAML files.
    """
    directory = golden_dir or GOLDEN_DIR_DEFAULT
    if not directory.is_dir():
        return []

    cases = [load_golden_case(path) for path in sorted(directory.glob("*.yaml"))]
    return sorted(cases, key=lambda c: c.meta.id)


# ---------------------------------------------------------------------------
# Profile materializer
# ---------------------------------------------------------------------------


def materialize_profile(golden: GoldenCase, target_dir: Path) -> Path:
    """Write a golden case's data to disk as a profile directory.

    Creates the directory structure that ``from_profile_dir()`` expects:
    ``curated.yaml``, ``job_description.txt``, ``curation_log.json``,
    and ``data/*.yaml`` for all sections.

    Args:
        golden: The golden case to materialize.
        target_dir: Target directory (created if it doesn't exist).

    Returns:
        The target directory path.

    Raises:
        EvalError: If the curation dict fails ResumeCuration validation
            (indicates the golden case's schema is stale).
    """
    from curator.models import ResumeCuration

    # Validate curation schema before writing.
    try:
        ResumeCuration.model_validate(golden.curation)
    except Exception as e:
        msg = (
            f"Golden case '{golden.meta.id}' has stale curation schema "
            f"— update to match current ResumeCuration model: {e}"
        )
        raise EvalError(msg) from e

    # Create directory structure.
    target_dir.mkdir(parents=True, exist_ok=True)
    data_dir = target_dir / "data"
    data_dir.mkdir(exist_ok=True)

    # Write curated.yaml.
    atomic_yaml_write(target_dir / "curated.yaml", golden.curation)

    # Write job_description.txt.
    atomic_text_write(target_dir / "job_description.txt", golden.job_description)

    # Write curation_log.json (required by from_profile_dir for schema check).
    # Format 2.3 carries max_pages so band-selection on materialized
    # goldens picks the rubric the case was authored against.
    atomic_json_write(
        target_dir / "curation_log.json",
        {
            "format_version": "2.3",
            "max_pages": golden.meta.max_pages,
        },
    )

    # Write basics.
    atomic_yaml_write(data_dir / "basics.yaml", golden.basics)

    # Write all renderable sections (empty list/dict for missing).
    for section_name in RENDERABLE_SECTIONS:
        section_path = data_dir / f"{section_name}.yaml"
        if section_name in RENDERER_MANAGED_SECTIONS:
            data = golden.section_data.get(section_name, copy.deepcopy(EMPTY_INTERESTS))
        else:
            data = golden.section_data.get(section_name, [])
        atomic_yaml_write(section_path, data)

    return target_dir


# ---------------------------------------------------------------------------
# PDF rendering for golden profiles
# ---------------------------------------------------------------------------


def render_golden_pdf(
    profile_dir: Path,
    template_path: Path | None = None,
) -> Path:
    """Render a PDF from a materialized golden profile.

    Writes ``layout.yaml``, copies the Typst template into the profile
    directory, and compiles to PDF via Typst.

    Must be called after :func:`materialize_profile`.

    Args:
        profile_dir: Path to a materialized profile directory.
        template_path: Path to the ``.typ`` template. Defaults to
            ``templates/curated.typ`` in the project root.

    Returns:
        Path to the generated ``resume.pdf``.

    Raises:
        FileNotFoundError: If Typst is not installed (callers handle
            graceful degradation).
        subprocess.TimeoutExpired: If Typst compilation hangs.
        EvalError: If curated.yaml is missing/invalid or Typst compilation
            fails.
    """
    template = template_path or _DEFAULT_TEMPLATE
    if not template.exists():
        msg = f"Template not found: {template}"
        raise EvalError(msg)

    # Write layout.yaml with selectable sections + interests appended.
    layout_data = {"section_order": list(RENDERABLE_SECTIONS)}
    atomic_yaml_write(profile_dir / "layout.yaml", layout_data)

    # Copy template into profile dir (Typst --root sandbox requires it).
    local_template = profile_dir / template.name
    shutil.copy2(template, local_template)

    # Compile PDF.
    pdf_path = profile_dir / "resume.pdf"
    try:
        compile_typst(
            root_dir=profile_dir,
            template_path=local_template,
            output_path=pdf_path,
        )
    except FileNotFoundError:
        raise
    except subprocess.TimeoutExpired:
        raise
    except Exception as e:
        msg = f"Golden PDF rendering failed for {profile_dir.name}: {e}"
        raise EvalError(msg) from e

    return pdf_path


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------


def compare_against_golden(
    report: EvalReport,
    golden: GoldenCase,
) -> GoldenComparisonResult:
    """Compare an EvalReport against a golden case's expectations.

    Args:
        report: The eval report to compare.
        golden: The golden case with baselines and expected selections.

    Returns:
        GoldenComparisonResult with pass/fail and detailed findings.
    """
    findings: list[RegressionFinding] = []

    # --- Schema version check ---
    # Major-version mismatch (different integer) is an ERROR: the JSON
    # shape may have shifted, baselines may not be comparable, and
    # downstream tooling that reads the JSON keys may break. WARNING
    # was the prior behavior; SA-3 (2026-04-26 review) upgraded major
    # mismatches to ERROR so the schema-version contract is enforced
    # rather than advisory. See architecture.md "Versioning Policy".
    if report.eval_schema_version != golden.meta.eval_schema_version:
        findings.append(
            RegressionFinding(
                severity=RegressionSeverity.ERROR,
                category=RegressionCategory.SCHEMA_MISMATCH,
                message=(
                    f"Schema version mismatch: report={report.eval_schema_version}, "
                    f"golden={golden.meta.eval_schema_version}. "
                    "Re-stamp the golden YAML's eval_schema_version once "
                    "baselines are confirmed compatible, or regenerate."
                ),
            )
        )

    # --- Aggregate score baseline ---
    if "aggregate_score" in golden.baselines:
        baseline = golden.baselines["aggregate_score"]
        _check_baseline_range(
            findings,
            "aggregate_score",
            report.aggregate_score,
            baseline,
            error_on_low=True,
        )

    # --- Per-metric baseline checks ---
    report_metrics = {m.name: m for m in report.metrics}
    for metric_name, baseline in golden.baselines.items():
        if metric_name == "aggregate_score":
            continue
        metric = report_metrics.get(metric_name)
        if metric is None:
            # Baseline references a metric not in the report (stale).
            findings.append(
                RegressionFinding(
                    severity=RegressionSeverity.ERROR,
                    category=RegressionCategory.METRIC_COUNT_MISMATCH,
                    message=f"Baseline metric '{metric_name}' not found in report",
                )
            )
            continue
        # Value range check (exclude bool — subclass of int in Python).
        if isinstance(metric.value, (int, float)) and not isinstance(
            metric.value, bool
        ):
            _check_baseline_range(findings, metric_name, metric.value, baseline)

        # Status flip check (opt-in via baseline.status).
        if baseline.status is not None and metric.status.name != baseline.status:
            findings.append(
                RegressionFinding(
                    severity=RegressionSeverity.ERROR,
                    category=RegressionCategory.STATUS_FLIP,
                    message=(
                        f"{metric_name}: status flipped from {baseline.status} "
                        f"(expected) to {metric.status.name} (current)"
                    ),
                )
            )

    # --- New metrics not in baselines (informational) ---
    # Only emit when baselines have been calibrated (more than just aggregate_score).
    baseline_names = set(golden.baselines.keys()) - {"aggregate_score"}
    if len(baseline_names) >= 5:
        findings.extend(
            RegressionFinding(
                severity=RegressionSeverity.WARNING,
                category=RegressionCategory.METRIC_COUNT_MISMATCH,
                message=f"New metric '{metric.name}' not in golden baselines",
            )
            for metric in report.metrics
            if metric.name not in baseline_names
        )

    # --- Selection expectations ---
    _check_expected_selections(findings, golden)

    # Determine pass/fail.
    has_errors = any(f.severity == RegressionSeverity.ERROR for f in findings)
    return GoldenComparisonResult(
        case_id=golden.meta.id,
        passed=not has_errors,
        report=report,
        findings=findings,
    )


def _check_baseline_range(
    findings: list[RegressionFinding],
    name: str,
    value: float,
    baseline: BaselineRange,
    *,
    error_on_low: bool = False,
) -> None:
    """Check if a value falls within a baseline range (inclusive)."""
    if baseline.min is not None and value < baseline.min:
        findings.append(
            RegressionFinding(
                severity=RegressionSeverity.ERROR
                if error_on_low
                else RegressionSeverity.WARNING,
                category=RegressionCategory.SCORE_DROP
                if error_on_low
                else RegressionCategory.BASELINE_VIOLATION,
                message=f"{name}: {value:.2f} below minimum {baseline.min}",
            )
        )
    if baseline.max is not None and value > baseline.max:
        # Exceeding max is direction-agnostic — for "lower is better" metrics
        # (counts of bad things) it indicates regression, for "higher is
        # better" it indicates improvement. Emit a WARNING so calibrators
        # can decide whether to retune the baseline range.
        findings.append(
            RegressionFinding(
                severity=RegressionSeverity.WARNING,
                category=RegressionCategory.BASELINE_VIOLATION,
                message=f"{name}: {value:.2f} above maximum {baseline.max}",
            )
        )


def _check_expected_selections(
    findings: list[RegressionFinding],
    golden: GoldenCase,
) -> None:
    """Check must_include and must_exclude expectations against curation."""
    curation = golden.curation

    # Build lookup of selected/ranked IDs per section.
    selected_ids: dict[str, set[str]] = {}

    # Work entries: all portfolio entries are always rendered under the new
    # schema (work_highlights). Presence means ranking, not selection.
    selected_ids["work"] = {
        w["work_id"] for w in curation.get("work_highlights", []) if isinstance(w, dict)
    }

    # Skills have nested structure.
    selected_ids["skills"] = {
        s["skill_id"] for s in curation.get("skills", []) if isinstance(s, dict)
    }

    # Projects are a simple ID list.
    proj_val = curation.get("projects", [])
    if isinstance(proj_val, list):
        selected_ids["projects"] = {item for item in proj_val if isinstance(item, str)}

    # Check must_include (skills and projects only; work entries are always
    # present under the new scope, and education/certs are renderer-managed).
    for section, required_ids in golden.expected.must_include.items():
        if section in ("work", "education", "certificates"):
            continue
        actual = selected_ids.get(section, set())
        findings.extend(
            RegressionFinding(
                severity=RegressionSeverity.ERROR,
                category=RegressionCategory.MISSING_ENTRY,
                message=f"{section}: required entry '{eid}' not selected",
            )
            for eid in required_ids
            if eid not in actual
        )

    # Check must_exclude (skip work; all work entries are always present
    # under the new schema). Also skip education/certificates.
    for section, excluded_ids in golden.expected.must_exclude.items():
        if section in ("work", "education", "certificates"):
            continue
        actual = selected_ids.get(section, set())
        findings.extend(
            RegressionFinding(
                severity=RegressionSeverity.ERROR,
                category=RegressionCategory.EXCLUDED_PRESENT,
                message=f"{section}: excluded entry '{eid}' is selected",
            )
            for eid in excluded_ids
            if eid in actual
        )


# ---------------------------------------------------------------------------
# Tier 2 judge comparison
# ---------------------------------------------------------------------------

#: Default tolerance thresholds for judge-human score comparison.
#: WARNING at diff > 1 for investigation; ERROR at diff > 2 fails the test.
_JUDGE_DEFAULT_WARN_TOLERANCE: int = 1
_JUDGE_DEFAULT_ERROR_TOLERANCE: int = 2

#: Per-dimension tolerance overrides as ``(warn, error)`` tuples.
#: ``section_selection`` is structural (selected vs not) and shouldn't
#: drift much across runs; ``overall_impression`` is holistic and noisier
#: by nature, so a wider band reduces false-positive ERRORs.
_JUDGE_DIMENSION_TOLERANCES: dict[str, tuple[int, int]] = {
    "section_selection": (0, 1),
    "overall_impression": (1, 3),
}


def _judge_tolerances(dim_name: str) -> tuple[int, int]:
    """Return ``(warn, error)`` tolerance for a judge dimension."""
    return _JUDGE_DIMENSION_TOLERANCES.get(
        dim_name,
        (_JUDGE_DEFAULT_WARN_TOLERANCE, _JUDGE_DEFAULT_ERROR_TOLERANCE),
    )


def compare_judge_against_golden(
    tier2: Tier2Report,
    golden: GoldenCase,
) -> list[RegressionFinding]:
    """Compare Tier 2 judge scores against human_scores in a golden case.

    Returns an empty list if ``golden.human_scores`` is empty (no false
    positives before calibration). Two-tier tolerance, configurable
    per dimension via ``_JUDGE_DIMENSION_TOLERANCES``:

    - WARNING when ``abs(judge - human) > warn_tolerance``
    - ERROR when ``abs(judge - human) > error_tolerance``

    Args:
        tier2: Tier 2 judge report from ``evaluate_tier2()``.
        golden: Golden case with optional ``human_scores``.

    Returns:
        List of regression findings (may be empty).
    """
    if not golden.human_scores:
        return []

    # Rubric-drift short-circuit: if the golden case was calibrated
    # against a different JUDGE_VERSION (or the field is missing on a
    # legacy case), human_scores are semantically incomparable. Emit a
    # loud WARNING and return early so a rubric rewrite does not produce
    # a storm of ERROR findings that hide real regressions.
    if golden.meta.judge_version != tier2.judge_version:
        from loguru import logger

        logger.warning(
            "Skipping human_scores comparison for {}: judge_version "
            "mismatch (golden={!r}, current={!r}). Recalibrate human_scores "
            "under the current rubric to re-enable comparison.",
            golden.meta.id,
            golden.meta.judge_version,
            tier2.judge_version,
        )
        return [
            RegressionFinding(
                severity=RegressionSeverity.WARNING,
                category=RegressionCategory.METRIC_COUNT_MISMATCH,
                message=(
                    f"human_scores calibrated against judge_version="
                    f"{golden.meta.judge_version!r}; current is "
                    f"{tier2.judge_version!r}. Comparison skipped. "
                    "Recalibrate to re-enable."
                ),
            )
        ]

    findings: list[RegressionFinding] = []
    dim_scores = {d.name: d.score for d in tier2.dimensions}

    for dim_name, human_score in golden.human_scores.items():
        judge_score = dim_scores.get(dim_name)
        if judge_score is None:
            findings.append(
                RegressionFinding(
                    severity=RegressionSeverity.WARNING,
                    category=RegressionCategory.METRIC_COUNT_MISMATCH,
                    message=(
                        f"human_scores has '{dim_name}' but judge did not score it"
                    ),
                )
            )
            continue

        warn_tol, err_tol = _judge_tolerances(dim_name)
        diff = abs(judge_score - human_score)
        if diff > err_tol:
            findings.append(
                RegressionFinding(
                    severity=RegressionSeverity.ERROR,
                    category=RegressionCategory.BASELINE_VIOLATION,
                    message=(
                        f"{dim_name}: judge={judge_score}, human={human_score:.1f} "
                        f"(diff={diff:.1f}, error_tolerance={err_tol})"
                    ),
                )
            )
        elif diff > warn_tol:
            findings.append(
                RegressionFinding(
                    severity=RegressionSeverity.WARNING,
                    category=RegressionCategory.BASELINE_VIOLATION,
                    message=(
                        f"{dim_name}: judge={judge_score}, human={human_score:.1f} "
                        f"(diff={diff:.1f}, warn_tolerance={warn_tol})"
                    ),
                )
            )

    return findings
