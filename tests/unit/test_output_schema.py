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
            "company_name",
            "work_highlights_by_id",
            "work_highlight_weights",
            "skills",
            "projects",
            "trim_priority",
        ]

    def test_top_level_required_lists_all_content_fields(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        # ``work_highlight_weights`` and ``trim_priority`` are optional
        # AI hints; they are present in ``properties`` but excluded
        # from ``required`` so the model may omit them.
        schema = build_curation_schema(realistic_portfolio)
        assert set(schema["required"]) == {
            "summary",
            "suggested_label",
            "company_name",
            "work_highlights_by_id",
            "skills",
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


class TestPerEntryEmitCap:
    """Per-entry highlight emission caps surfaced in description text.

    Anthropic's structured-output API does not enforce ``maxItems`` at
    decode time (verified by ``TestNoUnsupportedKeywords``). The cap
    reaches the model as guidance via the property's ``description``
    text and is enforced post-parse by the client adapter. These tests
    pin the formula: ``max(2, round(floor[i] * 1.5))`` from
    ``page_caps._caps_for_pages``.
    """

    def test_two_page_caps_match_formula(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        # 2-page floors: (8, 6, 6, 2, 2) -> caps: (12, 9, 9, 3, 3).
        # realistic_portfolio has two work entries (positions 0 and 1).
        wh = build_curation_schema(realistic_portfolio, max_pages=2)["properties"][
            "work_highlights_by_id"
        ]
        pos0 = wh["properties"]["pf-senior-engineer"]["description"]
        pos1 = wh["properties"]["aws-support-engineer"]["description"]
        assert "at most 12 IDs" in pos0
        assert "at most 9 IDs" in pos1

    def test_one_page_caps_match_formula(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        # 1-page floors: (3, 3, 0, 0, 0) -> caps: (5, 5, 2, 2, 2).
        wh = build_curation_schema(realistic_portfolio, max_pages=1)["properties"][
            "work_highlights_by_id"
        ]
        for prop in wh["properties"].values():
            assert "at most 5 IDs" in prop["description"]

    def test_three_page_caps_match_formula(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        # 3+-page floors: (10, 8, 8, 4, 4) -> caps: (15, 12, 12, 6, 6).
        wh = build_curation_schema(realistic_portfolio, max_pages=3)["properties"][
            "work_highlights_by_id"
        ]
        pos0 = wh["properties"]["pf-senior-engineer"]["description"]
        pos1 = wh["properties"]["aws-support-engineer"]["description"]
        assert "at most 15 IDs" in pos0
        assert "at most 12 IDs" in pos1

    def test_floor_of_two_for_zero_floor_positions(self) -> None:
        # 1-page positions 2..4 have floor 0; the cap should still be
        # at least 2 so the model isn't forbidden from emitting
        # anything (the cap is a ceiling, not a target).
        portfolio = _portfolio(
            work=[_work(f"w{i}", [f"w{i}-h{j}" for j in range(5)]) for i in range(5)]
        )
        wh = build_curation_schema(portfolio, max_pages=1)["properties"][
            "work_highlights_by_id"
        ]
        # Positions 2, 3, 4 inherit the floor=2 minimum.
        for i in (2, 3, 4):
            assert "at most 2 IDs" in wh["properties"][f"w{i}"]["description"]

    def test_max_pages_in_description_text(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        # The page budget is mentioned in the cap description so the
        # operator reading the rendered schema knows which budget it
        # was built for.
        wh = build_curation_schema(realistic_portfolio, max_pages=2)["properties"][
            "work_highlights_by_id"
        ]
        assert "2-page" in wh["properties"]["pf-senior-engineer"]["description"]


# ---------------------------------------------------------------------------
# skills (flat top-level array, no items.enum)
# ---------------------------------------------------------------------------


class TestSkills:
    """Skills wire shape under Option E (2026-05-14).

    The schema MUST emit a flat top-level ``skills: array[string]``. Any
    future addition of nested per-group properties or ``items.enum``
    here will re-trigger the 2026-05-13/14 "compiled grammar is too
    large" 400. See ``docs/architecture.md`` "Dynamic schema
    construction (API path)" for the design rationale.
    """

    def test_skills_is_top_level_array_with_string_items(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        schema = build_curation_schema(realistic_portfolio)
        sk = schema["properties"]["skills"]
        assert sk["type"] == "array"
        assert sk["items"]["type"] == "string"

    def test_skills_items_enum_is_portfolio_group_ids(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        # 2026-05-18 hybrid: items.enum constrains skill emissions to
        # portfolio skill group IDs. Surface is small (typically <30
        # groups), well under the 354-keyword surface that 400'd on
        # 2026-05-13 with the prior flat-keyword design.
        sk = build_curation_schema(realistic_portfolio)["properties"]["skills"]
        portfolio_group_ids = [g.id for g in realistic_portfolio.skills]
        assert sk["items"]["enum"] == portfolio_group_ids

    def test_skills_items_dict_has_only_type_and_enum(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        # Beyond items.enum: no drive-by additions of pattern, minLength,
        # etc., that would shrink the grammar budget unnecessarily.
        sk = build_curation_schema(realistic_portfolio)["properties"]["skills"]
        assert set(sk["items"].keys()) == {"type", "enum"}
        assert sk["items"]["type"] == "string"

    def test_skills_in_required_list(self, realistic_portfolio: PortfolioData) -> None:
        schema = build_curation_schema(realistic_portfolio)
        assert "skills" in schema["required"]

    def test_skills_field_order_after_work_highlights_before_projects(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        # Under constrained decoding, field order matters: skills
        # must come after work_highlights_by_id (so the model commits
        # to highlight ranking first) and before projects. As of
        # 2026-05-20 ``work_highlight_weights`` sits between
        # work_highlights_by_id and skills (weights ride on top of
        # the ranking decision); skills immediately precedes projects.
        keys = list(build_curation_schema(realistic_portfolio)["properties"].keys())
        assert keys.index("work_highlights_by_id") < keys.index("skills")
        assert keys.index("skills") < keys.index("projects")
        assert keys.index("skills") == keys.index("projects") - 1

    def test_schema_has_no_skills_by_id_anywhere(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        # Defense against a partial revert: the by-id shape must not
        # exist anywhere under the resume schema.
        schema = build_curation_schema(realistic_portfolio)
        assert "skills_by_id" not in schema["properties"]
        assert "skills_by_id" not in schema.get("required", [])

    def test_zero_keyword_portfolio_still_emits_group_id_enum(self) -> None:
        # 2026-05-18 hybrid: groups are emitted by ID regardless of
        # their keyword count. A portfolio whose every group has zero
        # keywords still produces a valid enum (the adapter would
        # later skip such groups because SkillRanking requires
        # min_length=1 keywords, but the schema itself remains well-
        # formed).
        portfolio = _portfolio(
            skills=[
                _skill("group-no-kw-a", []),
                _skill("group-no-kw-b", []),
            ]
        )
        schema = build_curation_schema(portfolio)
        sk = schema["properties"]["skills"]
        assert sk["type"] == "array"
        assert sk["items"]["enum"] == ["group-no-kw-a", "group-no-kw-b"]
        assert "skills" in schema["required"]


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

    def test_skills_description_mentions_group_ids_and_omission(
        self, realistic_portfolio: PortfolioData
    ) -> None:
        # 2026-05-18 hybrid: the `skills` field carries group IDs
        # only (the adapter fills keywords via JD scoring). The
        # top-level description must signal (1) the model emits
        # group IDs not keywords and (2) omit JD-irrelevant groups
        # rather than padding. Both reinforce prompt content.
        sk = build_curation_schema(realistic_portfolio)["properties"]["skills"]
        desc = sk["description"].lower()
        assert "group" in desc
        assert "omit" in desc or "irrelevant" in desc
