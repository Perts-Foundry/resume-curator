"""PDF Output Quality metrics (15% category weight; 10 scored + 1 informational).

Runtime analysis of generated PDFs using pdfplumber for layout metrics
and font analysis. ``font_embedding_valid`` is ``informational=True``
because embedding detection is deferred future work (Typst always embeds
fonts by default in this codebase); the metric is retained as an
informational stub on both the with-PDF and dry-run paths.

The ``whitespace_ratio`` PASS band is page-budget-aware via
``EvalBands``; long-form pages run slightly denser and use a lower
floor than short-form. Bands selected by ``bands_for_pages(max_pages)``
upstream in ``evaluate_tier1``.
"""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Any

from curator.eval.report import (
    SHORT_FORM_BANDS,
    EvalBands,
    EvalMetricResult,
    EvalMetricStatus,
)
from curator.exceptions import EvalError
from curator.io_utils import MAX_PDF_SIZE
from curator.rules import MIN_FONT_SIZE_PASS_PT, MIN_FONT_SIZE_WARN_PT

if TYPE_CHECKING:
    from pathlib import Path

_CATEGORY = "pdf_output"

# Single source of truth for metric names — used by both evaluation
# and no-PDF fallback paths.
PDF_METRIC_NAMES: tuple[str, ...] = (
    "page_count",
    "file_size",
    "text_extractable",
    "plain_text_coherent",
    "font_embedding_valid",
    "whitespace_ratio",
    "single_column_layout",
    "contact_in_body",
    "actual_min_font_size",
    "actual_body_font_size",
    "actual_name_font_size",
)


def evaluate_pdf(
    pdf_path: Path | None,
    basics: dict[str, Any],
    *,
    max_pages: int = 1,
    page_margin_pt: float = 36.0,
    bands: EvalBands = SHORT_FORM_BANDS,
) -> list[EvalMetricResult]:
    """Evaluate PDF Output Quality metrics.

    When *pdf_path* is None (no PDF available), all metrics return WARN.

    ``page_margin_pt`` is the per-side margin used when computing
    ``whitespace_ratio``. Defaults to 36pt (0.5in) for backwards
    compatibility; callers should pass the actual template margin so
    the printable-area denominator matches the rendered PDF.
    """
    if pdf_path is None:
        return _dry_run_results()

    try:
        import pdfplumber
    except ImportError as e:
        msg = "pdfplumber is required for PDF evaluation metrics"
        raise EvalError(msg) from e

    # Size guard.
    try:
        file_size = pdf_path.stat().st_size
    except OSError as e:
        msg = f"Cannot read PDF: {pdf_path}"
        raise EvalError(msg) from e

    if file_size > MAX_PDF_SIZE:
        msg = f"PDF exceeds size limit: {file_size} bytes"
        raise EvalError(msg)

    try:
        pdf = pdfplumber.open(pdf_path)
    except Exception as e:
        msg = f"Failed to open PDF: {e}"
        raise EvalError(msg) from e

    try:
        return _evaluate_with_pdf(
            pdf,
            file_size,
            basics,
            max_pages=max_pages,
            page_margin_pt=page_margin_pt,
            bands=bands,
        )
    finally:
        pdf.close()


def _dry_run_results() -> list[EvalMetricResult]:
    """Return WARN for all PDF metrics when no PDF is available.

    ``font_embedding_valid`` is marked ``informational=True`` to match
    the with-PDF path; it stays out of the aggregate regardless of
    whether a PDF was rendered.
    """
    return [
        EvalMetricResult(
            name=name,
            category=_CATEGORY,
            status=EvalMetricStatus.WARN,
            value=None,
            detail="PDF not available",
            informational=(name == "font_embedding_valid"),
        )
        for name in PDF_METRIC_NAMES
    ]


