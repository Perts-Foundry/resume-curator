"""Tests for curator.rules — word lists and threshold constants."""

from __future__ import annotations

import re

import pytest

from curator.rules import (
    ACTION_VERBS,
    AI_RED_FLAG_PHRASES,
    AI_RED_FLAG_WORDS,
    CATEGORY_WEIGHTS,
    COVER_LETTER_BODY_MAX_COUNT,
    COVER_LETTER_BODY_MIN_COUNT,
    COVER_LETTER_FORBIDDEN_PHRASES,
    COVER_LETTER_FORBIDDEN_WORDS,
    COVER_LETTER_MAX_TOKENS_HEADROOM,
    COVER_LETTER_PARAGRAPH_WORD_MAX,
    COVER_LETTER_PARAGRAPH_WORD_MIN,
    COVER_LETTER_PLACEHOLDER_PATTERN,
    COVER_LETTER_VALID_SIGN_OFFS,
    COVER_LETTER_WORD_MAX,
    COVER_LETTER_WORD_MIN,
    PLACEHOLDER_PATTERNS,
    SCORE_PASS_THRESHOLD,
    SCORE_WARN_THRESHOLD,
    SOFT_SKILLS,
    TRIVIAL_SKILLS,
    WEAK_PHRASES,
    render_cover_letter_forbidden_phrases_for_prompt,
    render_cover_letter_forbidden_words_for_prompt,
    render_cover_letter_valid_sign_offs_for_prompt,
)


@pytest.mark.unit
@pytest.mark.eval
class TestWeakPhrases:
    def test_non_empty(self) -> None:
        assert len(WEAK_PHRASES) > 0

    def test_no_duplicates(self) -> None:
        assert len(WEAK_PHRASES) == len(set(WEAK_PHRASES))

    def test_all_lowercase(self) -> None:
        for phrase in WEAK_PHRASES:
            assert phrase == phrase.lower(), f"'{phrase}' is not lowercase"

    def test_is_frozenset(self) -> None:
        assert isinstance(WEAK_PHRASES, frozenset)


@pytest.mark.unit
@pytest.mark.eval
class TestAIRedFlags:
    def test_words_non_empty(self) -> None:
        assert len(AI_RED_FLAG_WORDS) > 0

    def test_phrases_non_empty(self) -> None:
        assert len(AI_RED_FLAG_PHRASES) > 0

    def test_words_all_lowercase(self) -> None:
        for word in AI_RED_FLAG_WORDS:
            assert word == word.lower(), f"'{word}' is not lowercase"

    def test_phrases_all_lowercase(self) -> None:
        for phrase in AI_RED_FLAG_PHRASES:
            assert phrase == phrase.lower(), f"'{phrase}' is not lowercase"

    def test_words_no_duplicates(self) -> None:
        assert len(AI_RED_FLAG_WORDS) == len(set(AI_RED_FLAG_WORDS))

    def test_phrases_no_duplicates(self) -> None:
        assert len(AI_RED_FLAG_PHRASES) == len(set(AI_RED_FLAG_PHRASES))


@pytest.mark.unit
@pytest.mark.eval
class TestActionVerbs:
    def test_non_empty(self) -> None:
        assert len(ACTION_VERBS) > 0

    def test_contains_present_tense_forms(self) -> None:
        present_verbs = {"deploy", "build", "design", "develop", "lead"}
        assert present_verbs.issubset(ACTION_VERBS)

    def test_contains_past_tense_forms(self) -> None:
        past_verbs = {"deployed", "built", "designed", "developed", "led"}
        assert past_verbs.issubset(ACTION_VERBS)

    def test_is_frozenset(self) -> None:
        assert isinstance(ACTION_VERBS, frozenset)


@pytest.mark.unit
@pytest.mark.eval
class TestCategoryWeights:
    def test_sums_to_one(self) -> None:
        assert abs(sum(CATEGORY_WEIGHTS.values()) - 1.0) < 1e-9

    def test_has_expected_categories(self) -> None:
        expected = {
            "jd_alignment",
            "writing_quality",
            "pdf_output",
            "selection_quality",
            "content_density",
            "template_correctness",
            "date_consistency",
        }
        assert set(CATEGORY_WEIGHTS.keys()) == expected

    def test_all_weights_positive(self) -> None:
        for name, weight in CATEGORY_WEIGHTS.items():
            assert weight > 0, f"'{name}' has non-positive weight {weight}"


@pytest.mark.unit
@pytest.mark.eval
class TestTrivialSkills:
    def test_non_empty(self) -> None:
        assert len(TRIVIAL_SKILLS) > 0

    def test_all_lowercase(self) -> None:
        for skill in TRIVIAL_SKILLS:
            assert skill == skill.lower(), f"'{skill}' is not lowercase"

    def test_is_frozenset(self) -> None:
        assert isinstance(TRIVIAL_SKILLS, frozenset)


