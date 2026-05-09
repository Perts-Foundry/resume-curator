"""Tests for curator.eval.pdf."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pypdf import PdfWriter

if TYPE_CHECKING:
    from pathlib import Path

from dataclasses import dataclass

from typing import Any

from curator.eval.pdf import (
    _compute_whitespace_ratio,
    evaluate_pdf as _evaluate_pdf,
)
from curator.eval.report import SHORT_FORM_BANDS, EvalMetricResult, EvalMetricStatus
from curator.exceptions import EvalError


def evaluate_pdf(
    pdf_path: "Path | None",
    basics: dict[str, Any],
    **kwargs: Any,
) -> list[EvalMetricResult]:
    """Test wrapper that binds ``bands=SHORT_FORM_BANDS`` and ``max_pages=1``.

    Production ``evaluate_pdf`` requires both ``bands`` and ``max_pages``
    as keyword-only kwargs with no defaults; these short-form tests
    opt in once at module load.
    """
    kwargs.setdefault("bands", SHORT_FORM_BANDS)
    kwargs.setdefault("max_pages", 1)
    return _evaluate_pdf(pdf_path, basics, **kwargs)
from tests.helpers import find_metric


@dataclass
class _FakePage:
    """Stand-in for a pdfplumber page exposing only width/height."""

    width: float = 612.0  # US Letter pt
    height: float = 792.0


def _char(x0: float, x1: float, top: float, bottom: float) -> dict[str, float]:
    return {"x0": x0, "x1": x1, "top": top, "bottom": bottom}


def _create_minimal_pdf(path: Path, *, num_pages: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=612, height=792)  # US Letter
    with path.open("wb") as f:
        writer.write(f)


# ---------------------------------------------------------------------------
# No-PDF fallback behavior
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestDryRun:
    def test_returns_all_warn(self) -> None:
        results = evaluate_pdf(None, {})
        assert len(results) > 0
        for r in results:
            assert r.status == EvalMetricStatus.WARN

    def test_returns_expected_metric_names(self) -> None:
        results = evaluate_pdf(None, {})
        names = {r.name for r in results}
        expected = {
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
        }
        assert names == expected

    def test_all_values_are_none(self) -> None:
        results = evaluate_pdf(None, {})
        for r in results:
            assert r.value is None


# ---------------------------------------------------------------------------
# file_size
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestFileSize:
    def test_small_file_pass(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "resume.pdf"
        _create_minimal_pdf(pdf_path)
        results = evaluate_pdf(pdf_path, {"name": "Jane Doe"})
        m = find_metric(results, "file_size")
        assert m.status == EvalMetricStatus.PASS
        assert m.value > 0

    def test_missing_file_raises_eval_error(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "nonexistent.pdf"
        with pytest.raises(EvalError, match="Cannot read PDF"):
            evaluate_pdf(pdf_path, {})


# ---------------------------------------------------------------------------
# page_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestPageCount:
    def test_single_page_pass(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "resume.pdf"
        _create_minimal_pdf(pdf_path, num_pages=1)
        results = evaluate_pdf(pdf_path, {"name": "Jane Doe"})
        m = find_metric(results, "page_count")
        assert m.status == EvalMetricStatus.PASS
        assert m.value == 1

    def test_two_pages_fail_for_single_page_max(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "resume.pdf"
        _create_minimal_pdf(pdf_path, num_pages=2)
        results = evaluate_pdf(pdf_path, {"name": "Jane Doe"}, max_pages=1)
        m = find_metric(results, "page_count")
        assert m.status == EvalMetricStatus.FAIL
        assert m.value == 2

    def test_two_pages_pass_for_two_page_max(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "resume.pdf"
        _create_minimal_pdf(pdf_path, num_pages=2)
        results = evaluate_pdf(pdf_path, {"name": "Jane Doe"}, max_pages=2)
        m = find_metric(results, "page_count")
        assert m.status == EvalMetricStatus.PASS


# ---------------------------------------------------------------------------
# text_extractable
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestTextExtractable:
    def test_blank_pdf_no_text(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "resume.pdf"
        _create_minimal_pdf(pdf_path)
        results = evaluate_pdf(pdf_path, {"name": "Jane Doe"})
        m = find_metric(results, "text_extractable")
        # Blank page has no text, so it should fail.
        assert m.status == EvalMetricStatus.FAIL


# ---------------------------------------------------------------------------
# Return structure
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestReturnStructure:
    def test_all_category_is_pdf_output(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "resume.pdf"
        _create_minimal_pdf(pdf_path)
        results = evaluate_pdf(pdf_path, {"name": "Jane Doe"})
        for r in results:
            assert r.category == "pdf_output"

    def test_all_metrics_present(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "resume.pdf"
        _create_minimal_pdf(pdf_path)
        results = evaluate_pdf(pdf_path, {"name": "Jane Doe"})
        names = {r.name for r in results}
        expected = {
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
        }
        assert names == expected

    def test_font_embedding_valid_is_informational(self, tmp_path: Path) -> None:
        # font_embedding_valid is a deferred-detection stub; it MUST
        # carry informational=True on both the with-PDF and dry-run paths.
        pdf_path = tmp_path / "resume.pdf"
        _create_minimal_pdf(pdf_path)
        results = evaluate_pdf(pdf_path, {"name": "Jane Doe"})
        by_name = {r.name: r for r in results}
        assert by_name["font_embedding_valid"].informational is True

    def test_font_embedding_valid_dry_run_informational(self) -> None:
        # Parity check: dry-run path must also mark font_embedding_valid
        # as informational so the metric stays out of aggregation on both
        # paths. Non-informational metrics retain the default False state.
        from curator.eval.pdf import _dry_run_results

        by_name = {r.name: r for r in _dry_run_results()}
        assert by_name["font_embedding_valid"].informational is True
        assert by_name["actual_min_font_size"].informational is False


# ---------------------------------------------------------------------------
# whitespace_ratio thresholds (synthetic chars, no real PDF)
# ---------------------------------------------------------------------------

# US Letter (612 x 792) with the hardcoded 36pt margin yields a content
# area of 540 x 720 = 388_800 pt^2. Picking char rectangles with known
# total area lets us drive _compute_whitespace_ratio across each band.

_CONTENT_AREA = 540 * 720
_PAGE = _FakePage()


def _whitespace_for_target(target_char_fraction: float) -> float | None:
    char_area = target_char_fraction * _CONTENT_AREA
    side = char_area**0.5
    chars = [_char(x0=0, x1=side, top=0, bottom=side)]
    ratio: float | None = _compute_whitespace_ratio([_PAGE], chars)
    return ratio


@pytest.mark.unit
@pytest.mark.eval
class TestWhitespaceRatioThresholds:
    def test_pass_band_mid(self) -> None:
        # 35% char area -> 0.65 whitespace, comfortably inside 55-75 PASS.
        ratio = _whitespace_for_target(0.35)
        assert ratio is not None
        assert 0.55 <= ratio <= 0.75

    def test_pass_band_just_above_lower_bound(self) -> None:
        # 44% char area -> 0.56 whitespace, just above the 0.55 PASS floor.
        ratio = _whitespace_for_target(0.44)
        assert ratio is not None
        assert 0.55 <= ratio <= 0.75

    def test_warn_band_below_pass_lower_bound(self) -> None:
        # 50% char area -> 0.50 whitespace, in the 0.45-0.55 WARN gap below PASS.
        ratio = _whitespace_for_target(0.50)
        assert ratio is not None
        assert 0.45 <= ratio < 0.55

    def test_fail_band_dense_text(self) -> None:
        # 70% char area -> 0.30 whitespace, below the 0.45 WARN floor.
        ratio = _whitespace_for_target(0.70)
        assert ratio is not None
        assert ratio < 0.45

    def test_fail_band_too_sparse(self) -> None:
        # 15% char area -> 0.85 whitespace, above the 0.80 WARN ceiling.
        ratio = _whitespace_for_target(0.15)
        assert ratio is not None
        assert ratio > 0.80


# ---------------------------------------------------------------------------
# single_column_layout 180pt threshold (synthetic chars)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestSingleColumnLayoutThreshold:
    """Exercises the 180pt stdev threshold for single vs multi column."""

    def _ratio_status(self, x_positions: list[float]) -> tuple[float, bool]:
        import statistics

        stdev = statistics.stdev(x_positions) if len(x_positions) > 1 else 0.0
        return stdev, stdev < 180.0

    def test_two_x_bands_within_threshold_is_single(self) -> None:
        # Two clusters at x=50 and x=200 produce stdev ~75pt, well below 180.
        positions = [50.0] * 30 + [200.0] * 30
        stdev, is_single = self._ratio_status(positions)
        assert stdev < 180.0
        assert is_single is True

    def test_two_x_bands_far_apart_not_single(self) -> None:
        # Clusters at x=50 and x=550 produce stdev ~250pt, above 180.
        positions = [50.0] * 30 + [550.0] * 30
        stdev, is_single = self._ratio_status(positions)
        assert stdev > 180.0
        assert is_single is False