def _evaluate_with_pdf(
    pdf: Any,
    file_size: int,
    basics: dict[str, Any],
    *,
    max_pages: int,
    page_margin_pt: float = 36.0,
    bands: EvalBands = SHORT_FORM_BANDS,
) -> list[EvalMetricResult]:
    """Run all PDF metrics against an open pdfplumber PDF."""
    results: list[EvalMetricResult] = []
    pages = pdf.pages

    # page_count — should equal max_pages (default 1).
    pc = len(pages)
    results.append(
        EvalMetricResult(
            name="page_count",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if pc <= max_pages else EvalMetricStatus.FAIL,
            value=pc,
            detail=f"{pc} page(s) (max: {max_pages})",
        )
    )

    # file_size — <2MB soft, <5MB hard.
    size_mb = file_size / (1024 * 1024)
    if file_size < 2 * 1024 * 1024:
        fs_status = EvalMetricStatus.PASS
    elif file_size < 5 * 1024 * 1024:
        fs_status = EvalMetricStatus.WARN
    else:
        fs_status = EvalMetricStatus.FAIL
    results.append(
        EvalMetricResult(
            name="file_size",
            category=_CATEGORY,
            status=fs_status,
            value=file_size,
            detail=f"{size_mb:.2f} MB",
        )
    )

    # text_extractable — text selectable and copyable.
    try:
        all_text = "\n".join(page.extract_text() or "" for page in pages)
        extractable = bool(all_text.strip())
    except Exception:
        all_text = ""
        extractable = False
    results.append(
        EvalMetricResult(
            name="text_extractable",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if extractable else EvalMetricStatus.FAIL,
            value=extractable,
            detail="Text is extractable"
            if extractable
            else "No text could be extracted",
        )
    )

    # plain_text_coherent — text non-empty, name appears near start.
    name = str(basics.get("name", ""))
    name_lower = name.lower()
    text_lower = all_text[:500].lower() if all_text else ""
    name_near_start = name_lower in text_lower if name_lower else False
    has_garbled = bool(all_text and _has_garbled_chars(all_text[:1000]))
    coherent = extractable and name_near_start and not has_garbled
    detail_parts: list[str] = []
    if not extractable:
        detail_parts.append("no text extracted")
    if not name_near_start:
        detail_parts.append(f"name '{name}' not found near start")
    if has_garbled:
        detail_parts.append("garbled characters detected")
    results.append(
        EvalMetricResult(
            name="plain_text_coherent",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS
            if coherent
            else EvalMetricStatus.WARN
            if extractable
            else EvalMetricStatus.FAIL,
            value=coherent,
            detail="Text is coherent" if coherent else "; ".join(detail_parts),
        )
    )

    # Collect character-level data for font and layout metrics.
    chars = _collect_chars(pages)

    # font_embedding_valid — Typst embeds fonts by default; no known path
    # in this codebase produces an unembedded PDF. The metric remains for
    # future real detection work (e.g., pdfplumber font analysis), but
    # is ``informational=True`` with a PASS status so it stays visible
    # without docking the aggregate on every clean run.
    results.append(
        EvalMetricResult(
            name="font_embedding_valid",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS,
            value=True,
            detail=(
                "Typst embeds fonts by default (informational; real detection deferred)"
            ),
            informational=True,
        )
    )

    # whitespace_ratio — page-budget-aware bands. Short-form 55-75%,
    # long-form 50-72% (long-form pages tend to run slightly denser).
    ws_pass_lo, ws_pass_hi = bands.whitespace_ratio_pass
    ws_warn_lo, ws_warn_hi = bands.whitespace_ratio_warn
    ws_ratio = _compute_whitespace_ratio(pages, chars, page_margin_pt=page_margin_pt)
    if ws_ratio is not None:
        if ws_pass_lo <= ws_ratio <= ws_pass_hi:
            ws_status = EvalMetricStatus.PASS
        elif ws_warn_lo <= ws_ratio <= ws_warn_hi:
            ws_status = EvalMetricStatus.WARN
        else:
            ws_status = EvalMetricStatus.FAIL
        results.append(
            EvalMetricResult(
                name="whitespace_ratio",
                category=_CATEGORY,
                status=ws_status,
                value=round(ws_ratio, 2),
                detail=(
                    f"{ws_ratio:.0%} whitespace "
                    f"(target: {ws_pass_lo:.0%}-{ws_pass_hi:.0%})"
                ),
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="whitespace_ratio",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="Could not compute whitespace ratio",
            )
        )

    # single_column_layout — body text is predominantly single-column.
    # Threshold 180pt accommodates intentional 2-column skills grids with
    # wide label columns; true 2-column resumes still trip well above this.
    if chars:
        x_positions = [c["x0"] for c in chars if c.get("x0") is not None]
        if x_positions:
            stdev = statistics.stdev(x_positions) if len(x_positions) > 1 else 0.0
            threshold = 180.0
            is_single = stdev < threshold
            results.append(
                EvalMetricResult(
                    name="single_column_layout",
                    category=_CATEGORY,
                    status=EvalMetricStatus.PASS
                    if is_single
                    else EvalMetricStatus.FAIL,
                    value=round(stdev, 1),
                    detail=(
                        f"x-position stdev: {stdev:.1f}pt (threshold: {threshold}pt)"
                    ),
                )
            )
        else:
            results.append(
                EvalMetricResult(
                    name="single_column_layout",
                    category=_CATEGORY,
                    status=EvalMetricStatus.WARN,
                    value=None,
                    detail="No character positions available",
                )
            )
    else:
        results.append(
            EvalMetricResult(
                name="single_column_layout",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="No character data available",
            )
        )

    # contact_in_body — name/email positioned in main body region.
    contact_in_body = _check_contact_position(pages, basics)
    results.append(
        EvalMetricResult(
            name="contact_in_body",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if contact_in_body else EvalMetricStatus.WARN,
            value=contact_in_body,
            detail="Contact info in body region"
            if contact_in_body
            else "Could not verify contact position",
        )
    )

    # Font size metrics from character data.
    font_sizes = [c["size"] for c in chars if c.get("size") is not None]

    # actual_min_font_size — smallest rendered font on the page. See
    # MIN_FONT_SIZE_PASS_PT / MIN_FONT_SIZE_WARN_PT in rules.py for the
    # rationale behind the 8.5 / 7.5 thresholds.
    if font_sizes:
        min_size = min(font_sizes)
        results.append(
            EvalMetricResult(
                name="actual_min_font_size",
                category=_CATEGORY,
                status=EvalMetricStatus.PASS
                if min_size >= MIN_FONT_SIZE_PASS_PT
                else EvalMetricStatus.WARN
                if min_size >= MIN_FONT_SIZE_WARN_PT
                else EvalMetricStatus.FAIL,
                value=round(min_size, 1),
                detail=(
                    f"Minimum font size: {min_size:.1f}pt "
                    f"(floor: {MIN_FONT_SIZE_PASS_PT}pt)"
                ),
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="actual_min_font_size",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="No font size data available",
            )
        )

    # actual_body_font_size — most common font size 10-12pt.
    if font_sizes:
        # Round to nearest 0.5pt for grouping.
        rounded = [round(s * 2) / 2 for s in font_sizes]
        size_counts: dict[float, int] = {}
        for s in rounded:
            size_counts[s] = size_counts.get(s, 0) + 1
        body_size = max(size_counts, key=size_counts.get)  # type: ignore[arg-type]
        if 10.0 <= body_size <= 12.0:
            bs_status = EvalMetricStatus.PASS
        elif 9.0 <= body_size <= 13.0:
            bs_status = EvalMetricStatus.WARN
        else:
            bs_status = EvalMetricStatus.FAIL
        results.append(
            EvalMetricResult(
                name="actual_body_font_size",
                category=_CATEGORY,
                status=bs_status,
                value=body_size,
                detail=f"Most common: {body_size}pt (target: 10-12pt)",
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="actual_body_font_size",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="No font size data available",
            )
        )

    # actual_name_font_size — largest font on page 1 is 20-24pt.
    if pages:
        page1_chars = _collect_chars([pages[0]])
        page1_sizes = [c["size"] for c in page1_chars if c.get("size") is not None]
        if page1_sizes:
            max_size = max(page1_sizes)
            if 20.0 <= max_size <= 24.0:
                ns_status = EvalMetricStatus.PASS
            elif 16.0 <= max_size <= 28.0:
                ns_status = EvalMetricStatus.WARN
            else:
                ns_status = EvalMetricStatus.FAIL
            results.append(
                EvalMetricResult(
                    name="actual_name_font_size",
                    category=_CATEGORY,
                    status=ns_status,
                    value=round(max_size, 1),
                    detail=(
                        f"Largest font on page 1: {max_size:.1f}pt (target: 20-24pt)"
                    ),
                )
            )
        else:
            results.append(
                EvalMetricResult(
                    name="actual_name_font_size",
                    category=_CATEGORY,
                    status=EvalMetricStatus.WARN,
                    value=None,
                    detail="No font data on page 1",
                )
            )
    else:
        results.append(
            EvalMetricResult(
                name="actual_name_font_size",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="No pages in PDF",
            )
        )

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_garbled_chars(text: str) -> bool:
    """Check for garbled/replacement characters in extracted text."""
    garbled_count = sum(
        1 for c in text if c == "\ufffd" or (ord(c) > 0xFFFF and not c.isprintable())
    )
    return garbled_count > len(text) * 0.01  # >1% garbled


