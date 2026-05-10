"""Score aggregation, thresholds, and evaluation report."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from curator.page_caps import _caps_for_pages
from curator.rules import CATEGORY_WEIGHTS, SCORE_PASS_THRESHOLD, SCORE_WARN_THRESHOLD

EVAL_SCHEMA_VERSION: int = 4

type EvalMetricValue = (
    int | float | str | bool | None | list[Any] | dict[str, Any] | tuple[Any, ...]
)
"""Permitted runtime types for ``EvalMetricResult.value``.

Metrics return primitives (counts, ratios, parsed strings, booleans),
``None`` when a value cannot be determined, or container types for
structured outputs (e.g., parsed margins dict, font family list).
"""


class EvalMetricStatus(enum.IntEnum):
    """Evaluation result status. IntEnum enables arithmetic in aggregation."""

    FAIL = 0
    WARN = 1
    PASS = 2


@dataclass(frozen=True)
class EvalBands:
    """Page-budget-aware PASS/WARN bands for Tier 1 metrics.

    A long-form rubric (``LONG_FORM_BANDS``) and a short-form rubric
    (``SHORT_FORM_BANDS``) are selected by ``bands_for_pages(max_pages)``
    so 1-page and multi-page resumes score against geometry-appropriate
    thresholds. ``EvalBands`` is threaded through ``evaluate_content``,
    ``evaluate_selection``, and ``evaluate_pdf`` from ``evaluate_tier1``
    by way of the ``EvalContext.max_pages`` value.

    Per-bullet metrics (``bullet_word_count_*``) are intentionally equal
    across both forms: bullet length is a per-bullet quality signal, not
    a per-page volume signal.
    """

    # Content density (eval/content.py)
    word_count_pass: tuple[int, int]
    word_count_warn: tuple[int, int]
    bullet_word_count_pass: tuple[int, int]
    bullet_word_count_warn: tuple[int, int]
    # Selection quality (eval/selection.py).
    #
    # ``work_position_floors`` is sourced from the renderer's
    # ``_caps_for_pages`` so the eval rubric and the renderer cascade
    # cannot drift out of agreement on per-position highlight counts.
    # Selection metrics derive position bands from this tuple directly.
    work_position_floors: tuple[int, ...]
    total_highlight_count_pass: tuple[int, int]
    total_highlight_count_warn: tuple[int, int]
    skills_keyword_count_pass: tuple[int, int]
    skills_keyword_count_warn: tuple[int, int]
    # PDF output quality (eval/pdf.py)
    whitespace_ratio_pass: tuple[float, float]
    whitespace_ratio_warn: tuple[float, float]


SHORT_FORM_BANDS = EvalBands(
    word_count_pass=(475, 700),
    word_count_warn=(400, 800),
    bullet_word_count_pass=(8, 35),
    bullet_word_count_warn=(5, 40),
    work_position_floors=_caps_for_pages(1).work_position_floors,
    total_highlight_count_pass=(6, 25),
    total_highlight_count_warn=(4, 30),
    skills_keyword_count_pass=(20, 70),
    skills_keyword_count_warn=(10, 90),
    whitespace_ratio_pass=(0.55, 0.75),
    whitespace_ratio_warn=(0.45, 0.80),
)
"""1-page rubric. Preserves the band values that were hardcoded prior
to the page-budget-aware refactor (2026-05-09)."""


LONG_FORM_BANDS = EvalBands(
    word_count_pass=(900, 1400),
    word_count_warn=(750, 1600),
    bullet_word_count_pass=(8, 35),
    bullet_word_count_warn=(5, 40),
    work_position_floors=_caps_for_pages(2).work_position_floors,
    # Pre-emptively widened to accommodate the new graduated 2-page
    # floors (8+6+6+2+2 = 24 minimum guaranteed by the renderer).
    total_highlight_count_pass=(20, 38),
    total_highlight_count_warn=(15, 45),
    skills_keyword_count_pass=(35, 110),
    skills_keyword_count_warn=(20, 140),
    whitespace_ratio_pass=(0.50, 0.72),
    whitespace_ratio_warn=(0.40, 0.78),
)
"""2+-page rubric. Numeric bands are calibrated against the long-form
goldens added alongside this rubric; tighten in lockstep when adding new
long-form goldens. ``work_position_floors`` is shared with the renderer
so older-role bullet expectations stay self-consistent."""


def bands_for_pages(max_pages: int) -> EvalBands:
    """Return the eval band profile for a given page budget.

    Plateaus at ``max_pages >= 2`` today; future executive-CV calibration
    may add a finer profile (``EXEC_FORM_BANDS``) for ``max_pages >= 4``
    (the renderer now scales 2-vs-3+ asymmetrically while the eval still
    treats them as one).
    """
    return SHORT_FORM_BANDS if max_pages <= 1 else LONG_FORM_BANDS


@dataclass(frozen=True)
class EvalMetricResult:
    """Result of a single evaluation metric.

    ``informational=True`` marks a metric whose status and value are
    useful context (e.g., portfolio-JD fit signals, deferred-detection
    stubs) but should NOT drive category aggregation. Informational
    metrics remain visible in ``metrics`` and the serialized output, and
    are collected into ``PortfolioFitReport`` when they measure
    portfolio-fit. ``score_category`` excludes them from aggregation.
    """

    name: str
    category: str
    status: EvalMetricStatus
    value: EvalMetricValue
    detail: str = ""
    weight: float = 1.0
    informational: bool = False


@dataclass(frozen=True)
class CategoryScore:
    """Aggregated score for one metric category."""

    name: str
    score: float  # 0-100
    status: EvalMetricStatus
    weight: float
    metrics: list[EvalMetricResult] = field(default_factory=list)


@dataclass(frozen=True)
class PortfolioFitReport:
    """Sidecar report surfacing portfolio-JD fit signals.

    Collects all ``informational=True`` metrics whose semantics are
    "portfolio covers the JD's requirements" rather than "curator used
    the portfolio well." Aggregates them into a separate score so the
    user can see at a glance whether the candidate's career matches the
    JD, independent of how the resume was curated.

    Today's members (Tier 1): ``jd_match_rate``,
    ``acronym_expansion_pairs``. Add signals here as they land
    (candidates tracked in TODO.md).
    """

    metrics: list[EvalMetricResult]
    aggregate_score: float  # 0-100
    status: EvalMetricStatus

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report to a JSON-compatible dict."""
        return {
            "aggregate_score": round(self.aggregate_score, 2),
            "status": self.status.name,
            "metrics": [
                {
                    "name": m.name,
                    "category": m.category,
                    "status": m.status.name,
                    "value": m.value,
                    "detail": m.detail,
                }
                for m in self.metrics
            ],
        }


