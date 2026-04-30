"""Tests for curator.eval.template."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from curator import default_template_path

if TYPE_CHECKING:
    from pathlib import Path
from curator.eval.report import EvalMetricStatus
from curator.eval.template import evaluate_template
from tests.helpers import find_metric

# Path to the real template for integration-style parsing tests.
_REAL_TEMPLATE = default_template_path()


# ---------------------------------------------------------------------------
# Missing template
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestMissingTemplate:
    def test_none_returns_all_warn(self) -> None:
        results = evaluate_template(None)
        assert len(results) > 0
        for r in results:
            assert r.status == EvalMetricStatus.WARN

    def test_nonexistent_path_returns_all_warn(self, tmp_path: Path) -> None:
        results = evaluate_template(tmp_path / "nonexistent.typ")
        for r in results:
            assert r.status == EvalMetricStatus.WARN

    def test_expected_metric_names(self) -> None:
        results = evaluate_template(None)
        names = {r.name for r in results}
        expected = {
            "template_body_font_size",
            "template_name_font_size",
            "template_heading_font_size",
            "template_margins",
            "template_font_families",
            "template_accent_color",
            "template_line_spacing",
            "template_bullet_indent",
            "template_section_spacing",
        }
        assert names == expected


# ---------------------------------------------------------------------------
# Real template parsing
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestRealTemplateParsing:
    @pytest.fixture(autouse=True)
    def _skip_if_no_template(self) -> None:
        if not _REAL_TEMPLATE.exists():
            pytest.skip("Real template not available")

    def test_body_font_size_10pt_pass(self) -> None:
        # Parser tolerates the nested font tuple
        # font: ("Inter", "Ubuntu Sans", "DejaVu Sans") and reads size: 10pt.
        results = evaluate_template(_REAL_TEMPLATE)
        m = find_metric(results, "template_body_font_size")
        assert m.value == 10.0
        assert m.status == EvalMetricStatus.PASS

    def test_name_font_size_picks_largest_bold_block(self) -> None:
        # The template has bold #text() at 20pt (name), 14pt (heading wrapper),
        # and 12pt (contact). The parser collects all candidates and picks the
        # largest, so reordering the source no longer flips the metric.
        results = evaluate_template(_REAL_TEMPLATE)
        m = find_metric(results, "template_name_font_size")
        assert m.value == 20.0
        assert m.status == EvalMetricStatus.PASS

    def test_heading_font_size_14pt_pass(self) -> None:
        results = evaluate_template(_REAL_TEMPLATE)
        m = find_metric(results, "template_heading_font_size")
        # 14pt is in the 14-16pt PASS range.
        assert m.value == 14.0
        assert m.status == EvalMetricStatus.PASS

    def test_margins_0_3in(self) -> None:
        results = evaluate_template(_REAL_TEMPLATE)
        m = find_metric(results, "template_margins")
        assert isinstance(m.value, dict)
        for side in ("top", "right", "bottom", "left"):
            assert m.value[side] == 0.3
        assert m.status == EvalMetricStatus.PASS

    def test_font_families_3_pass(self) -> None:
        results = evaluate_template(_REAL_TEMPLATE)
        m = find_metric(results, "template_font_families")
        assert isinstance(m.value, list)
        assert len(m.value) == 3
        assert m.status == EvalMetricStatus.PASS
        # Primary font should be sans-serif.
        assert m.value[0].lower() == "inter"

    def test_accent_color_navy(self) -> None:
        results = evaluate_template(_REAL_TEMPLATE)
        m = find_metric(results, "template_accent_color")
        assert m.value == "#003366"
        assert m.status == EvalMetricStatus.PASS

    def test_line_spacing(self) -> None:
        results = evaluate_template(_REAL_TEMPLATE)
        m = find_metric(results, "template_line_spacing")
        assert m.value == 0.5
        assert m.status == EvalMetricStatus.PASS

    def test_bullet_indent(self) -> None:
        results = evaluate_template(_REAL_TEMPLATE)
        m = find_metric(results, "template_bullet_indent")
        assert m.value == 0.3
        assert m.status == EvalMetricStatus.PASS

    def test_section_spacing(self) -> None:
        results = evaluate_template(_REAL_TEMPLATE)
        m = find_metric(results, "template_section_spacing")
        # v(16pt) + v(8pt) = 24pt
        assert m.value == 24.0
        assert m.status == EvalMetricStatus.PASS


# ---------------------------------------------------------------------------
# Synthetic template content
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestSyntheticTemplate:
    def test_body_font_size_out_of_range_fail(self, tmp_path: Path) -> None:
        template = tmp_path / "test.typ"
        template.write_text('#set text(font: "Arial", size: 7pt)\n')
        results = evaluate_template(template)
        m = find_metric(results, "template_body_font_size")
        assert m.value == 7.0
        assert m.status == EvalMetricStatus.FAIL

    def test_uniform_margin_parsed(self, tmp_path: Path) -> None:
        template = tmp_path / "test.typ"
        template.write_text('#set page(paper: "us-letter", margin: 0.75in)\n')
        results = evaluate_template(template)
        m = find_metric(results, "template_margins")
        assert isinstance(m.value, dict)
        assert m.value["top"] == 0.75
        assert m.status == EvalMetricStatus.PASS

    def test_dict_margin_parsed(self, tmp_path: Path) -> None:
        template = tmp_path / "test.typ"
        template.write_text(
            "#set page(margin: (top: 0.5in, right: 0.5in, "
            "bottom: 0.5in, left: 0.5in))\n"
        )
        results = evaluate_template(template)
        m = find_metric(results, "template_margins")
        assert isinstance(m.value, dict)
        assert m.value["left"] == 0.5

    def test_single_font_parsed(self, tmp_path: Path) -> None:
        template = tmp_path / "test.typ"
        template.write_text('#set text(font: "Inter", size: 10pt)\n')
        results = evaluate_template(template)
        m = find_metric(results, "template_font_families")
        assert m.value == ["Inter"]

    def test_accent_color_low_contrast_fail(self, tmp_path: Path) -> None:
        template = tmp_path / "test.typ"
        # Very light color => low contrast against white.
        template.write_text('#show link: set text(fill: rgb("#EEEEEE"))\n')
        results = evaluate_template(template)
        m = find_metric(results, "template_accent_color")
        assert m.status == EvalMetricStatus.FAIL

    def test_line_spacing_out_of_range(self, tmp_path: Path) -> None:
        template = tmp_path / "test.typ"
        template.write_text("#set par(leading: 1.5em)\n")
        results = evaluate_template(template)
        m = find_metric(results, "template_line_spacing")
        assert m.value == 1.5
        assert m.status == EvalMetricStatus.FAIL

    def test_bullet_indent_out_of_range(self, tmp_path: Path) -> None:
        template = tmp_path / "test.typ"
        template.write_text("#set list(indent: 1.0in)\n")
        results = evaluate_template(template)
        m = find_metric(results, "template_bullet_indent")
        assert m.value == 1.0
        assert m.status == EvalMetricStatus.FAIL


# ---------------------------------------------------------------------------
# Return structure
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.eval
class TestReturnStructure:
    def test_all_category_is_template_correctness(self) -> None:
        results = evaluate_template(None)
        for r in results:
            assert r.category == "template_correctness"