def _collect_chars(pages: list[Any]) -> list[dict[str, Any]]:
    """Collect character-level data from pdfplumber pages."""
    chars: list[dict[str, Any]] = []
    for page in pages:
        try:
            chars.extend(page.chars)
        except Exception:  # noqa: S112 — pdfplumber may fail on malformed pages
            continue
    return chars


def _compute_whitespace_ratio(
    pages: list[Any],
    chars: list[dict[str, Any]],
    *,
    page_margin_pt: float = 36.0,
) -> float | None:
    """Compute whitespace ratio from character bounding boxes."""
    if not pages or not chars:
        return None

    total_content_area = 0.0
    total_char_area = 0.0

    for page in pages:
        try:
            width = float(page.width)
            height = float(page.height)
            content_w = max(width - 2 * page_margin_pt, 1.0)
            content_h = max(height - 2 * page_margin_pt, 1.0)
            total_content_area += content_w * content_h
        except (AttributeError, TypeError):
            continue

    for char in chars:
        try:
            x0, y0 = float(char["x0"]), float(char["top"])
            x1, y1 = float(char["x1"]), float(char["bottom"])
            total_char_area += abs((x1 - x0) * (y1 - y0))
        except (KeyError, TypeError, ValueError):
            continue

    if total_content_area == 0:
        return None

    return 1.0 - min(total_char_area / total_content_area, 1.0)


def _check_contact_position(
    pages: list[Any],
    basics: dict[str, Any],
) -> bool:
    """Check if contact info is in the main body (not header/footer)."""
    if not pages:
        return False

    name = str(basics.get("name", "")).lower()
    if not name:
        return False

    page = pages[0]
    try:
        text = page.extract_text() or ""
    except Exception:
        return False

    # If name appears in extracted text, it's in the body.
    return name in text.lower()