@pytest.mark.unit
@pytest.mark.eval
class TestSoftSkills:
    def test_non_empty(self) -> None:
        assert len(SOFT_SKILLS) > 0

    def test_all_lowercase(self) -> None:
        for skill in SOFT_SKILLS:
            assert skill == skill.lower(), f"'{skill}' is not lowercase"

    def test_is_frozenset(self) -> None:
        assert isinstance(SOFT_SKILLS, frozenset)


@pytest.mark.unit
@pytest.mark.eval
class TestPlaceholderPatterns:
    def test_non_empty(self) -> None:
        assert len(PLACEHOLDER_PATTERNS) > 0

    def test_all_lowercase(self) -> None:
        for pattern in PLACEHOLDER_PATTERNS:
            assert pattern == pattern.lower(), f"'{pattern}' is not lowercase"


@pytest.mark.unit
@pytest.mark.eval
class TestWordListConsistency:
    def test_no_overlap_action_verbs_and_ai_red_flags(self) -> None:
        overlap = ACTION_VERBS & AI_RED_FLAG_WORDS
        assert not overlap, f"ACTION_VERBS/AI_RED_FLAG_WORDS overlap: {overlap}"

    def test_no_overlap_action_verbs_and_weak_phrases(self) -> None:
        # Action verbs should not appear as weak phrases (single-word overlap)
        action_lower = {v.lower() for v in ACTION_VERBS}
        weak_single = {p for p in WEAK_PHRASES if " " not in p}
        overlap = action_lower & weak_single
        assert not overlap, f"Overlap: {overlap}"


@pytest.mark.unit
@pytest.mark.eval
class TestScoringThresholds:
    def test_pass_above_warn(self) -> None:
        assert SCORE_PASS_THRESHOLD > SCORE_WARN_THRESHOLD

    def test_expected_values(self) -> None:
        assert SCORE_PASS_THRESHOLD == 85
        assert SCORE_WARN_THRESHOLD == 75


@pytest.mark.unit
class TestCoverLetterConstants:
    def test_forbidden_words_non_empty(self) -> None:
        assert len(COVER_LETTER_FORBIDDEN_WORDS) > 0

    def test_forbidden_words_lowercase(self) -> None:
        for word in COVER_LETTER_FORBIDDEN_WORDS:
            assert word == word.lower(), f"'{word}' is not lowercase"

    def test_forbidden_phrases_non_empty(self) -> None:
        assert len(COVER_LETTER_FORBIDDEN_PHRASES) > 0

    def test_forbidden_phrases_lowercase(self) -> None:
        for phrase in COVER_LETTER_FORBIDDEN_PHRASES:
            assert phrase == phrase.lower(), f"'{phrase}' is not lowercase"

    def test_are_frozensets(self) -> None:
        assert isinstance(COVER_LETTER_FORBIDDEN_WORDS, frozenset)
        assert isinstance(COVER_LETTER_FORBIDDEN_PHRASES, frozenset)
        assert isinstance(COVER_LETTER_VALID_SIGN_OFFS, frozenset)

    def test_word_count_band_sensible(self) -> None:
        assert 0 < COVER_LETTER_WORD_MIN < COVER_LETTER_WORD_MAX
        assert 0 < COVER_LETTER_PARAGRAPH_WORD_MIN < COVER_LETTER_PARAGRAPH_WORD_MAX

    def test_body_count_band_sensible(self) -> None:
        assert 1 <= COVER_LETTER_BODY_MIN_COUNT <= COVER_LETTER_BODY_MAX_COUNT

    def test_max_tokens_headroom_positive(self) -> None:
        assert COVER_LETTER_MAX_TOKENS_HEADROOM > 0

    def test_sign_offs_populated(self) -> None:
        # Sincerely is the canonical default and must be in the allowed set.
        assert "Sincerely" in COVER_LETTER_VALID_SIGN_OFFS

    def test_placeholder_pattern_matches_known_tokens(self) -> None:
        pattern = re.compile(COVER_LETTER_PLACEHOLDER_PATTERN)
        assert pattern.search("[COMPANY]") is not None
        assert pattern.search("[HIRING_MANAGER_NAME]") is not None
        assert pattern.search("[TAILOR: replace this sentence.]") is not None

    def test_placeholder_pattern_ignores_prose_brackets(self) -> None:
        pattern = re.compile(COVER_LETTER_PLACEHOLDER_PATTERN)
        assert pattern.search("[a quote in prose]") is None
        assert pattern.search("[1]") is None


@pytest.mark.unit
class TestCoverLetterRenderers:
    def test_forbidden_words_prompt_has_every_entry(self) -> None:
        rendered = render_cover_letter_forbidden_words_for_prompt()
        for word in COVER_LETTER_FORBIDDEN_WORDS:
            assert word in rendered

    def test_forbidden_phrases_prompt_has_every_entry(self) -> None:
        rendered = render_cover_letter_forbidden_phrases_for_prompt()
        for phrase in COVER_LETTER_FORBIDDEN_PHRASES:
            assert phrase in rendered

    def test_sign_offs_prompt_has_every_entry(self) -> None:
        rendered = render_cover_letter_valid_sign_offs_for_prompt()
        for sign_off in COVER_LETTER_VALID_SIGN_OFFS:
            assert sign_off in rendered
