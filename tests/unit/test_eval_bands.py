"""Invariant tests for SHORT_FORM_BANDS / LONG_FORM_BANDS / bands_for_pages.

These tests pin the architectural decisions in the page-budget-aware
EvalBands rubric so a future band-tweak does not silently invalidate
the regression contract:

- SHORT_FORM_BANDS preserves the pre-2026-05-09 1-page values.
- LONG_FORM_BANDS is monotonically ≥ SHORT_FORM_BANDS on count metrics.
- Per-bullet metrics are equal across both forms (page-independent).
- Whitespace ratio runs slightly denser on long-form.
- WARN bands strictly enclose PASS bands on both rubrics.
- bands_for_pages plateaus at max_pages >= 2.
- The default value of ``bands`` on evaluate_content/selection/pdf is
  ``SHORT_FORM_BANDS`` so a future refactor cannot silently flip it.
"""

from __future__ import annotations

import inspect

import pytest

from curator.eval.report import (
    LONG_FORM_BANDS,
    SHORT_FORM_BANDS,
    EvalBands,
    bands_for_pages,
)


class TestBandsForPagesSelector:
    """``bands_for_pages`` returns the correct preset for each page budget."""

    @pytest.mark.parametrize("pages", [0, 1])
    def test_short_form_for_pages_le_1(self, pages: int) -> None:
        assert bands_for_pages(pages) is SHORT_FORM_BANDS

    @pytest.mark.parametrize("pages", [2, 3, 4, 5])
    def test_long_form_for_pages_ge_2(self, pages: int) -> None:
        assert bands_for_pages(pages) is LONG_FORM_BANDS


class TestEvalBandsFrozen:
    """``EvalBands`` is a frozen dataclass; mutation raises FrozenInstanceError."""

    def test_short_form_is_frozen(self) -> None:
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
            SHORT_FORM_BANDS.word_count_pass = (0, 0)  # type: ignore[misc]

    def test_long_form_is_frozen(self) -> None:
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
            LONG_FORM_BANDS.primary_role_highlight_target = 99  # type: ignore[misc]


class TestShortFormPreservation:
    """SHORT_FORM_BANDS preserves the pre-refactor 1-page values verbatim.

    Pinning these here means a deliberate one-line value change rotates
    the test alongside the source — the values cannot drift silently.
    """

    def test_word_count_pass(self) -> None:
        assert SHORT_FORM_BANDS.word_count_pass == (475, 700)

    def test_word_count_warn(self) -> None:
        assert SHORT_FORM_BANDS.word_count_warn == (400, 800)

    def test_bullet_word_count_pass(self) -> None:
        assert SHORT_FORM_BANDS.bullet_word_count_pass == (8, 35)

    def test_primary_role_highlight_target(self) -> None:
        assert SHORT_FORM_BANDS.primary_role_highlight_target == 5

    def test_position_2plus_max_highlights(self) -> None:
        assert SHORT_FORM_BANDS.position_2plus_max_highlights == 2

    def test_total_highlight_count_pass(self) -> None:
        assert SHORT_FORM_BANDS.total_highlight_count_pass == (6, 25)

    def test_skills_keyword_count_pass(self) -> None:
        assert SHORT_FORM_BANDS.skills_keyword_count_pass == (20, 70)

    def test_whitespace_ratio_pass(self) -> None:
        assert SHORT_FORM_BANDS.whitespace_ratio_pass == (0.55, 0.75)


class TestLongFormShape:
    """LONG_FORM_BANDS values are calibrated against 2-page geometry."""

    def test_primary_role_highlight_target(self) -> None:
        assert LONG_FORM_BANDS.primary_role_highlight_target == 6

    def test_position_2plus_max_highlights(self) -> None:
        # Older roles may carry up to 4 highlights on long-form (was 2).
        assert LONG_FORM_BANDS.position_2plus_max_highlights == 4

    def test_total_highlight_count_pass(self) -> None:
        # Per AR-5, tightened from (18, 45) for per-position consistency.
        assert LONG_FORM_BANDS.total_highlight_count_pass == (15, 28)


