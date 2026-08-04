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
    APIRefusalError,
    APIResponseError,
    APISpendGuardError,
    HeadlessCLIError,
    HeadlessUsageLimitError,
)
from curator.headless import (
    HEADLESS_DISALLOWED_TOOLS,
    HEADLESS_STRIPPED_ENV_VARS,
    HeadlessCuratorClient,
    HeadlessResult,
    flatten_system_blocks,
    run_structured_prompt,
)
from curator.models import CoverLetterCuration, PortfolioData, ResumeCuration
from curator.output_schema import build_curation_schema
from curator.prompt import build_system_prompt, build_user_message
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

    def __init__(
        self,
        envelope: dict[str, Any] | str,
        *,
        returncode: int = 0,
        stderr: str = "",
    ) -> None:
        self.envelope = envelope
        self.returncode = returncode
        self.stderr = stderr
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
            {"returncode": self.returncode, "stdout": stdout, "stderr": self.stderr},
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
        # Literal membership, not a tautology against the constant:
        # comparing argv to HEADLESS_DISALLOWED_TOOLS alone still passes
        # when a tool is dropped from the constant. The deny list is
        # name-based, so a newly added CLI tool is NOT denied by default;
        # re-check this list on every Claude Code upgrade.
        assert {
            "Bash",
            "Write",
            "Edit",
            "Read",
            "WebFetch",
            "WebSearch",
            "Task",
        } <= set(HEADLESS_DISALLOWED_TOOLS)

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

    @pytest.mark.parametrize(
        "var",
        [
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_CUSTOM_HEADERS",
            "ANTHROPIC_MODEL",
            "ANTHROPIC_SMALL_FAST_MODEL",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
        ],
    )
    def test_credential_and_redirect_vars_stripped(
        self, mocker: Any, monkeypatch: pytest.MonkeyPatch, var: str
    ) -> None:
        """Literal membership, not a tautology against the constant.

        A leaked key or auth token silently bills the API instead of the
        subscription; a redirected base URL (or a Bedrock/Vertex switch)
        would ship the serialized portfolio to a third-party endpoint.
        """
        assert var in HEADLESS_STRIPPED_ENV_VARS
        monkeypatch.setenv(var, "should-vanish")
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        _run_with_fake(mocker, fake)
        _, kwargs = fake.calls[0]
        assert var not in kwargs["env"]

    def test_strip_debug_log_names_never_values(
        self, mocker: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
        mock_debug = mocker.patch("curator.headless.logger.debug")
        fake = _FakeClaudeRun(_make_envelope({"summary": "ok"}))
        _run_with_fake(mocker, fake)

        logged = " ".join(
            str(arg) for call in mock_debug.call_args_list for arg in call.args
        )
        assert "ANTHROPIC_API_KEY" in logged
        assert "ANTHROPIC_BASE_URL" in logged
        assert "sk-ant-secret-value" not in logged
        assert "evil.example" not in logged

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
        "value",
        ["1200", None, True, 12.5, {"nested": 1}],
        ids=["string", "null", "bool", "float", "dict"],
    )
    def test_non_int_usage_values_default_to_zero(
        self, mocker: Any, value: Any
    ) -> None:
        """A wrong-typed count must not reach CurationResult or the audit log.

        The usage dict itself being wrong-typed was already covered; this
        pins the per-VALUE case, where a stringified or null count would
        otherwise flow straight through into token arithmetic downstream.
        """
        fake = _FakeClaudeRun(
            _make_envelope({"summary": "ok"}, usage={"input_tokens": value})
        )
        result = _run_with_fake(mocker, fake)
        assert result.input_tokens == 0
        assert isinstance(result.input_tokens, int)
        assert not isinstance(result.input_tokens, bool)

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

    @pytest.mark.parametrize(
        "result_text",
        [
            "You've hit your session limit · resets 3:45pm",
            "You've hit your weekly limit",
            "You're out of usage credits",
            "usage limit reached",
            "Credit balance is too low",
            "Credit balance too low",
        ],
        ids=[
            "session-limit",
            "weekly-limit",
            "out-of-credits",
            "limit-reached",
            "balance-too-low",
            "balance-too-low-terse",
        ],
    )
    def test_usage_limit_phrasing_family(self, mocker: Any, result_text: str) -> None:
        """Every phrasing of "stop, you are out of quota" maps to one error.

        The CLI wording varies by limit type (session, weekly, credits,
        empty balance); all of them are clock- or billing-bound, so all
        must raise HeadlessUsageLimitError rather than the generic
        response error a user cannot act on.
        """
        fake = _FakeClaudeRun(
            _make_envelope(None, is_error=True, result_text=result_text)
        )
        with pytest.raises(HeadlessUsageLimitError):
            _run_with_fake(mocker, fake)

    @pytest.mark.parametrize(
        "result_text",
        [
            "Not logged in. Please log in.",
            "Invalid API key",
            "401 Unauthorized",
            "403 Forbidden",
            "OAuth token expired",
            "OAuth token has expired",
            "bad credentials",
            "authentication failed",
        ],
        ids=[
            "not-logged-in",
            "invalid-api-key",
            "401",
            "403",
            "oauth-expired",
            "oauth-has-expired",
            "bad-credentials",
            "auth-failed",
        ],
    )
    def test_auth_phrasing_family(self, mocker: Any, result_text: str) -> None:
        """Auth failures share one operator fix, so they share one error."""
        fake = _FakeClaudeRun(
            _make_envelope(None, is_error=True, result_text=result_text)
        )
        with pytest.raises(APIAuthError, match="claude /login"):
            _run_with_fake(mocker, fake)

    def test_usage_limit_wins_over_login_wording(self, mocker: Any) -> None:
        """Documented check order: usage-limit text is tested first.

        A limit message can also mention logging in; classifying it as an
        auth error would send the operator to re-run 'claude /login',
        which cannot fix a clock-bound limit.
        """
        fake = _FakeClaudeRun(
            _make_envelope(
                None,
                is_error=True,
                result_text=(
                    "You've hit your session limit · resets 3:45pm. "
                    "Please log in again later."
                ),
            )
        )
        with pytest.raises(HeadlessUsageLimitError):
            _run_with_fake(mocker, fake)

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

    def test_missing_structured_output_still_classifies_usage_limit(
        self, mocker: Any
    ) -> None:
        """is_error may be false while the result text says "usage limit".

        The missing-structured_output branch must not swallow that into
        the generic deny-list message.
        """
        fake = _FakeClaudeRun(
            _make_envelope(
                None,
                is_error=False,
                result_text="You've hit your session limit · resets 3:45pm",
            )
        )
        with pytest.raises(HeadlessUsageLimitError) as exc_info:
            _run_with_fake(mocker, fake)
        assert exc_info.value.reset_text == "3:45pm"

    def test_missing_structured_output_still_classifies_auth(self, mocker: Any) -> None:
        fake = _FakeClaudeRun(
            _make_envelope(
                None, is_error=False, result_text="Not logged in. Please log in."
            )
        )
        with pytest.raises(APIAuthError, match="claude /login"):
            _run_with_fake(mocker, fake)

    @pytest.mark.parametrize(
        "result_text",
        [
            "I can't help with that request.",
            "I cannot assist with this.",
            "I'm unable to comply with these instructions.",
            "I must decline to produce that content.",
        ],
        ids=["cant", "cannot", "unable", "decline"],
    )
    def test_missing_structured_output_refusal_raises_refusal_error(
        self, mocker: Any, result_text: str
    ) -> None:
        """A model refusal is a refusal, not a denied-tool misdiagnosis.

        The CLI envelope has no ``stop_reason``, so the repo's
        "always check for refusal" rule is enforced on the result text.
        """
        fake = _FakeClaudeRun(_make_envelope(None, result_text=result_text))
        with pytest.raises(APIRefusalError, match="refusal"):
            _run_with_fake(mocker, fake)

    def test_missing_structured_output_falls_back_to_deny_list_message(
        self, mocker: Any
    ) -> None:
        fake = _FakeClaudeRun(_make_envelope(None, result_text="Done."))
        with pytest.raises(APIResponseError, match="disallowed-tools"):
            _run_with_fake(mocker, fake)

    def test_error_envelope_with_missing_structured_output_unchanged(
        self, mocker: Any
    ) -> None:
        """The is_error=True path still wins and still classifies."""
        fake = _FakeClaudeRun(
            _make_envelope(
                None, is_error=True, result_text="You've hit your session limit"
            )
        )
        with pytest.raises(HeadlessUsageLimitError):
            _run_with_fake(mocker, fake)

    @pytest.mark.parametrize(
        "exc",
        [
            PermissionError(13, "Permission denied"),
            OSError(8, "Exec format error"),
        ],
        ids=["permission", "exec-format"],
    )
    def test_oserror_family_maps_to_headless_cli_error(
        self, mocker: Any, exc: OSError
    ) -> None:
        """A non-executable or wrong-arch binary must stay in the taxonomy.

        PermissionError/OSError are not FileNotFoundError, so without the
        widened handler they escape ``CuratorError`` and cli.py's handler
        misses them.
        """
        mocker.patch("curator.headless.subprocess.run", side_effect=exc)
        with pytest.raises(HeadlessCLIError, match="Could not launch 'claude'"):
            run_structured_prompt(
                system_text="s",
                user_text="u",
                schema=_SCHEMA,
                model="claude-opus-5",
                effort=None,
                timeout=600,
            )

    @pytest.mark.parametrize(
        "stdout",
        ["", "not json {", '["a", "json", "array"]'],
        ids=["empty", "malformed", "non-object"],
    )
    def test_unparseable_stdout(self, mocker: Any, stdout: str) -> None:
        fake = _FakeClaudeRun(stdout)
        with pytest.raises(HeadlessCLIError):
            _run_with_fake(mocker, fake)

    def test_unparseable_stdout_truncates_stderr(self, mocker: Any) -> None:
        """Stderr is untrusted, unbounded CLI output: it must be clipped.

        A crashing CLI can emit megabytes of stack trace; the error
        message keeps the first 500 stripped characters only.
        """
        noise = "E" * 900
        fake = _FakeClaudeRun("not json {", returncode=2, stderr=f"  {noise}  ")
        with pytest.raises(HeadlessCLIError) as exc_info:
            _run_with_fake(mocker, fake)
        message = str(exc_info.value)
        assert "exit 2" in message
        assert "E" * 500 in message
        assert "E" * 501 not in message

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

    def test_session_id_flows_into_error_and_recovery_filename(
        self,
        mocker: Any,
        headless_settings: CuratorSettings,
        portfolio_data: PortfolioData,
    ) -> None:
        """The correlation id must be the real session id, not the fallback.

        Without this, ``result.session_id or "headless"`` could always
        take the fallback branch and every recovery file for every run
        would collide on the same name.
        """
        wire = _valid_wire_dict()
        wire["work_highlights_by_id"] = {"ghost-corp-role": ["ghost-highlight"]}
        fake = _FakeClaudeRun(_make_envelope(wire, session_id="sess-corr-9f2"))
        mocker.patch("curator.headless.subprocess.run", side_effect=fake)

        client = HeadlessCuratorClient(headless_settings)
        with pytest.raises(APIResponseError) as exc_info:
            client.curate(portfolio_data, "Senior role at Acme.")

        assert "sess-corr-9f2" in str(exc_info.value)
        raw_files = list(headless_settings.output_dir.glob("curation_raw-*.json"))
        assert len(raw_files) == 1
        assert "sess-corr-9f2" in raw_files[0].name
        assert "headless" not in raw_files[0].name

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
        tmp_path: Path,
        portfolio_data: PortfolioData,
    ) -> None:
        """Non-default model and timeout, so hardcoding cannot pass.

        The shared fixture uses model == HEADLESS_DEFAULT_MODEL and
        headless_timeout == the module default, which makes an
        implementation that ignores settings indistinguishable.
        """
        settings = CuratorSettings(
            anthropic_api_key=None,
            backend="claude-code",
            model="claude-sonnet-4-6",
            headless_timeout=123,
            allow_api_spend=True,
            portfolio_path=tmp_path,
            output_dir=tmp_path / "profiles",
        )
        fake = _FakeClaudeRun(_make_envelope(_valid_wire_dict()))
        mocker.patch("curator.headless.subprocess.run", side_effect=fake)

        client = HeadlessCuratorClient(settings)
        client.curate(portfolio_data, "Senior role at Acme.")

        cmd, kwargs = fake.calls[0]
        assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-6"
        assert kwargs["timeout"] == 123

    @pytest.mark.parametrize("effort", ["high", None], ids=["set", "none"])
    def test_curate_forwards_settings_effort(
        self,
        mocker: Any,
        tmp_path: Path,
        portfolio_data: PortfolioData,
        effort: str | None,
    ) -> None:
        """settings.effort must reach argv (a hardcoded None survives otherwise)."""
        settings = CuratorSettings(
            anthropic_api_key=None,
            backend="claude-code",
            model="claude-opus-5",
            effort=effort,
            allow_api_spend=True,
            portfolio_path=tmp_path,
            output_dir=tmp_path / "profiles",
        )
        fake = _FakeClaudeRun(_make_envelope(_valid_wire_dict()))
        mocker.patch("curator.headless.subprocess.run", side_effect=fake)

        HeadlessCuratorClient(settings).curate(portfolio_data, "Senior role at Acme.")

        cmd, _ = fake.calls[0]
        if effort is None:
            assert "--effort" not in cmd
        else:
            assert cmd[cmd.index("--effort") + 1] == effort

    @pytest.mark.parametrize("with_cover_letter", [False, True])
    def test_curate_input_wiring(
        self,
        mocker: Any,
        headless_settings: CuratorSettings,
        portfolio_data: PortfolioData,
        with_cover_letter: bool,
    ) -> None:
        """The curate prompts and schema actually reach the subprocess.

        The happy-path test pins the output side only (the fake returns a
        canned envelope regardless of inputs), so this pins the input
        side: the system-prompt file content, the stdin user message, the
        exact ``--json-schema`` payload (including ``with_cover_letter``
        and ``max_pages``), and the trust boundary - the JD must never
        appear in the system prompt.
        """
        structured: dict[str, Any] = (
            {
                "resume": _valid_wire_dict(),
                "cover_letter": valid_cover_letter().model_dump(mode="json"),
            }
            if with_cover_letter
            else _valid_wire_dict()
        )
        fake = _FakeClaudeRun(_make_envelope(structured))
        mocker.patch("curator.headless.subprocess.run", side_effect=fake)

        jd_text = "Senior platform role at Acme. Kubernetes required."
        client = HeadlessCuratorClient(headless_settings)
        client.curate(portfolio_data, jd_text, with_cover_letter=with_cover_letter)

        cmd, kwargs = fake.calls[0]

        expected_system = flatten_system_blocks(
            build_system_prompt(
                portfolio_data,
                with_cover_letter=with_cover_letter,
                cache_ttl=headless_settings.cache_ttl,
            )
        )
        assert fake.system_prompt_contents[0] == expected_system

        expected_user = build_user_message(
            jd_text, with_cover_letter=with_cover_letter
        )[0]["content"]
        assert kwargs["input"] == expected_user

        wire_schema = json.loads(cmd[cmd.index("--json-schema") + 1])
        assert wire_schema == build_curation_schema(
            portfolio_data,
            with_cover_letter=with_cover_letter,
            max_pages=headless_settings.max_pages,
        )

        # Trust boundary: the JD is user data and rides stdin only. A
        # swapped system/user text would otherwise go unnoticed.
        assert jd_text not in fake.system_prompt_contents[0]


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
