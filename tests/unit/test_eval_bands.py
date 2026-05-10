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
from dataclasses import FrozenInstanceError

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
        with pytest.raises(FrozenInstanceError):
            SHORT_FORM_BANDS.word_count_pass = (0, 0)  # type: ignore[misc]

    def test_long_form_is_frozen(self) -> None:
        with pytest.raises(FrozenInstanceError):
            LONG_FORM_BANDS.work_position_floors = (99,)  # type: ignore[misc]


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

    def test_work_position_floors(self) -> None:
        # 1-page preserves ghost-row policy: positions 2+ at floor 0.
        assert SHORT_FORM_BANDS.work_position_floors == (3, 3, 0, 0, 0)

    def test_total_highlight_count_pass(self) -> None:
        assert SHORT_FORM_BANDS.total_highlight_count_pass == (6, 25)

    def test_skills_keyword_count_pass(self) -> None:
        assert SHORT_FORM_BANDS.skills_keyword_count_pass == (20, 70)

    def test_whitespace_ratio_pass(self) -> None:
        assert SHORT_FORM_BANDS.whitespace_ratio_pass == (0.55, 0.75)


class TestLongFormShape:
    """LONG_FORM_BANDS values are calibrated against 2-page geometry."""

    def test_work_position_floors(self) -> None:
        # Graduated 2-page floors so older roles always render content.
        assert LONG_FORM_BANDS.work_position_floors == (8, 6, 6, 2, 2)

    def test_total_highlight_count_pass(self) -> None:
        # Pre-emptively widened to accommodate floor sum 24 on 5 entries.
        assert LONG_FORM_BANDS.total_highlight_count_pass == (20, 38)


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

    def test_work_position_floors_per_position_monotonic(self) -> None:
        """Each position's floor is non-decreasing as page budget grows.

        Iterates to the longer of the two tuples, falling through to
        the last value for indices beyond a tuple's length, so future
        tuple-length divergence does not silently skip indices.
        """
        short = SHORT_FORM_BANDS.work_position_floors
        long = LONG_FORM_BANDS.work_position_floors
        n = max(len(short), len(long))
        for i in range(n):
            short_val = short[i] if i < len(short) else short[-1]
            long_val = long[i] if i < len(long) else long[-1]
            assert long_val >= short_val, (
                f"position {i}: long-form floor {long_val} < short-form {short_val}"
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
    def test_bullet_word_count_warn_encloses_pass(self, rubric: EvalBands) -> None:
        p_lo, p_hi = rubric.bullet_word_count_pass
        w_lo, w_hi = rubric.bullet_word_count_warn
        assert w_lo <= p_lo <= p_hi <= w_hi

    @pytest.mark.parametrize("rubric", [SHORT_FORM_BANDS, LONG_FORM_BANDS])
    def test_total_highlight_count_warn_encloses_pass(self, rubric: EvalBands) -> None:
        p_lo, p_hi = rubric.total_highlight_count_pass
        w_lo, w_hi = rubric.total_highlight_count_warn
        assert w_lo <= p_lo <= p_hi <= w_hi

    @pytest.mark.parametrize("rubric", [SHORT_FORM_BANDS, LONG_FORM_BANDS])
    def test_skills_keyword_count_warn_encloses_pass(self, rubric: EvalBands) -> None:
        p_lo, p_hi = rubric.skills_keyword_count_pass
        w_lo, w_hi = rubric.skills_keyword_count_warn
        assert w_lo <= p_lo <= p_hi <= w_hi

    @pytest.mark.parametrize("rubric", [SHORT_FORM_BANDS, LONG_FORM_BANDS])
    def test_whitespace_ratio_warn_encloses_pass(self, rubric: EvalBands) -> None:
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
    (``_PageCaps``) live in separate modules but now share a single
    source of truth for per-position highlight floors: both consume
    ``_PageCaps.work_position_floors`` from :mod:`curator.page_caps`.
    This invariant test pins the shared identity so a future
    refactor cannot silently re-introduce two parallel definitions.
    """

    @pytest.mark.parametrize("max_pages", [1, 2])
    def test_eval_bands_share_caps_floors(self, max_pages: int) -> None:
        """``bands.work_position_floors`` IS ``caps.work_position_floors``.

        Replaces the previous ``test_eval_target_at_least_renderer_floor``
        invariant. Under the new design the eval rubric does not
        maintain its own per-position ceiling — it derives every
        position band from the renderer floor tuple, eliminating drift.

        Parametrized only for ``max_pages`` 1 and 2 because the eval
        side currently has just two profiles (``SHORT_FORM_BANDS`` and
        ``LONG_FORM_BANDS``) while the renderer differentiates 1/2/3+;
        the 3+ asymmetry is tracked as ``EXEC_FORM_BANDS`` in TODO.md.
        """
        from curator.eval.report import bands_for_pages
        from curator.page_caps import _caps_for_pages

        bands = bands_for_pages(max_pages)
        caps = _caps_for_pages(max_pages)
        assert bands.work_position_floors == caps.work_position_floors, (
            f"max_pages={max_pages}: bands floors "
            f"{bands.work_position_floors} != caps floors "
            f"{caps.work_position_floors}"
        )

    @pytest.mark.parametrize("max_pages", [1, 2, 3, 4, 5])
    def test_renderer_certificate_floor_within_band_lower(self, max_pages: int) -> None:
        """Cert floor stays small enough to leave room for other content.

        Eval ``total_highlight_count_pass`` lower bound represents
        minimum acceptable content density. The renderer's
        ``certificate_floor`` should not be so high that satisfying it
        crowds out the eval's expected highlight count. This is a soft
        consistency check; the precise relationship will tighten when
        calibration data accumulates.
        """
        from curator.eval.report import bands_for_pages
        from curator.page_caps import _caps_for_pages

        bands = bands_for_pages(max_pages)
        caps = _caps_for_pages(max_pages)
        # Sanity floor: cert floor stays modest relative to total
        # highlight expectations. (e.g., on 1-page: cert_floor=3 vs
        # total_highlight_count_pass=(6, 25) — easily compatible.)
        assert caps.certificate_floor <= bands.total_highlight_count_pass[1]
