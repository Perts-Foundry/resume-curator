"""Resume quality evaluation framework.

Public API for Tier 1 deterministic metrics and Tier 2 LLM judge scoring.
Two convenience loaders provide the ``EvalContext`` needed by both tiers:

- ``from_profile_dir(path)`` — reads disk artifacts for the CLI ``eval`` command.
- ``from_pipeline_result(result, jd_text, settings)`` — extracts in-memory objects.

Planned improvements tracked in TODO.md.

NOTE: Template metrics exercised via render_golden_pdf() default lookup.
NOTE: Portfolio-dependent metrics skipped via GOLDEN_SKIP_METRICS in golden.py.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from curator import default_template_path
from curator.eval.alignment import ALIGNMENT_METRIC_NAMES, evaluate_alignment
from curator.eval.content import evaluate_content
from curator.eval.dates import evaluate_dates
from curator.eval.judge import Tier2Report, evaluate_tier2
from curator.eval.pdf import evaluate_pdf
from curator.eval.report import (
    EvalMetricResult,
    EvalReport,
    PortfolioFitReport,
    build_report,
)
from curator.eval.selection import evaluate_selection
from curator.eval.template import evaluate_template, get_uniform_page_margin_pt
from curator.eval.writing import evaluate_writing
from curator.exceptions import EvalError
from curator.io_utils import MAX_TEXT_SIZE, get_page_count, load_yaml_safe
from curator.models import RENDERABLE_SECTIONS, PortfolioData, ResumeCuration

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from curator.config import CuratorSettings
    from curator.eval.golden import GoldenCase
    from curator.pipeline import PipelineResult

__all__ = [
    "EvalContext",
    "EvalMetricResult",
    "EvalReport",
    "PortfolioFitReport",
    "Tier2Report",
    "evaluate_tier1",
    "evaluate_tier2",
    "from_golden_case",
    "from_pipeline_result",
    "from_profile_dir",
]


@dataclass(frozen=True)
class EvalContext:
    """All inputs needed by Tier 1 metric functions.

    ``source`` records whether the underlying curation came from an API
    call (``"api"``) or deterministic static synthesis (``"static"``).
    Static-mode profiles have no job description, so JD-alignment metrics
    are skipped and the Tier 2 judge is refused (see
    ``evaluate_tier1``/``evaluate_tier2``).
    """

    curation: ResumeCuration
    section_data: dict[str, Any]
    basics: dict[str, Any]
    jd_text: str | None = None
    pdf_path: Path | None = None
    template_path: Path | None = None
    portfolio: PortfolioData | None = None
    max_pages: int = 1
    source: str = "api"
    #: Projection of ``portfolio.work[*].id -> len(highlights)`` computed
    #: once at context-build time. Eval modules that only need authored
    #: highlight counts (today: ``selection.highlight_counts`` clamping)
    #: consume this projection rather than the full ``PortfolioData``,
    #: keeping eval-to-domain coupling minimal. See ``[AR-2]`` (testing
    #: 2026-04-27): the projection precedent applies any time an eval
    #: module needs a single derived quantity from the portfolio.
    work_authored_highlight_counts: Mapping[str, int] = field(default_factory=dict)


def _project_work_authored_highlight_counts(
    portfolio: PortfolioData | None,
) -> dict[str, int]:
    """Compute the work_authored_highlight_counts projection from a portfolio.

    Returns an empty dict when ``portfolio`` is None so callers can
    unconditionally pass the result to ``EvalContext``.
    """
    if portfolio is None:
        return {}
    return {w.id: len(w.highlights) for w in portfolio.work}


def from_profile_dir(
    path: Path,
    *,
    portfolio: PortfolioData | None = None,
    template_path: Path | None = None,
) -> EvalContext:
    """Build an EvalContext from a profile output directory.

    Args:
        path: Profile directory (e.g., ``profiles/2026-03-16-acme-corp/``).
        portfolio: Optional full portfolio data for keyword coverage metrics.
        template_path: Optional path to Typst template for template metrics.

    Raises:
        EvalError: If the directory structure is invalid or data is malformed.
    """
    resolved = path.resolve()

    curated_yaml = resolved / "curated.yaml"
    data_dir = resolved / "data"
    if not curated_yaml.exists():
        msg = f"Missing curated.yaml in {resolved}"
        raise EvalError(msg)
    if not data_dir.is_dir():
        msg = f"Missing data/ directory in {resolved}"
        raise EvalError(msg)

    # Check for old-schema profiles (missing format_version).
    log_path = resolved / "curation_log.json"
    log_data: dict[str, Any] = {}
    if log_path.exists():
        try:
            log_size = log_path.stat().st_size
            if log_size > MAX_TEXT_SIZE:
                msg = f"curation_log.json exceeds size limit: {log_size} bytes"
                raise EvalError(msg)
            log_data = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            msg = f"Cannot read curation_log.json: {e}"
            raise EvalError(msg) from e
        fmt_ver = log_data.get("format_version", "")
        if not fmt_ver or (isinstance(fmt_ver, str) and fmt_ver.startswith("1.")):
            msg = (
                f"Profile at {resolved} uses an old schema format "
                f"(format_version={fmt_ver!r}). "
                "Please regenerate via 'curator curate --jd ...'."
            )
            raise EvalError(msg)
        # format_version >= 2.1 carries explicit source provenance; older
        # 2.0 profiles default to "api" (static mode didn't exist yet).
        source_field = log_data.get("source", "api")
    else:
        source_field = "api"

    # Load curation (with legacy-schema detection).
    try:
        raw = load_yaml_safe(curated_yaml)
        legacy_markers = (
            "reasoning",
            "selected_work",
            "summary_suggestion",
            "selected_education",
            "selected_certificates",
            "selected_volunteer",
            "selected_skills",
            "selected_projects",
        )
        if isinstance(raw, dict):
            present = [k for k in legacy_markers if k in raw]
            if present:
                msg = (
                    f"Profile at {resolved} uses the pre-2026-04 curation schema "
                    f"(legacy fields present: {', '.join(present)}). "
                    "Please regenerate via 'curator curate --jd ...'."
                )
                raise EvalError(msg)
        curation = ResumeCuration.model_validate(raw)
    except (yaml.YAMLError, ValueError, OSError) as e:
        msg = f"Failed to load curated.yaml: {e}"
        raise EvalError(msg) from e

    # Load section data.
    section_data: dict[str, Any] = {}
    for section_name in RENDERABLE_SECTIONS:
        section_path = data_dir / f"{section_name}.yaml"
        if section_path.exists():
            try:
                section_data[section_name] = load_yaml_safe(section_path)
            except (yaml.YAMLError, ValueError, OSError) as e:
                msg = f"Failed to load data/{section_name}.yaml: {e}"
                raise EvalError(msg) from e

    # Load basics.
    basics_path = data_dir / "basics.yaml"
    try:
        basics: dict[str, Any] = load_yaml_safe(basics_path) or {}
    except (yaml.YAMLError, ValueError, OSError) as e:
        from loguru import logger

        logger.warning("Could not load basics.yaml, proceeding with empty: {}", e)
        basics = {}

    # JD text (size-guarded).
    jd_path = resolved / "job_description.txt"
    jd_text: str | None = None
    if jd_path.exists():
        with contextlib.suppress(OSError):
            if jd_path.stat().st_size <= MAX_TEXT_SIZE:
                jd_text = jd_path.read_text(encoding="utf-8").strip() or None

    # PDF path.
    pdf_file = resolved / "resume.pdf"
    pdf_path = pdf_file if pdf_file.exists() else None

    # max_pages inference: PDF reality > log intent > default 1.
    # PDF wins over log because the log records intent at render time and
    # the PDF records what actually came out; eval band selection should
    # follow the rendered artifact, not the requested target. The
    # ``page_count`` metric independently measures intent-vs-reality, so
    # the band-selection signal is decoupled from the convergence signal.
    # Malformed log values (missing, non-int, bool, out-of-range) fall
    # through to the default rather than propagate as nonsense.
    from curator.exceptions import RenderError as _RenderError

    inferred_max_pages: int | None = None
    inference_source = "default"
    if pdf_path is not None:
        with contextlib.suppress(_RenderError):
            inferred_max_pages = get_page_count(pdf_path)
            inference_source = "pdf"
    if inferred_max_pages is None:
        raw_mp = log_data.get("max_pages")
        if (
            isinstance(raw_mp, int)
            and not isinstance(raw_mp, bool)
            and 1 <= raw_mp <= 5
        ):
            inferred_max_pages = raw_mp
            inference_source = "log"
    if inferred_max_pages is None:
        inferred_max_pages = 1

    if inference_source != "pdf":
        from loguru import logger

        logger.info(
            "Inferred max_pages={} from {} for profile {}",
            inferred_max_pages,
            inference_source,
            resolved.name,
        )

    # Template — use provided or fall back to the bundled default.
    if template_path is None:
        candidate = default_template_path()
        if candidate.exists():
            template_path = candidate

    return EvalContext(
        curation=curation,
        section_data=section_data,
        basics=basics,
        jd_text=jd_text,
        pdf_path=pdf_path,
        template_path=template_path,
        portfolio=portfolio,
        source=source_field if isinstance(source_field, str) else "api",
        work_authored_highlight_counts=_project_work_authored_highlight_counts(
            portfolio
        ),
        max_pages=inferred_max_pages,
    )


def from_golden_case(
    case: GoldenCase,
    *,
    pdf_path: Path | None = None,
    template_path: Path | None = None,
) -> EvalContext:
    """Build an EvalContext directly from a GoldenCase.

    Avoids the materialize-to-disk roundtrip for tests and callers that
    only need to exercise data-driven metrics. PDF metrics will return
    WARN/None unless the caller passes ``pdf_path`` from a prior
    ``render_golden_pdf()`` call.

    Args:
        case: The golden case.
        pdf_path: Optional path to a pre-rendered PDF.
        template_path: Optional Typst template path. Defaults to the
            packaged template.

    Raises:
        EvalError: If the golden case's curation does not validate
            against the current ``ResumeCuration`` schema.
    """
    try:
        curation = ResumeCuration.model_validate(case.curation)
    except Exception as e:
        msg = (
            f"Golden case '{case.meta.id}' has stale curation schema "
            f"— update to match current ResumeCuration model: {e}"
        )
        raise EvalError(msg) from e

    if template_path is None:
        candidate = default_template_path()
        if candidate.exists():
            template_path = candidate

    # Golden cases are self-contained without a PortfolioData object, so
    # work_authored_highlight_counts defaults to an empty dict;
    # highlight_counts clamping falls back to position-only bands on
    # golden runs (which is the correct behavior since goldens cannot
    # represent the portfolio's authored highlight count).
    #
    # ``max_pages`` is read from the case meta; cases authored against
    # the long-form rubric carry ``meta.max_pages: 2`` so band selection
    # in ``evaluate_tier1`` matches their geometry. Caller cannot
    # override (the case knows what it is — see AR-10).
    return EvalContext(
        curation=curation,
        section_data=dict(case.section_data),
        basics=dict(case.basics),
        jd_text=case.job_description or None,
        pdf_path=pdf_path,
        template_path=template_path,
        max_pages=case.meta.max_pages,
    )


def from_pipeline_result(
    result: PipelineResult,
    jd_text: str,
    settings: CuratorSettings,
) -> EvalContext:
    """Build an EvalContext from an in-memory pipeline result.

    Args:
        result: Completed pipeline result.
        jd_text: Original job description text.
        settings: Application settings for config values.
    """
    # Read section data from written files.
    section_data: dict[str, Any] = {}
    for section_name in RENDERABLE_SECTIONS:
        file_path = result.render_output.data_files.get(section_name)
        if file_path is not None and file_path.exists():
            section_data[section_name] = load_yaml_safe(file_path)

    # Read basics from written file.
    basics_path = result.render_output.data_files.get("basics")
    basics: dict[str, Any] = {}
    if basics_path is not None and basics_path.exists():
        basics = load_yaml_safe(basics_path) or {}

    # max_pages priority: rendered PDF page count (reality) > settings (intent).
    # Mirrors from_profile_dir's PDF-first chain so band selection follows the
    # rendered artifact across in-memory and on-disk paths. If the trim cascade
    # ran out of iterations and shipped a 3-page PDF on a 2-page budget, eval
    # scores against the long-form rubric to match what actually shipped; the
    # page_count metric independently surfaces the intent-vs-reality gap.
    inferred_max_pages = (
        result.render_output.page_count
        if result.render_output.page_count is not None
        else settings.max_pages
    )

    return EvalContext(
        curation=result.curation.curation,
        section_data=section_data,
        basics=basics,
        jd_text=jd_text,
        pdf_path=result.render_output.pdf_path,
        template_path=settings.template_path,
        portfolio=result.portfolio,
        max_pages=inferred_max_pages,
        source=result.curation.source,
        work_authored_highlight_counts=_project_work_authored_highlight_counts(
            result.portfolio
        ),
    )


def evaluate_tier1(
    ctx: EvalContext,
    *,
    skip_metrics: frozenset[str] = frozenset(),
) -> EvalReport:
    """Run all Tier 1 deterministic metrics and return an EvalReport.

    Args:
        ctx: Evaluation context with all needed inputs.
        skip_metrics: Metric names to exclude from evaluation.
    """
    all_metrics: list[EvalMetricResult] = []

    # Page-budget-aware band selection. SHORT_FORM_BANDS for max_pages
    # <= 1, LONG_FORM_BANDS otherwise. Threaded into Tier 1 metrics that
    # use page-sensitive PASS/WARN ranges (content, selection, pdf).
    from curator.eval.report import bands_for_pages

    bands = bands_for_pages(ctx.max_pages)

    # Content Density.
    all_metrics.extend(
        evaluate_content(ctx.section_data, ctx.basics, bands=bands)
    )

    # Selection Quality.
    all_metrics.extend(
        evaluate_selection(
            ctx.curation,
            ctx.basics,
            section_data=ctx.section_data,
            work_authored_highlight_counts=ctx.work_authored_highlight_counts,
            bands=bands,
        )
    )

    # Writing Quality.
    all_metrics.extend(evaluate_writing(ctx.section_data, ctx.basics, ctx.curation))

    # JD Alignment — skip entirely if no JD text.
    if ctx.jd_text is not None:
        all_metrics.extend(
            evaluate_alignment(
                ctx.jd_text,
                ctx.section_data,
                ctx.basics,
                ctx.curation,
                ctx.portfolio,
            )
        )
    else:
        # No JD text means alignment cannot be computed. For static-mode
        # profiles this is expected (no JD by design), so the skip reason
        # names that explicitly. For API-path profiles missing jd_text we
        # still report WARN because it signals a broken audit trail.
        # Names in ``PORTFOLIO_FIT_METRIC_NAMES`` are marked informational
        # so the placeholder set has the same shape as the JD-present
        # path: jd_match_rate and acronym_expansion_pairs roll into the
        # PortfolioFitReport sidecar instead of the jd_alignment
        # category aggregate. (CR-1 from 2026-04-26 review.)
        from curator.eval.report import PORTFOLIO_FIT_METRIC_NAMES, EvalMetricStatus

        detail = (
            "Alignment metrics skipped: profile is static (no JD by design)"
            if ctx.source == "static"
            else "JD text not available, skipping alignment metrics"
        )
        all_metrics.extend(
            EvalMetricResult(
                name=name,
                category="jd_alignment",
                status=EvalMetricStatus.WARN,
                value=None,
                detail=detail,
                informational=name in PORTFOLIO_FIT_METRIC_NAMES,
            )
            for name in ALIGNMENT_METRIC_NAMES
        )

    # Date & Format Consistency.
    all_metrics.extend(evaluate_dates(ctx.section_data))

    # PDF Output Quality.
    pdf_kwargs: dict[str, Any] = {"max_pages": ctx.max_pages, "bands": bands}
    template_margin = get_uniform_page_margin_pt(ctx.template_path)
    if template_margin is not None:
        pdf_kwargs["page_margin_pt"] = template_margin
    all_metrics.extend(evaluate_pdf(ctx.pdf_path, ctx.basics, **pdf_kwargs))

    # Template Correctness.
    all_metrics.extend(evaluate_template(ctx.template_path))

    # Apply skip_metrics filter.
    if skip_metrics:
        all_metrics = [m for m in all_metrics if m.name not in skip_metrics]

    return build_report(all_metrics)