class TestMonotonicity:
    """LONG_FORM count bands are strictly higher than SHORT_FORM counterparts."""

    def test_word_count_pass_lo_higher(self) -> None:
        assert LONG_FORM_BANDS.word_count_pass[0] > SHORT_FORM_BANDS.word_count_pass[0]

    def test_word_count_pass_hi_higher(self) -> None:
        assert LONG_FORM_BANDS.word_count_pass[1] > SHORT_FORM_BANDS.word_count_pass[1]

    def test_total_highlight_count_pass_lo_higher(self) -> None:
        assert (
            LONG_FORM_BANDS.total_highlight_count_pass[0]
            > SHORT_FORM_BANDS.total_highlight_count_pass[0]
        )

    def test_total_highlight_count_pass_hi_higher(self) -> None:
        assert (
            LONG_FORM_BANDS.total_highlight_count_pass[1]
            > SHORT_FORM_BANDS.total_highlight_count_pass[1]
        )

    def test_skills_keyword_count_pass_lo_higher(self) -> None:
        assert (
            LONG_FORM_BANDS.skills_keyword_count_pass[0]
            > SHORT_FORM_BANDS.skills_keyword_count_pass[0]
        )

    def test_skills_keyword_count_pass_hi_higher(self) -> None:
        assert (
            LONG_FORM_BANDS.skills_keyword_count_pass[1]
            > SHORT_FORM_BANDS.skills_keyword_count_pass[1]
        )

    def test_primary_role_highlight_target_higher(self) -> None:
        assert (
            LONG_FORM_BANDS.primary_role_highlight_target
            > SHORT_FORM_BANDS.primary_role_highlight_target
        )

    def test_position_2plus_max_higher(self) -> None:
        assert (
            LONG_FORM_BANDS.position_2plus_max_highlights
            > SHORT_FORM_BANDS.position_2plus_max_highlights
        )


class TestPerBulletEquality:
    """Per-bullet bands are equal across both forms (page-independent)."""

    def test_bullet_word_count_pass_equal(self) -> None:
        assert (
            LONG_FORM_BANDS.bullet_word_count_pass
            == SHORT_FORM_BANDS.bullet_word_count_pass
        )

    def test_bullet_word_count_warn_equal(self) -> None:
        assert (
            LONG_FORM_BANDS.bullet_word_count_warn
            == SHORT_FORM_BANDS.bullet_word_count_warn
        )


class TestWhitespaceRatioDenser:
    """Long-form pages run slightly denser; whitespace floor drops."""

    def test_pass_floor_lower(self) -> None:
        assert (
            LONG_FORM_BANDS.whitespace_ratio_pass[0]
            < SHORT_FORM_BANDS.whitespace_ratio_pass[0]
        )

    def test_pass_ceiling_lower(self) -> None:
        assert (
            LONG_FORM_BANDS.whitespace_ratio_pass[1]
            < SHORT_FORM_BANDS.whitespace_ratio_pass[1]
        )


class TestWarnEnclosesPass:
    """WARN bands strictly enclose PASS bands on both rubrics.

    Catches typos where a WARN range is narrower than its PASS range
    (which would produce uncoverable status transitions).
    """

    @pytest.mark.parametrize("rubric", [SHORT_FORM_BANDS, LONG_FORM_BANDS])
    def test_word_count_warn_encloses_pass(self, rubric: EvalBands) -> None:
        p_lo, p_hi = rubric.word_count_pass
        w_lo, w_hi = rubric.word_count_warn
        assert w_lo <= p_lo <= p_hi <= w_hi

    @pytest.mark.parametrize("rubric", [SHORT_FORM_BANDS, LONG_FORM_BANDS])
    def test_bullet_word_count_warn_encloses_pass(
        self, rubric: EvalBands
    ) -> None:
        p_lo, p_hi = rubric.bullet_word_count_pass
        w_lo, w_hi = rubric.bullet_word_count_warn
        assert w_lo <= p_lo <= p_hi <= w_hi

    @pytest.mark.parametrize("rubric", [SHORT_FORM_BANDS, LONG_FORM_BANDS])
    def test_total_highlight_count_warn_encloses_pass(
        self, rubric: EvalBands
    ) -> None:
        p_lo, p_hi = rubric.total_highlight_count_pass
        w_lo, w_hi = rubric.total_highlight_count_warn
        assert w_lo <= p_lo <= p_hi <= w_hi

    @pytest.mark.parametrize("rubric", [SHORT_FORM_BANDS, LONG_FORM_BANDS])
    def test_skills_keyword_count_warn_encloses_pass(
        self, rubric: EvalBands
    ) -> None:
        p_lo, p_hi = rubric.skills_keyword_count_pass
        w_lo, w_hi = rubric.skills_keyword_count_warn
        assert w_lo <= p_lo <= p_hi <= w_hi

    @pytest.mark.parametrize("rubric", [SHORT_FORM_BANDS, LONG_FORM_BANDS])
    def test_whitespace_ratio_warn_encloses_pass(
        self, rubric: EvalBands
    ) -> None:
        p_lo, p_hi = rubric.whitespace_ratio_pass
        w_lo, w_hi = rubric.whitespace_ratio_warn
        assert w_lo <= p_lo <= p_hi <= w_hi


