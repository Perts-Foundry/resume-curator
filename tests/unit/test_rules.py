"""Pin tests for ``curator.rules`` constants.

These pins exist so a deliberate one-line value change rotates the
test alongside the source; the constants cannot drift silently.
"""

from __future__ import annotations

import pytest

from curator.rules import (
    COVER_LETTER_PARAGRAPH_PROMPT_TARGET_MAX,
    COVER_LETTER_PARAGRAPH_WORD_MAX,
    COVER_LETTER_PARAGRAPH_WORD_MIN,
    COVER_LETTER_WORD_MAX,
    COVER_LETTER_WORD_MIN,
    COVER_LETTER_WORD_TARGET,
)


@pytest.mark.unit
class TestCoverLetterWordConstants:
    """Cover-letter word-count constants form a coherent target band."""

    def test_word_target_pinned_value(self) -> None:
        """Raised from 265 to 305 (2026-05-17) when COVER_LETTER_WORD_MAX
        moved from 300 to 360; the target re-centers inside the new band
        so the prompt-side target prose stays a sensible aim point.

        Earlier values: 275 (initial), 265 (lowered 2026-05-10 to track a
        prompt-prose tightening to 80-87 per body paragraph)."""
        assert COVER_LETTER_WORD_TARGET == 305

    def test_word_min_max_unchanged(self) -> None:
        """Validator hard min unchanged; soft cap moved from 300 to 360
        on 2026-05-17. Real-world validation (10 JDs) showed the model's
        natural total-word band is ~290-360; the prior 300 cap fired
        ``over_cap=true`` on 7/9 cover letters that nonetheless passed
        every other validator. The bump removes the chronic false alarm
        without changing rejection semantics."""
        assert COVER_LETTER_WORD_MIN == 250
        assert COVER_LETTER_WORD_MAX == 360

    def test_word_target_in_band(self) -> None:
        """Target lives strictly inside the validator band."""
        assert COVER_LETTER_WORD_MIN < COVER_LETTER_WORD_TARGET < COVER_LETTER_WORD_MAX

    def test_paragraph_word_max_unchanged(self) -> None:
        """Per-paragraph hard cap stays at 90.

        The prompt prose was tightened from 80-90 to 80-87 to give the
        model a tighter steering target, but the validator cap stays at
        90 so legitimate cases in the 86-90 range still ship. Mirrors
        the summary's ``SUMMARY_WORD_TARGET_MAX < SUMMARY_WORD_HARD_MAX``
        slack pattern.
        """
        assert COVER_LETTER_PARAGRAPH_WORD_MAX == 90
        assert COVER_LETTER_PARAGRAPH_WORD_MIN == 40

    def test_paragraph_prompt_target_max_pinned(self) -> None:
        """Prompt-side body upper bound (87) is below validator cap (90).

        Surfaced to the model in both the prompt rulebook prose AND the
        Pydantic field descriptions for ``body_paragraph_*`` so the
        schema-level constraint and the prose agree. Prevents the
        prompt-vs-schema inconsistency that the prompt-reviewer CRIT-1
        finding flagged: schema-level constraints are weighted heavily
        by the model, so the prose alone (saying 80-87) was undermined
        when the schema said 80-90.
        """
        assert COVER_LETTER_PARAGRAPH_PROMPT_TARGET_MAX == 87
        assert (
            COVER_LETTER_PARAGRAPH_PROMPT_TARGET_MAX < COVER_LETTER_PARAGRAPH_WORD_MAX
        )
        assert (
            COVER_LETTER_PARAGRAPH_PROMPT_TARGET_MAX > COVER_LETTER_PARAGRAPH_WORD_MIN
        )
