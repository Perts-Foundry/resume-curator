"""Template Correctness metrics (5% weight; static .typ parsing).

Parses ``templates/curated.typ`` to extract declared formatting values
via regex on ``#set`` declarations. Catches template source drift.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from curator.eval.report import EvalMetricResult, EvalMetricStatus
from curator.exceptions import EvalError

if TYPE_CHECKING:
    from pathlib import Path

_CATEGORY = "template_correctness"
_MAX_TEMPLATE_SIZE: int = 1_048_576  # 1 MiB

TEMPLATE_METRIC_NAMES: tuple[str, ...] = (
    "template_body_font_size",
    "template_name_font_size",
    "template_heading_font_size",
    "template_margins",
    "template_font_families",
    "template_accent_color",
    "template_line_spacing",
    "template_bullet_indent",
    "template_section_spacing",
)

# Known sans-serif font families for validation.
_SANS_SERIF_FONTS: frozenset[str] = frozenset(
    {
        "inter",
        "arial",
        "helvetica",
        "ubuntu sans",
        "dejavu sans",
        "roboto",
        "open sans",
        "noto sans",
        "calibri",
        "verdana",
        "trebuchet ms",
        "source sans pro",
        "lato",
        "segoe ui",
        "liberation sans",
        "fira sans",
    }
)


def evaluate_template(template_path: Path | None) -> list[EvalMetricResult]:
    """Evaluate Template Correctness metrics.

    When *template_path* is None or doesn't exist, all metrics return WARN.
    """
    if template_path is None or not template_path.exists():
        return _missing_template_results()

    try:
        file_size = template_path.stat().st_size
        if file_size > _MAX_TEMPLATE_SIZE:
            msg = f"Template exceeds size limit: {file_size} bytes"
            raise EvalError(msg)
        content = template_path.read_text(encoding="utf-8")
    except OSError as e:
        msg = f"Cannot read template: {e}"
        raise EvalError(msg) from e

    return _evaluate_template_content(content)


def _missing_template_results() -> list[EvalMetricResult]:
    """Return WARN for all template metrics when template is unavailable."""
    return [
        EvalMetricResult(
            name=name,
            category=_CATEGORY,
            status=EvalMetricStatus.WARN,
            value=None,
            detail="Template not available",
        )
        for name in TEMPLATE_METRIC_NAMES
    ]


def _evaluate_template_content(content: str) -> list[EvalMetricResult]:
    """Run all template metrics against the .typ content."""
    results: list[EvalMetricResult] = []

    # template_body_font_size — #set text(... size: ...)
    body_size = _extract_text_size(content)
    if body_size is not None:
        if 10 <= body_size <= 12:
            bs_status = EvalMetricStatus.PASS
        elif 9 <= body_size <= 13:
            bs_status = EvalMetricStatus.WARN
        else:
            bs_status = EvalMetricStatus.FAIL
        results.append(
            EvalMetricResult(
                name="template_body_font_size",
                category=_CATEGORY,
                status=bs_status,
                value=body_size,
                detail=f"{body_size}pt (target: 10-12pt)",
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="template_body_font_size",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="Could not parse body font size",
            )
        )

    # template_name_font_size — text(size: 20pt) for name.
    name_size = _extract_name_size(content)
    if name_size is not None:
        if 20 <= name_size <= 24:
            ns_status = EvalMetricStatus.PASS
        elif 16 <= name_size <= 28:
            ns_status = EvalMetricStatus.WARN
        else:
            ns_status = EvalMetricStatus.FAIL
        results.append(
            EvalMetricResult(
                name="template_name_font_size",
                category=_CATEGORY,
                status=ns_status,
                value=name_size,
                detail=f"{name_size}pt (target: 20-24pt)",
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="template_name_font_size",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="Could not parse name font size",
            )
        )

    # template_heading_font_size — text(size: ...) in heading show rule.
    heading_size = _extract_heading_size(content)
    if heading_size is not None:
        if 14 <= heading_size <= 16:
            hs_status = EvalMetricStatus.PASS
        elif 12 <= heading_size <= 18:
            hs_status = EvalMetricStatus.WARN
        else:
            hs_status = EvalMetricStatus.FAIL
        results.append(
            EvalMetricResult(
                name="template_heading_font_size",
                category=_CATEGORY,
                status=hs_status,
                value=heading_size,
                detail=f"{heading_size}pt (target: 14-16pt)",
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="template_heading_font_size",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="Could not parse heading font size",
            )
        )

    # template_margins — #set page(margin: ...).
    # 0.3in is the tightest lower bound the templates render at across all
    # page budgets; 1.0in is the upper bound for generous breathing room.
    # 0.25-1.1in is the WARN fallback.
    margins = _extract_margins(content)
    if margins:
        all_ok = all(0.3 <= m <= 1.0 for m in margins.values())
        nearly_ok = all(0.25 <= m <= 1.1 for m in margins.values())
        margin_str = ", ".join(f"{k}: {v}in" for k, v in margins.items())
        results.append(
            EvalMetricResult(
                name="template_margins",
                category=_CATEGORY,
                status=EvalMetricStatus.PASS
                if all_ok
                else EvalMetricStatus.WARN
                if nearly_ok
                else EvalMetricStatus.FAIL,
                value=margins,
                detail=f"Margins: {margin_str} (target: 0.3-1.0in)",
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="template_margins",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="Could not parse margins",
            )
        )

    # template_font_families — max 3, primary must be sans-serif.
    fonts = _extract_font_families(content)
    if fonts:
        count_ok = len(fonts) <= 3
        primary_sans = fonts[0].lower() in _SANS_SERIF_FONTS
        results.append(
            EvalMetricResult(
                name="template_font_families",
                category=_CATEGORY,
                status=EvalMetricStatus.PASS
                if count_ok and primary_sans
                else EvalMetricStatus.WARN
                if count_ok or primary_sans
                else EvalMetricStatus.FAIL,
                value=fonts,
                detail=f"{len(fonts)} families: {', '.join(fonts)}"
                + (
                    " (primary is sans-serif)"
                    if primary_sans
                    else " (primary is NOT sans-serif)"
                ),
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="template_font_families",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="Could not parse font families",
            )
        )

    # template_accent_color — WCAG AA >=4.5:1 against white.
    color = _extract_accent_color(content)
    if color is not None:
        contrast = _wcag_contrast_ratio(color, (255, 255, 255))
        if contrast >= 4.5:
            ac_status = EvalMetricStatus.PASS
        elif contrast >= 3.0:
            ac_status = EvalMetricStatus.WARN
        else:
            ac_status = EvalMetricStatus.FAIL
        hex_str = "#{:02x}{:02x}{:02x}".format(*color)
        results.append(
            EvalMetricResult(
                name="template_accent_color",
                category=_CATEGORY,
                status=ac_status,
                value=hex_str,
                detail=f"{hex_str} contrast ratio: {contrast:.1f}:1 (min: 4.5:1)",
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="template_accent_color",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="Could not parse accent color",
            )
        )

    # template_line_spacing — #set par(leading: ...).
    leading = _extract_leading(content)
    if leading is not None:
        if 0.5 <= leading <= 0.65:
            ls_status = EvalMetricStatus.PASS
        elif 0.45 <= leading <= 0.75:
            ls_status = EvalMetricStatus.WARN
        else:
            ls_status = EvalMetricStatus.FAIL
        results.append(
            EvalMetricResult(
                name="template_line_spacing",
                category=_CATEGORY,
                status=ls_status,
                value=leading,
                detail=f"{leading}em (target: 0.5-0.65em)",
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="template_line_spacing",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="Could not parse line spacing",
            )
        )

    # template_bullet_indent — #set list(indent: ...).
    indent = _extract_bullet_indent(content)
    if indent is not None:
        if 0.2 <= indent <= 0.5:
            bi_status = EvalMetricStatus.PASS
        elif 0.1 <= indent <= 0.6:
            bi_status = EvalMetricStatus.WARN
        else:
            bi_status = EvalMetricStatus.FAIL
        results.append(
            EvalMetricResult(
                name="template_bullet_indent",
                category=_CATEGORY,
                status=bi_status,
                value=indent,
                detail=f"{indent}in (target: 0.2-0.5in)",
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="template_bullet_indent",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="Could not parse bullet indent",
            )
        )

    # template_section_spacing — v() calls in heading show rules (HARD).
    # Sum of vertical spacing around the section heading. 8-26pt covers
    # both compact rule-based layouts and airy tracked-caps layouts.
    spacing = _extract_section_spacing(content)
    if spacing is not None:
        if 8 <= spacing <= 26:
            ss_status = EvalMetricStatus.PASS
        elif 6 <= spacing <= 32:
            ss_status = EvalMetricStatus.WARN
        else:
            ss_status = EvalMetricStatus.FAIL
        results.append(
            EvalMetricResult(
                name="template_section_spacing",
                category=_CATEGORY,
                status=ss_status,
                value=spacing,
                detail=f"{spacing}pt (target: 8-26pt)",
            )
        )
    else:
        results.append(
            EvalMetricResult(
                name="template_section_spacing",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="Could not parse section spacing",
            )
        )

    return results


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _extract_text_size(content: str) -> float | None:
    """Extract body text size from ``#set text(... size: Npt ...)``.

    Tolerates one level of nested parens so font tuples like
    ``font: ("Inter", "Ubuntu Sans")`` do not terminate the match early.
    """
    match = re.search(
        r"#set\s+text\([^()]*(?:\([^()]*\)[^()]*)*size:\s*([\d.]+)pt",
        content,
    )
    return float(match.group(1)) if match else None


def _extract_name_size(content: str) -> float | None:
    """Extract name font size from bold ``#text(size: Npt, ...)`` blocks.

    The name is identified as the largest bold ``#text`` block in the
    template. Permits trailing args after ``weight: "bold"`` (e.g.,
    ``tracking: 0.5pt``) so heading and contact blocks are considered
    candidates and the largest wins.
    """
    matches = re.findall(
        r'#text\(size:\s*([\d.]+)pt[^)]*weight:\s*"bold"',
        content,
    )
    if not matches:
        return None
    return max(float(m) for m in matches)


def _extract_heading_size(content: str) -> float | None:
    """Extract heading font size from show heading rule."""
    # Look for text(size: ...) inside heading show rule.
    match = re.search(
        r"#show\s+heading\.where.*?text\(size:\s*([\d.]+)pt",
        content,
        re.DOTALL,
    )
    return float(match.group(1)) if match else None


def get_uniform_page_margin_pt(template_path: Path | None) -> float | None:
    """Read the template's per-side page margin in points.

    Reads ``#set page(margin: ...)`` and averages the four sides; returns
    ``None`` when the template is missing or the margin can't be parsed.
    Used by the PDF whitespace_ratio metric to compute the printable
    area without hardcoding 0.5in.
    """
    if template_path is None or not template_path.exists():
        return None
    try:
        content = template_path.read_text(encoding="utf-8")
    except OSError:
        return None
    margins = _extract_margins(content)
    if not margins:
        return None
    sides = [margins.get(side) for side in ("top", "right", "bottom", "left")]
    valid = [s for s in sides if s is not None]
    if not valid:
        return None
    avg_inches = sum(valid) / len(valid)
    return avg_inches * 72.0


def _extract_margins(content: str) -> dict[str, float] | None:
    """Extract page margins from ``#set page(... margin: ...)``."""
    # Uniform margin: margin: 0.5in
    match = re.search(r"#set\s+page\([^)]*margin:\s*([\d.]+)in", content)
    if match:
        val = float(match.group(1))
        return {"top": val, "right": val, "bottom": val, "left": val}

    # Dict margin: margin: (top: 0.5in, left: 0.5in, ...)
    match = re.search(r"#set\s+page\([^)]*margin:\s*\(([^)]+)\)", content)
    if match:
        margins: dict[str, float] = {}
        for side_match in re.finditer(r"(\w+):\s*([\d.]+)in", match.group(1)):
            margins[side_match.group(1)] = float(side_match.group(2))
        return margins or None

    return None


