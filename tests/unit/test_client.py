"""Unit tests for the Anthropic API client wrapper."""

from __future__ import annotations

import json
import sys
from dataclasses import FrozenInstanceError, replace
from typing import Any
from unittest.mock import MagicMock

import anthropic
import pytest
from pydantic import SecretStr, ValidationError

from curator.client import CurationResult, CuratorClient, _validate_curation_ids
from curator.config import CuratorSettings
from curator.exceptions import (
    APIAuthError,
    APIError,
    APIRateLimitError,
    APIRefusalError,
    APIResponseError,
    APISpendGuardError,
)
from curator.models import (
    CoverLetterCuration,
    PortfolioData,
    ResumeCuration,
    ResumeCurationWithCoverLetter,
)


def _curation_to_wire_dict(obj: Any) -> dict[str, Any]:
    """Convert a Pydantic curation (or wrapper) to the wire-shape dict.

    The wire schema keys highlights by parent work_id (for grammar-time
    cross-parent attribution enforcement) and emits skills as a flat
    top-level array of keyword strings (Option E, 2026-05-14: the
    object-keyed-by-skill-group shape exceeded Anthropic's
    compiled-grammar budget; see ``docs/architecture.md`` "Dynamic
    schema construction (API path)"). Production code at
    ``client._adapt_curation_dict`` walks each flat keyword back to its
    parent portfolio group, so this helper must flatten in the order
    the production model would emit (group-rank-major, then
    keyword-rank-minor) to produce realistic round-trip fixtures.
    """
    if isinstance(obj, ResumeCurationWithCoverLetter):
        return {
            "resume": _curation_to_wire_dict(obj.resume),
            "cover_letter": obj.cover_letter.model_dump(mode="json"),
        }
    if isinstance(obj, ResumeCuration):
        skills_flat: list[str] = []
        for sr in obj.skills:
            skills_flat.extend(sr.keywords)
        return {
            "summary": obj.summary,
            "suggested_label": obj.suggested_label,
            "company_slug": obj.company_slug,
            "work_highlights_by_id": {
                wh.work_id: list(wh.highlight_ids) for wh in obj.work_highlights
            },
            "skills": skills_flat,
            "projects": list(obj.projects),
        }
    if isinstance(obj, dict):
        return obj
    msg = f"unsupported curation type for wire-dict conversion: {type(obj).__name__}"
    raise TypeError(msg)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings(tmp_path: Any) -> CuratorSettings:
    """CuratorSettings with a test API key and sensible defaults."""
    return CuratorSettings(
        anthropic_api_key=SecretStr("sk-ant-test-not-real"),
        model="claude-sonnet-4-6-20260217",
        max_tokens=4096,
        effort=None,
        api_max_retries=1,
        portfolio_path=tmp_path,
        allow_api_spend=True,
    )


@pytest.fixture
def valid_curation_dict() -> dict[str, object]:
    """ResumeCuration dict where every ID matches the portfolio_data fixture.

    Unlike the shared ``resume_curation_dict`` in conftest (which has
    ``"kubernetes"`` and ``"infra-toolkit"`` IDs not in portfolio_data),
    this fixture is aligned for happy-path testing through ID validation.
    """
    from tests.helpers import make_curation_dict

    return make_curation_dict(
        company_slug="acme-corp",
        work_highlights=[
            {
                "work_id": "acme-senior-engineer",
                "highlight_ids": ["acme-deployed-k8s"],
            },
        ],
        skills=[{"skill_id": "cloud-aws", "keywords": ["EKS"]}],
        projects=["my-project"],
    )


@pytest.fixture
def valid_curation(valid_curation_dict: dict[str, object]) -> ResumeCuration:
    """Validated ResumeCuration with IDs matching portfolio_data."""
    return ResumeCuration.model_validate(valid_curation_dict)


def _make_mock_usage(
    *,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cache_creation_input_tokens: int = 800,
    cache_read_input_tokens: int = 200,
) -> MagicMock:
    """Build a mock Usage object."""
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    usage.cache_creation_input_tokens = cache_creation_input_tokens
    usage.cache_read_input_tokens = cache_read_input_tokens
    return usage


def _make_mock_message(
    curation: Any = None,
    *,
    stop_reason: str = "end_turn",
    model: str = "claude-sonnet-4-6-20260217",
    usage: MagicMock | None = None,
    raw_text: str | None = None,
    include_text_block: bool = True,
) -> MagicMock:
    """Build a mock Message with controllable attributes.

    The client extracts the JSON from ``message.content[0].text`` (the
    raw-dict structured-output path; ``parsed_output`` stays None when
    the schema is a dict rather than a Pydantic class). This helper
    serializes the curation to wire-shape JSON and stuffs it into a
    text content block.

    Args:
        curation: A ``ResumeCuration``, ``ResumeCurationWithCoverLetter``,
            or dict to serialize. ``None`` produces no text content.
        raw_text: Override the serialized JSON entirely (used to test
            JSON parse failures, wrong types, etc.).
        include_text_block: When False, the message has no text blocks
            (used to test the "no text in response" path).
    """
    message = MagicMock()
    message.parsed_output = None
    message.stop_reason = stop_reason
    message.model = model
    message.id = "msg_test_123"
    message.usage = usage or _make_mock_usage()
    if not include_text_block or (curation is None and raw_text is None):
        message.content = []
    else:
        text = (
            raw_text
            if raw_text is not None
            else json.dumps(_curation_to_wire_dict(curation))
        )
        text_block = MagicMock(spec=anthropic.types.TextBlock)
        text_block.type = "text"
        text_block.text = text
        message.content = [text_block]
    return message


def _wire_mock_stream(
    mocker: Any,
    message: MagicMock,
) -> Any:
    """Patch anthropic.Anthropic and wire the stream context manager chain.

    Returns the mock Anthropic class for assertion on constructor args.
    """
    mock_anthropic = mocker.patch("curator.client.anthropic.Anthropic")
    mock_instance = mock_anthropic.return_value
    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__enter__ = MagicMock(return_value=mock_stream_ctx)
    mock_stream_ctx.__exit__ = MagicMock(return_value=False)
    mock_stream_ctx.get_final_message.return_value = message
    mock_instance.messages.stream.return_value = mock_stream_ctx
    return mock_anthropic


# ---------------------------------------------------------------------------
# TestCurationResult
# ---------------------------------------------------------------------------


