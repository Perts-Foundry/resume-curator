"""Tests for the zero-API static-mode curation synthesis."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from loguru import logger

from curator.exceptions import StaticModeError

if TYPE_CHECKING:
    from collections.abc import Generator
from curator.models import (
    Basics,
    CertificateEntry,
    CoverLetterCuration,
    EducationEntry,
    InterestData,
    LanguageEntry,
    PortfolioData,
    ProjectEntry,
    PublicationEntry,
    ResumeCuration,
    ServiceEntry,
    SkillEntry,
    VolunteerEntry,
    WorkEntry,
    validate_curation_ids,
)
from curator.rules import SUMMARY_MANDATORY_MENTION
from curator.static_mode import (
    build_static_result,
    synthesize_curation,
)


def _work_entry(
    entry_id: str, highlights: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "name": "Some Company",
        "position": "Engineer",
        "startDate": "2020-01",
        "endDate": "",
        "highlights": highlights or [],
    }


def _highlight(highlight_id: str) -> dict[str, Any]:
    return {"id": highlight_id, "text": f"Did {highlight_id}."}


@pytest.fixture
def rich_portfolio_dict(
    basics_dict: dict[str, object],
    education_entry_dict: dict[str, object],
    certificate_entry_dict: dict[str, object],
    volunteer_entry_dict: dict[str, object],
    publication_entry_dict: dict[str, object],
    language_entry_dict: dict[str, object],
    interest_data_dict: dict[str, object],
    service_entry_dict: dict[str, object],
) -> dict[str, Any]:
    """Rich portfolio payload for static-mode tests.

    Has two work entries, two skill groups, and three projects with mixed
    weights (including None) so sort and tie-breaking cases are exercised.
    """
    return {
        "basics": basics_dict,
        "work": [
            _work_entry(
                "acme-senior-engineer",
                highlights=[_highlight(f"h{i}") for i in range(5)],
            ),
            _work_entry(
                "prior-role",
                highlights=[_highlight(f"p{i}") for i in range(3)],
            ),
        ],
        "skills": [
            {
                "id": "cloud-aws",
                "name": "AWS",
                "level": "Advanced",
                "keywords": ["EKS", "Lambda"],
            },
            {
                "id": "languages",
                "name": "Languages",
                "level": "Advanced",
                "keywords": ["Python", "Go"],
            },
        ],
        "projects": [
            {"id": "proj-b", "name": "B", "description": "B", "weight": 2},
            {"id": "proj-a", "name": "A", "description": "A", "weight": 1},
            {"id": "proj-c", "name": "C", "description": "C"},
        ],
        "certificates": [certificate_entry_dict],
        "education": [education_entry_dict],
        "volunteer": [volunteer_entry_dict],
        "publications": [publication_entry_dict],
        "languages": [language_entry_dict],
        "interests": interest_data_dict,
        "services": [service_entry_dict],
    }


def _build_portfolio(
    payload: dict[str, Any], *, with_cover_letter: bool = True
) -> PortfolioData:
    from tests.helpers import valid_cover_letter

    return PortfolioData(
        basics=Basics.model_validate(payload["basics"]),
        work=[WorkEntry.model_validate(w) for w in payload["work"]],
        education=[EducationEntry.model_validate(e) for e in payload["education"]],
        skills=[SkillEntry.model_validate(s) for s in payload["skills"]],
        certificates=[
            CertificateEntry.model_validate(c) for c in payload["certificates"]
        ],
        projects=[ProjectEntry.model_validate(p) for p in payload["projects"]],
        volunteer=[VolunteerEntry.model_validate(v) for v in payload["volunteer"]],
        publications=[
            PublicationEntry.model_validate(p) for p in payload["publications"]
        ],
        languages=[LanguageEntry.model_validate(lang) for lang in payload["languages"]],
        interests=InterestData.model_validate(payload["interests"]),
        services=[ServiceEntry.model_validate(s) for s in payload["services"]],
        cover_letter=valid_cover_letter() if with_cover_letter else None,
    )


@pytest.fixture
def rich_portfolio(rich_portfolio_dict: dict[str, Any]) -> PortfolioData:
    return _build_portfolio(rich_portfolio_dict)


@pytest.fixture
def loguru_warnings() -> Generator[list[str], None, None]:
    """Capture WARNING-level log messages emitted via Loguru during the test.

    Loguru does not route to pytest's ``caplog`` by default, so we attach a
    list-sink for the duration of the test and hand callers the accumulated
    messages to assert on.
    """
    messages: list[str] = []
    handler_id = logger.add(
        lambda record: messages.append(record.record["message"]),
        level="WARNING",
    )
    try:
        yield messages
    finally:
        logger.remove(handler_id)


class TestSynthesizeCuration:
    """Deterministic ResumeCuration construction from portfolio data."""

    def test_returns_valid_resume_curation(self, rich_portfolio: PortfolioData) -> None:
        curation = synthesize_curation(rich_portfolio)
        assert isinstance(curation, ResumeCuration)
        # Round-trip model_validate to prove it's a valid instance.
        ResumeCuration.model_validate(curation.model_dump())

    def test_all_six_fields_populated(self, rich_portfolio: PortfolioData) -> None:
        curation = synthesize_curation(rich_portfolio)
        assert curation.summary == rich_portfolio.basics.summary
        assert curation.suggested_label == rich_portfolio.basics.label
        assert curation.company_slug == "general"
        assert {wh.work_id for wh in curation.work_highlights} == {
            "acme-senior-engineer",
            "prior-role",
        }
        assert {s.skill_id for s in curation.skills} == {"cloud-aws", "languages"}
        assert set(curation.projects) == {"proj-a", "proj-b", "proj-c"}

    def test_custom_name_becomes_slug(self, rich_portfolio: PortfolioData) -> None:
        curation = synthesize_curation(rich_portfolio, name="Acme Corp, Inc.")
        # ``slugify`` strips the trailing ``Inc`` legal-entity suffix
        # on 2026-05-17 (CORPORATE_SLUG_SUFFIXES). ``Corp`` is not in
        # the suffix set because it is often part of the public-facing
        # brand. Pre-strip slug was ``acme-corp-inc``.
        assert curation.company_slug == "acme-corp"

    def test_every_work_entry_gets_ranking(self, rich_portfolio: PortfolioData) -> None:
        curation = synthesize_curation(rich_portfolio)
        # validate_curation_ids enforces one ranking per portfolio work entry.
        validate_curation_ids(curation, rich_portfolio)

    def test_projects_sorted_by_weight_ascending(
        self, rich_portfolio: PortfolioData
    ) -> None:
        curation = synthesize_curation(rich_portfolio)
        # Weighted 1, 2 first (ascending), then unset.
        assert curation.projects == ["proj-a", "proj-b", "proj-c"]

    def test_weight_tie_breaking_is_stable(
        self, rich_portfolio_dict: dict[str, Any]
    ) -> None:
        rich_portfolio_dict["projects"] = [
            {"id": "first", "name": "First", "description": "x", "weight": 1},
            {"id": "second", "name": "Second", "description": "x", "weight": 1},
            {"id": "third", "name": "Third", "description": "x"},
            {"id": "fourth", "name": "Fourth", "description": "x"},
        ]
        portfolio = _build_portfolio(rich_portfolio_dict)
        curation = synthesize_curation(portfolio)
        assert curation.projects == ["first", "second", "third", "fourth"]

    def test_max_highlights_cap_applies(self, rich_portfolio: PortfolioData) -> None:
        curation = synthesize_curation(rich_portfolio, max_highlights_per_work=2)
        for wh in curation.work_highlights:
            assert len(wh.highlight_ids) <= 2

    def test_max_highlights_noop_when_entry_smaller(
        self, rich_portfolio: PortfolioData
    ) -> None:
        curation = synthesize_curation(rich_portfolio, max_highlights_per_work=100)
        # prior-role has 3 highlights; no truncation.
        prior = next(
            wh for wh in curation.work_highlights if wh.work_id == "prior-role"
        )
        assert len(prior.highlight_ids) == 3

    def test_skill_group_with_empty_keywords_is_skipped(
        self, rich_portfolio_dict: dict[str, Any], loguru_warnings: list[str]
    ) -> None:
        rich_portfolio_dict["skills"].append(
            {"id": "empty", "name": "Empty", "level": "None", "keywords": []}
        )
        portfolio = _build_portfolio(rich_portfolio_dict)
        curation = synthesize_curation(portfolio)
        skill_ids = {s.skill_id for s in curation.skills}
        assert "empty" not in skill_ids
        assert any("empty" in m and "zero keywords" in m for m in loguru_warnings)

    def test_summary_verbatim_when_present(self, rich_portfolio: PortfolioData) -> None:
        curation = synthesize_curation(rich_portfolio)
        assert curation.summary == "Experienced engineer."

    def test_summary_warns_when_mandatory_mention_missing(
        self, rich_portfolio_dict: dict[str, Any], loguru_warnings: list[str]
    ) -> None:
        basics = dict(rich_portfolio_dict["basics"])
        basics["summary"] = "A clean summary without the expected attribution."
        rich_portfolio_dict["basics"] = basics
        portfolio = _build_portfolio(rich_portfolio_dict)
        synthesize_curation(portfolio)
        assert any(
            "mandatory mention" in m and "Perts Foundry" in m for m in loguru_warnings
        )

    def test_summary_truncates_when_oversized(
        self, rich_portfolio_dict: dict[str, Any]
    ) -> None:
        basics = dict(rich_portfolio_dict["basics"])
        basics["summary"] = "x" * 1000
        rich_portfolio_dict["basics"] = basics
        portfolio = _build_portfolio(rich_portfolio_dict)
        curation = synthesize_curation(portfolio)
        assert len(curation.summary) == 600
        assert curation.summary.endswith("...")

    def test_summary_fallback_when_missing(
        self, rich_portfolio_dict: dict[str, Any]
    ) -> None:
        basics = dict(rich_portfolio_dict["basics"])
        basics.pop("summary", None)
        rich_portfolio_dict["basics"] = basics
        portfolio = _build_portfolio(rich_portfolio_dict)
        curation = synthesize_curation(portfolio)
        assert SUMMARY_MANDATORY_MENTION in curation.summary

    def test_label_fallback_when_missing(
        self, rich_portfolio_dict: dict[str, Any]
    ) -> None:
        basics = dict(rich_portfolio_dict["basics"])
        basics.pop("label", None)
        rich_portfolio_dict["basics"] = basics
        portfolio = _build_portfolio(rich_portfolio_dict)
        curation = synthesize_curation(portfolio)
        assert curation.suggested_label == "Professional"

    def test_label_truncates_to_60(self, rich_portfolio_dict: dict[str, Any]) -> None:
        basics = dict(rich_portfolio_dict["basics"])
        basics["label"] = "X" * 100
        rich_portfolio_dict["basics"] = basics
        portfolio = _build_portfolio(rich_portfolio_dict)
        curation = synthesize_curation(portfolio)
        assert len(curation.suggested_label) == 60

    def test_zero_projects_produces_empty_list(
        self, rich_portfolio_dict: dict[str, Any]
    ) -> None:
        rich_portfolio_dict["projects"] = []
        portfolio = _build_portfolio(rich_portfolio_dict)
        curation = synthesize_curation(portfolio)
        assert curation.projects == []

    def test_work_entry_with_zero_highlights_warns(
        self, rich_portfolio_dict: dict[str, Any], loguru_warnings: list[str]
    ) -> None:
        rich_portfolio_dict["work"][1]["highlights"] = []
        portfolio = _build_portfolio(rich_portfolio_dict)
        curation = synthesize_curation(portfolio)
        prior = next(
            wh for wh in curation.work_highlights if wh.work_id == "prior-role"
        )
        assert prior.highlight_ids == []
        assert any(
            "prior-role" in m and "zero highlights" in m for m in loguru_warnings
        )

    def test_empty_work_raises_static_mode_error(
        self, rich_portfolio_dict: dict[str, Any]
    ) -> None:
        rich_portfolio_dict["work"] = []
        portfolio = _build_portfolio(rich_portfolio_dict)
        with pytest.raises(StaticModeError, match="at least one work entry"):
            synthesize_curation(portfolio)

    def test_json_roundtrip(self, rich_portfolio: PortfolioData) -> None:
        curation = synthesize_curation(rich_portfolio)
        raw = curation.model_dump_json(indent=2)
        roundtripped = ResumeCuration.model_validate(json.loads(raw))
        assert roundtripped.company_slug == curation.company_slug


class TestBuildStaticResult:
    """Wrapper around synthesize_curation that produces a CurationResult."""

    def test_source_and_model_sentinels(self, rich_portfolio: PortfolioData) -> None:
        result = build_static_result(rich_portfolio)
        assert result.source == "static"
        assert result.model == "n/a"

    def test_token_counts_zero(self, rich_portfolio: PortfolioData) -> None:
        result = build_static_result(rich_portfolio)
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.cache_creation_input_tokens == 0
        assert result.cache_read_input_tokens == 0

    def test_inner_curation_is_valid(self, rich_portfolio: PortfolioData) -> None:
        result = build_static_result(rich_portfolio)
        validate_curation_ids(result.curation, rich_portfolio)

    def test_custom_name_flows_through(self, rich_portfolio: PortfolioData) -> None:
        result = build_static_result(rich_portfolio, name="Acme Co")
        assert result.curation.company_slug == "acme-co"


# ---------------------------------------------------------------------------
# Cover letter synthesis
# ---------------------------------------------------------------------------


import inspect  # noqa: E402

from curator.exceptions import CurationValidationError  # noqa: E402
from curator.static_mode import synthesize_cover_letter  # noqa: E402


@pytest.fixture
def portfolio_without_cover_letter(
    rich_portfolio_dict: dict[str, Any],
) -> PortfolioData:
    return _build_portfolio(rich_portfolio_dict, with_cover_letter=False)


class TestSynthesizeCoverLetter:
    def test_returns_portfolio_letter_verbatim(
        self, rich_portfolio: PortfolioData
    ) -> None:
        letter = synthesize_cover_letter(rich_portfolio)
        assert letter is rich_portfolio.cover_letter

    def test_byte_for_byte_passthrough(self, rich_portfolio: PortfolioData) -> None:
        letter = synthesize_cover_letter(rich_portfolio)
        assert rich_portfolio.cover_letter is not None
        assert letter.model_dump() == rich_portfolio.cover_letter.model_dump()

    def test_salutation_ends_with_comma(self, rich_portfolio: PortfolioData) -> None:
        letter = synthesize_cover_letter(rich_portfolio)
        assert letter.salutation.rstrip().endswith(",")

    def test_sign_off_has_no_trailing_comma(
        self, rich_portfolio: PortfolioData
    ) -> None:
        letter = synthesize_cover_letter(rich_portfolio)
        assert not letter.sign_off.rstrip().endswith(",")

    def test_missing_cover_letter_raises_with_guidance(
        self, portfolio_without_cover_letter: PortfolioData
    ) -> None:
        with pytest.raises(StaticModeError) as exc_info:
            synthesize_cover_letter(portfolio_without_cover_letter)
        msg = str(exc_info.value)
        assert "data/cover-letter.yaml" in msg
        assert "COVER_LETTER_" in msg

    def test_signature_has_no_name_parameter(self) -> None:
        """--name no longer affects the letter; the signature should reflect."""
        params = set(inspect.signature(synthesize_cover_letter).parameters)
        assert params == {"portfolio"}


class TestBuildStaticResultCoverLetter:
    def test_flag_off_leaves_field_none(self, rich_portfolio: PortfolioData) -> None:
        result = build_static_result(rich_portfolio, with_cover_letter=False)
        assert result.cover_letter is None

    def test_flag_on_populates_field(self, rich_portfolio: PortfolioData) -> None:
        result = build_static_result(rich_portfolio, with_cover_letter=True)
        assert result.cover_letter is not None
        assert result.cover_letter is rich_portfolio.cover_letter

    def test_name_has_no_effect_on_cover_letter_content(
        self, rich_portfolio: PortfolioData
    ) -> None:
        r1 = build_static_result(rich_portfolio, name="acme", with_cover_letter=True)
        r2 = build_static_result(rich_portfolio, name="contoso", with_cover_letter=True)
        assert r1.cover_letter is not None
        assert r2.cover_letter is not None
        assert r1.cover_letter.model_dump() == r2.cover_letter.model_dump()

    def test_missing_cover_letter_raises(
        self, portfolio_without_cover_letter: PortfolioData
    ) -> None:
        with pytest.raises(StaticModeError, match=r"data/cover-letter\.yaml"):
            build_static_result(portfolio_without_cover_letter, with_cover_letter=True)

    def test_validator_failure_wrapped_as_static_mode_error(
        self, rich_portfolio_dict: dict[str, Any]
    ) -> None:
        from tests.helpers import body_paragraph_embedding, valid_cover_letter_kwargs

        # Inject a forbidden phrase to trip the validator.
        kwargs = valid_cover_letter_kwargs()
        kwargs["body_paragraphs"][0] = body_paragraph_embedding(
            "I am a proven track record kind of person."
        )
        bad_letter = CoverLetterCuration(**kwargs)

        portfolio = _build_portfolio(rich_portfolio_dict, with_cover_letter=False)
        from dataclasses import replace as _replace

        portfolio = _replace(portfolio, cover_letter=bad_letter)

        with pytest.raises(StaticModeError) as exc_info:
            build_static_result(portfolio, with_cover_letter=True)
        msg = str(exc_info.value)
        assert "data/cover-letter.yaml" in msg
        assert "COVER_LETTER_" in msg
        assert isinstance(exc_info.value.__cause__, CurationValidationError)