def _extract_font_families(content: str) -> list[str] | None:
    """Extract font families from ``#set text(font: (...), ...)``."""
    match = re.search(r"#set\s+text\(font:\s*\(([^)]+)\)", content)
    if match:
        fonts = re.findall(r'"([^"]+)"', match.group(1))
        return fonts or None
    # Single font: #set text(font: "Inter", ...)
    match = re.search(r'#set\s+text\(font:\s*"([^"]+)"', content)
    if match:
        return [match.group(1)]
    return None


def _extract_accent_color(content: str) -> tuple[int, int, int] | None:
    """Extract accent color from ``rgb("#RRGGBB")``."""
    match = re.search(r'rgb\("?#([0-9a-fA-F]{6})"?\)', content)
    if match:
        hex_str = match.group(1)
        r = int(hex_str[0:2], 16)
        g = int(hex_str[2:4], 16)
        b = int(hex_str[4:6], 16)
        return (r, g, b)
    return None


def _extract_leading(content: str) -> float | None:
    """Extract line spacing from ``#set par(... leading: Nem ...)``."""
    match = re.search(r"#set\s+par\([^)]*leading:\s*([\d.]+)em", content)
    return float(match.group(1)) if match else None


def _extract_bullet_indent(content: str) -> float | None:
    """Extract bullet indent from ``#set list(... indent: Nin ...)``."""
    match = re.search(r"#set\s+list\([^)]*indent:\s*([\d.]+)in", content)
    return float(match.group(1)) if match else None


def _extract_section_spacing(content: str) -> float | None:
    """Extract section spacing from v() calls in heading show rules (HARD).

    Sums v(Npt) values in the heading show rule body.
    """
    # Find heading show rule block.
    match = re.search(
        r"#show\s+heading\.where\(level:\s*2\).*?\{(.*?)\}",
        content,
        re.DOTALL,
    )
    if not match:
        return None

    body = match.group(1)
    # Sum all v(Npt) values.
    v_values = re.findall(r"v\(([\d.]+)pt\)", body)
    if v_values:
        return sum(float(v) for v in v_values)
    return None


def _wcag_contrast_ratio(
    fg: tuple[int, int, int],
    bg: tuple[int, int, int],
) -> float:
    """Compute WCAG 2.0 contrast ratio between two sRGB colors."""

    def relative_luminance(rgb: tuple[int, int, int]) -> float:
        vals: list[float] = []
        for c in rgb:
            s = c / 255.0
            vals.append(s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4)
        return 0.2126 * vals[0] + 0.7152 * vals[1] + 0.0722 * vals[2]

    l1 = relative_luminance(fg)
    l2 = relative_luminance(bg)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)
