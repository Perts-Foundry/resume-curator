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


class TestEvalFunctionDefaults:
    """``bands`` parameter on evaluate_* defaults to SHORT_FORM_BANDS.

    A silent flip of the default would mis-score every direct caller
    that omits the kwarg. Pin the default here so a refactor that
    accidentally changes it (or removes the default) fails this test.
    """

    def test_evaluate_content_default(self) -> None:
        from curator.eval.content import evaluate_content

        sig = inspect.signature(evaluate_content)
        assert sig.parameters["bands"].default is SHORT_FORM_BANDS

    def test_evaluate_selection_default(self) -> None:
        from curator.eval.selection import evaluate_selection

        sig = inspect.signature(evaluate_selection)
        assert sig.parameters["bands"].default is SHORT_FORM_BANDS

    def test_evaluate_pdf_default(self) -> None:
        from curator.eval.pdf import evaluate_pdf

        sig = inspect.signature(evaluate_pdf)
        assert sig.parameters["bands"].default is SHORT_FORM_BANDS