#: Names of informational metrics that count as "portfolio-JD fit"
#: signals and should be rolled into ``PortfolioFitReport``. Add to this
#: set when a new portfolio-fit metric is introduced. Stub/deferred
#: informational metrics (e.g., ``font_embedding_valid``) are NOT
#: portfolio-fit and do not go here.
PORTFOLIO_FIT_METRIC_NAMES: frozenset[str] = frozenset(
    {"jd_match_rate", "acronym_expansion_pairs"}
)


#: Thresholds for the portfolio-fit sidecar. These are intentionally
#: looser than the general ``status_from_score`` thresholds (PASS=85,
#: WARN=75): the sidecar reports portfolio coverage of JD signals, which
#: is rarely 100% on real-world JDs (the typical strong-fit case has
#: jd_match_rate=1-5% because JDs are dense and the portfolio is bounded).
#: Tighter thresholds here would stamp every case FAIL and lose signal
#: differentiation. Adjust if more portfolio-fit metrics land. CR-9.
_PORTFOLIO_FIT_PASS_THRESHOLD: float = 100.0
_PORTFOLIO_FIT_WARN_THRESHOLD: float = 50.0


def _portfolio_fit_status(score: float) -> EvalMetricStatus:
    """Map a 0-100 portfolio-fit score to a status using sidecar thresholds."""
    if score >= _PORTFOLIO_FIT_PASS_THRESHOLD:
        return EvalMetricStatus.PASS
    if score >= _PORTFOLIO_FIT_WARN_THRESHOLD:
        return EvalMetricStatus.WARN
    return EvalMetricStatus.FAIL


