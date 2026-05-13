"""Unit tests for the per-call dynamic JSON schema builder.

The builder constructs the JSON schema sent to Anthropic for the
structured-output call. Decode-time grammar enforcement of per-property
``items.enum`` was empirically verified 2026-05-13 against the
production Haiku model.
These tests pin the schema shape, determinism, and the no-empty-enum /
no-unsupported-keyword invariants.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from curator.models import (
    Basics,
    PortfolioData,
    ProjectEntry,
    SkillEntry,
    TaggedHighlight,
    WorkEntry,
)
from curator.output_schema import build_curation_schema
from curator.rules import COVER_LETTER_VALID_SIGN_OFFS

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _portfolio(
    *,
    work: list[WorkEntry] | None = None,
    skills: list[SkillEntry] | None = None,
    projects: list[ProjectEntry] | None = None,
) -> PortfolioData:
    return PortfolioData(
        basics=Basics(name="Test Candidate"),
        work=work or [],
        education=[],
        skills=skills or [],
        certificates=[],
        projects=projects or [],
        volunteer=[],
        publications=[],
        languages=[],
        interests=None,
        services=[],
    )


def _work(wid: str, highlight_ids: list[str], position: str = "Engineer") -> WorkEntry:
    return WorkEntry(
        id=wid,
        name="Co",
        position=position,
        startDate="2020-01",
        highlights=[
            TaggedHighlight(id=hid, text=f"text for {hid}") for hid in highlight_ids
        ],
    )


def _skill(sid: str, keywords: list[str]) -> SkillEntry:
    return SkillEntry(id=sid, name=sid, keywords=keywords)


def _project(pid: str) -> ProjectEntry:
    return ProjectEntry(id=pid, name=pid)


@pytest.fixture
def realistic_portfolio() -> PortfolioData:
    """Two work entries, two skill groups, two projects."""
    return _portfolio(
        work=[
            _work("pf-senior-engineer", ["pf-h1", "pf-h2", "pf-h3"]),
            _work("aws-support-engineer", ["aws-h1", "aws-h2"]),
        ],
        skills=[
            _skill("cloud-aws", ["EC2", "S3", "RDS"]),
            _skill("cicd", ["GitHub Actions", "Terraform"]),
        ],
        projects=[_project("proj-alpha"), _project("proj-beta")],
    )


# ---------------------------------------------------------------------------
# Top-level shape
# ---------------------------------------------------------------------------


class TestTopLevelShape:
    def test_top_level_keys_in_declared_order(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        schema = build_curation_schema(realistic_portfolio)
        assert list(schema["properties"].keys()) == [
            "summary",
            "suggested_label",
            "company_slug",
            "work_highlights_by_id",
            "skills_by_id",
            "projects",
        ]

    def test_top_level_required_lists_all_fields(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        schema = build_curation_schema(realistic_portfolio)
        assert set(schema["required"]) == {
            "summary",
            "suggested_label",
            "company_slug",
            "work_highlights_by_id",
            "skills_by_id",
            "projects",
        }

    def test_top_level_additional_properties_false(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        schema = build_curation_schema(realistic_portfolio)
        assert schema["additionalProperties"] is False

    def test_summary_is_first_property_for_constrained_decoding(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        # Constrained decoding emits fields in declared order. summary
        # MUST be first so the model commits to tone before ranking.
        schema = build_curation_schema(realistic_portfolio)
        assert next(iter(schema["properties"])) == "summary"


# ---------------------------------------------------------------------------
# work_highlights_by_id
# ---------------------------------------------------------------------------


class TestWorkHighlightsByID:
    def test_key_per_work_entry_in_portfolio_order(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        schema = build_curation_schema(realistic_portfolio)
        wh = schema["properties"]["work_highlights_by_id"]
        assert list(wh["properties"].keys()) == [
            "pf-senior-engineer",
            "aws-support-engineer",
        ]

    def test_each_value_enum_scoped_to_that_entrys_highlights(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        wh = build_curation_schema(realistic_portfolio)["properties"][
            "work_highlights_by_id"
        ]
        assert wh["properties"]["pf-senior-engineer"]["items"]["enum"] == [
            "pf-h1",
            "pf-h2",
            "pf-h3",
        ]
        assert wh["properties"]["aws-support-engineer"]["items"]["enum"] == [
            "aws-h1",
            "aws-h2",
        ]

    def test_items_type_is_string(self, realistic_portfolio: PortfolioData) -> None:
        wh = build_curation_schema(realistic_portfolio)["properties"][
            "work_highlights_by_id"
        ]
        for prop in wh["properties"].values():
            assert prop["type"] == "array"
            assert prop["items"]["type"] == "string"

    def test_required_lists_every_present_work_entry(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        wh = build_curation_schema(realistic_portfolio)["properties"][
            "work_highlights_by_id"
        ]
        assert wh["required"] == ["pf-senior-engineer", "aws-support-engineer"]

    def test_additional_properties_false(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        wh = build_curation_schema(realistic_portfolio)["properties"][
            "work_highlights_by_id"
        ]
        assert wh["additionalProperties"] is False

    def test_work_entry_with_zero_highlights_omitted(self) -> None:
        # Empty enum is a 400 from Anthropic; entry must be omitted
        # from the schema. Adapter synthesizes an empty ranking later.
        portfolio = _portfolio(
            work=[
                _work("with-highlights", ["h1"]),
                _work("zero-highlights", []),
            ]
        )
        wh = build_curation_schema(portfolio)["properties"]["work_highlights_by_id"]
        assert list(wh["properties"].keys()) == ["with-highlights"]
        assert wh["required"] == ["with-highlights"]


# ---------------------------------------------------------------------------
# skills_by_id
# ---------------------------------------------------------------------------


class TestSkillsByID:
    def test_key_per_skill_group_in_portfolio_order(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        sk = build_curation_schema(realistic_portfolio)["properties"]["skills_by_id"]
        assert list(sk["properties"].keys()) == ["cloud-aws", "cicd"]

    def test_each_value_enum_scoped_to_that_groups_keywords(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        sk = build_curation_schema(realistic_portfolio)["properties"]["skills_by_id"]
        assert sk["properties"]["cloud-aws"]["items"]["enum"] == ["EC2", "S3", "RDS"]
        assert sk["properties"]["cicd"]["items"]["enum"] == [
            "GitHub Actions",
            "Terraform",
        ]

    def test_zero_keyword_skill_group_omitted(self) -> None:
        portfolio = _portfolio(
            skills=[
                _skill("group-with-kw", ["A", "B"]),
                _skill("group-no-kw", []),
            ]
        )
        sk = build_curation_schema(portfolio)["properties"]["skills_by_id"]
        assert list(sk["properties"].keys()) == ["group-with-kw"]
        assert sk["required"] == ["group-with-kw"]


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------


class TestProjects:
    def test_items_enum_lists_all_project_ids(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        projects = build_curation_schema(realistic_portfolio)["properties"]["projects"]
        assert projects["type"] == "array"
        assert projects["items"]["type"] == "string"
        assert projects["items"]["enum"] == ["proj-alpha", "proj-beta"]

    def test_empty_portfolio_drops_enum_but_keeps_string_items(self) -> None:
        # Empty enum would 400; degrade to unconstrained string array
        # for the rare zero-projects portfolio. Validator catches
        # bogus IDs post-parse.
        portfolio = _portfolio(projects=[])
        projects = build_curation_schema(portfolio)["properties"]["projects"]
        assert projects["type"] == "array"
        assert projects["items"] == {"type": "string"}
        assert "enum" not in projects["items"]


# ---------------------------------------------------------------------------
# Cover-letter wrapper
# ---------------------------------------------------------------------------


class TestCoverLetterWrapper:
    def test_with_cover_letter_wraps_resume(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        schema = build_curation_schema(realistic_portfolio, with_cover_letter=True)
        assert list(schema["properties"].keys()) == ["resume", "cover_letter"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {"resume", "cover_letter"}

    def test_resume_branch_is_byte_identical_to_resume_only_schema(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        wrapped = build_curation_schema(realistic_portfolio, with_cover_letter=True)
        bare = build_curation_schema(realistic_portfolio, with_cover_letter=False)
        assert wrapped["properties"]["resume"] == bare

    def test_cover_letter_field_order(self, realistic_portfolio: PortfolioData) -> None:
        cl = build_curation_schema(realistic_portfolio, with_cover_letter=True)[
            "properties"
        ]["cover_letter"]
        assert list(cl["properties"].keys()) == [
            "salutation",
            "opening",
            "body_paragraph_1",
            "body_paragraph_2",
            "closing",
            "sign_off",
        ]

    def test_cover_letter_sign_off_enum_matches_rules(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        cl = build_curation_schema(realistic_portfolio, with_cover_letter=True)[
            "properties"
        ]["cover_letter"]
        assert set(cl["properties"]["sign_off"]["enum"]) == set(
            COVER_LETTER_VALID_SIGN_OFFS
        )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_byte_identical_across_two_builds(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        s1 = build_curation_schema(realistic_portfolio)
        s2 = build_curation_schema(realistic_portfolio)
        assert s1 == s2
        # And serialized — guards against any iteration-order non-determinism
        # that dict equality alone wouldn't catch.
        assert json.dumps(s1, sort_keys=False) == json.dumps(s2, sort_keys=False)

    def test_byte_identical_with_cover_letter(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        s1 = build_curation_schema(realistic_portfolio, with_cover_letter=True)
        s2 = build_curation_schema(realistic_portfolio, with_cover_letter=True)
        assert json.dumps(s1, sort_keys=False) == json.dumps(s2, sort_keys=False)


# ---------------------------------------------------------------------------
# No unsupported Anthropic keywords
# ---------------------------------------------------------------------------


def _walk(node: Any) -> Any:
    """Yield every dict node in the schema tree."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