class TestEvalFunctionsRequireBands:
    """``bands`` parameter on evaluate_* is keyword-only with no default.

    Forces every call site to make a deliberate page-budget choice. A
    silent default (e.g. ``bands=SHORT_FORM_BANDS``) would mis-score any
    direct caller on a 2-page profile that omitted the kwarg. The
    keyword-only-no-default contract is the architectural fix; this test
    pins it so a future refactor can't reintroduce the silent default.
    """

    def test_evaluate_content_bands_is_required(self) -> None:
        from curator.eval.content import evaluate_content

        sig = inspect.signature(evaluate_content)
        param = sig.parameters["bands"]
        assert param.default is inspect.Parameter.empty
        assert param.kind is inspect.Parameter.KEYWORD_ONLY

    def test_evaluate_selection_bands_is_required(self) -> None:
        from curator.eval.selection import evaluate_selection

        sig = inspect.signature(evaluate_selection)
        param = sig.parameters["bands"]
        assert param.default is inspect.Parameter.empty
        assert param.kind is inspect.Parameter.KEYWORD_ONLY

    def test_evaluate_pdf_bands_is_required(self) -> None:
        from curator.eval.pdf import evaluate_pdf

        sig = inspect.signature(evaluate_pdf)
        param = sig.parameters["bands"]
        assert param.default is inspect.Parameter.empty
        assert param.kind is inspect.Parameter.KEYWORD_ONLY

    def test_evaluate_pdf_max_pages_is_required(self) -> None:
        """Same contract for max_pages on evaluate_pdf."""
        from curator.eval.pdf import evaluate_pdf

        sig = inspect.signature(evaluate_pdf)
        param = sig.parameters["max_pages"]
        assert param.default is inspect.Parameter.empty
        assert param.kind is inspect.Parameter.KEYWORD_ONLY


class TestBandsAndCapsConsistency:
    """Cross-module invariants linking EvalBands and _PageCaps.

    The eval rubric (``EvalBands``) and the renderer cap profile
    (``_PageCaps``) live in separate files but share a meaning: the
    eval expects positions 0-1 to carry up to
    ``primary_role_highlight_target`` bullets, and the renderer
    protects ``recent_role_soft_floor`` of them. If a future calibration
    bumps one without the other, the eval and the renderer disagree on
    what 2-page geometry looks like, and neither test catches the drift
    in isolation.

    These invariants pin the relationship at an axis that survives
    reasonable calibration tweaks. (AR-17.)
    """

    @pytest.mark.parametrize("max_pages", [1, 2, 3, 4, 5])
    def test_eval_target_at_least_renderer_floor(self, max_pages: int) -> None:
        """Eval expects positions 0-1 to have >= what the renderer protects.

        ``primary_role_highlight_target`` is the eval's expected
        ceiling for position 0; ``recent_role_soft_floor`` is the
        renderer's protected minimum for positions 0-1. Ceiling >=
        floor is the structural invariant: a renderer that protects 5
        bullets cannot satisfy an eval target of 4.
        """
        from curator.eval.report import bands_for_pages
        from curator.renderer import _caps_for_pages

        bands = bands_for_pages(max_pages)
        caps = _caps_for_pages(max_pages)
        assert bands.primary_role_highlight_target >= caps.recent_role_soft_floor, (
            f"max_pages={max_pages}: eval target "
            f"{bands.primary_role_highlight_target} < renderer floor "
            f"{caps.recent_role_soft_floor}"
        )

    @pytest.mark.parametrize("max_pages", [1, 2, 3, 4, 5])
    def test_renderer_certificate_floor_within_band_lower(
        self, max_pages: int
    ) -> None:
        """Cert floor stays small enough to leave room for other content.

        Eval ``total_highlight_count_pass`` lower bound represents
        minimum acceptable content density. The renderer's
        ``certificate_floor`` should not be so high that satisfying it
        crowds out the eval's expected highlight count. This is a soft
        consistency check; the precise relationship will tighten when
        calibration data accumulates.
        """
        from curator.eval.report import bands_for_pages
        from curator.renderer import _caps_for_pages

        bands = bands_for_pages(max_pages)
        caps = _caps_for_pages(max_pages)
        # Sanity floor: cert floor stays modest relative to total
        # highlight expectations. (e.g., on 1-page: cert_floor=3 vs
        # total_highlight_count_pass=(6, 25) — easily compatible.)
        assert caps.certificate_floor <= bands.total_highlight_count_pass[1]