def build_portfolio_fit_report(
    metrics: list[EvalMetricResult],
) -> PortfolioFitReport:
    """Extract portfolio-fit metrics from ``metrics`` and score them."""
    fit_metrics = [
        m for m in metrics if m.informational and m.name in PORTFOLIO_FIT_METRIC_NAMES
    ]
    if not fit_metrics:
        return PortfolioFitReport(
            metrics=[], aggregate_score=100.0, status=EvalMetricStatus.PASS
        )
    # Equal-weight mean across status values; mirrors score_category
    # semantics but informational metrics are excluded there, so we
    # compute the score here directly.
    weighted_sum = sum(m.status for m in fit_metrics)
    max_possible = len(fit_metrics) * EvalMetricStatus.PASS
    score = (weighted_sum / max_possible) * 100 if max_possible else 100.0
    return PortfolioFitReport(
        metrics=fit_metrics,
        aggregate_score=score,
        status=_portfolio_fit_status(score),
    )


@dataclass(frozen=True)
class EvalReport:
    """Complete Tier 1 evaluation report."""

    metrics: list[EvalMetricResult]
    categories: list[CategoryScore]
    aggregate_score: float  # 0-100
    status: EvalMetricStatus
    eval_schema_version: int = EVAL_SCHEMA_VERSION
    portfolio_fit: PortfolioFitReport | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report to a JSON-compatible dict.

        Status values are serialized as name strings ("PASS"/"WARN"/"FAIL")
        for readability and JSON compatibility.
        """
        return {
            "eval_schema_version": self.eval_schema_version,
            "aggregate_score": round(self.aggregate_score, 2),
            "status": self.status.name,
            "categories": [
                {
                    "name": c.name,
                    "score": round(c.score, 2),
                    "status": c.status.name,
                    "weight": c.weight,
                }
                for c in self.categories
            ],
            "metrics": [
                {
                    "name": m.name,
                    "category": m.category,
                    "status": m.status.name,
                    "value": m.value,
                    "detail": m.detail,
                    "weight": m.weight,
                    "informational": m.informational,
                }
                for m in self.metrics
            ],
            "portfolio_fit": (
                self.portfolio_fit.to_dict() if self.portfolio_fit else None
            ),
        }


def status_from_score(score: float) -> EvalMetricStatus:
    """Map a 0-100 score to a status using configured thresholds."""
    if score >= SCORE_PASS_THRESHOLD:
        return EvalMetricStatus.PASS
    if score >= SCORE_WARN_THRESHOLD:
        return EvalMetricStatus.WARN
    return EvalMetricStatus.FAIL


def score_category(metrics: list[EvalMetricResult]) -> float:
    """Compute weighted score for a category (0-100).

    Informational metrics (``informational=True``) are excluded from the
    aggregate; they surface in the metrics list but do not drive scoring.
    """
    scored = [m for m in metrics if not m.informational]
    if not scored:
        return 100.0
    total_weight = sum(m.weight for m in scored)
    if total_weight == 0:
        return 100.0
    weighted_sum = sum(m.status * m.weight for m in scored)
    # Normalize: max status is PASS=2
    return (weighted_sum / (total_weight * EvalMetricStatus.PASS)) * 100


def build_report(
    metrics: list[EvalMetricResult],
    category_weights: dict[str, float] | None = None,
) -> EvalReport:
    """Build an EvalReport from a flat list of metric results."""
    weights = category_weights or CATEGORY_WEIGHTS

    # Group by category.
    by_category: dict[str, list[EvalMetricResult]] = {}
    for m in metrics:
        by_category.setdefault(m.category, []).append(m)

    categories: list[CategoryScore] = []
    for cat_name, cat_metrics in by_category.items():
        cat_score = score_category(cat_metrics)
        cat_status = status_from_score(cat_score)
        cat_weight = weights.get(cat_name, 0.0)
        categories.append(
            CategoryScore(
                name=cat_name,
                score=cat_score,
                status=cat_status,
                weight=cat_weight,
                metrics=cat_metrics,
            )
        )

    # Weighted aggregate across categories.
    total_weight = sum(c.weight for c in categories)
    if total_weight > 0:
        aggregate = sum(c.score * c.weight for c in categories) / total_weight
    else:
        aggregate = 0.0

    return EvalReport(
        metrics=metrics,
        categories=categories,
        aggregate_score=aggregate,
        status=status_from_score(aggregate),
        portfolio_fit=build_portfolio_fit_report(metrics),
    )