class TestCurationResult:
    def test_fields_accessible(self, valid_curation: ResumeCuration) -> None:
        result = CurationResult(
            curation=valid_curation,
            model="claude-sonnet-4-6-20260217",
            input_tokens=1000,
            output_tokens=500,
            cache_creation_input_tokens=800,
            cache_read_input_tokens=200,
        )
        assert result.curation is valid_curation
        assert result.model == "claude-sonnet-4-6-20260217"
        assert result.input_tokens == 1000
        assert result.output_tokens == 500
        assert result.cache_creation_input_tokens == 800
        assert result.cache_read_input_tokens == 200

    def test_frozen(self, valid_curation: ResumeCuration) -> None:
        result = CurationResult(
            curation=valid_curation,
            model="test",
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        with pytest.raises(FrozenInstanceError):
            result.model = "changed"  # type: ignore[misc]

    def test_equality(self, valid_curation: ResumeCuration) -> None:
        kwargs: dict[str, Any] = {
            "curation": valid_curation,
            "model": "test",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        assert CurationResult(**kwargs) == CurationResult(**kwargs)


# ---------------------------------------------------------------------------
# TestCuratorClientInit
# ---------------------------------------------------------------------------


class TestCuratorClientInit:
    def test_creates_client(self, mocker: Any, mock_settings: CuratorSettings) -> None:
        mock_anthropic = mocker.patch("curator.client.anthropic.Anthropic")
        CuratorClient(mock_settings)
        mock_anthropic.assert_called_once()

    def test_extracts_api_key(
        self, mocker: Any, mock_settings: CuratorSettings
    ) -> None:
        mock_anthropic = mocker.patch("curator.client.anthropic.Anthropic")
        CuratorClient(mock_settings)
        call_kwargs = mock_anthropic.call_args[1]
        expected = "sk-ant-test-not-real"  # pragma: allowlist secret
        assert call_kwargs["api_key"] == expected

    def test_passes_max_retries(
        self, mocker: Any, mock_settings: CuratorSettings
    ) -> None:
        mock_anthropic = mocker.patch("curator.client.anthropic.Anthropic")
        CuratorClient(mock_settings)
        call_kwargs = mock_anthropic.call_args[1]
        assert call_kwargs["max_retries"] == 1

    def test_context_manager_enter_returns_self(
        self, mocker: Any, mock_settings: CuratorSettings
    ) -> None:
        mocker.patch("curator.client.anthropic.Anthropic")
        client = CuratorClient(mock_settings)
        assert client.__enter__() is client

    def test_context_manager_exit_calls_close(
        self, mocker: Any, mock_settings: CuratorSettings
    ) -> None:
        mock_anthropic = mocker.patch("curator.client.anthropic.Anthropic")
        client = CuratorClient(mock_settings)
        client.__exit__(None, None, None)
        mock_anthropic.return_value.close.assert_called_once()

    def test_close_directly(self, mocker: Any, mock_settings: CuratorSettings) -> None:
        mock_anthropic = mocker.patch("curator.client.anthropic.Anthropic")
        client = CuratorClient(mock_settings)
        client.close()
        mock_anthropic.return_value.close.assert_called_once()

    def test_double_close_safe(
        self, mocker: Any, mock_settings: CuratorSettings
    ) -> None:
        mocker.patch("curator.client.anthropic.Anthropic")
        client = CuratorClient(mock_settings)
        client.close()
        client.close()  # Should not raise

    def test_spend_guard_blocks_client_creation(self, tmp_path: Any) -> None:
        """CuratorClient raises APISpendGuardError when allow_api_spend=False."""
        settings = CuratorSettings(
            anthropic_api_key=SecretStr("sk-ant-test-not-real"),
            allow_api_spend=False,
            portfolio_path=tmp_path,
        )
        with pytest.raises(APISpendGuardError, match="not authorized"):
            CuratorClient(settings)


# ---------------------------------------------------------------------------
# TestCurate
# ---------------------------------------------------------------------------


class TestCurate:
    def test_happy_path_returns_curation_result(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        message = _make_mock_message(valid_curation)
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        result = client.curate(portfolio_data, "Senior SRE role at Acme.")

        assert isinstance(result, CurationResult)
        # Equal, not identical: the curation is re-parsed from JSON in the
        # response text block and reconstructed via model_validate. Identity
        # held under the legacy parsed_output path but is no longer
        # meaningful with raw-dict structured outputs.
        assert result.curation == valid_curation

    def test_returns_actual_model_from_response(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        message = _make_mock_message(valid_curation, model="claude-sonnet-4-6-20260301")
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        result = client.curate(portfolio_data, "Job description.")
        assert result.model == "claude-sonnet-4-6-20260301"

    def test_usage_stats_in_result(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        usage = _make_mock_usage(
            input_tokens=1500,
            output_tokens=750,
            cache_creation_input_tokens=1200,
            cache_read_input_tokens=300,
        )
        message = _make_mock_message(valid_curation, usage=usage)
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        result = client.curate(portfolio_data, "Job description.")
        assert result.input_tokens == 1500
        assert result.output_tokens == 750
        assert result.cache_creation_input_tokens == 1200
        assert result.cache_read_input_tokens == 300

    def test_passes_dynamic_schema_via_output_config(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        """The client injects the per-call dynamic JSON schema, not a Pydantic class.

        Locks the invariant from both sides: ``output_format`` must be
        ABSENT (no legacy Pydantic-class path), AND
        ``output_config.format.schema`` must be PRESENT and shaped
        like the dict from ``build_curation_schema``.
        """
        from curator.output_schema import build_curation_schema

        message = _make_mock_message(valid_curation)
        mock_anthropic = _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        client.curate(portfolio_data, "Job description.")

        stream_kwargs = mock_anthropic.return_value.messages.stream.call_args[1]
        assert "output_format" not in stream_kwargs
        output_config = stream_kwargs["output_config"]
        assert output_config["format"]["type"] == "json_schema"
        # Schema must match the one build_curation_schema produces from
        # this portfolio (determinism is asserted in test_output_schema).
        assert output_config["format"]["schema"] == build_curation_schema(
            portfolio_data, with_cover_letter=False
        )

    def test_effort_omitted_when_none(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        message = _make_mock_message(valid_curation)
        mock_anthropic = _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)
        assert mock_settings.effort is None

        client.curate(portfolio_data, "Job description.")

        stream_kwargs = mock_anthropic.return_value.messages.stream.call_args[1]
        # output_config is always present (carries the schema), but
        # effort is omitted when None.
        assert "effort" not in stream_kwargs["output_config"]

    def test_effort_included_when_set(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        mock_settings = CuratorSettings(
            anthropic_api_key=SecretStr("sk-ant-test-not-real"),
            model="claude-sonnet-4-6-20260217",
            max_tokens=4096,
            effort="high",
            api_max_retries=1,
            portfolio_path=mock_settings.portfolio_path,
            allow_api_spend=True,
        )
        message = _make_mock_message(valid_curation)
        mock_anthropic = _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        client.curate(portfolio_data, "Job description.")

        stream_kwargs = mock_anthropic.return_value.messages.stream.call_args[1]
        assert stream_kwargs["output_config"]["effort"] == "high"
        assert stream_kwargs["output_config"]["format"]["type"] == "json_schema"

    def test_calls_build_system_prompt(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        message = _make_mock_message(valid_curation)
        _wire_mock_stream(mocker, message)
        mock_build = mocker.patch("curator.client.build_system_prompt")
        mock_build.return_value = [{"type": "text", "text": "test"}]
        client = CuratorClient(mock_settings)

        client.curate(portfolio_data, "Job description.")

        mock_build.assert_called_once_with(portfolio_data, with_cover_letter=False)

    def test_calls_build_user_message(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        message = _make_mock_message(valid_curation)
        _wire_mock_stream(mocker, message)
        mock_build = mocker.patch("curator.client.build_user_message")
        mock_build.return_value = [{"role": "user", "content": "test"}]
        client = CuratorClient(mock_settings)

        client.curate(portfolio_data, "Job description.")

        mock_build.assert_called_once_with("Job description.", with_cover_letter=False)

    def test_job_description_error_propagates_unwrapped(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        """JobDescriptionError from build_user_message is not wrapped in APIError."""
        from curator.exceptions import JobDescriptionError

        mocker.patch("curator.client.anthropic.Anthropic")
        client = CuratorClient(mock_settings)

        with pytest.raises(JobDescriptionError):
            client.curate(portfolio_data, "")


# ---------------------------------------------------------------------------
# TestCurateOverrideParams
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestCurateStopReasons
# ---------------------------------------------------------------------------


class TestCurateStopReasons:
    def test_refusal_raises_api_refusal_error(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        message = _make_mock_message(None, stop_reason="refusal")
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        with pytest.raises(APIRefusalError, match="refused"):
            client.curate(portfolio_data, "Job description.")

    def test_max_tokens_raises_api_response_error(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        message = _make_mock_message(None, stop_reason="max_tokens")
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        with pytest.raises(APIResponseError, match="CURATOR_MAX_TOKENS"):
            client.curate(portfolio_data, "Job description.")

    def test_end_turn_with_no_content_raises(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        """A grammar-constrained response without any content blocks is
        an upstream bug; surface as APIResponseError. (Replaces the
        legacy ``test_end_turn_with_none_parsed_output`` check; with
        raw-dict schemas, ``parsed_output`` is always None and the
        equivalent failure is "no text content".)"""
        message = _make_mock_message(
            None, stop_reason="end_turn", include_text_block=False
        )
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        with pytest.raises(APIResponseError, match="No content in API response"):
            client.curate(portfolio_data, "Job description.")

    def test_end_turn_with_valid_output_succeeds(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        message = _make_mock_message(valid_curation, stop_reason="end_turn")
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        result = client.curate(portfolio_data, "Job description.")
        assert result.curation == valid_curation

    def test_invalid_curation_ids_raises_through_curate(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        resume_curation_dict: dict[str, object],
    ) -> None:
        """ID validation failure through curate() logs request_id."""
        curation = ResumeCuration.model_validate(resume_curation_dict)
        message = _make_mock_message(curation)
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        with pytest.raises(APIResponseError, match="invalid ID"):
            client.curate(portfolio_data, "Job description.")


# ---------------------------------------------------------------------------
# TestCurateGrammarSchemaAdapter
#
# Pins the wire-shape adapter introduced when curate() switched from
# output_format=PydanticClass to output_config.format with a raw-dict
# schema. Covers the unique invariants of the new path: empty
# work-entry synthesis, empty-skill-group filtering with INFO log,
# JSON parse failures, and shape-mismatch handling.
# ---------------------------------------------------------------------------


class TestCurateGrammarSchemaAdapter:
    def test_concatenates_multiple_text_blocks(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        """If the SDK / model returns the structured JSON across two text
        blocks (or after a reasoning block), the adapter must
        concatenate them rather than reading only block[0]. Reading
        only the first block would silently truncate the response and
        surface as a generic JSON parse error."""
        full_text = json.dumps(_curation_to_wire_dict(valid_curation))
        # Split into two halves to simulate streaming or post-thinking
        # text emission.
        mid = len(full_text) // 2
        block1 = MagicMock(spec=anthropic.types.TextBlock)
        block1.type = "text"
        block1.text = full_text[:mid]
        block2 = MagicMock(spec=anthropic.types.TextBlock)
        block2.type = "text"
        block2.text = full_text[mid:]
        message = MagicMock()
        message.parsed_output = None
        message.stop_reason = "end_turn"
        message.model = "claude-sonnet-4-6-20260217"
        message.id = "msg_test_123"
        message.usage = _make_mock_usage()
        message.content = [block1, block2]
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        result = client.curate(portfolio_data, "Job description.")

        assert result.curation == valid_curation

    def test_truncation_with_unparseable_json_surfaces_truncation_hint(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        """When --cover-letter is on and stop_reason=max_tokens, the
        client soft-warns and tries to parse the partial response. If
        the JSON is unparseable (truncated mid-object), the error
        message must include the truncation hint so the user knows to
        bump CURATOR_MAX_TOKENS rather than chasing a generic parse
        error."""
        message = _make_mock_message(
            raw_text='{"resume": {"summary": "incomplete',
            stop_reason="max_tokens",
        )
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        with pytest.raises(APIResponseError, match="truncated at max_tokens"):
            client.curate(portfolio_data, "Job description.", with_cover_letter=True)

    def test_synthesizes_empty_ranking_for_omitted_work_entry(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        """Work entries with zero highlights are omitted from the schema
        (Anthropic rejects empty enums). The adapter must synthesize an
        empty WorkHighlightRanking for each omitted entry so the
        validator's "every portfolio work entry has a ranking"
        invariant holds.
        """
        # Build a wire dict that omits one of the portfolio's work entries.
        wire = _curation_to_wire_dict(valid_curation)
        portfolio_work_ids = [w.id for w in portfolio_data.work]
        # Identify a work ID present in the portfolio but emit no rankings
        # for at least one of them in the wire response.
        wire["work_highlights_by_id"] = {
            wid: hids
            for wid, hids in wire["work_highlights_by_id"].items()
            if wid != portfolio_work_ids[0]
        }
        message = _make_mock_message(raw_text=json.dumps(wire))
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        result = client.curate(portfolio_data, "Job description.")

        # The synthesized ranking must be present with an empty list.
        synth = next(
            wh
            for wh in result.curation.work_highlights
            if wh.work_id == portfolio_work_ids[0]
        )
        assert synth.highlight_ids == []

    def test_adapter_groups_flat_skills_by_portfolio_lookup(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        """Wire emits a flat ``skills`` array; the adapter reverse-looks
        each keyword to its parent portfolio group and builds
        ``list[SkillRanking]`` for the domain model."""
        # Use a wire dict the production adapter sees: skills is a
        # flat list of portfolio-verbatim keywords.
        wire = _curation_to_wire_dict(valid_curation)
        message = _make_mock_message(raw_text=json.dumps(wire))
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        result = client.curate(portfolio_data, "Job description.")

        # Every reconstructed SkillRanking references a real portfolio
        # group, and every keyword belongs to its parent group's
        # portfolio keyword list.
        portfolio_keyword_to_group: dict[str, str] = {}
        for g in portfolio_data.skills:
            for kw in g.keywords:
                portfolio_keyword_to_group.setdefault(kw, g.id)
        for sr in result.curation.skills:
            for kw in sr.keywords:
                assert portfolio_keyword_to_group[kw] == sr.skill_id

    def test_adapter_preserves_model_emit_order_for_skill_groups(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        """Group order in the reconstructed list = first-appearance of
        any keyword from that group in the wire array."""
        # The default portfolio fixture has only one skill group; extend
        # it with a second group so we can verify cross-group ordering.
        from dataclasses import replace

        from curator.models import SkillEntry

        extended = replace(
            portfolio_data,
            skills=[
                *portfolio_data.skills,
                SkillEntry(
                    id="extra-group",
                    name="Extra Group",
                    keywords=["EXTRA-KW"],
                ),
            ],
        )
        g_a = portfolio_data.skills[0].id
        g_b = "extra-group"
        kw_a = portfolio_data.skills[0].keywords[0]
        kw_b = "EXTRA-KW"

        wire = _curation_to_wire_dict(valid_curation)
        # Emit b's keyword first, then a's: b should rank above a.
        wire["skills"] = [kw_b, kw_a]
        message = _make_mock_message(raw_text=json.dumps(wire))
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        result = client.curate(extended, "Job description.")

        emitted_group_order = [sr.skill_id for sr in result.curation.skills]
        assert emitted_group_order == [g_b, g_a], emitted_group_order

    def test_adapter_dedupes_keyword_repeated_in_emit_order(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        """If the model emits the same keyword twice, the adapter keeps
        only the first occurrence to prevent duplicate bullets on the
        rendered PDF."""
        first_group = next(g for g in portfolio_data.skills if g.keywords)
        kw = first_group.keywords[0]
        wire = _curation_to_wire_dict(valid_curation)
        wire["skills"] = [kw, kw]
        message = _make_mock_message(raw_text=json.dumps(wire))
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        result = client.curate(portfolio_data, "Job description.")

        # Exactly one SkillRanking, exactly one keyword.
        srs = [sr for sr in result.curation.skills if sr.skill_id == first_group.id]
        assert len(srs) == 1
        assert srs[0].keywords == [kw]

    def test_adapter_drops_unknown_keyword_with_warn(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        """An emitted keyword that doesn't exist in any portfolio skill
        group is dropped by the adapter (not the validator), and a
        single WARN log line names every drop."""
        wire = _curation_to_wire_dict(valid_curation)
        wire["skills"] = [*wire["skills"], "this-is-not-a-portfolio-keyword"]
        message = _make_mock_message(raw_text=json.dumps(wire))
        _wire_mock_stream(mocker, message)
        warn_mock = mocker.patch("curator.client.logger.warning")
        client = CuratorClient(mock_settings)

        result = client.curate(portfolio_data, "Job description.")

        # No SkillRanking contains the unknown keyword.
        for sr in result.curation.skills:
            assert "this-is-not-a-portfolio-keyword" not in sr.keywords
        # Exactly one WARN log line names the drop.
        matching = [
            call
            for call in warn_mock.call_args_list
            if isinstance(call.args[0], str)
            and "not in any portfolio skill group" in call.args[0]
        ]
        assert len(matching) == 1, warn_mock.call_args_list
        assert "this-is-not-a-portfolio-keyword" in str(matching[0].args)

    def test_adapter_empty_skills_array_emits_empty_list(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        """``ResumeCuration.skills`` has no min_length; an empty wire
        ``skills`` array (model decided no group is JD-relevant) must
        round-trip to an empty domain list, not raise."""
        wire = _curation_to_wire_dict(valid_curation)
        wire["skills"] = []
        message = _make_mock_message(raw_text=json.dumps(wire))
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        result = client.curate(portfolio_data, "Job description.")

        assert result.curation.skills == []

    def test_adapter_raises_apiresponseerror_when_skills_not_list(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        """Wire shape must be a list; an object (legacy by-id shape) or
        anything else surfaces as APIResponseError carrying the
        request_id."""
        wire = _curation_to_wire_dict(valid_curation)
        # Legacy by-id object shape — no longer accepted under Option E.
        wire["skills"] = {"cloud-aws": ["EKS"]}
        message = _make_mock_message(raw_text=json.dumps(wire))
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        with pytest.raises(APIResponseError, match="skills must be an array"):
            client.curate(portfolio_data, "Job description.")

    def test_json_parse_failure_raises_api_response_error(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        """Truncated or otherwise malformed JSON in the response text
        block surfaces as APIResponseError with the request_id."""
        message = _make_mock_message(raw_text='{"summary": "incomplete')  # truncated
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        with pytest.raises(APIResponseError, match="not valid JSON"):
            client.curate(portfolio_data, "Job description.")

    def test_response_must_be_json_object_not_array(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        """The schema mandates an object at the root; a JSON array would
        be a grammar bug. Adapter rejects it with APIResponseError."""
        message = _make_mock_message(raw_text="[1, 2, 3]")
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        with pytest.raises(APIResponseError, match="not an object"):
            client.curate(portfolio_data, "Job description.")

    def test_wrong_shape_for_work_highlights_raises(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        """If the grammar regressed and emitted a list instead of an
        object for work_highlights_by_id, the adapter rejects with a
        diagnostic message rather than letting Pydantic surface a
        confusing nested error. The skills-array type guard is covered
        by ``test_adapter_raises_apiresponseerror_when_skills_not_list``
        in the adapter test class."""
        message = _make_mock_message(
            raw_text=json.dumps(
                {
                    "summary": "x",
                    "suggested_label": "y",
                    "company_slug": "z",
                    "work_highlights_by_id": [],  # wrong: should be object
                    "skills": [],
                    "projects": [],
                }
            )
        )
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        with pytest.raises(
            APIResponseError, match="work_highlights_by_id must be an object"
        ):
            client.curate(portfolio_data, "Job description.")


# ---------------------------------------------------------------------------
# TestCurateErrorMapping
# ---------------------------------------------------------------------------


class TestCurateErrorMapping:
    @pytest.mark.parametrize(
        ("sdk_exc_class", "expected_exc_class", "match_text"),
        [
            (
                anthropic.AuthenticationError,
                APIAuthError,
                "Invalid or missing",
            ),
            (
                anthropic.PermissionDeniedError,
                APIAuthError,
                "lacks required permissions",
            ),
            (
                anthropic.RateLimitError,
                APIRateLimitError,
                "rate limit",
            ),
            (
                anthropic.BadRequestError,
                APIResponseError,
                "rejected the request",
            ),
            (
                anthropic.APIConnectionError,
                APIError,
                "check network",
            ),
            (
                anthropic.APITimeoutError,
                APIError,
                "timed out",
            ),
            (
                anthropic.InternalServerError,
                APIError,
                "Anthropic API error",
            ),
        ],
    )
    def test_sdk_exception_mapping(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        sdk_exc_class: Any,
        expected_exc_class: type[Exception],
        match_text: str,
    ) -> None:
        mock_anthropic = mocker.patch("curator.client.anthropic.Anthropic")
        mock_instance = mock_anthropic.return_value

        # Build an appropriate SDK exception — connection/timeout types
        # take only `request`, while status-code errors need response+body.
        connection_types = (
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
        )
        if sdk_exc_class in connection_types:
            exc: Exception = sdk_exc_class(request=MagicMock())
        else:
            exc = sdk_exc_class(
                message="test error",
                response=MagicMock(
                    status_code=400,
                    headers={},
                    request=MagicMock(),
                ),
                body=None,
            )
            exc.request_id = "req_test_123"  # type: ignore[attr-defined]

        mock_instance.messages.stream.side_effect = exc
        client = CuratorClient(mock_settings)

        with pytest.raises(expected_exc_class, match=match_text) as exc_info:
            client.curate(portfolio_data, "Job description.")

        assert exc_info.value.__cause__ is exc

    def test_generic_api_error_catch_all(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        mock_anthropic = mocker.patch("curator.client.anthropic.Anthropic")
        mock_instance = mock_anthropic.return_value
        exc = anthropic.APIError(
            message="unknown error",
            request=MagicMock(),
            body=None,
        )
        exc.request_id = "req_test_456"
        mock_instance.messages.stream.side_effect = exc
        client = CuratorClient(mock_settings)

        with pytest.raises(APIError, match="Anthropic API error") as exc_info:
            client.curate(portfolio_data, "Job description.")

        assert exc_info.value.__cause__ is exc


# ---------------------------------------------------------------------------
# TestValidateCurationIds
# ---------------------------------------------------------------------------


class TestValidateCurationIds:
    def test_valid_ids_pass(
        self,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        _validate_curation_ids(valid_curation, portfolio_data)

    def test_unknown_work_id_fails(
        self,
        portfolio_data: PortfolioData,
        valid_curation_dict: dict[str, object],
    ) -> None:
        valid_curation_dict["work_highlights"] = [
            {"work_id": "nonexistent-job", "highlight_ids": []},
            {"work_id": "acme-senior-engineer", "highlight_ids": ["acme-deployed-k8s"]},
        ]
        curation = ResumeCuration.model_validate(valid_curation_dict)

        with pytest.raises(APIResponseError, match="unknown work_id"):
            _validate_curation_ids(curation, portfolio_data)

    def test_missing_work_entry_fails(
        self,
        portfolio_data: PortfolioData,
        valid_curation_dict: dict[str, object],
    ) -> None:
        valid_curation_dict["work_highlights"] = []
        with pytest.raises(ValidationError):
            ResumeCuration.model_validate(valid_curation_dict)

    def test_duplicate_work_id_fails(
        self,
        portfolio_data: PortfolioData,
        valid_curation_dict: dict[str, object],
    ) -> None:
        first_work = portfolio_data.work[0]
        valid_curation_dict["work_highlights"] = [
            {"work_id": first_work.id, "highlight_ids": [first_work.highlights[0].id]},
            {"work_id": first_work.id, "highlight_ids": [first_work.highlights[0].id]},
        ]
        curation = ResumeCuration.model_validate(valid_curation_dict)
        with pytest.raises(APIResponseError, match="duplicate work_ids"):
            _validate_curation_ids(curation, portfolio_data)

    def test_unknown_highlight_id_in_known_work_entry_dropped_with_warn(
        self,
        portfolio_data: PortfolioData,
        valid_curation_dict: dict[str, object],
    ) -> None:
        # Unknown highlight_id inside a known work_id is now soft: the
        # validator drops it with a WARNING and returns a sanitized
        # curation. The renderer safety-net then fills omitted IDs in
        # portfolio order. Mirrors the skill-keyword precedent.
        valid_curation_dict["work_highlights"] = [
            {
                "work_id": "acme-senior-engineer",
                "highlight_ids": ["acme-deployed-k8s", "bogus-highlight"],
            },
        ]
        curation = ResumeCuration.model_validate(valid_curation_dict)

        sanitized = _validate_curation_ids(curation, portfolio_data)

        assert sanitized.work_highlights[0].work_id == "acme-senior-engineer"
        assert sanitized.work_highlights[0].highlight_ids == ["acme-deployed-k8s"]

    def test_multiple_unknown_highlight_ids_dropped_with_single_consolidated_warn(
        self,
        portfolio_data: PortfolioData,
        valid_curation_dict: dict[str, object],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from loguru import logger as _logger

        _logger.remove()
        _logger.add(sys.stderr, level="WARNING", format="{message}")

        valid_curation_dict["work_highlights"] = [
            {
                "work_id": "acme-senior-engineer",
                "highlight_ids": [
                    "acme-deployed-k8s",
                    "bogus-one",
                    "bogus-two",
                    "bogus-three",
                ],
            },
        ]
        curation = ResumeCuration.model_validate(valid_curation_dict)

        sanitized = _validate_curation_ids(curation, portfolio_data)

        assert sanitized.work_highlights[0].highlight_ids == ["acme-deployed-k8s"]

        captured = capsys.readouterr()
        # One consolidated WARN line listing all three bogus IDs.
        drop_lines = [
            line
            for line in captured.err.splitlines()
            if "dropped" in line and "hallucinated highlight_id" in line
        ]
        assert len(drop_lines) == 1
        assert "bogus-one" in drop_lines[0]
        assert "bogus-two" in drop_lines[0]
        assert "bogus-three" in drop_lines[0]
        # Pin the consolidated WARN format: "Kept N/M." where N is the
        # post-sanitize count and M is the model-emitted count. Catches a
        # future refactor that reorders or removes the counts.
        assert "Kept 1/4" in drop_lines[0]
        _logger.remove()

    def test_unknown_highlight_ids_across_multiple_work_entries_each_warn_independently(
        self,
        valid_curation_dict: dict[str, object],
        capsys: pytest.CaptureFixture[str],
        portfolio_data: PortfolioData,
    ) -> None:
        from loguru import logger as _logger

        from curator.models import WorkEntry

        _logger.remove()
        _logger.add(sys.stderr, level="WARNING", format="{message}")

        # Augment portfolio with a second work entry so we can place bogus IDs
        # under two different parents.
        second_work = WorkEntry.model_validate(
            {
                "id": "beta-staff-engineer",
                "name": "Beta Inc",
                "position": "Staff Engineer",
                "startDate": "2021-01",
                "endDate": "2023-05",
                "location": "Remote",
                "summary": "Beta work.",
                "highlights": [
                    {
                        "id": "beta-launched-feature",
                        "text": "Launched feature serving 1M users.",
                        "tags": ["product"],
                        "resume_variants": ["general"],
                        "technologies": ["Python"],
                    },
                ],
                "tags": ["product"],
                "resume_variants": ["general"],
                "technologies": ["Python"],
            },
        )
        portfolio = replace(
            portfolio_data,
            work=[portfolio_data.work[0], second_work],
        )

        valid_curation_dict["work_highlights"] = [
            {
                "work_id": "acme-senior-engineer",
                "highlight_ids": ["acme-deployed-k8s", "bogus-A"],
            },
            {
                "work_id": "beta-staff-engineer",
                "highlight_ids": ["beta-launched-feature", "bogus-B1", "bogus-B2"],
            },
        ]
        curation = ResumeCuration.model_validate(valid_curation_dict)

        sanitized = _validate_curation_ids(curation, portfolio)

        assert sanitized.work_highlights[0].highlight_ids == ["acme-deployed-k8s"]
        assert sanitized.work_highlights[1].highlight_ids == ["beta-launched-feature"]

        captured = capsys.readouterr()
        drop_lines = [
            line
            for line in captured.err.splitlines()
            if "dropped" in line and "hallucinated highlight_id" in line
        ]
        # One WARN per affected work entry.
        assert len(drop_lines) == 2
        acme_line = next(line for line in drop_lines if "acme-senior-engineer" in line)
        beta_line = next(line for line in drop_lines if "beta-staff-engineer" in line)
        assert "bogus-A" in acme_line
        assert "bogus-B1" in beta_line
        assert "bogus-B2" in beta_line
        _logger.remove()

    def test_omitted_warning_count_uses_sanitized_list_not_raw(
        self,
        valid_curation_dict: dict[str, object],
        capsys: pytest.CaptureFixture[str],
        portfolio_data: PortfolioData,
    ) -> None:
        from loguru import logger as _logger

        from curator.models import WorkEntry

        _logger.remove()
        _logger.add(sys.stderr, level="WARNING", format="{message}")

        # Portfolio entry with 3 highlights; model emits 1 real + 1 bogus
        # = 1 kept, so omitted is 2/3 (NOT 3/3 against the raw count).
        work = WorkEntry.model_validate(
            {
                "id": "acme-senior-engineer",
                "name": "Acme Corp",
                "position": "Senior Engineer",
                "startDate": "2023-06",
                "endDate": "",
                "location": "Remote",
                "summary": "Led platform engineering.",
                "highlights": [
                    {
                        "id": "acme-deployed-k8s",
                        "text": "Deployed Kubernetes cluster.",
                        "tags": [],
                        "resume_variants": ["general"],
                        "technologies": [],
                    },
                    {
                        "id": "acme-built-pipeline",
                        "text": "Built CI/CD pipeline.",
                        "tags": [],
                        "resume_variants": ["general"],
                        "technologies": [],
                    },
                    {
                        "id": "acme-launched-platform",
                        "text": "Launched platform.",
                        "tags": [],
                        "resume_variants": ["general"],
                        "technologies": [],
                    },
                ],
                "tags": [],
                "resume_variants": ["general"],
                "technologies": [],
            },
        )
        portfolio = replace(portfolio_data, work=[work])

        valid_curation_dict["work_highlights"] = [
            {
                "work_id": "acme-senior-engineer",
                "highlight_ids": ["acme-deployed-k8s", "bogus-highlight"],
            },
        ]
        curation = ResumeCuration.model_validate(valid_curation_dict)

        _validate_curation_ids(curation, portfolio)

        captured = capsys.readouterr()
        omitted_lines = [
            line for line in captured.err.splitlines() if "highlights ranked" in line
        ]
        assert len(omitted_lines) == 1
        # Post-sanitize: 1 kept out of 3 portfolio highlights, 2 omitted.
        assert "1/3 highlights ranked" in omitted_lines[0]
        assert "2 will be appended" in omitted_lines[0]
        _logger.remove()

    def test_all_highlight_ids_unknown_collapses_to_empty_and_warns(
        self,
        portfolio_data: PortfolioData,
        valid_curation_dict: dict[str, object],
    ) -> None:
        # Edge: every emitted highlight_id is bogus. Validator drops them
        # all, returns a ranking with empty highlight_ids. Renderer
        # safety-net then appends all portfolio highlights in portfolio
        # order.
        valid_curation_dict["work_highlights"] = [
            {
                "work_id": "acme-senior-engineer",
                "highlight_ids": ["bogus-one", "bogus-two"],
            },
        ]
        curation = ResumeCuration.model_validate(valid_curation_dict)

        sanitized = _validate_curation_ids(curation, portfolio_data)

        assert sanitized.work_highlights[0].work_id == "acme-senior-engineer"
        assert sanitized.work_highlights[0].highlight_ids == []

    def test_cross_entry_attribution_drops_real_id_from_wrong_parent(
        self,
        valid_curation_dict: dict[str, object],
        portfolio_data: PortfolioData,
    ) -> None:
        # Reproduces the real-world cross-entry attribution failure: the
        # model emits a REAL highlight_id that exists in the portfolio
        # but belongs to a DIFFERENT work entry than the one being ranked.
        # The validator must drop it from the wrong parent regardless of
        # whether it's a real ID elsewhere in the portfolio.
        from curator.models import WorkEntry

        beta_work = WorkEntry.model_validate(
            {
                "id": "beta-staff-engineer",
                "name": "Beta Inc",
                "position": "Staff Engineer",
                "startDate": "2021-01",
                "endDate": "2023-05",
                "location": "Remote",
                "summary": "Beta work.",
                "highlights": [
                    {
                        "id": "beta-launched-feature",
                        "text": "Launched feature serving 1M users.",
                        "tags": ["product"],
                        "resume_variants": ["general"],
                        "technologies": ["Python"],
                    },
                ],
                "tags": ["product"],
                "resume_variants": ["general"],
                "technologies": ["Python"],
            },
        )
        portfolio = replace(
            portfolio_data,
            work=[portfolio_data.work[0], beta_work],
        )

        # Place beta-launched-feature (a REAL id, but belongs to beta) under
        # acme-senior-engineer. This is the cross-entry attribution shape.
        valid_curation_dict["work_highlights"] = [
            {
                "work_id": "acme-senior-engineer",
                "highlight_ids": ["acme-deployed-k8s", "beta-launched-feature"],
            },
            {
                "work_id": "beta-staff-engineer",
                "highlight_ids": ["beta-launched-feature"],
            },
        ]
        curation = ResumeCuration.model_validate(valid_curation_dict)

        sanitized = _validate_curation_ids(curation, portfolio)

        acme_ranking = next(
            wh
            for wh in sanitized.work_highlights
            if wh.work_id == "acme-senior-engineer"
        )
        beta_ranking = next(
            wh
            for wh in sanitized.work_highlights
            if wh.work_id == "beta-staff-engineer"
        )
        # acme loses the cross-attributed beta ID.
        assert acme_ranking.highlight_ids == ["acme-deployed-k8s"]
        # beta keeps its own ID untouched.
        assert beta_ranking.highlight_ids == ["beta-launched-feature"]

    def test_kept_highlight_ids_preserve_ai_emitted_order(
        self,
        valid_curation_dict: dict[str, object],
        portfolio_data: PortfolioData,
    ) -> None:
        # The renderer respects the ranking the AI emitted; the safety-net
        # appends omitted IDs in portfolio order AFTER the kept ones. So
        # the validator's filter must preserve AI emission order across
        # surviving IDs (not re-sort by portfolio order).
        from curator.models import WorkEntry

        work = WorkEntry.model_validate(
            {
                "id": "acme-senior-engineer",
                "name": "Acme Corp",
                "position": "Senior Engineer",
                "startDate": "2023-06",
                "endDate": "",
                "location": "Remote",
                "summary": "Led platform engineering.",
                "highlights": [
                    {
                        "id": "acme-deployed-k8s",
                        "text": "Deployed Kubernetes cluster.",
                        "tags": [],
                        "resume_variants": ["general"],
                        "technologies": [],
                    },
                    {
                        "id": "acme-built-pipeline",
                        "text": "Built CI/CD pipeline.",
                        "tags": [],
                        "resume_variants": ["general"],
                        "technologies": [],
                    },
                    {
                        "id": "acme-launched-platform",
                        "text": "Launched platform.",
                        "tags": [],
                        "resume_variants": ["general"],
                        "technologies": [],
                    },
                ],
                "tags": [],
                "resume_variants": ["general"],
                "technologies": [],
            },
        )
        portfolio = replace(portfolio_data, work=[work])

        # AI emits: real-3 (last in portfolio order), bogus, real-1 (first
        # in portfolio order). Kept order must be [real-3, real-1].
        valid_curation_dict["work_highlights"] = [
            {
                "work_id": "acme-senior-engineer",
                "highlight_ids": [
                    "acme-launched-platform",
                    "bogus-highlight",
                    "acme-deployed-k8s",
                ],
            },
        ]
        curation = ResumeCuration.model_validate(valid_curation_dict)

        sanitized = _validate_curation_ids(curation, portfolio)

        assert sanitized.work_highlights[0].highlight_ids == [
            "acme-launched-platform",
            "acme-deployed-k8s",
        ]

    def test_no_drops_returns_same_curation_instance(
        self,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        # Identity-preservation contract (models.py docstring): when the
        # validator drops nothing, the returned curation IS the input
        # object, not a copy. Locks the contract against future refactors
        # of the return path.
        sanitized = _validate_curation_ids(valid_curation, portfolio_data)
        assert sanitized is valid_curation

    def test_drops_return_new_instance(
        self,
        portfolio_data: PortfolioData,
        valid_curation_dict: dict[str, object],
    ) -> None:
        # Companion to the no-drops identity test: when a drop occurs, the
        # returned curation MUST be a copy (frozen model semantics, and so
        # callers that compare by `is` notice the change).
        valid_curation_dict["work_highlights"] = [
            {
                "work_id": "acme-senior-engineer",
                "highlight_ids": ["acme-deployed-k8s", "bogus"],
            },
        ]
        curation = ResumeCuration.model_validate(valid_curation_dict)

        sanitized = _validate_curation_ids(curation, portfolio_data)

        assert sanitized is not curation
        assert sanitized.work_highlights[0] is not curation.work_highlights[0]

    def test_invalid_project_id_fails(
        self,
        portfolio_data: PortfolioData,
        valid_curation_dict: dict[str, object],
    ) -> None:
        valid_curation_dict["projects"] = ["nonexistent-project"]
        curation = ResumeCuration.model_validate(valid_curation_dict)

        with pytest.raises(APIResponseError, match="nonexistent-project"):
            _validate_curation_ids(curation, portfolio_data)

    def test_invalid_keyword_in_valid_skill_group_dropped_with_warn(
        self,
        portfolio_data: PortfolioData,
        valid_curation_dict: dict[str, object],
    ) -> None:
        # Hallucinated keywords inside a valid skill group are now soft:
        # the validator drops them with a WARNING and returns a sanitized
        # curation. Hard reject only on unknown skill_group_id.
        valid_curation_dict["skills"] = [
            {"skill_id": "cloud-aws", "keywords": ["EKS", "nonexistent-kw"]}
        ]
        curation = ResumeCuration.model_validate(valid_curation_dict)

        sanitized = _validate_curation_ids(curation, portfolio_data)
        # Bogus keyword removed; valid keyword preserved.
        assert sanitized.skills[0].skill_id == "cloud-aws"
        assert sanitized.skills[0].keywords == ["EKS"]

    def test_multiple_errors_accumulated(
        self,
        portfolio_data: PortfolioData,
        valid_curation_dict: dict[str, object],
    ) -> None:
        valid_curation_dict["skills"] = [{"skill_id": "bad-skill", "keywords": ["kw"]}]
        valid_curation_dict["projects"] = ["bad-project"]
        curation = ResumeCuration.model_validate(valid_curation_dict)

        with pytest.raises(APIResponseError, match="2 invalid ID") as exc_info:
            _validate_curation_ids(curation, portfolio_data)

        assert "bad-skill" in str(exc_info.value)
        assert "bad-project" in str(exc_info.value)

    def test_empty_skills_and_projects_pass(
        self,
        portfolio_data: PortfolioData,
        valid_curation_dict: dict[str, object],
    ) -> None:
        valid_curation_dict["skills"] = []
        valid_curation_dict["projects"] = []
        curation = ResumeCuration.model_validate(valid_curation_dict)

        _validate_curation_ids(curation, portfolio_data)

    def test_unknown_work_entry_skips_highlight_validation(
        self,
        portfolio_data: PortfolioData,
        valid_curation_dict: dict[str, object],
    ) -> None:
        valid_curation_dict["work_highlights"] = [
            {"work_id": "nonexistent-job", "highlight_ids": ["also-nonexistent"]},
            {"work_id": "acme-senior-engineer", "highlight_ids": ["acme-deployed-k8s"]},
        ]
        curation = ResumeCuration.model_validate(valid_curation_dict)

        with pytest.raises(APIResponseError) as exc_info:
            _validate_curation_ids(curation, portfolio_data)

        assert "nonexistent-job" in str(exc_info.value)
        assert "also-nonexistent" not in str(exc_info.value)

    def test_existing_resume_curation_dict_has_mismatched_ids(
        self,
        portfolio_data: PortfolioData,
        resume_curation_dict: dict[str, object],
    ) -> None:
        curation = ResumeCuration.model_validate(resume_curation_dict)

        with pytest.raises(APIResponseError, match="invalid ID"):
            _validate_curation_ids(curation, portfolio_data)


# ---------------------------------------------------------------------------
# TestCurateLogging
# ---------------------------------------------------------------------------


class TestCurateLogging:
    """Tests for logging in CuratorClient.curate()."""

    def test_api_request_log_includes_sizes(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """API request log includes prompt chars, JD chars, max_tokens."""
        from loguru import logger as _logger

        _logger.remove()
        _logger.add(sys.stderr, level="INFO", format="{message}")

        message = _make_mock_message(valid_curation)
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        client.curate(portfolio_data, "Senior SRE role at Acme.")

        captured = capsys.readouterr()
        assert "API request:" in captured.err
        assert "prompt=" in captured.err
        assert "jd=" in captured.err
        assert "max_tokens=4096" in captured.err
        _logger.remove()

    def test_api_request_log_includes_effort_when_set(
        self,
        mocker: Any,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Any,
    ) -> None:
        """API request log includes effort when explicitly configured."""
        from loguru import logger as _logger

        _logger.remove()
        _logger.add(sys.stderr, level="INFO", format="{message}")

        settings_with_effort = CuratorSettings(
            anthropic_api_key=SecretStr("sk-ant-test-not-real"),
            model="claude-sonnet-4-6-20260217",
            max_tokens=4096,
            effort="high",
            api_max_retries=1,
            portfolio_path=tmp_path,
            allow_api_spend=True,
        )

        message = _make_mock_message(valid_curation)
        _wire_mock_stream(mocker, message)
        client = CuratorClient(settings_with_effort)

        client.curate(portfolio_data, "Job description.")

        captured = capsys.readouterr()
        assert "effort=high" in captured.err
        _logger.remove()

    def test_api_response_logged_at_info_with_tokens(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """API response log is at INFO with token counts and cache stats."""
        from loguru import logger as _logger

        _logger.remove()
        _logger.add(sys.stderr, level="INFO", format="{message}")

        usage = _make_mock_usage(
            input_tokens=2500,
            output_tokens=800,
            cache_creation_input_tokens=1500,
            cache_read_input_tokens=400,
        )
        message = _make_mock_message(valid_curation, usage=usage)
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        client.curate(portfolio_data, "Job description.")

        captured = capsys.readouterr()
        assert "API response:" in captured.err
        assert "in=2500" in captured.err
        assert "out=800" in captured.err
        assert "cache_create=1500" in captured.err
        assert "cache_read=400" in captured.err
        _logger.remove()

    def test_curation_summary_includes_selection_details(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Curation summary log includes company slug, counts, and section order."""
        from loguru import logger as _logger

        _logger.remove()
        _logger.add(sys.stderr, level="INFO", format="{message}")

        message = _make_mock_message(valid_curation)
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        client.curate(portfolio_data, "Job description.")

        captured = capsys.readouterr()
        assert "Curation:" in captured.err
        assert "company=acme-corp" in captured.err
        assert "work=" in captured.err
        assert "skills=" in captured.err
        _logger.remove()

    def test_curation_summary_highlight_count(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Curation summary includes total highlight count."""
        from loguru import logger as _logger

        _logger.remove()
        _logger.add(sys.stderr, level="INFO", format="{message}")

        message = _make_mock_message(valid_curation)
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        client.curate(portfolio_data, "Job description.")

        captured = capsys.readouterr()
        # valid_curation has 1 work entry with 1 highlight
        assert "1 highlights" in captured.err
        _logger.remove()


# ---------------------------------------------------------------------------
# Cover letter flag tests (single-call invariant, partial recovery)
# ---------------------------------------------------------------------------


from curator.rules import COVER_LETTER_MAX_TOKENS_HEADROOM  # noqa: E402


def _valid_cover_letter() -> CoverLetterCuration:
    """Mirror the fixture in test_models so validator passes."""
    from tests.helpers import valid_cover_letter_kwargs as _valid_letter_kwargs

    return CoverLetterCuration(**_valid_letter_kwargs())


class TestCurationResultCoverLetterField:
    def test_defaults_to_none(self, valid_curation: ResumeCuration) -> None:
        result = CurationResult(
            curation=valid_curation,
            model="test",
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        assert result.cover_letter is None

    def test_accepts_cover_letter(self, valid_curation: ResumeCuration) -> None:
        letter = _valid_cover_letter()
        result = CurationResult(
            curation=valid_curation,
            model="test",
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            cover_letter=letter,
        )
        assert result.cover_letter is letter


class TestCurateSingleCallInvariant:
    # The retry-with-feedback feature tracked in TODO.md "Curation
    # Reliability > Retry-with-feedback loop" would bump the call count
    # to 2+ when validation fails. When that lands, this class must be
    # parametrized over a retry-budget flag and the assertion adjusted
    # to `<= 1 + retry_budget`, NOT deleted. Removing this invariant
    # would silently double the cost of every paid run on grammar
    # regression.

    """Lock the 'no double paying' rule at the unit test layer.

    The assertion covers stream/create/count_tokens so a future refactor
    that swaps one for another does not slip past silently.
    """

    @pytest.mark.parametrize("with_cover_letter", [False, True])
    def test_exactly_one_billable_api_call(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
        with_cover_letter: bool,
    ) -> None:
        if with_cover_letter:
            wrapped = ResumeCurationWithCoverLetter(
                resume=valid_curation, cover_letter=_valid_cover_letter()
            )
            message = _make_mock_message(wrapped)
        else:
            message = _make_mock_message(valid_curation)
        mock_anthropic = _wire_mock_stream(mocker, message)
        mock_instance = mock_anthropic.return_value
        client = CuratorClient(mock_settings)

        client.curate(
            portfolio_data,
            "Senior role at Acme.",
            with_cover_letter=with_cover_letter,
        )

        assert mock_instance.messages.stream.call_count == 1
        # Guard against future SDK method additions.
        assert mock_instance.messages.create.call_count == 0
        assert mock_instance.messages.count_tokens.call_count == 0


class TestCurateWithCoverLetter:
    def test_returns_cover_letter_from_single_parse(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        letter = _valid_cover_letter()
        wrapped = ResumeCurationWithCoverLetter(
            resume=valid_curation, cover_letter=letter
        )
        message = _make_mock_message(wrapped)
        mock_anthropic = _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        result = client.curate(portfolio_data, "JD text.", with_cover_letter=True)

        assert result.cover_letter is not None
        assert result.cover_letter.salutation == letter.salutation
        assert result.curation == valid_curation
        assert mock_anthropic.return_value.messages.stream.call_count == 1

    def test_omits_cover_letter_when_flag_off(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        message = _make_mock_message(valid_curation)
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        result = client.curate(portfolio_data, "JD text.", with_cover_letter=False)
        assert result.cover_letter is None

    def test_applies_max_tokens_headroom(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        wrapped = ResumeCurationWithCoverLetter(
            resume=valid_curation, cover_letter=_valid_cover_letter()
        )
        message = _make_mock_message(wrapped)
        mock_anthropic = _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        client.curate(portfolio_data, "JD text.", with_cover_letter=True)

        stream_kwargs = mock_anthropic.return_value.messages.stream.call_args[1]
        expected = mock_settings.max_tokens + COVER_LETTER_MAX_TOKENS_HEADROOM
        assert stream_kwargs["max_tokens"] == expected

    def test_does_not_mutate_self_max_tokens(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        wrapped = ResumeCurationWithCoverLetter(
            resume=valid_curation, cover_letter=_valid_cover_letter()
        )
        # First call with flag on.
        message1 = _make_mock_message(wrapped)
        mock_anthropic = _wire_mock_stream(mocker, message1)
        client = CuratorClient(mock_settings)
        client.curate(portfolio_data, "JD one.", with_cover_letter=True)
        # Now call with flag off, same client.
        message2 = _make_mock_message(valid_curation)
        stream_ctx = mock_anthropic.return_value.messages.stream.return_value
        stream_ctx.get_final_message.return_value = message2
        client.curate(portfolio_data, "JD two.", with_cover_letter=False)

        last_call = mock_anthropic.return_value.messages.stream.call_args_list[-1]
        assert last_call.kwargs["max_tokens"] == mock_settings.max_tokens

    def test_cover_letter_validation_failure_persists_resume(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
        tmp_path: Any,
    ) -> None:
        """Resume is written to a side file when cover letter validation fails."""
        from curator.exceptions import CurationValidationError

        wrapped = ResumeCurationWithCoverLetter(
            resume=valid_curation, cover_letter=_valid_cover_letter()
        )
        message = _make_mock_message(wrapped)
        _wire_mock_stream(mocker, message)

        # Force validator failure.
        mocker.patch(
            "curator.client.validate_cover_letter",
            side_effect=CurationValidationError("test trigger"),
        )
        # Inject the output dir via settings (no monkey-patching needed
        # now that the client stores its own settings).
        mock_settings_output = tmp_path / "profiles"
        scoped_settings = CuratorSettings(
            anthropic_api_key=mock_settings.anthropic_api_key,
            allow_api_spend=True,
            portfolio_path=mock_settings.portfolio_path,
            output_dir=mock_settings_output,
        )

        client = CuratorClient(scoped_settings)
        with pytest.raises(APIResponseError, match="Cover letter validation failed"):
            client.curate(portfolio_data, "JD.", with_cover_letter=True)

        # Side file should exist under output_dir; verify it round-trips
        # to a valid ResumeCuration so the recovery artifact is usable.
        import yaml

        from curator.models import ResumeCuration

        written = list(mock_settings_output.glob("curation_partial-*.yaml"))
        assert len(written) == 1
        data = yaml.safe_load(written[0].read_text(encoding="utf-8"))
        ResumeCuration.model_validate(data)
        # Filename includes company_slug and a sanitized request id slice.
        assert valid_curation.company_slug in written[0].name
        assert "msg_test_123" in written[0].name

    def test_stop_reason_max_tokens_with_cover_letter_returns_partial(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        """On truncation with flag on, client WARNS and returns the partial."""
        wrapped = ResumeCurationWithCoverLetter(
            resume=valid_curation, cover_letter=_valid_cover_letter()
        )
        message = _make_mock_message(wrapped, stop_reason="max_tokens")
        mock_anthropic = _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        result = client.curate(portfolio_data, "JD text.", with_cover_letter=True)
        assert result.cover_letter is not None
        assert mock_anthropic.return_value.messages.stream.call_count == 1

    def test_stop_reason_max_tokens_without_flag_still_raises(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        """Pre-existing behavior off-path: max_tokens still raises APIResponseError."""
        message = _make_mock_message(valid_curation, stop_reason="max_tokens")
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)
        with pytest.raises(APIResponseError):
            client.curate(portfolio_data, "JD text.", with_cover_letter=False)
