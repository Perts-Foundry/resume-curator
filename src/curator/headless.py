"""Headless Claude Code backend for resume curation.

Shells out to ``claude -p`` (headless Claude Code) so curation rides the
operator's Claude subscription login instead of the Anthropic API. The
subprocess returns a JSON envelope whose ``structured_output`` field carries
the same wire-format curation JSON the API path produces, so everything
downstream of the transport (adapter, ID validation, cover-letter policy,
recovery persistence) is reused from :mod:`curator.client` unchanged.

Transport-only module: prompt construction stays in :mod:`curator.prompt`,
schema construction in :mod:`curator.output_schema`, and validation in
:mod:`curator.client` / :mod:`curator.models`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, cast

from loguru import logger

from curator.client import (
    CurationResult,
    _adapt_curation_dict,
    _persist_partial_resume,
    _persist_raw_response,
    _validate_curation_ids,
)
from curator.config import spend_guard_message
from curator.exceptions import (
    APIAuthError,
    APIError,
    APIRefusalError,
    APIResponseError,
    APISpendGuardError,
    CurationValidationError,
    HeadlessCLIError,
    HeadlessUsageLimitError,
)
from curator.models import validate_cover_letter
from curator.output_schema import build_curation_schema
from curator.prompt import build_system_prompt, build_user_message

if TYPE_CHECKING:
    from collections.abc import Sequence

    from anthropic.types import TextBlockParam

    from curator.config import CuratorSettings
    from curator.models import CoverLetterCuration, PortfolioData


# Explicit deny list for the headless subprocess. Never use "*": a wildcard
# also denies the CLI-internal StructuredOutput tool, and the envelope then
# reports ``subtype: success`` with no ``structured_output`` (verified on
# CLI 2.1.220). The curation call needs zero agentic tools; everything the
# CLI could reach is denied by name.
#
# This is a name-based list and therefore fails OPEN for any tool the CLI
# adds after it was written; ``--safe-mode`` (below) is the structural
# backstop that disables skills, plugins, MCP, custom agents/commands, and
# hooks regardless of this list, and the ``--json-schema`` StructuredOutput
# call never invokes a tool in practice. Re-verify the built-in names on
# each CLI upgrade. Last verified against CLI 2.1.226 (2026-08-08): the
# agentic surface added ``Agent``, ``Skill``, ``Workflow``, and
# ``ToolSearch`` since the original 2.1.220 list, all of which can read
# files or run code, so they are denied here too.
HEADLESS_DISALLOWED_TOOLS: tuple[str, ...] = (
    "Bash",
    "Edit",
    "Write",
    "Read",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Task",
    "Agent",
    "Skill",
    "Workflow",
    "ToolSearch",
    "NotebookEdit",
    "TodoWrite",
    "KillShell",
    "BashOutput",
    "ExitPlanMode",
)

# Environment variables removed from the subprocess environment. Each one
# either outranks the subscription login or redirects where the call goes:
# a leaked key or auth token silently bills the Anthropic API instead of
# the subscription this backend exists to use, and a redirected base URL
# (or a Bedrock/Vertex switch) would ship the serialized portfolio to a
# third-party endpoint. The model overrides are stripped because the model
# is an explicit argv flag; an inherited env value must not shadow it.
HEADLESS_STRIPPED_ENV_VARS: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
    }
)

# Default subprocess timeout in seconds. The CLI has no --max-turns bound
# (removed by 2.1.220), so the subprocess timeout is the runaway guard; an
# observed successful curate run takes ~75s. Runtime value comes from
# ``settings.headless_timeout``; this constant is the shared default.
_HEADLESS_TIMEOUT: int = 600

# Usage-limit envelopes carry result text like
# "You've hit your session limit · resets 3:45pm", but the CLI has
# several phrasings for the same condition (session/weekly limits,
# exhausted credits, an empty credit balance). All of them are
# clock-bound or billing-bound, so all map to HeadlessUsageLimitError.
_USAGE_LIMIT_PATTERN = re.compile(
    r"hit your .*limit"
    r"|usage limit"
    r"|out of usage credits"
    r"|credit balance (?:is )?too low",
    re.IGNORECASE,
)
_USAGE_RESET_PATTERN = re.compile(r"resets\s+(.+)$")
# Auth failures likewise arrive in several phrasings: a login prompt, an
# HTTP status line, or an expired/rejected token. All are fixed by the
# same operator action, so all map to APIAuthError.
_LOGIN_PATTERN = re.compile(
    r"log in|login|authenticate|authentication failed"
    r"|API key|setup-token|401 unauthorized|403 forbidden"
    r"|oauth token|token (?:has )?expired|bad credentials",
    re.IGNORECASE,
)
# The CLI envelope has no ``stop_reason`` field, so a model refusal
# arrives as prose in ``result`` with no ``structured_output``. These cues
# stand in for the API path's ``stop_reason == "refusal"`` check.
_REFUSAL_PATTERN = re.compile(
    r"I (?:can(?:no|')t|cannot|won't|will not|am unable to|'m unable to)|"
    r"I (?:must|have to) decline|unable to (?:help|assist|comply)",
    re.IGNORECASE,
)

# Client-side ``@``-mention expansion is a headless-only exfiltration
# surface with no API-path analog. The CLI expands an ``@`` that sits at a
# whitespace/start boundary and is followed by a path (``@/etc/passwd``,
# ``@~/.ssh/id_rsa``, ``@../file``) into that file's contents, injecting
# them into the prompt BEFORE any tool runs -- so the deny list and even
# ``--safe-mode`` do not stop it (confirmed on CLI 2.1.226: a random canary
# in ``@/abs/path`` inside ``<job_description>`` reached structured output
# with every tool denied). The JD arrives on stdin as untrusted data, so
# every boundary ``@`` mention is neutralized to the CLI's own literal-@
# escape (``\@``) before the subprocess sees it. The boundary anchor means
# emails (``jobs@acme.com``, a non-space before ``@``) and mid-word ``@``
# are left untouched; only mention-shaped tokens are escaped, and escaping
# is inert to curation (an ``@``-prefixed path in a JD is never a job
# requirement). An already-escaped ``\@`` is not re-escaped (the preceding
# backslash is non-whitespace).
_AT_MENTION_BOUNDARY_RE = re.compile(r"(?<!\S)@(?=\S)")


def neutralize_at_mentions(text: str) -> str:
    r"""Escape ``@``-mention tokens so the CLI cannot expand them to files.

    Replaces every ``@`` at a whitespace/start boundary that is followed by
    a non-whitespace character with ``\@`` (the CLI's literal-@ escape).
    Text with no such token is returned byte-identical, so the common JD
    (no ``@`` mentions) is unchanged and prompt-cache behavior is unaffected.

    Args:
        text: Untrusted user-message text (the job description, or the
            judge's user message which embeds it).

    Returns:
        The text with boundary ``@`` mentions escaped.
    """
    return _AT_MENTION_BOUNDARY_RE.sub(r"\\@", text)


def flatten_system_blocks(blocks: Sequence[TextBlockParam]) -> str:
    """Join system prompt blocks into a single plain-text prompt.

    The headless CLI takes one system prompt string (via
    ``--system-prompt-file``), not the API's list of content blocks, so the
    blocks built by :mod:`curator.prompt` are joined with a blank line and
    any ``cache_control`` markers are dropped (subscription auth caches
    automatically at a 1h TTL; there is no headless cache-ttl analog).

    Args:
        blocks: System content blocks from ``build_system_prompt`` (or the
            judge's ``_build_system_blocks``).

    Returns:
        The block texts joined with a blank line.
    """
    return "\n\n".join(block["text"] for block in blocks)


@dataclass(frozen=True)
class HeadlessResult:
    """Parsed success envelope from a ``claude -p`` structured-output call.

    Token fields mirror the API path's usage shape so ``cache_outcome``
    derivation keeps working. ``total_cost_usd`` is notional on
    subscription auth (no marginal charge). ``session_id`` is log-only,
    like the API path's ``request_id``; it must never reach anything
    git-facing.
    """

    structured_output: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    total_cost_usd: float | None
    session_id: str | None


def _usage_int(usage: dict[str, Any], key: str) -> int:
    """Read one token count from the envelope's usage dict.

    Wrong-typed values (a stringified count, null, a bool) are dropped to
    0 rather than flowing into ``CurationResult`` and the audit log, where
    a non-int would break arithmetic downstream. bool is excluded because
    it subclasses int.
    """
    value = usage.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _cli_version() -> str:
    """Return the ``claude --version`` string, or ``"unknown"`` on any error.

    Used only to enrich envelope-contract failure messages so CLI drift
    surfaces as an actionable "which version produced this" hint rather than
    a bare parse error. The envelope shape (``is_error``, ``subtype``,
    ``structured_output``, ``usage`` casing) is verified against one CLI
    version; when the shape is unexpected, naming the running version points
    the operator at a likely cause. Never raises: a failure here must not
    mask the original envelope failure it is annotating.
    """
    # Variable (not a literal list) so ruff's S607 partial-path check does
    # not fire, matching the main invocation below; the executable name is
    # fixed and there is no shell.
    version_cmd = ["claude", "--version"]
    try:
        completed = subprocess.run(  # noqa: S603
            version_cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    version = completed.stdout.strip() or completed.stderr.strip()
    return version or "unknown"


def _match_known_error(result_text: str) -> APIError | None:
    """Classify envelope result text, or return None when nothing matches.

    Check order: usage-limit text first (it can mention "limit" without any
    login phrasing), then login-ish text. Returned rather than raised so
    callers own the raise site and the fallback.
    """
    if _USAGE_LIMIT_PATTERN.search(result_text):
        reset_match = _USAGE_RESET_PATTERN.search(result_text)
        reset_text = reset_match.group(1).strip() if reset_match else None
        msg = (
            f"Claude subscription usage limit reached: {result_text}. "
            "The limit resets on a clock; retrying now will not help."
        )
        return HeadlessUsageLimitError(msg, reset_text=reset_text)
    if _LOGIN_PATTERN.search(result_text):
        msg = (
            f"Headless Claude Code is not logged in: {result_text}. "
            "Run 'claude /login' (or 'claude setup-token' for "
            "non-interactive use), or switch to --backend api."
        )
        return APIAuthError(msg)
    return None


def _translate_error_result(result_text: str) -> APIError:
    """Map an ``is_error`` envelope's result text to the exception taxonomy.

    Falls back to a generic response error when no known failure mode
    matches. Returned rather than raised so ``_parse_envelope`` owns the
    raise site.
    """
    known = _match_known_error(result_text)
    if known is not None:
        return known
    return APIResponseError(f"claude -p reported an error: {result_text}")


def _parse_envelope(
    completed: subprocess.CompletedProcess[str],
    *,
    requested_model: str,
) -> HeadlessResult:
    """Validate and map the ``claude -p --output-format json`` envelope.

    Envelope health alone is NOT success: a run whose internal
    StructuredOutput tool was denied still reports ``subtype: success``
    with no ``structured_output``, so that field's presence is checked
    explicitly. A non-zero exit code with a parseable error envelope is
    reported from the envelope (richer text), not the exit code.
    """
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        stderr = completed.stderr.strip()[:500]
        msg = (
            f"claude -p produced unparseable output "
            f"(exit {completed.returncode}, CLI {_cli_version()}): {stderr}"
        )
        raise HeadlessCLIError(msg) from exc
    if not isinstance(envelope, dict):
        msg = (
            f"claude -p envelope is not a JSON object "
            f"(exit {completed.returncode}, CLI {_cli_version()}, "
            f"got {type(envelope).__name__})"
        )
        raise HeadlessCLIError(msg)

    if envelope.get("is_error") is True:
        raise _translate_error_result(str(envelope.get("result", "")))

    subtype = envelope.get("subtype")
    if subtype != "success":
        msg = f"claude -p returned subtype={subtype!r}: {envelope.get('result')}"
        raise APIResponseError(msg)

    structured_output = envelope.get("structured_output")
    if not isinstance(structured_output, dict):
        # A denied StructuredOutput tool is only one of the ways this
        # branch is reached. A usage-limit or auth failure has been seen
        # arriving with ``is_error`` true AND with it false, and a model
        # refusal arrives as prose with a healthy envelope, so classify
        # those first and keep the deny-list message as the fallback.
        result_text = str(envelope.get("result", ""))
        known = _match_known_error(result_text)
        if known is not None:
            raise known
        if _REFUSAL_PATTERN.search(result_text):
            msg = (
                "claude -p returned a refusal instead of structured output: "
                f"{result_text}"
            )
            raise APIRefusalError(msg)
        msg = (
            "claude -p succeeded but returned no structured_output. "
            "This usually means the CLI-internal StructuredOutput tool was "
            "denied; the disallowed-tools list must never contain '*'."
        )
        raise APIResponseError(msg)

    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    token_counts = {
        key: _usage_int(usage, key)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    }
    model_usage = envelope.get("modelUsage")
    if isinstance(model_usage, dict) and len(model_usage) == 1:
        model = next(iter(model_usage))
    else:
        model = requested_model
    total_cost_usd = envelope.get("total_cost_usd")
    session_id = envelope.get("session_id")

    # total_cost_usd is notional on subscription auth (the real budget is
    # the subscription's usage window). session_id is log-only, never
    # git-facing, mirroring the request_id convention on the API path.
    logger.info(
        "Headless response: model={}, tokens(in={}, out={}, cache_create={}, "
        "cache_read={}), notional_cost_usd={}, session_id={}",
        model,
        token_counts["input_tokens"],
        token_counts["output_tokens"],
        token_counts["cache_creation_input_tokens"],
        token_counts["cache_read_input_tokens"],
        total_cost_usd,
        session_id,
    )

    return HeadlessResult(
        structured_output=structured_output,
        model=model,
        input_tokens=token_counts["input_tokens"],
        output_tokens=token_counts["output_tokens"],
        cache_creation_input_tokens=token_counts["cache_creation_input_tokens"],
        cache_read_input_tokens=token_counts["cache_read_input_tokens"],
        # json.loads parses a whole-number cost (e.g. ``2``) as int, so
        # accept both numeric types; bool is excluded (it subclasses int).
        total_cost_usd=(
            float(total_cost_usd)
            if isinstance(total_cost_usd, int | float)
            and not isinstance(total_cost_usd, bool)
            else None
        ),
        session_id=session_id if isinstance(session_id, str) else None,
    )


def run_structured_prompt(
    *,
    system_text: str,
    user_text: str,
    schema: dict[str, Any],
    model: str,
    effort: str | None,
    timeout: int = _HEADLESS_TIMEOUT,
) -> HeadlessResult:
    """Run one ``claude -p`` structured-output call and parse its envelope.

    Single subprocess + envelope engine shared by the curate and judge
    backends. The system prompt (~80KB) travels via a temp file and
    ``--system-prompt-file`` (avoids ARG_MAX and ``/proc/*/cmdline``
    leakage); the user prompt arrives on stdin; the schema rides argv as
    compact JSON. The temp dir is also the subprocess cwd so no repo
    CLAUDE.md or project context bleeds into the call.

    ``HEADLESS_STRIPPED_ENV_VARS`` is removed from the subprocess
    environment: those variables outrank the subscription login or
    redirect the call to a third-party endpoint, and the whole point of
    this backend is to bill the subscription. ``--safe-mode`` disables the
    child's own customizations (settings, hooks, global CLAUDE.md, skills,
    plugins, custom agents/commands), and the untrusted ``user_text`` is
    passed through ``neutralize_at_mentions`` first so a JD-embedded
    ``@``-mention cannot client-side-expand a file into the prompt. No
    ``--bare`` (API-key-only, never reads OAuth) and no ``--max-turns``
    (does not exist in CLI 2.1.220); the subprocess timeout is the runaway
    bound.

    Args:
        system_text: Flattened system prompt (see ``flatten_system_blocks``).
        user_text: User message content, delivered on stdin.
        schema: JSON schema for ``--json-schema`` structured output.
        model: Model identifier, passed verbatim (aliases drift on the
            CLI: ``sonnet`` has resolved to a newer major than the pinned
            default).
        effort: Effort level; the flag is emitted only when not None
            (Haiku models reject it).
        timeout: Subprocess timeout in seconds.

    Returns:
        Parsed success envelope.

    Raises:
        HeadlessCLIError: Missing ``claude`` binary, subprocess timeout,
            or unparseable envelope.
        HeadlessUsageLimitError: Subscription usage limit reached.
        APIAuthError: Headless CLI is not logged in.
        APIRefusalError: The model refused instead of producing
            structured output.
        APIResponseError: Error envelope, non-success subtype, or a
            success envelope with no structured output.
    """
    # Neutralize headless-only ``@``-mention expansion in the untrusted user
    # text before it reaches the subprocess (see ``neutralize_at_mentions``).
    # This is the single subprocess chokepoint for both the curate and judge
    # backends, so escaping here covers both.
    user_text = neutralize_at_mentions(user_text)

    with tempfile.TemporaryDirectory(prefix="curator-headless-") as tmpdir:
        system_path = Path(tmpdir) / "system_prompt.txt"
        system_path.write_text(system_text, encoding="utf-8")
        cmd = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, separators=(",", ":")),
            "--system-prompt-file",
            str(system_path),
            "--model",
            model,
        ]
        if effort is not None:
            cmd.extend(["--effort", effort])
        # --safe-mode disables the child's own customizations (user- and
        # project-scope settings, hooks, global ~/.claude/CLAUDE.md, skills,
        # plugins, custom agents/commands, MCP). It closes the user-scope
        # context bleed that the tmpdir cwd (project scope) and
        # --strict-mcp-config (MCP) did not, and structured output still
        # works under it (verified on CLI 2.1.226). --no-session-persistence
        # keeps the run stateless.
        cmd.extend(["--safe-mode", "--no-session-persistence", "--strict-mcp-config"])
        # Variadic flag placed last (safe: the prompt arrives on stdin, so
        # nothing after the deny list could be swallowed by it).
        cmd.extend(["--disallowed-tools", *HEADLESS_DISALLOWED_TOOLS])

        env = {
            k: v for k, v in os.environ.items() if k not in HEADLESS_STRIPPED_ENV_VARS
        }
        stripped = sorted(HEADLESS_STRIPPED_ENV_VARS & os.environ.keys())
        if stripped:
            # Names only, never values: these carry keys and tokens.
            logger.debug(
                "Stripped from headless subprocess env: {}", ", ".join(stripped)
            )

        logger.debug(
            "Headless claude call: model={}, effort={}, timeout={}s",
            model,
            effort,
            timeout,
        )
        start = time.perf_counter()
        # S603 safe: list-form args (no shell=True); the only interpolated
        # values are the validated model/effort settings, a compact JSON
        # dump, and a temp-file path this function just created.
        try:
            completed = subprocess.run(  # noqa: S603
                cmd,
                input=user_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=tmpdir,
            )
        except FileNotFoundError as exc:
            msg = (
                "'claude' not found on PATH. Install Claude Code and log in "
                "(claude /login), or switch to --backend api."
            )
            raise HeadlessCLIError(msg) from exc
        except subprocess.TimeoutExpired as exc:
            msg = (
                f"Headless Claude Code call timed out after {timeout}s. "
                "Raise CURATOR_HEADLESS_TIMEOUT (or --headless-timeout) if "
                "the run legitimately needs more time."
            )
            raise HeadlessCLIError(msg) from exc
        except OSError as exc:
            # A non-executable, wrong-arch, or otherwise unlaunchable
            # 'claude' raises PermissionError/OSError, not FileNotFoundError;
            # without this it escapes the CuratorError taxonomy and the CLI
            # handler shows a traceback instead of an actionable message.
            msg = (
                f"Could not launch 'claude': {exc}. Check that Claude Code "
                "is installed and executable, or switch to --backend api."
            )
            raise HeadlessCLIError(msg) from exc
        elapsed = time.perf_counter() - start
        logger.info(
            "Headless claude call finished in {:.1f}s (exit {})",
            elapsed,
            completed.returncode,
        )
        return _parse_envelope(completed, requested_model=model)


class HeadlessCuratorClient:
    """Headless Claude Code client for resume curation.

    Satisfies the ``CuratorClient`` contract (constructor, context manager,
    ``curate``) but transports the call through a ``claude -p`` subprocess
    billed against the operator's Claude subscription. No API key is
    required or read; ``HEADLESS_STRIPPED_ENV_VARS`` (the API key, auth
    token, base URL, and provider switches) are actively stripped from
    the subprocess environment.

    Sits behind the same ``allow_api_spend`` gate as the API client:
    subscription usage is a billable quota, and a second knob would double
    the scrub surface for no safety gain.

    Args:
        settings: Validated application configuration.

    Example::

        with HeadlessCuratorClient(settings) as client:
            result = client.curate(portfolio, job_description)
    """

    def __init__(self, settings: CuratorSettings) -> None:
        """Initialise the client from validated application settings."""
        if not settings.allow_api_spend:
            raise APISpendGuardError(spend_guard_message("claude-code"))
        self._settings = settings

    # -- Context manager --------------------------------------------------

    def __enter__(self) -> Self:
        """Enter the context manager, returning the client."""
        return self

    def __exit__(self, *args: object) -> None:
        """Exit the context manager (no resources held)."""
        self.close()

    def close(self) -> None:
        """No-op: each curate call owns its subprocess and temp dir."""

    # -- Public API -------------------------------------------------------

    def curate(
        self,
        portfolio: PortfolioData,
        job_description: str,
        *,
        with_cover_letter: bool = False,
    ) -> CurationResult:
        """Curate via one headless ``claude -p`` structured-output call.

        Builds the same prompts and per-call JSON schema as the API path,
        runs exactly one subprocess, and feeds the envelope's structured
        output through the API path's validation ladder (adapter, ID
        validation, cover-letter policy) reused from :mod:`curator.client`.
        The same recovery persistence applies: post-extract validation
        failures persist the raw wire dict, cover-letter policy failures
        persist the otherwise-valid resume curation.

        Args:
            portfolio: Validated portfolio data from the loader.
            job_description: Raw text of the job posting.
            with_cover_letter: When True, also produce a cover letter in
                the same structured-output call.

        Returns:
            Validated curation result with ``backend="claude-code"`` and
            ``source="api"`` (AI-produced, subscription-transported).
            ``cache_ttl`` is None: subscription auth auto-caches at a
            fixed 1h TTL and the configurable knob does not apply.

        Raises:
            HeadlessCLIError: Missing binary, timeout, or unparseable
                envelope.
            HeadlessUsageLimitError: Subscription usage limit reached.
            APIAuthError: Headless CLI is not logged in.
            APIResponseError: Invalid envelope payload or curation that
                fails validation.
            JobDescriptionError: Empty or oversized job description
                (propagated from ``build_user_message``).
        """
        settings = self._settings

        # 1. Build prompts and schema (same builders as the API path).
        system_blocks = build_system_prompt(
            portfolio,
            with_cover_letter=with_cover_letter,
            cache_ttl=settings.cache_ttl,
        )
        system_text = flatten_system_blocks(system_blocks)
        # build_user_message always constructs str content; the cast
        # narrows MessageParam's wider content union for mypy.
        user_text = cast(
            "str",
            build_user_message(job_description, with_cover_letter=with_cover_letter)[0][
                "content"
            ],
        )
        schema = build_curation_schema(
            portfolio,
            with_cover_letter=with_cover_letter,
            max_pages=settings.max_pages,
        )

        logger.info(
            "Headless request: model={}, prompt={}chars, jd={}chars, cover_letter={}{}",
            settings.model,
            len(system_text),
            len(job_description),
            with_cover_letter,
            f", effort={settings.effort}" if settings.effort else "",
        )

        # 2. Exactly one subprocess call (locked by test).
        result = run_structured_prompt(
            system_text=system_text,
            user_text=user_text,
            schema=schema,
            model=settings.model,
            effort=settings.effort,
            timeout=settings.headless_timeout,
        )
        parsed_dict = result.structured_output
        # Correlation id for logs and recovery filenames. The session_id
        # stays local-only (logs + gitignored profile output), matching
        # the request_id convention on the API path.
        correlation_id = result.session_id or "headless"

        # 3. Validation ladder, reused from client.py. Persist the parsed
        # payload before re-raise so post-extract failures don't waste the
        # subscription call. Recovery via ``scripts/rerender.py --raw``.
        try:
            curation, cover_letter = _adapt_curation_dict(
                parsed_dict,
                portfolio,
                with_cover_letter=with_cover_letter,
                request_id=correlation_id,
                max_pages=settings.max_pages,
                jd_text=job_description,
            )
            curation = _validate_curation_ids(curation, portfolio)
        except APIResponseError as exc:
            raw_path: Path | None = None
            try:
                raw_path = _persist_raw_response(
                    parsed_dict,
                    output_dir=settings.output_dir,
                    request_id=correlation_id,
                )
            except (OSError, ValueError) as persist_exc:
                logger.error(
                    "Failed to persist raw headless response after post-"
                    "extract validation failure: {}",
                    persist_exc,
                )
            logger.error(
                "Post-extract validation failed (session_id={}). "
                "Raw response saved to {} for recovery via "
                "'uv run python scripts/rerender.py --raw <path>'. "
                "Original error: {}",
                correlation_id,
                raw_path,
                exc,
            )
            persist_hint = (
                f" Raw response persisted to {raw_path}."
                if raw_path is not None
                else " Raw response not persisted (see logs)."
            )
            msg = f"{exc}.{persist_hint} (session_id={correlation_id})"
            raise APIResponseError(msg) from exc

        # 4. Cover-letter policy validation, with the same recovery
        # persistence as the API path (rebuild the resume PDF via
        # scripts/rerender.py without a second subscription call).
        validated_cover_letter: CoverLetterCuration | None = cover_letter
        if validated_cover_letter is not None:
            try:
                validate_cover_letter(validated_cover_letter, portfolio)
            except CurationValidationError as exc:
                partial_path: Path | None = None
                try:
                    partial_path = _persist_partial_resume(
                        curation,
                        output_dir=settings.output_dir,
                        request_id=correlation_id,
                    )
                except (OSError, ValueError) as persist_exc:
                    logger.error(
                        "Failed to persist partial resume after cover-"
                        "letter validation failure: {}",
                        persist_exc,
                    )
                logger.error(
                    "Cover letter validation failed (session_id={}). "
                    "Resume saved to {} for recovery via "
                    "scripts/rerender.py. Original error: {}",
                    correlation_id,
                    partial_path,
                    exc,
                )
                persist_hint = (
                    f" Resume persisted to {partial_path}."
                    if partial_path is not None
                    else " Resume not persisted (see logs)."
                )
                msg = (
                    f"Cover letter validation failed: {exc}.{persist_hint} "
                    f"(session_id={correlation_id})"
                )
                raise APIResponseError(msg) from exc

        # 5. Log curation summary at INFO (parity with the API path).
        total_highlights = sum(len(wh.highlight_ids) for wh in curation.work_highlights)
        logger.info(
            "Curation: company={}, work={} ({} highlights ranked), "
            "skills={}, projects={}",
            curation.company_slug,
            len(curation.work_highlights),
            total_highlights,
            len(curation.skills),
            len(curation.projects),
        )

        return CurationResult(
            curation=curation,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_creation_input_tokens=result.cache_creation_input_tokens,
            cache_read_input_tokens=result.cache_read_input_tokens,
            source="api",
            cover_letter=validated_cover_letter,
            cache_ttl=None,
            backend="claude-code",
        )