class TestNoUnsupportedKeywords:
    @pytest.mark.parametrize(
        "keyword",
        [
            "oneOf",
            "not",
            "if",
            "then",
            "else",
            "dependentRequired",
            "dependentSchemas",
            "discriminator",
            "minLength",
            "maxLength",
            "pattern",
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "multipleOf",
            "maxItems",
        ],
    )
    def test_keyword_absent_from_resume_schema(
        self, realistic_portfolio: PortfolioData, keyword: str
    ) -> None:
        schema = build_curation_schema(realistic_portfolio)
        for node in _walk(schema):
            assert keyword not in node, f"unexpected {keyword!r} in {node}"

    @pytest.mark.parametrize(
        "keyword",
        [
            "oneOf",
            "not",
            "dependentSchemas",
            "minLength",
            "maxLength",
            "pattern",
            "maxItems",
        ],
    )
    def test_keyword_absent_from_cover_letter_schema(
        self, realistic_portfolio: PortfolioData, keyword: str
    ) -> None:
        schema = build_curation_schema(realistic_portfolio, with_cover_letter=True)
        for node in _walk(schema):
            assert keyword not in node, f"unexpected {keyword!r} in {node}"


class TestNoEmptyEnums:
    """Empty enums get 400 from Anthropic; guard against accidental emission."""

    def test_no_empty_enum_in_realistic_schema(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        schema = build_curation_schema(realistic_portfolio, with_cover_letter=True)
        for node in _walk(schema):
            if "enum" in node:
                assert len(node["enum"]) > 0, f"empty enum in {node}"

    def test_no_empty_enum_when_portfolio_has_only_empty_entries(self) -> None:
        # Each entry below would-be-empty; the schema must omit them
        # rather than emit `enum: []`.
        portfolio = _portfolio(
            work=[_work("empty-work", [])],
            skills=[_skill("empty-skill", [])],
            projects=[],
        )
        schema = build_curation_schema(portfolio)
        for node in _walk(schema):
            if "enum" in node:
                assert len(node["enum"]) > 0


# ---------------------------------------------------------------------------
# Defensive ID validation
# ---------------------------------------------------------------------------


class TestIDPatternDefense:
    """ID_PATTERN is enforced at portfolio-load time, but the schema
    builder revalidates as defense-in-depth against regressions."""

    def test_invalid_work_id_raises(self) -> None:
        # Bypass Pydantic validation by constructing the WorkEntry via
        # model_construct, then check the schema builder catches it.
        bad = WorkEntry.model_construct(
            id="Bad ID With Spaces",
            name="x",
            position="x",
            start_date="2020-01",
            highlights=[TaggedHighlight(id="h1", text="t")],
        )
        portfolio = _portfolio(work=[bad])
        with pytest.raises(ValueError, match="work entry id"):
            build_curation_schema(portfolio)


# ---------------------------------------------------------------------------
# Description visibility (smoke test that fresh per-property descriptions
# replace the legacy Pydantic descriptions that assumed a sibling
# discriminator field that no longer exists).
# ---------------------------------------------------------------------------


class TestDescriptions:
    def test_work_property_description_mentions_parent_key(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        wh = build_curation_schema(realistic_portfolio)["properties"][
            "work_highlights_by_id"
        ]
        # Per-property description must reference the parent key so the
        # model knows the property identity is the work entry ID.
        desc = wh["properties"]["pf-senior-engineer"]["description"]
        assert "pf-senior-engineer" in desc

    def test_skill_property_description_mentions_skip_semantics(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        sk = build_curation_schema(realistic_portfolio)["properties"]["skills_by_id"]
        desc = sk["properties"]["cloud-aws"]["description"]
        # Empty-array-means-skip is the contract the prompt also
        # communicates; redundant in two places to keep the model
        # aligned.
        assert "skip" in desc.lower() or "empty" in desc.lower()
