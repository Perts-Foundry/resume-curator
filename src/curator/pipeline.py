"""Pipeline orchestration for resume curation.

Coordinates two end-to-end paths:

- ``run_pipeline``: load portfolio, call Claude once, render a PDF with
  deterministic page-fit trimming.
- ``run_static_pipeline``: load portfolio, synthesize a deterministic
  ``CurationResult`` from portfolio data (no API call), render a PDF with
  the same trim loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from curator.client import CuratorClient
from curator.loader import load_portfolio
from curator.renderer import render
from curator.static_mode import build_static_result

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from curator.client import CurationResult
    from curator.config import CuratorSettings
    from curator.models import PortfolioData
    from curator.renderer import RenderOutput


@dataclass(frozen=True)
class PipelineResult:
    """Result of a full pipeline run (API or static).

    The API path calls the model exactly once; the static path calls the
    model zero times. Page-fitting is handled by the renderer's
    deterministic trim loop in both cases.
    """

    curation: CurationResult
    render_output: RenderOutput
    portfolio: PortfolioData
    skip_pdf: bool
    page_count: int | None
    converged: bool
    total_input_tokens: int
    total_output_tokens: int
    trim_log: list[str] = field(default_factory=list)
    published_paths: list[Path] | None = None
    """Destinations written by the publish step when ``publish_to`` is set on
    the pipeline call; ``None`` when ``--publish`` was not used. Ordered as
    in :data:`curator.renderer.RENDER_PUBLISH_FILENAMES`."""


def _maybe_publish(
    render_output: RenderOutput,
    publish_to: Path | None,
    on_status: Callable[[str], None],
) -> list[Path] | None:
    """Copy upload-ready artifacts to ``publish_to`` if set.

    Imported lazily so the common (non-publish) pipeline path doesn't pay
    the import cost of :mod:`curator.publish` (which imports
    :mod:`shutil`). Returns ``None`` when publishing is disabled so the
    pipeline result can distinguish "didn't publish" from "published
    zero files".
    """
    if publish_to is None:
        return None
    from curator.publish import publish_artifacts

    on_status(f"Publishing to {publish_to.expanduser()}...")
    paths = publish_artifacts(render_output.profile_dir, publish_to)
    on_status(f"Published {len(paths)} file(s)")
    return paths


def _summarize_pipeline_result(
    *,
    curation: CurationResult,
    render_output: RenderOutput,
    portfolio: PortfolioData,
    skip_pdf: bool,
    settings: CuratorSettings,
    on_status: Callable[[str], None],
    published_paths: list[Path] | None = None,
) -> PipelineResult:
    """Assemble a ``PipelineResult`` and emit convergence logs.

    Shared between ``run_pipeline`` and ``run_static_pipeline`` so both paths
    produce identical audit output and convergence reporting.
    """
    page_count = render_output.page_count
    converged = True
    if not skip_pdf and page_count is not None:
        converged = page_count <= settings.max_pages

        if render_output.trim_log:
            on_status(
                f"Trimmed {len(render_output.trim_log)} item(s) to fit "
                f"{page_count} page(s)"
            )

    if converged:
        logger.info(
            "Pipeline complete: {} page(s), {} trim(s) (tokens: {} in, {} out)",
            page_count,
            len(render_output.trim_log),
            curation.input_tokens,
            curation.output_tokens,
        )
    else:
        logger.warning(
            "Page count {} exceeds target {} after {} trim(s) (tokens: {} in, {} out)",
            page_count,
            settings.max_pages,
            len(render_output.trim_log),
            curation.input_tokens,
            curation.output_tokens,
        )

    # Paste-ready text surfaces first because it's the headline deliverable
    # for the user's email/web-form workflow; the PDF is the attached
    # version. Surfacing the .txt second would invite the user to copy
    # from the PDF (the workflow we're trying to replace).
    if render_output.cover_letter_txt_path is not None:
        on_status(
            f"Cover letter paste-ready: {render_output.cover_letter_txt_path.name}"
        )
    if render_output.cover_letter_pdf_path is not None:
        on_status(f"Cover letter generated: {render_output.cover_letter_pdf_path.name}")
    elif render_output.cover_letter_yaml_path is not None:
        on_status(
            f"Cover letter data written: {render_output.cover_letter_yaml_path.name}"
        )

    return PipelineResult(
        curation=curation,
        render_output=render_output,
        portfolio=portfolio,
        skip_pdf=skip_pdf,
        page_count=page_count,
        converged=converged,
        total_input_tokens=curation.input_tokens,
        total_output_tokens=curation.output_tokens,
        trim_log=render_output.trim_log,
        published_paths=published_paths,
    )


def run_pipeline(
    settings: CuratorSettings,
    jd_text: str,
    *,
    skip_pdf: bool = False,
    with_cover_letter: bool = False,
    publish_to: Path | None = None,
    on_status: Callable[[str], None] | None = None,
) -> PipelineResult:
    """Execute the full curation pipeline with renderer-side page fitting.

    Loads the portfolio, calls the Claude API once for curation decisions,
    and renders the result to a PDF via Typst. If the PDF exceeds
    ``settings.max_pages``, the renderer trims content deterministically
    (lowest-value first) and re-compiles until the PDF fits.

    When *skip_pdf* is True, the API is called once but Typst compilation
    and page-count enforcement are skipped.

    Args:
        settings: Validated application settings.
        jd_text: Job description text.
        skip_pdf: Skip PDF compilation when True. Still calls the API.
        with_cover_letter: Bundle a tailored cover letter into the same API
            call when True. No additional billable call is made.
        publish_to: When set, copy upload-ready artifacts (resume.pdf,
            cover_letter.pdf, cover_letter.txt) to
            ``<publish_to>/<profile_name>/`` after rendering. The CLI
            ``--publish DIR`` option supplies this kwarg inline.
        on_status: Optional callback for progress updates (e.g., Rich status).

    Returns:
        PipelineResult with curation decisions, render output, and token usage.

    Raises:
        PortfolioNotFoundError: If the portfolio directory is missing.
        PortfolioValidationError: If portfolio YAML is malformed.
        APIError: On API call failures.
        RenderError: On rendering or Typst compilation failures.
    """
    update = on_status or (lambda _msg: None)

    # Load portfolio.
    update("Loading portfolio...")
    t0 = time.perf_counter()
    portfolio = load_portfolio(settings.portfolio_data_path)
    logger.info("Portfolio loaded in {:.1f}s", time.perf_counter() - t0)

    # Single API call for curation (optionally bundled with a cover letter).
    with CuratorClient(settings) as client:
        update(
            "Curating resume and cover letter..."
            if with_cover_letter
            else "Curating resume..."
        )
        t0 = time.perf_counter()
        result = client.curate(
            portfolio=portfolio,
            job_description=jd_text,
            with_cover_letter=with_cover_letter,
        )
        logger.info("API call completed in {:.1f}s", time.perf_counter() - t0)

    # Render (with trimming if PDF mode).
    if skip_pdf:
        update("Writing audit artifacts...")
        logger.info("No-PDF mode: skipping PDF compilation")
    else:
        update("Rendering PDF...")

    t0 = time.perf_counter()
    render_output = render(
        result,
        portfolio,
        jd_text,
        settings,
        skip_pdf=skip_pdf,
        safety_net=True,
    )
    logger.info("Rendering completed in {:.1f}s", time.perf_counter() - t0)

    published = _maybe_publish(render_output, publish_to, update)

    return _summarize_pipeline_result(
        curation=result,
        render_output=render_output,
        portfolio=portfolio,
        skip_pdf=skip_pdf,
        settings=settings,
        on_status=update,
        published_paths=published,
    )


def run_static_pipeline(
    settings: CuratorSettings,
    *,
    name: str = "general",
    max_highlights: int | None = None,
    skip_pdf: bool = False,
    with_cover_letter: bool = False,
    publish_to: Path | None = None,
    on_status: Callable[[str], None] | None = None,
) -> PipelineResult:
    """Execute the static (zero-API) curation pipeline.

    Loads the portfolio, synthesizes a deterministic ``CurationResult``
    from portfolio data, and renders via the existing trim loop. No
    Anthropic API call is made; token counts on the result are zero and
    ``source="static"`` in the audit log.

    Args:
        settings: Validated application settings.
        name: Output slug / audit name (default ``"general"``).
        max_highlights: Optional per-work-entry highlight cap applied at
            synthesis time (pre-renderer).
        skip_pdf: Skip PDF compilation when True.
        with_cover_letter: Attach the portfolio-authored cover letter
            (loaded verbatim from ``data/cover-letter.yaml``) when True.
            No synthesis, no placeholders, no TEMPLATE banner; the letter
            must pass ``validate_cover_letter``.
        publish_to: When set, copy upload-ready artifacts (resume.pdf,
            cover_letter.pdf, cover_letter.txt) to
            ``<publish_to>/<profile_name>/`` after rendering. The CLI
            ``--publish DIR`` option supplies this kwarg inline.
        on_status: Optional callback for progress updates.

    Returns:
        PipelineResult with static curation, render output, and zero tokens.

    Raises:
        PortfolioNotFoundError: If the portfolio directory is missing.
        PortfolioValidationError: If portfolio YAML is malformed.
        StaticModeError: If the portfolio has zero work entries, or
            (when ``with_cover_letter=True``) if ``data/cover-letter.yaml``
            is missing or fails ``validate_cover_letter``.
        RenderError: On rendering or Typst compilation failures.
    """
    update = on_status or (lambda _msg: None)

    update("Loading portfolio...")
    t0 = time.perf_counter()
    portfolio = load_portfolio(settings.portfolio_data_path)
    logger.info("Portfolio loaded in {:.1f}s", time.perf_counter() - t0)

    update(
        "Synthesizing static curation and cover letter..."
        if with_cover_letter
        else "Synthesizing static curation..."
    )
    t0 = time.perf_counter()
    result = build_static_result(
        portfolio,
        name=name,
        max_highlights_per_work=max_highlights,
        with_cover_letter=with_cover_letter,
    )
    logger.info(
        "Static curation synthesized in {:.2f}s (source={})",
        time.perf_counter() - t0,
        result.source,
    )

    if skip_pdf:
        update("Writing audit artifacts...")
        logger.info("No-PDF mode: skipping PDF compilation")
    else:
        update("Rendering PDF...")

    t0 = time.perf_counter()
    render_output = render(
        result,
        portfolio,
        jd_text=None,
        settings=settings,
        skip_pdf=skip_pdf,
        safety_net=False,
    )
    logger.info("Rendering completed in {:.1f}s", time.perf_counter() - t0)

    published = _maybe_publish(render_output, publish_to, update)

    return _summarize_pipeline_result(
        curation=result,
        render_output=render_output,
        portfolio=portfolio,
        skip_pdf=skip_pdf,
        settings=settings,
        on_status=update,
        published_paths=published,
    )
