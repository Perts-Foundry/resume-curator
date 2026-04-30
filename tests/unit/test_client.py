"""Unit tests for the Anthropic API client wrapper."""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
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
    PortfolioData,
    ResumeCuration,
)

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
) -> MagicMock:
    """Build a mock ParsedMessage with controllable attributes."""
    message = MagicMock()
    message.parsed_output = curation
    message.stop_reason = stop_reason
    message.model = model
    message.id = "msg_test_123"
    message.usage = usage or _make_mock_usage()
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
        assert result.curation is valid_curation

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

    def test_passes_output_format(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        valid_curation: ResumeCuration,
    ) -> None:
        message = _make_mock_message(valid_curation)
        mock_anthropic = _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        client.curate(portfolio_data, "Job description.")

        stream_kwargs = mock_anthropic.return_value.messages.stream.call_args[1]
        assert stream_kwargs["output_format"] is ResumeCuration

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
        assert "output_config" not in stream_kwargs

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
        assert stream_kwargs["output_config"] == {"effort": "high"}

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

    def test_end_turn_with_none_parsed_output(
        self,
        mocker: Any,
        mock_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        message = _make_mock_message(None, stop_reason="end_turn")
        _wire_mock_stream(mocker, message)
        client = CuratorClient(mock_settings)

        with pytest.raises(APIResponseError, match="No structured output"):
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
        assert result.curation is valid_curation

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

    def test_unknown_highlight_id_fails(
        self,
        portfolio_data: PortfolioData,
        valid_curation_dict: dict[str, object],
    ) -> None:
        valid_curation_dict["work_highlights"] = [
            {
                "work_id": "acme-senior-engineer",
                "highlight_ids": ["bogus-highlight"],
            },
        ]
        curation = ResumeCuration.model_validate(valid_curation_dict)

        with pytest.raises(APIResponseError, match="bogus-highlight"):
            _validate_curation_ids(curation, portfolio_data)

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


from curator.models import (  # noqa: E402
    CoverLetterCuration,
    ResumeCurationWithCoverLetter,
)
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
        assert result.curation is valid_curation
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
