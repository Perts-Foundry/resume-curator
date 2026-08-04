"""Unit tests for the headless Claude Code backend.

All subprocess activity is faked by patching ``curator.headless.subprocess.run``
(the ``_fake_typst_run`` pattern from tests/integration/test_render_pipeline.py);
no test here spawns a real ``claude`` process or consumes subscription quota.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from curator.client import CurationResult
from curator.config import CuratorSettings
from curator.exceptions import (
    APIAuthError,
    APIError,
    APIResponseError,
    APISpendGuardError,
    HeadlessCLIError,
    HeadlessUsageLimitError,
)
from curator.headless import (
    HEADLESS_DISALLOWED_TOOLS,
    HeadlessCuratorClient,
    HeadlessResult,
    flatten_system_blocks,
    run_structured_prompt,
)
from curator.models import CoverLetterCuration, PortfolioData, ResumeCuration
from tests.helpers import make_curation_dict, valid_cover_letter
from tests.unit.test_client import _curation_to_wire_dict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
}

_DEFAULT_USAGE: dict[str, int] = {
    "input_tokens": 1200,
    "output_tokens": 640,
    "cache_creation_input_tokens": 900,
    "cache_read_input_tokens": 300,
}

_SERVED_MODEL = "claude-opus-5-20260115"


def _make_envelope(
    structured_output: dict[str, Any] | None,
    *,
    subtype: str = "success",
    is_error: bool = False,
    result_text: str = "",
    usage: dict[str, int] | None = None,
    model_usage: dict[str, Any] | None = None,
    total_cost_usd: float | None = 1.23,
    session_id: str | None = "sess-test-abc",
) -> dict[str, Any]:
    """Build a ``claude -p --output-format json`` result envelope."""
    envelope: dict[str, Any] = {
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "result": result_text,
        "usage": dict(_DEFAULT_USAGE) if usage is None else usage,
        "modelUsage": {_SERVED_MODEL: {}} if model_usage is None else model_usage,
        "total_cost_usd": total_cost_usd,
        "session_id": session_id,
    }
    if structured_output is not None:
        envelope["structured_output"] = structured_output
    return envelope


class _FakeClaudeRun:
    """Fake ``subprocess.run`` recording ``(cmd, kwargs)`` per call.

    Also snapshots the ``--system-prompt-file`` content while the call is
    in flight, since the temp dir is gone by the time the test asserts.
    """

    def __init__(self, envelope: dict[str, Any] | str, *, returncode: int = 0) -> None:
        self.envelope = envelope
        self.returncode = returncode
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.system_prompt_contents: list[str] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> Any:
        self.calls.append((cmd, kwargs))
        if "--system-prompt-file" in cmd:
            path = Path(cmd[cmd.index("--system-prompt-file") + 1])
            self.system_prompt_contents.append(path.read_text(encoding="utf-8"))
        stdout = (
            self.envelope
            if isinstance(self.envelope, str)
            else json.dumps(self.envelope)
        )
        return type(
            "CompletedProcess",
            (),
            {"returncode": self.returncode, "stdout": stdout, "stderr": ""},
        )()


def _valid_wire_dict() -> dict[str, Any]:
    """Wire-shape curation dict whose IDs match the portfolio_data fixture."""
    curation = ResumeCuration.model_validate(
        make_curation_dict(
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
    )
    return _curation_to_wire_dict(curation)


def _run_with_fake(
    mocker: Any,
    fake: Any,
    **overrides: Any,
) -> HeadlessResult:
    """Patch subprocess.run with *fake* and invoke run_structured_prompt."""
    mocker.patch("curator.headless.subprocess.run", side_effect=fake)
    params: dict[str, Any] = {
        "system_text": "SYSTEM BLOCK ONE\n\nSYSTEM BLOCK TWO",
        "user_text": "USER PROMPT TEXT",
        "schema": _SCHEMA,
        "model": "claude-opus-5",
        "effort": None,
        "timeout": 600,
    }
    params.update(overrides)
    return run_structured_prompt(**params)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def headless_settings(tmp_path: Path) -> CuratorSettings:
    """Claude-code backend settings with NO API key configured."""
    return CuratorSettings(
        anthropic_api_key=None,
        backend="claude-code",
        model="claude-opus-5",
        allow_api_spend=True,
        portfolio_path=tmp_path,
        output_dir=tmp_path / "profiles",
    )


# ---------------------------------------------------------------------------
# TestFlattenSystemBlocks
# ---------------------------------------------------------------------------


class TestFlattenSystemBlocks:
    def test_joins_texts_with_blank_line(self) -> None:
        blocks = [
            {"type": "text", "text": "first block"},
            {"type": "text", "text": "second block"},
        ]
        assert flatten_system_blocks(blocks) == "first block\n\nsecond block"  # type: ignore[arg-type]

    def test_drops_cache_control(self) -> None:
        blocks = [
            {"type": "text", "text": "instructions"},
            {
                "type": "text",
                "text": "portfolio data",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ]
        flat = flatten_system_blocks(blocks)  # type: ignore[arg-type]
        assert flat == "instructions\n\nportfolio data"
        assert "cache_control" not in flat
        assert "ephemeral" not in flat

    def test_single_block_passthrough(self) -> None:
        assert flatten_system_blocks([{"type": "text", "text": "only"}]) == "only"  # type: ignore[arg-type]

    def test_matches_real_system_prompt_shape(
        self, portfolio_data: PortfolioData
    ) -> None:
        from curator.prompt import build_system_prompt

        blocks = build_system_prompt(portfolio_data)
        flat = flatten_system_blocks(blocks)
        assert flat == "\n\n".join(b["text"] for b in blocks)


# ---------------------------------------------------------------------------
# TestRunStructuredPromptContract
# ---------------------------------------------------------------------------


class TestRunStructuredPromptContract:
    """Pin the exact `claude -p` argv/kwargs contract (D8)."""

    def test_argv_prefix_and_required_flags(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        _run_with_fake(mocker, fake)
        cmd, _ = fake.calls[0]
        assert cmd[:2] == ["claude", "-p"]
        assert cmd[cmd.index("--output-format") + 1] == "json"
        assert "--no-session-persistence" in cmd
        assert "--strict-mcp-config" in cmd

    def test_deny_list_explicit_and_never_wildcard(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        _run_with_fake(mocker, fake)
        cmd, _ = fake.calls[0]
        # A "*" deny list also denies the CLI-internal StructuredOutput
        # tool, which silently kills structured output on a "success"
        # envelope. The list must stay explicit.
        assert "*" not in cmd
        deny_start = cmd.index("--disallowed-tools") + 1
        assert tuple(cmd[deny_start:]) == HEADLESS_DISALLOWED_TOOLS

    def test_model_passed_verbatim(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        _run_with_fake(mocker, fake, model="claude-sonnet-4-6")
        cmd, _ = fake.calls[0]
        # Aliases drift on the CLI (`sonnet` resolved to a newer major),
        # so the settings value must reach argv unmodified.
        assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-6"

    def test_json_schema_round_trips(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        _run_with_fake(mocker, fake)
        cmd, _ = fake.calls[0]
        assert json.loads(cmd[cmd.index("--json-schema") + 1]) == _SCHEMA

    def test_no_bare_and_no_max_turns(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        _run_with_fake(mocker, fake)
        cmd, _ = fake.calls[0]
        # --bare is API-key-only (never reads OAuth); --max-turns does
        # not exist in CLI 2.1.220 (the timeout is the runaway bound).
        assert "--bare" not in cmd
        assert "--max-turns" not in cmd

    def test_effort_omitted_when_none(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        _run_with_fake(mocker, fake, effort=None)
        cmd, _ = fake.calls[0]
        assert "--effort" not in cmd

    def test_effort_flag_when_set(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        _run_with_fake(mocker, fake, effort="high")
        cmd, _ = fake.calls[0]
        assert cmd[cmd.index("--effort") + 1] == "high"

    def test_user_prompt_arrives_on_stdin(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        _run_with_fake(mocker, fake, user_text="the exact user prompt")
        _, kwargs = fake.calls[0]
        assert kwargs["input"] == "the exact user prompt"

    def test_system_prompt_file_exists_during_call(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        _run_with_fake(mocker, fake, system_text="FLAT\n\nSYSTEM")
        cmd, kwargs = fake.calls[0]
        # The fake reads the file while the subprocess "runs"; the temp
        # dir (also the subprocess cwd) is cleaned up afterwards.
        assert fake.system_prompt_contents == ["FLAT\n\nSYSTEM"]
        system_path = Path(cmd[cmd.index("--system-prompt-file") + 1])
        assert kwargs["cwd"] == str(system_path.parent)
        assert not system_path.exists()

    def test_api_key_stripped_from_env(
        self, mocker: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-should-vanish")
        monkeypatch.setenv("HEADLESS_TEST_MARKER", "kept")
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        _run_with_fake(mocker, fake)
        _, kwargs = fake.calls[0]
        # The API key outranks the subscription login inside the CLI.
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]
        assert kwargs["env"]["HEADLESS_TEST_MARKER"] == "kept"

    def test_timeout_forwarded(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        _run_with_fake(mocker, fake, timeout=123)
        _, kwargs = fake.calls[0]
        assert kwargs["timeout"] == 123

    def test_capture_output_text_mode(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        _run_with_fake(mocker, fake)
        _, kwargs = fake.calls[0]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True

    def test_success_maps_tokens_and_metadata(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        result = _run_with_fake(mocker, fake)
        assert result.structured_output == {"summary": "ok"}
        assert result.input_tokens == 1200
        assert result.output_tokens == 640
        assert result.cache_creation_input_tokens == 900
        assert result.cache_read_input_tokens == 300
        assert result.total_cost_usd == 1.23
        assert result.session_id == "sess-test-abc"

    def test_model_from_sole_model_usage_key(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(
            _make_envelope({"summary": "ok"}, model_usage={_SERVED_MODEL: {}})
        )
        result = _run_with_fake(mocker, fake, model="claude-opus-5")
        assert result.model == _SERVED_MODEL

    @pytest.mark.parametrize(
        "model_usage",
        [{}, {"model-a": {}, "model-b": {}}, None, "not-a-dict"],
        ids=["empty", "two-keys", "null", "wrong-type"],
    )
    def test_model_falls_back_to_requested(self, mocker: Any, model_usage: Any) -> None:
        envelope = _make_envelope({"summary": "ok"})
        envelope["modelUsage"] = model_usage
        fake = _FakeClaudeRun(envelope)
        result = _run_with_fake(mocker, fake, model="claude-opus-5")
        assert result.model == "claude-opus-5"

    def test_missing_usage_keys_default_to_zero(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}, usage={}))
        result = _run_with_fake(mocker, fake)
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.cache_creation_input_tokens == 0
        assert result.cache_read_input_tokens == 0

    @pytest.mark.parametrize(
        "usage",
        [None, "not-a-dict"],
        ids=["null", "wrong-type"],
    )
    def test_non_dict_usage_defaults_to_zero(self, mocker: Any, usage: Any) -> None:
        envelope = _make_envelope({"summary": "ok"})
        envelope["usage"] = usage
        fake = _FakeClaudeRun(envelope)
        result = _run_with_fake(mocker, fake)
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.cache_creation_input_tokens == 0
        assert result.cache_read_input_tokens == 0

    @pytest.mark.parametrize(
        ("total_cost_usd", "expected"),
        [
            (1.23, 1.23),
            # json.loads parses a whole-number cost as int; it must
            # coerce to float, not silently drop to None.
            (2, 2.0),
            # bool subclasses int and must NOT be treated as a cost.
            (True, None),
            ("1.23", None),
            (None, None),
        ],
        ids=["float", "int", "bool", "string", "null"],
    )
    def test_total_cost_usd_numeric_coercion(
        self, mocker: Any, total_cost_usd: Any, expected: float | None
    ) -> None:
        envelope = _make_envelope({"summary": "ok"})
        envelope["total_cost_usd"] = total_cost_usd
        fake = _FakeClaudeRun(envelope)
        result = _run_with_fake(mocker, fake)
        assert result.total_cost_usd == expected
        if expected is not None:
            assert isinstance(result.total_cost_usd, float)

    def test_error_envelope_wins_over_nonzero_exit(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(
            _make_envelope(None, is_error=True, result_text="something broke"),
            returncode=1,
        )
        with pytest.raises(APIResponseError, match="something broke"):
            _run_with_fake(mocker, fake)


# ---------------------------------------------------------------------------
# TestEnvelopeFailureModes
# ---------------------------------------------------------------------------


class TestEnvelopeFailureModes:
    """Full failure-mode matrix for envelope parsing and subprocess errors."""

    def test_usage_limit_carries_reset_text(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(
            _make_envelope(
                None,
                is_error=True,
                result_text="You've hit your session limit · resets 3:45pm",
            )
        )
        with pytest.raises(HeadlessUsageLimitError) as exc_info:
            _run_with_fake(mocker, fake)
        assert exc_info.value.reset_text == "3:45pm"

    def test_usage_limit_without_reset_time(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(
            _make_envelope(None, is_error=True, result_text="You've hit your limit")
        )
        with pytest.raises(HeadlessUsageLimitError) as exc_info:
            _run_with_fake(mocker, fake)
        assert exc_info.value.reset_text is None

    def test_not_logged_in_is_actionable_auth_error(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(
            _make_envelope(
                None, is_error=True, result_text="Not logged in. Please log in."
            )
        )
        with pytest.raises(APIAuthError) as exc_info:
            _run_with_fake(mocker, fake)
        message = str(exc_info.value)
        assert "claude /login" in message
        assert "claude setup-token" in message or "setup-token" in message
        assert "--backend api" in message

    def test_generic_error_result(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(
            _make_envelope(None, is_error=True, result_text="internal failure")
        )
        with pytest.raises(APIResponseError, match="internal failure"):
            _run_with_fake(mocker, fake)

    def test_non_success_subtype(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(
            _make_envelope({"summary": "ok"}, subtype="error_during_execution")
        )
        with pytest.raises(APIResponseError, match="error_during_execution"):
            _run_with_fake(mocker, fake)

    def test_success_without_structured_output(self, mocker: Any) -> None:
        # Envelope health is NOT sufficient: a denied StructuredOutput
        # tool still reports subtype=success with no structured_output.
        fake = _FakeClaudeRun(_make_envelope(None))
        with pytest.raises(APIResponseError, match="structured_output"):
            _run_with_fake(mocker, fake)

    @pytest.mark.parametrize(
        "stdout",
        ["", "not json {", '["a", "json", "array"]'],
        ids=["empty", "malformed", "non-object"],
    )
    def test_unparseable_stdout(self, mocker: Any, stdout: str) -> None:
        fake = _FakeClaudeRun(stdout)
        with pytest.raises(HeadlessCLIError):
            _run_with_fake(mocker, fake)

    def test_missing_binary_names_backend_api(self, mocker: Any) -> None:
        mocker.patch(
            "curator.headless.subprocess.run",
            side_effect=FileNotFoundError("claude"),
        )
        with pytest.raises(HeadlessCLIError, match="--backend api"):
            run_structured_prompt(
                system_text="s",
                user_text="u",
                schema=_SCHEMA,
                model="claude-opus-5",
                effort=None,
                timeout=600,
            )

    def test_timeout_names_headless_timeout_knob(self, mocker: Any) -> None:
        mocker.patch(
            "curator.headless.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=600),
        )
        with pytest.raises(HeadlessCLIError, match="CURATOR_HEADLESS_TIMEOUT"):
            run_structured_prompt(
                system_text="s",
                user_text="u",
                schema=_SCHEMA,
                model="claude-opus-5",
                effort=None,
                timeout=600,
            )

    @pytest.mark.parametrize(
        "exception_type", [HeadlessCLIError, HeadlessUsageLimitError]
    )
    def test_headless_exceptions_are_api_errors(
        self, exception_type: type[Exception]
    ) -> None:
        # Existing `except APIError` handlers must cover the headless
        # backend without new except clauses.
        assert issubclass(exception_type, APIError)


# ---------------------------------------------------------------------------
# TestHeadlessCurate
# ---------------------------------------------------------------------------


class TestHeadlessCurate:
    def test_happy_path_provenance_and_mapping(
        self,
        mocker: Any,
        headless_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        fake = _FakeClaudeRun(_make_envelope(_valid_wire_dict()))
        mocker.patch("curator.headless.subprocess.run", side_effect=fake)
        # No API key anywhere: the headless path never reads one.
        assert headless_settings.anthropic_api_key is None

        client = HeadlessCuratorClient(headless_settings)
        result = client.curate(portfolio_data, "Senior role at Acme.")

        assert isinstance(result, CurationResult)
        assert result.backend == "claude-code"
        assert result.source == "api"
        assert result.model == _SERVED_MODEL
        assert result.input_tokens == 1200
        assert result.output_tokens == 640
        assert result.cache_creation_input_tokens == 900
        assert result.cache_read_input_tokens == 300
        assert result.cache_ttl is None
        assert result.cover_letter is None
        assert result.curation.company_slug == "acme-corp"

    def test_downstream_validation_failure_persists_raw(
        self,
        mocker: Any,
        headless_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        wire = _valid_wire_dict()
        # Unknown work_id is a hard ID mismatch in _validate_curation_ids.
        wire["work_highlights_by_id"] = {"ghost-corp-role": ["ghost-highlight"]}
        fake = _FakeClaudeRun(_make_envelope(wire))
        mocker.patch("curator.headless.subprocess.run", side_effect=fake)

        client = HeadlessCuratorClient(headless_settings)
        with pytest.raises(APIResponseError, match="persisted"):
            client.curate(portfolio_data, "Senior role at Acme.")

        raw_files = list(headless_settings.output_dir.glob("curation_raw-*.json"))
        assert len(raw_files) == 1
        persisted = json.loads(raw_files[0].read_text(encoding="utf-8"))
        assert persisted == wire

    def test_raw_persist_failure_degrades_to_hint(
        self,
        mocker: Any,
        headless_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        """A failed raw-response persist must not mask the original error."""
        wire = _valid_wire_dict()
        wire["work_highlights_by_id"] = {"ghost-corp-role": ["ghost-highlight"]}
        fake = _FakeClaudeRun(_make_envelope(wire))
        mocker.patch("curator.headless.subprocess.run", side_effect=fake)
        mocker.patch(
            "curator.headless._persist_raw_response",
            side_effect=OSError("disk full"),
        )
        mock_error = mocker.patch("curator.headless.logger.error")

        client = HeadlessCuratorClient(headless_settings)
        with pytest.raises(APIResponseError, match=r"not persisted \(see logs\)"):
            client.curate(portfolio_data, "Senior role at Acme.")

        logged = " ".join(str(c.args[0]) for c in mock_error.call_args_list)
        assert "Failed to persist raw headless response" in logged

    def test_cover_letter_happy_path(
        self,
        mocker: Any,
        headless_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        letter = valid_cover_letter()
        structured = {
            "resume": _valid_wire_dict(),
            "cover_letter": letter.model_dump(mode="json"),
        }
        fake = _FakeClaudeRun(_make_envelope(structured))
        mocker.patch("curator.headless.subprocess.run", side_effect=fake)

        client = HeadlessCuratorClient(headless_settings)
        result = client.curate(
            portfolio_data, "Senior role at Acme.", with_cover_letter=True
        )

        assert isinstance(result.cover_letter, CoverLetterCuration)
        assert result.cover_letter.sign_off == letter.sign_off
        assert result.backend == "claude-code"

    def test_cover_letter_validator_failure_persists_partial(
        self,
        mocker: Any,
        headless_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        from tests.helpers import body_paragraph_embedding, valid_cover_letter_kwargs

        kwargs = valid_cover_letter_kwargs()
        # A bracketed [UPPERCASE] placeholder is a strict policy reject.
        kwargs["body_paragraphs"] = [
            body_paragraph_embedding("[COMPANY]"),
            kwargs["body_paragraphs"][1],
        ]
        structured = {
            "resume": _valid_wire_dict(),
            "cover_letter": CoverLetterCuration(**kwargs).model_dump(mode="json"),
        }
        fake = _FakeClaudeRun(_make_envelope(structured))
        mocker.patch("curator.headless.subprocess.run", side_effect=fake)

        client = HeadlessCuratorClient(headless_settings)
        with pytest.raises(APIResponseError, match="Cover letter validation failed"):
            client.curate(
                portfolio_data, "Senior role at Acme.", with_cover_letter=True
            )

        partial_files = list(
            headless_settings.output_dir.glob("curation_partial-*.yaml")
        )
        assert len(partial_files) == 1

    def test_partial_persist_failure_degrades_to_hint(
        self,
        mocker: Any,
        headless_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        """A failed partial-resume persist must not mask the policy error."""
        from tests.helpers import body_paragraph_embedding, valid_cover_letter_kwargs

        kwargs = valid_cover_letter_kwargs()
        kwargs["body_paragraphs"] = [
            body_paragraph_embedding("[COMPANY]"),
            kwargs["body_paragraphs"][1],
        ]
        structured = {
            "resume": _valid_wire_dict(),
            "cover_letter": CoverLetterCuration(**kwargs).model_dump(mode="json"),
        }
        fake = _FakeClaudeRun(_make_envelope(structured))
        mocker.patch("curator.headless.subprocess.run", side_effect=fake)
        mocker.patch(
            "curator.headless._persist_partial_resume",
            side_effect=OSError("disk full"),
        )
        mock_error = mocker.patch("curator.headless.logger.error")

        client = HeadlessCuratorClient(headless_settings)
        with pytest.raises(APIResponseError, match=r"not persisted \(see logs\)"):
            client.curate(
                portfolio_data, "Senior role at Acme.", with_cover_letter=True
            )

        logged = " ".join(str(c.args[0]) for c in mock_error.call_args_list)
        assert "Failed to persist partial resume" in logged

    def test_spend_guard_uses_subscription_wording(self, tmp_path: Path) -> None:
        settings = CuratorSettings(
            anthropic_api_key=None,
            backend="claude-code",
            allow_api_spend=False,
            portfolio_path=tmp_path,
        )
        with pytest.raises(APISpendGuardError, match="subscription"):
            HeadlessCuratorClient(settings)

    def test_context_manager_protocol(self, headless_settings: CuratorSettings) -> None:
        with HeadlessCuratorClient(headless_settings) as client:
            assert isinstance(client, HeadlessCuratorClient)
        # close() is a no-op and safe to call repeatedly.
        client.close()
        client.close()

    def test_curate_argv_uses_settings_model_and_timeout(
        self,
        mocker: Any,
        headless_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        fake = _FakeClaudeRun(_make_envelope(_valid_wire_dict()))
        mocker.patch("curator.headless.subprocess.run", side_effect=fake)

        client = HeadlessCuratorClient(headless_settings)
        client.curate(portfolio_data, "Senior role at Acme.")

        cmd, kwargs = fake.calls[0]
        assert cmd[cmd.index("--model") + 1] == headless_settings.model
        assert kwargs["timeout"] == headless_settings.headless_timeout


# ---------------------------------------------------------------------------
# TestHeadlessSingleCallInvariant
# ---------------------------------------------------------------------------


class TestHeadlessSingleCallInvariant:
    """Lock the 'no double spending' rule at the unit test layer.

    Subscription usage is a billable quota, so the headless path holds
    the same invariant as ``TestCurateSingleCallInvariant`` on the API
    path: exactly one subprocess per ``curate()``.

    If a retry-with-feedback feature ever lands here, this class must be
    parametrized over a retry-budget flag and the assertion adjusted to
    ``<= 1 + retry_budget``, NOT deleted. Removing this invariant would
    silently multiply subscription usage on every validation regression.
    """

    @pytest.mark.parametrize("with_cover_letter", [False, True])
    def test_exactly_one_subprocess_per_curate(
        self,
        mocker: Any,
        headless_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        with_cover_letter: bool,
    ) -> None:
        if with_cover_letter:
            structured: dict[str, Any] = {
                "resume": _valid_wire_dict(),
                "cover_letter": valid_cover_letter().model_dump(mode="json"),
            }
        else:
            structured = _valid_wire_dict()
        fake = _FakeClaudeRun(_make_envelope(structured))
        run_mock = mocker.patch("curator.headless.subprocess.run", side_effect=fake)

        client = HeadlessCuratorClient(headless_settings)
        client.curate(
            portfolio_data,
            "Senior role at Acme.",
            with_cover_letter=with_cover_letter,
        )

        assert run_mock.call_count == 1
        assert len(fake.calls) == 1
