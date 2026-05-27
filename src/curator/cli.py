"""Typer CLI entry point for resume-curator."""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from types import FrameType

    import anthropic
    from loguru import Record

    from curator.config import CuratorSettings
    from curator.eval import EvalContext
    from curator.eval.judge import Tier2Report
    from curator.eval.report import EvalReport
    from curator.models import PortfolioData

app = typer.Typer(
    name="curator",
    help="AI-powered resume curation CLI.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


# ---------------------------------------------------------------------------
# Logging infrastructure
# ---------------------------------------------------------------------------


class _InterceptHandler(logging.Handler):
    """Route stdlib logging (httpx, anthropic SDK) through Loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame: FrameType | None = logging.currentframe()
        depth = 2
        while frame is not None and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


# Catches sk-ant-api03-XXXXXXXX... including quoted variants
# (e.g., 'sk-ant-...' from Python repr or JSON serialization).
_API_KEY_PATTERN = re.compile(r"['\"]?sk-ant-[a-zA-Z0-9_-]+['\"]?")

# Catches key=value patterns with optional quotes (JSON, Python repr, env vars).
# Supplements _API_KEY_PATTERN for non-Anthropic secrets.
_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|token|password|secret)\s*[=:]\s*['\"]?\S+['\"]?"
)

# Stdlib loggers that are noisy at DEBUG level. Without suppression,
# basicConfig(level=0) floods the always-on file sink with httpx
# request/response traces, asyncio internals, etc.
_NOISY_LOGGERS = ("httpx", "httpcore", "urllib3", "asyncio", "filelock", "anthropic")


def _redacting_filter(record: Record) -> bool:
    """Scrub API keys and secret-like patterns from log messages.

    Used as a sink ``filter`` so it runs for ALL callers of the global
    ``logger`` — not just a patched instance. Loguru calls filters AFTER
    message formatting (args already substituted), so this catches secrets
    passed as format arguments::

        logger.info("Key: {}", api_key)  # filter sees "Key: sk-ant-..."

    Returns:
        Always ``True`` — this is a mutating filter, not a gatekeeping one.
    """
    msg: str = record["message"]
    msg = _API_KEY_PATTERN.sub("[REDACTED_API_KEY]", msg)
    msg = _SECRET_PATTERN.sub(r"\1=[REDACTED]", msg)
    record["message"] = msg
    return True


def _log_dir() -> Path:
    """Return XDG-compliant log directory for Linux/WSL.

    Respects ``XDG_STATE_HOME`` if set, falling back to ``~/.local/state``.
    """
    xdg = os.environ.get("XDG_STATE_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "curator" / "log"


def configure_logging(*, verbose: bool = False, _testing: bool = False) -> None:
    """Set up Loguru sinks for CLI output and persistent debug file logging.

    Two sinks are configured:

    * **stderr** (always active): Human-readable, colored output. Level is INFO
      by default, DEBUG when *verbose* is True. When verbose, includes source
      location (``module:function:line``). ``diagnose=False`` to prevent secret
      leakage through exception traceback variable inspection.
    * **JSON file** (always active at DEBUG): Structured JSON Lines written to
      ``~/.local/state/curator/log/debug.jsonl``. Rotated at 10 MB, retained
      3 days. ``diagnose=False`` to prevent secret leakage in persistent files.

    A regex redaction filter is applied to ALL sinks as defense-in-depth.

    Note:
        The always-on file sink intentionally deviates from the common
        guidance of gating the file sink behind ``--verbose``. Our use case
        benefits from a persistent complete record for post-hoc debugging
        and AI-assisted analysis.

    Args:
        verbose: When True, console output includes DEBUG-level messages.
            The log file always captures all levels regardless of this setting.
        _testing: When True, skip file sink creation (for unit tests).
    """
    logger.remove()

    # Route stdlib loggers (httpx, httpcore, anthropic SDK) through Loguru.
    # level=0 forwards everything; per-logger suppression below controls noise.
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

    # Suppress noisy third-party loggers. When verbose, allow INFO-level
    # SDK messages (useful context without request/response body floods).
    # When non-verbose, suppress to WARNING only.
    sdk_level = logging.INFO if verbose else logging.WARNING
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(sdk_level)

    console_level = "DEBUG" if verbose else "INFO"

    # Verbose format includes timestamps and source location for debugging.
    console_format = (
        "<green>{time:HH:mm:ss.SSS}</green> | "
        "<level>{level:<8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
        if verbose
        else "<level>{level:<8}</level> | <level>{message}</level>"
    )

    # Sink 1: User-facing on stderr — always active.
    # diagnose=False on both sinks: Loguru's diagnose feature dumps local
    # variable values in tracebacks, which can leak secrets (API keys, PII)
    # regardless of the redaction filter (filter only covers record["message"],
    # not exception formatting). Redaction filter is defense-in-depth.
    logger.add(
        sys.stderr,
        level=console_level,
        format=console_format,
        colorize=True,
        diagnose=False,
        filter=_redacting_filter,
    )

    # Sink 2: Structured debug file — ALWAYS active at DEBUG level.
    # diagnose=False: secrets must never land in persistent storage.
    if not _testing:
        try:
            log_path = _log_dir()
            log_path.mkdir(parents=True, exist_ok=True, mode=0o700)
            logger.add(
                log_path / "debug.jsonl",
                level="DEBUG",
                serialize=True,
                rotation="10 MB",
                retention="3 days",
                diagnose=False,
                filter=_redacting_filter,
            )
        except OSError:
            logger.warning(
                "Could not create log directory {}, file logging disabled", log_path
            )

    logger.debug("curator {} starting", version("resume-curator"))


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"curator {version('resume-curator')}")
        raise typer.Exit


@app.callback()
def main(
    *,
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Enable debug logging to console. Log file always captures all levels.",
    ),
) -> None:
    """AI-powered resume curation CLI."""
    configure_logging(verbose=verbose)


def _display_dry_run_preview(
    console: Console,
    settings: CuratorSettings,
    portfolio: PortfolioData,
    jd_text: str,
) -> None:
    """Display a zero-cost preview of what would be sent to the API."""
    total_highlights = sum(len(e.highlights) for e in portfolio.work)
    total_keywords = sum(len(s.keywords) for s in portfolio.skills)

    other_counts: list[str] = []
    for label, count in [
        ("edu", len(portfolio.education)),
        ("cert", len(portfolio.certificates)),
        ("proj", len(portfolio.projects)),
    ]:
        if count:
            other_counts.append(f"{label}={count}")

    effort_str = settings.effort or "default"

    table = Table(title="Dry run — no API call made", show_header=False)
    table.add_column("Setting", style="cyan")
    table.add_column("Value")

    table.add_row("Portfolio", str(settings.portfolio_data_path))
    table.add_row(
        "  Work entries",
        f"{len(portfolio.work)} ({total_highlights} highlights)",
    )
    table.add_row(
        "  Skill groups",
        f"{len(portfolio.skills)} ({total_keywords} keywords)",
    )
    if other_counts:
        table.add_row("  Other sections", " ".join(other_counts))
    table.add_row("Job description", f"{len(jd_text):,} chars")
    table.add_row("Model", settings.model)
    table.add_row("Max tokens", f"{settings.max_tokens:,}")
    table.add_row("Effort", effort_str)
    table.add_row(
        "Target pages",
        f"{settings.max_pages} (up to {settings.max_trim_iterations} trims)",
    )
    table.add_row("Estimated cost", "~$0.07 first / ~$0.02 cached (Sonnet)")

    console.print()
    console.print(table)


_JD_ARGUMENT = typer.Argument(
    None,
    help="Path to a job description text file, or '-' for stdin.",
)


def _read_jd_text(
    job_description: Path | None,
    *,
    clipboard: bool,
) -> str:
    """Acquire raw JD text from file, stdin, or clipboard.

    Handles I/O concerns only: file existence, permissions, stdin TTY
    detection, clipboard provider availability, and a bounded stdin read
    to prevent unbounded memory allocation. Content validation (empty,
    whitespace-only, length, reserved XML tags) is delegated downstream
    to :func:`curator.prompt.build_user_message`, which is the single
    source of truth for JD validity so every caller (CLI, library,
    future surfaces) goes through the same rules.

    Args:
        job_description: Path to JD file, ``Path("-")`` for stdin, or None
            to auto-detect stdin.
        clipboard: Read from system clipboard if True.

    Returns:
        Raw JD text as read from the source, un-stripped. Downstream
        validation normalizes and rejects invalid content.

    Raises:
        JobDescriptionError: I/O failure (file not found, permission
            denied, clipboard provider unavailable, no input on a TTY,
            or mutually exclusive flags).
    """
    from curator.exceptions import JobDescriptionError
    from curator.rules import MAX_JD_LENGTH

    # Bounded stdin read: read one byte past the limit so the downstream
    # length check in build_user_message can reliably detect overflow
    # without allocating unbounded memory for pathological input.
    stdin_read_bound = MAX_JD_LENGTH + 1

    if clipboard and job_description is not None:
        msg = "--clipboard and a file argument are mutually exclusive."
        raise JobDescriptionError(msg)

    if clipboard:
        try:
            import pyperclip
        except ImportError:
            msg = (
                "pyperclip is required for --clipboard. "
                "Install with: uv sync --extra clipboard"
            )
            raise JobDescriptionError(msg) from None
        try:
            text: str = pyperclip.paste()
        except Exception as e:
            msg = (
                "Failed to read from system clipboard. "
                "Is a clipboard provider installed?"
            )
            raise JobDescriptionError(msg) from e
    elif job_description is None or str(job_description) == "-":
        # No argument + no pipe = user forgot to specify input.
        if job_description is None and sys.stdin.isatty():
            msg = (
                "No input specified. Provide a file path, "
                "use '-' for stdin, or use --clipboard."
            )
            raise JobDescriptionError(msg)
        if sys.stdin.isatty():
            sys.stderr.write("Reading from stdin (Ctrl+D to finish)...\n")
        text = sys.stdin.read(stdin_read_bound)
    else:
        path = job_description
        if not path.is_file():
            msg = f"Not a file: {path}"
            raise JobDescriptionError(msg)
        try:
            text = path.read_text(encoding="utf-8")
        except PermissionError:
            msg = f"Permission denied: {path}"
            raise JobDescriptionError(msg) from None

    return text


def _resolve_publish_destination(
    settings: CuratorSettings,
    *,
    publish: bool,
    override: Path | None = None,
) -> Path | None:
    """Resolve the publish destination for ``--publish`` / ``curator publish``.

    Precedence: explicit ``override`` (subcommand ``-d``) > ``settings.publish_dir``
    (env / settings). When ``publish`` is False and no override is given,
    returns ``None`` (publish step is skipped). When publishing is requested
    but no destination is configured, raises ``PublishError`` with a hint
    pointing at the env var.
    """
    from curator.exceptions import PublishError

    if override is not None:
        return override
    if not publish:
        return None
    if settings.publish_dir is None:
        msg = (
            "--publish requires a destination. Set CURATOR_PUBLISH_DIR "
            "(e.g. /mnt/c/Users/<you>/Downloads/resume-curator) or "
            "pass a value via CuratorSettings.publish_dir."
        )
        raise PublishError(msg)
    return settings.publish_dir


@app.command()
def curate(
    job_description: Path | None = _JD_ARGUMENT,
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview what would be sent to the API without making any call.",
    ),
    no_pdf: bool = typer.Option(
        False,
        "--no-pdf",
        help="Call the API and write artifacts, but skip PDF compilation.",
    ),
    clipboard: bool = typer.Option(
        False,
        "--clipboard",
        help="Read job description from system clipboard (requires pyperclip).",
    ),
    pages: int | None = typer.Option(
        None,
        "--pages",
        min=1,
        max=5,
        help=(
            "Target page count (1..5). Overrides CuratorSettings.max_pages "
            "(default 2). Pass --pages 1 for short-form output."
        ),
    ),
    cover_letter: bool = typer.Option(
        False,
        "--cover-letter/--no-cover-letter",
        help=(
            "Also generate a tailored cover letter in the same API call. "
            "Costs a few extra output tokens; no additional API call is "
            "made. Produces cover_letter.pdf and data/cover_letter.yaml "
            "in the profile directory."
        ),
    ),
    cache_ttl: str | None = typer.Option(
        None,
        "--cache-ttl",
        click_type=click.Choice(["5m", "1h"]),
        help=(
            "Anthropic prompt-cache TTL on the portfolio block. '5m' "
            "matches Anthropic's default; '1h' uses the extended GA cache "
            "(2x write, same read; default for this tool). Overrides "
            "CURATOR_CACHE_TTL."
        ),
    ),
    publish: bool = typer.Option(
        False,
        "--publish/--no-publish",
        help=(
            "After rendering, copy resume.pdf / cover_letter.pdf / "
            "cover_letter.txt into CURATOR_PUBLISH_DIR/<profile>/. Useful for "
            "moving artifacts out of WSL so Windows browsers can upload them; "
            "see README troubleshooting."
        ),
    ),
) -> None:
    """Curate a resume tailored to a job description."""
    from pydantic import ValidationError

    from curator.config import CuratorSettings
    from curator.exceptions import ConfigError, CuratorError
    from curator.pipeline import run_pipeline

    console = Console(stderr=True)

    try:
        if dry_run and no_pdf:
            console.print(
                "[red]Error:[/] --dry-run and --no-pdf are mutually exclusive."
            )
            raise typer.Exit(code=1)

        try:
            overrides: dict[str, Any] = {}
            if pages is not None:
                overrides["max_pages"] = pages
            if cache_ttl is not None:
                overrides["cache_ttl"] = cache_ttl
            settings = CuratorSettings(**overrides)
        except ValidationError as e:
            raise ConfigError(str(e)) from e

        # Log resolved config for troubleshooting.
        logger.info(
            "Config: model={}, max_tokens={}, effort={}, "
            "max_pages={}, max_trim={}, retries={}, cache_ttl={}",
            settings.model,
            settings.max_tokens,
            settings.effort,
            settings.max_pages,
            settings.max_trim_iterations,
            settings.api_max_retries,
            settings.cache_ttl,
        )
        logger.info("Portfolio: {}", settings.portfolio_data_path)
        logger.info("Output dir: {}", settings.output_dir)

        # Read job description from file, stdin, or clipboard, then
        # validate content before doing any expensive work. Validation is
        # delegated to prompt.validate_job_description so CLI and library
        # callers share a single source of truth for JD rules.
        jd_text = _read_jd_text(job_description, clipboard=clipboard)
        from curator.prompt import validate_job_description

        validate_job_description(jd_text)

        # --- Dry-run: zero-cost preview, no API call ---
        if dry_run:
            from curator.loader import load_portfolio

            portfolio = load_portfolio(settings.portfolio_data_path)
            _display_dry_run_preview(console, settings, portfolio, jd_text)
            if cover_letter:
                console.print(
                    "[cyan]Note:[/] --cover-letter also on; the same API call "
                    "would produce a cover letter alongside the resume."
                )
            return

        publish_to = _resolve_publish_destination(settings, publish=publish)

        pipeline_start = time.perf_counter()

        with console.status("Starting...") as status:
            result = run_pipeline(
                settings,
                jd_text,
                skip_pdf=no_pdf,
                with_cover_letter=cover_letter,
                publish_to=publish_to,
                on_status=status.update,
            )

        logger.info("Total pipeline: {:.1f}s", time.perf_counter() - pipeline_start)

        _display_pipeline_result(
            console,
            result,
            title_prefix="Resume curated for",
            max_pages=settings.max_pages,
        )

    except CuratorError as e:
        console.print(f"[red]Error:[/] {e}")
        raise typer.Exit(code=1) from None


def _display_pipeline_result(
    console: Console,
    result: Any,
    *,
    title_prefix: str,
    max_pages: int,
) -> None:
    """Render the shared pipeline-result summary table and output paths.

    Used by both ``curate`` and ``static`` so the CLI surface stays
    consistent across paths.
    """
    rc = result.curation.curation
    output = result.render_output
    console.print()

    title_parts = [f"{title_prefix} {rc.company_slug}"]
    if result.page_count is not None:
        trims = len(result.trim_log)
        trim_info = f", {trims} trim(s)" if trims else ""
        title_parts.append(f"{result.page_count} page(s){trim_info}")
    table = Table(title=" - ".join(title_parts))
    table.add_column("Section", style="cyan")
    table.add_column("Entries", style="green", justify="right")

    total_highlights = sum(len(wh.highlight_ids) for wh in rc.work_highlights)
    table.add_row(
        "work (ranked)",
        f"{len(rc.work_highlights)} entries, {total_highlights} highlights",
    )
    if rc.skills:
        table.add_row("skills", str(len(rc.skills)))
    if rc.projects:
        table.add_row("projects", str(len(rc.projects)))

    console.print(table)
    console.print(f"\n[green]Output:[/] {output.profile_dir}")
    if output.pdf_path is not None:
        console.print(f"[green]PDF:[/]    {output.pdf_path}")
    else:
        console.print("[yellow]No-PDF mode -- PDF compilation skipped[/]")
    if output.cover_letter_pdf_path is not None:
        console.print(f"[green]Cover letter PDF:[/] {output.cover_letter_pdf_path}")
    elif output.cover_letter_yaml_path is not None:
        console.print(f"[green]Cover letter YAML:[/] {output.cover_letter_yaml_path}")
    if output.skipped_ids > 0:
        console.print(
            f"[yellow]Warning:[/] {output.skipped_ids} ID(s) "
            "from curation not found in portfolio (see --verbose)"
        )
    if output.safety_net_additions > 0:
        console.print(
            f"[yellow]Warning:[/] {output.safety_net_additions} highlight(s) "
            "appended by safety net (AI omitted them from ranking)"
        )

    published = getattr(result, "published_paths", None)
    if published:
        # Files all share a parent (<publish_dir>/<profile>/) so showing the
        # parent once + filenames keeps the output scannable.
        publish_root = published[0].parent
        console.print(f"[green]Published to:[/] {publish_root}")
        for path in published:
            console.print(f"  - {path.name}")

    if not result.converged and not result.skip_pdf:
        trims = len(result.trim_log)
        console.print(
            f"\n[yellow]Warning:[/] Resume is {result.page_count} page(s)"
            f" after {trims} trim(s)"
            f" (target: {max_pages})."
            " Manual editing may be needed."
        )


@app.command(name="static")
def static_cmd(
    name: str = typer.Option(
        "general",
        "--name",
        help=(
            "Free-text name used as the output slug and audit descriptor. "
            "Non-alphanumerics collapse to '-'; falls back to 'general' if "
            "the result is empty."
        ),
    ),
    pages: int = typer.Option(
        2,
        "--pages",
        min=1,
        max=5,
        help=(
            "Target page count (1..5). Default 2 (matches curator curate); "
            "pass --pages 1 for short-form output."
        ),
    ),
    max_highlights: int | None = typer.Option(
        None,
        "--max-highlights",
        min=1,
        max=50,
        help=(
            "Optional per-work-entry highlight cap applied before rendering. "
            "When set, each role shows at most this many bullets."
        ),
    ),
    no_pdf: bool = typer.Option(
        False,
        "--no-pdf",
        help="Write audit artifacts but skip Typst PDF compilation.",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help=(
            "Print a JSON envelope ({'source': 'static', 'schema_version': "
            "'static-1.0', 'curation': {...}}) to stdout and exit without "
            "writing a profile directory. The inner 'curation' object matches "
            "curator.models.ResumeCuration. Output includes portfolio "
            "summary/label; do not pipe into shared CI logs or paste buckets."
        ),
    ),
    cover_letter: bool = typer.Option(
        False,
        "--cover-letter/--no-cover-letter",
        help=(
            "Render the candidate-authored cover letter from "
            "<portfolio>/data/cover-letter.yaml verbatim. No placeholders, "
            "no TEMPLATE banner; the rendered PDF is submittable as-is. "
            "--name does not affect letter content (only output directory "
            "naming). See COVER_LETTER_* constants in src/curator/rules.py "
            "for the machine-enforced authoring constraints. No API call "
            "is made."
        ),
    ),
    publish: bool = typer.Option(
        False,
        "--publish/--no-publish",
        help=(
            "After rendering, copy resume.pdf / cover_letter.pdf / "
            "cover_letter.txt into CURATOR_PUBLISH_DIR/<profile>/. Useful for "
            "moving artifacts out of WSL so Windows browsers can upload them; "
            "see README troubleshooting."
        ),
    ),
) -> None:
    """Generate a polished, general-purpose resume with zero API cost.

    Deterministically selects portfolio content (all work highlights, all
    skill groups and keywords, projects by ``weight``) and renders via the
    same Typst template used by ``curate``. No Anthropic API call is made.
    """
    from pydantic import ValidationError

    from curator.config import CuratorSettings
    from curator.exceptions import ConfigError, CuratorError
    from curator.loader import load_portfolio
    from curator.pipeline import run_static_pipeline
    from curator.static_mode import synthesize_curation

    console = Console(stderr=True)

    try:
        if json_out and no_pdf:
            console.print("[red]Error:[/] --json and --no-pdf are mutually exclusive.")
            raise typer.Exit(code=1)

        try:
            settings = CuratorSettings(max_pages=pages)
        except ValidationError as e:
            raise ConfigError(str(e)) from e

        logger.info(
            "Static config: name={}, pages={}, max_highlights={}, no_pdf={}, json={}",
            name,
            pages,
            max_highlights,
            no_pdf,
            json_out,
        )
        logger.info("Portfolio: {}", settings.portfolio_data_path)

        from curator.static_mode import DEFAULT_NAME as _DEFAULT_NAME

        if cover_letter and name != _DEFAULT_NAME:
            logger.info(
                "Static cover letter is generic; --name={} only affects "
                "output directory naming, not letter content.",
                name,
            )

        if json_out:
            import json as _json

            from curator.exceptions import CurationValidationError
            from curator.models import validate_cover_letter
            from curator.static_mode import synthesize_cover_letter

            portfolio = load_portfolio(settings.portfolio_data_path)
            curation = synthesize_curation(
                portfolio,
                name=name,
                max_highlights_per_work=max_highlights,
            )
            # Envelope so consumers can branch on provenance without inferring
            # it from absence of token fields.
            payload: dict[str, Any] = {
                "source": "static",
                "schema_version": "static-1.0",
                "curation": curation.model_dump(),
            }
            if cover_letter:
                letter = synthesize_cover_letter(portfolio)
                # Mirror the validation the non-JSON path runs, so a broken
                # letter fails here instead of silently shipping clean JSON
                # with invalid content.
                try:
                    validate_cover_letter(letter, portfolio)
                except CurationValidationError as exc:
                    console.print(
                        f"[red]Error:[/] Cover letter in "
                        f"<portfolio>/data/cover-letter.yaml failed validation: "
                        f"{exc}"
                    )
                    raise typer.Exit(code=1) from exc
                payload["cover_letter"] = letter.model_dump()
            sys.stdout.write(_json.dumps(payload, indent=2))
            sys.stdout.write("\n")
            return

        publish_to = _resolve_publish_destination(settings, publish=publish)

        pipeline_start = time.perf_counter()

        with console.status("Starting...") as status:
            result = run_static_pipeline(
                settings,
                name=name,
                max_highlights=max_highlights,
                skip_pdf=no_pdf,
                with_cover_letter=cover_letter,
                publish_to=publish_to,
                on_status=status.update,
            )

        logger.info("Total pipeline: {:.1f}s", time.perf_counter() - pipeline_start)

        _display_pipeline_result(
            console,
            result,
            title_prefix="Static resume for",
            max_pages=settings.max_pages,
        )

    except CuratorError as e:
        console.print(f"[red]Error:[/] {e}")
        raise typer.Exit(code=1) from None


_PUBLISH_PROFILE_ARG = typer.Argument(
    ...,
    help="Profile directory to publish (e.g. profiles/2026-05-27-acme).",
)
_PUBLISH_DESTINATION_OPT = typer.Option(
    None,
    "--destination",
    "-d",
    help="Override CURATOR_PUBLISH_DIR for this invocation.",
)


@app.command(name="publish")
def publish_cmd(
    profile_dir: Path = _PUBLISH_PROFILE_ARG,
    destination: Path | None = _PUBLISH_DESTINATION_OPT,
) -> None:
    """Copy a profile's upload-ready artifacts to the publish directory.

    Useful for re-publishing past profiles, or republishing after a hand
    edit. Does NOT publish to any registry or remote -- the name reflects
    "make these files available for upload from a Windows browser", not
    package distribution.
    """
    from pydantic import ValidationError

    from curator.config import CuratorSettings
    from curator.exceptions import ConfigError, CuratorError, PublishError
    from curator.publish import publish_artifacts

    console = Console(stderr=True)

    try:
        try:
            settings = CuratorSettings()
        except ValidationError as e:
            raise ConfigError(str(e)) from e

        if not profile_dir.is_dir():
            msg = f"Profile directory not found: {profile_dir}"
            raise PublishError(msg)

        dest = _resolve_publish_destination(
            settings, publish=True, override=destination
        )
        # dest is non-None here because publish=True with a destination
        # forces resolution to either override or settings.publish_dir.
        assert dest is not None

        paths = publish_artifacts(profile_dir, dest)
        publish_root = dest.expanduser() / profile_dir.name
        if not paths:
            console.print(f"[yellow]No publishable files found in {profile_dir}[/]")
            raise typer.Exit(code=1)
        console.print(f"[green]Published to:[/] {publish_root}")
        for path in paths:
            console.print(f"  - {path.name}")

    except CuratorError as e:
        console.print(f"[red]Error:[/] {e}")
        raise typer.Exit(code=1) from None


_EVAL_PROFILE_ARG = typer.Argument(
    None,
    help="Path to a profile output directory.",
)
_EVAL_PORTFOLIO_OPT = typer.Option(
    None,
    "--portfolio",
    help="Path to portfolio data directory (enables keyword coverage metrics).",
)
_EVAL_SKIP_OPT = typer.Option(
    None,
    "--skip",
    help="Metric names to skip (repeatable).",
)
_EVAL_GOLDEN_OPT = typer.Option(
    False,
    "--golden",
    help="Run evaluation against golden dataset for regression testing.",
)
_EVAL_GOLDEN_DIR_OPT = typer.Option(
    None,
    "--golden-dir",
    help="Path to golden dataset directory (default: tests/eval/golden/).",
)
_EVAL_CALIBRATE_OPT = typer.Option(
    False,
    "--calibrate",
    help="Show proposed baselines for golden cases (use with --golden).",
)
_EVAL_APPLY_OPT = typer.Option(
    False,
    "--apply",
    help="Write baselines into golden YAML files (use with --calibrate).",
)
_EVAL_JUDGE_OPT = typer.Option(
    False,
    "--judge",
    help="Run Tier 2 LLM judge evaluation (requires API key, costs ~$0.05).",
)
_EVAL_JSON_OPT = typer.Option(
    False,
    "--json",
    help="Output results as JSON to stdout (machine-readable).",
)
_EVAL_PAGES_OPT = typer.Option(
    None,
    "--pages",
    min=1,
    max=5,
    help=(
        "Override the inferred max_pages used for band selection (1..5). "
        "Without this flag, max_pages is inferred from the rendered PDF "
        "(priority 1) or curation_log.json (priority 2). Rejected when "
        "--golden is set: each golden case owns its own meta.max_pages."
    ),
)


@app.command(name="eval")
def eval_cmd(
    profile_dir: Path | None = _EVAL_PROFILE_ARG,
    portfolio: Path | None = _EVAL_PORTFOLIO_OPT,
    skip: list[str] | None = _EVAL_SKIP_OPT,
    golden: bool = _EVAL_GOLDEN_OPT,
    golden_dir: Path | None = _EVAL_GOLDEN_DIR_OPT,
    calibrate: bool = _EVAL_CALIBRATE_OPT,
    apply: bool = _EVAL_APPLY_OPT,
    judge: bool = _EVAL_JUDGE_OPT,
    json_output: bool = _EVAL_JSON_OPT,
    pages: int | None = _EVAL_PAGES_OPT,
) -> None:
    """Evaluate a curated resume profile with quality metrics."""
    from curator.exceptions import CuratorError

    console = Console(stderr=True)

    # Validate flag dependencies.
    if calibrate and not golden:
        console.print("[red]Error:[/] --calibrate requires --golden.")
        raise typer.Exit(code=1)
    if apply and not calibrate:
        console.print("[red]Error:[/] --apply requires --calibrate.")
        raise typer.Exit(code=1)
    if golden and pages is not None:
        # Golden cases own their own meta.max_pages and must not be
        # overridden globally; doing so would silently re-rate 1-page
        # goldens against the long-form rubric (or vice versa).
        console.print(
            "[red]Error:[/] --pages is not allowed with --golden; each "
            "golden case carries its own meta.max_pages."
        )
        raise typer.Exit(code=1)

    try:
        if golden:
            _run_golden_eval(
                console,
                golden_dir,
                skip,
                calibrate=calibrate,
                apply_baselines=apply,
                judge=judge,
                json_output=json_output,
            )
        elif profile_dir is not None:
            _run_profile_eval(
                console,
                profile_dir,
                portfolio,
                skip,
                judge=judge,
                json_output=json_output,
                pages_override=pages,
            )
        else:
            console.print("[red]Error:[/] Provide a profile directory or use --golden.")
            raise typer.Exit(code=1)
    except CuratorError as e:
        console.print(f"[red]Error:[/] {e}")
        raise typer.Exit(code=1) from None


def _run_profile_eval(
    console: Console,
    profile_dir: Path,
    portfolio: Path | None,
    skip: list[str] | None,
    *,
    judge: bool = False,
    json_output: bool = False,
    pages_override: int | None = None,
) -> None:
    """Run eval against a single profile directory."""
    import json
    from dataclasses import replace

    from curator.eval import evaluate_tier1, from_profile_dir

    # Validate profile_dir exists (moved from Typer argument).
    if not profile_dir.is_dir():
        console.print(f"[red]Error:[/] Not a directory: {profile_dir}")
        raise typer.Exit(code=1)

    # Load portfolio if provided.
    portfolio_data = None
    if portfolio is not None:
        if not portfolio.is_dir():
            console.print(f"[red]Error:[/] Not a directory: {portfolio}")
            raise typer.Exit(code=1)
        from curator.loader import load_portfolio

        portfolio_data = load_portfolio(portfolio)

    ctx = from_profile_dir(profile_dir, portfolio=portfolio_data)
    if pages_override is not None and pages_override != ctx.max_pages:
        # User-supplied override disagrees with PDF/log inference; warn so
        # the divergence is visible (user may be re-scoring a profile
        # against the wrong rubric on purpose, or by mistake).
        console.print(
            f"[yellow]Warning:[/] --pages={pages_override} differs from "
            f"inferred max_pages={ctx.max_pages}; scoring against the "
            f"override."
        )
        ctx = replace(ctx, max_pages=pages_override)
    skip_set = frozenset(skip) if skip else frozenset()
    report = evaluate_tier1(ctx, skip_metrics=skip_set)

    # Tier 2 judge (optional).
    tier2 = None
    if judge:
        from curator.config import CuratorSettings

        settings = CuratorSettings()
        tier2 = _run_judge(ctx, settings)

    if json_output:
        result: dict[str, Any] = {"tier1": report.to_dict()}
        if tier2 is not None:
            result["tier2"] = tier2.to_dict()
        print(json.dumps(result, indent=2))  # noqa: T201
    else:
        _display_eval_report(console, profile_dir.name, report)
        if tier2 is not None:
            _display_tier2_report(console, tier2)


def _run_golden_eval(
    console: Console,
    golden_dir: Path | None,
    skip: list[str] | None,
    *,
    calibrate: bool = False,
    apply_baselines: bool = False,
    judge: bool = False,
    json_output: bool = False,
) -> None:
    """Run eval against the golden dataset for regression testing."""
    import json
    import subprocess
    import tempfile

    from curator.eval import evaluate_tier1, from_profile_dir
    from curator.eval.golden import (
        GOLDEN_SKIP_METRICS,
        RegressionFinding,
        RegressionSeverity,
        compare_against_golden,
        compare_judge_against_golden,
        discover_golden_cases,
        materialize_profile,
        render_golden_pdf,
    )
    from curator.exceptions import CuratorError
    from curator.rules import BASELINE_MARGIN

    cases = discover_golden_cases(golden_dir)
    if not cases:
        console.print("[yellow]No golden cases found.[/]")
        raise typer.Exit(code=0)

    # Always skip portfolio-dependent metrics for golden cases.
    skip_set = frozenset(skip) if skip else frozenset()
    skip_set = skip_set | GOLDEN_SKIP_METRICS

    all_passed = True
    failed_results: list[tuple[str, list[RegressionFinding]]] = []
    calibration_data: list[tuple[str, str, float]] = []  # (id, name, score)
    # Tier 2 judge calibration: (case_id, name, tier, {dimension: score})
    judge_cal_data: list[tuple[str, str, str, dict[str, int]]] = []
    json_cases: list[dict[str, Any]] = []

    # Set up shared judge client for batch reuse (one TCP connection).
    judge_client = None
    settings = None
    if judge:
        from curator.config import CuratorSettings

        settings = CuratorSettings()

        # Fail fast: check spend guard before creating the client and
        # iterating 24 golden cases that would each fail individually.
        if not settings.allow_api_spend:
            console.print(
                "[red]API spending is not authorized.[/] "
                "Set CURATOR_ALLOW_API_SPEND=true to allow judge API calls."
            )
            raise typer.Exit(code=1)

        import anthropic
        import httpx

        judge_client = anthropic.Anthropic(
            api_key=settings.require_api_key(),
            max_retries=settings.api_max_retries,
            timeout=httpx.Timeout(120.0, connect=5.0),
        )

    # Use a home-based temp dir to avoid snap Typst /tmp restriction.
    cache_base = Path.home() / ".cache" / "curator-golden-eval"
    cache_base.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="golden-", dir=cache_base) as tmp_root:
            results_table = Table(title=f"Golden Regression — {len(cases)} cases")
            results_table.add_column("Case", style="cyan")
            results_table.add_column("Role")
            results_table.add_column("Score", justify="right")
            if judge:
                results_table.add_column("Judge", justify="right")
            results_table.add_column("Status")
            results_table.add_column("Findings", justify="right")

            typst_available = True
            for case in cases:
                profile_path = Path(tmp_root) / case.meta.id
                materialize_profile(case, profile_path)

                # Render PDF if Typst is available.
                if typst_available:
                    try:
                        render_golden_pdf(profile_path)
                    except FileNotFoundError:
                        console.print(
                            "[yellow]Typst not installed — "
                            "PDF metrics will return WARN[/]"
                        )
                        typst_available = False
                    except subprocess.TimeoutExpired:
                        console.print(f"[yellow]Typst timed out for {case.meta.id}[/]")
                    except Exception as e:
                        console.print(
                            f"[yellow]PDF render skipped for {case.meta.id}: {e}[/]"
                        )

                ctx = from_profile_dir(profile_path)
                report = evaluate_tier1(ctx, skip_metrics=skip_set)
                comparison = compare_against_golden(report, case)

                # Run judge if requested.
                tier2 = None
                if judge and settings is not None:
                    from curator.eval.judge import evaluate_tier2

                    try:
                        tier2 = evaluate_tier2(
                            ctx,
                            settings=settings,
                            client=judge_client,
                        )

                        # Collect calibration data before comparison.
                        if calibrate:
                            judge_cal_data.append(
                                (
                                    case.meta.id,
                                    case.meta.name,
                                    case.meta.tier,
                                    {d.name: d.score for d in tier2.dimensions},
                                )
                            )

                        # Structured calibration logging.
                        for d in tier2.dimensions:
                            human_val = case.human_scores.get(d.name)
                            logger.info(
                                "calibration: case={}, dim={}, judge={},"
                                " human={}, diff={}",
                                case.meta.id,
                                d.name,
                                d.score,
                                human_val,
                                abs(d.score - human_val)
                                if human_val is not None
                                else None,
                            )

                        # Check judge scores against human_scores.
                        judge_findings = compare_judge_against_golden(tier2, case)
                        if judge_findings:
                            comparison = type(comparison)(
                                case_id=comparison.case_id,
                                passed=comparison.passed
                                and not any(
                                    f.severity == RegressionSeverity.ERROR
                                    for f in judge_findings
                                ),
                                report=comparison.report,
                                findings=[
                                    *comparison.findings,
                                    *judge_findings,
                                ],
                            )
                    except CuratorError as e:
                        console.print(
                            f"[yellow]Judge failed for {case.meta.id}: {e}[/]"
                        )

                if not comparison.passed:
                    all_passed = False
                    failed_results.append((case.meta.id, list(comparison.findings)))

                if calibrate:
                    calibration_data.append(
                        (case.meta.id, case.meta.name, report.aggregate_score)
                    )

                error_count = sum(
                    1
                    for f in comparison.findings
                    if f.severity == RegressionSeverity.ERROR
                )
                warn_count = len(comparison.findings) - error_count

                status = (
                    "[green]PASS[/green]" if comparison.passed else "[red]FAIL[/red]"
                )
                finding_str = ""
                if error_count:
                    finding_str += f"[red]{error_count}E[/red]"
                if warn_count:
                    if finding_str:
                        finding_str += " "
                    finding_str += f"[yellow]{warn_count}W[/yellow]"
                if not finding_str:
                    finding_str = "-"

                row = [
                    case.meta.id,
                    case.meta.name,
                    f"{report.aggregate_score:.1f}",
                ]
                if judge:
                    row.append(f"{tier2.aggregate_score:.1f}" if tier2 else "-")
                row.extend([status, finding_str])
                results_table.add_row(*row)

                if json_output:
                    case_dict: dict[str, Any] = {
                        "case_id": case.meta.id,
                        "passed": comparison.passed,
                        "findings": [
                            {
                                "severity": f.severity.value,
                                "category": f.category.value,
                                "message": f.message,
                            }
                            for f in comparison.findings
                        ],
                        "tier1": report.to_dict(),
                    }
                    if tier2 is not None:
                        case_dict["tier2"] = tier2.to_dict()
                    json_cases.append(case_dict)

            if json_output:
                print(json.dumps(json_cases, indent=2))  # noqa: T201
            else:
                console.print()
                console.print(results_table)
    finally:
        if judge_client is not None:
            judge_client.close()

    if not json_output:
        # Surface per-case finding details for failures.
        for case_id, findings in failed_results:
            console.print(f"\n[red]{case_id}:[/red]")
            for f in findings:
                style = "red" if f.severity == RegressionSeverity.ERROR else "yellow"
                console.print(f"  [{style}][{f.category}][/{style}] {f.message}")

        console.print(
            f"\n{len(cases)} golden cases evaluated"
            + (", all passed" if all_passed else ", regressions detected")
        )

        # Calibration output.
        if calibrate and calibration_data:
            from curator.eval.golden import GOLDEN_DIR_DEFAULT

            apply_dir = (golden_dir or GOLDEN_DIR_DEFAULT) if apply_baselines else None
            _emit_calibration_output(
                console,
                calibration_data,
                margin=BASELINE_MARGIN,
                apply_to_dir=apply_dir,
            )

        # Tier 2 judge calibration output.
        if calibrate and judge and judge_cal_data:
            from curator.eval.golden import GOLDEN_DIR_DEFAULT

            judge_apply_dir = (
                (golden_dir or GOLDEN_DIR_DEFAULT) if apply_baselines else None
            )
            _emit_judge_calibration_output(
                console,
                judge_cal_data,
                apply_to_dir=judge_apply_dir,
            )

    if not all_passed:
        raise typer.Exit(code=1)


def _emit_calibration_output(
    console: Console,
    calibration_data: list[tuple[str, str, float]],
    margin: int,
    *,
    apply_to_dir: Path | None = None,
) -> None:
    """Emit proposed baselines and optionally write them to golden files."""
    import re

    from curator.eval.golden import GOLDEN_DIR_DEFAULT
    from curator.io_utils import atomic_text_write

    console.print("\n[bold]Proposed baselines:[/]\n")

    cal_table = Table(title="Calibration")
    cal_table.add_column("Case", style="cyan")
    cal_table.add_column("Actual", justify="right")
    cal_table.add_column("Baseline min", justify="right")

    golden_dir = apply_to_dir or GOLDEN_DIR_DEFAULT
    updated_count = 0

    for case_id, _name, score in sorted(calibration_data, key=lambda x: x[0]):
        baseline_min = int(score - margin)
        cal_table.add_row(case_id, f"{score:.1f}", str(baseline_min))

        if apply_to_dir is not None:
            golden_file = golden_dir / f"{case_id}.yaml"
            if golden_file.exists():
                content = golden_file.read_text(encoding="utf-8")
                new_block = f"baselines:\n  aggregate_score:\n    min: {baseline_min}"
                # Replace empty baselines or existing baselines block.
                if "baselines: {}" in content:
                    content = content.replace("baselines: {}", new_block)
                else:
                    # Match baselines: and all continuation lines
                    # indented by 2+ spaces (any nesting depth).
                    content = re.sub(
                        r"baselines:\n(?:  +.*\n)*",
                        new_block + "\n",
                        content,
                    )
                atomic_text_write(golden_file, content)
                updated_count += 1

    console.print(cal_table)

    if apply_to_dir is not None:
        console.print(
            f"\n[green]Updated {updated_count} golden files in {golden_dir}[/]"
        )


def _emit_judge_calibration_output(
    console: Console,
    judge_cal_data: list[tuple[str, str, str, dict[str, int]]],
    *,
    apply_to_dir: Path | None = None,
) -> None:
    """Emit Tier 2 judge calibration statistics and optionally apply human_scores."""
    import statistics

    from curator.eval.judge import JUDGE_DIMENSIONS

    console.print("\n[bold]Tier 2 Judge Calibration:[/]\n")

    # Per-dimension summary.
    dim_table = Table(title="Per-Dimension Summary")
    dim_table.add_column("Dimension", style="cyan")
    dim_table.add_column("Mean", justify="right")
    dim_table.add_column("Std", justify="right")
    dim_table.add_column("Min", justify="right")
    dim_table.add_column("Max", justify="right")

    for dim in JUDGE_DIMENSIONS:
        scores = [d[dim] for _, _, _, d in judge_cal_data if dim in d]
        if scores:
            mean = statistics.mean(scores)
            std = statistics.stdev(scores) if len(scores) > 1 else 0.0
            dim_table.add_row(
                dim,
                f"{mean:.1f}",
                f"{std:.1f}",
                str(min(scores)),
                str(max(scores)),
            )

    console.print(dim_table)

    # Per-tier breakdown.
    tier_table = Table(title="Per-Tier Breakdown (mean scores)")
    tier_table.add_column("Tier", style="cyan")
    tier_table.add_column("Cases", justify="right")
    for dim in JUDGE_DIMENSIONS:
        tier_table.add_column(dim[:8], justify="right")

    tiers: dict[str, list[dict[str, int]]] = {
        "strong": [],
        "good": [],
        "moderate": [],
        "poor": [],
    }
    for _case_id, _name, tier, dim_scores in judge_cal_data:
        if tier in tiers:
            tiers[tier].append(dim_scores)

    for tier_name, tier_cases in tiers.items():
        if not tier_cases:
            continue
        row = [tier_name, str(len(tier_cases))]
        for dim in JUDGE_DIMENSIONS:
            scores = [c[dim] for c in tier_cases if dim in c]
            if scores:
                row.append(f"{statistics.mean(scores):.1f}")
            else:
                row.append("-")
        tier_table.add_row(*row)

    console.print()
    console.print(tier_table)

    # Batch token summary.
    # $0.05 per eval is an approximation based on Sonnet 4.6 pricing with
    # ~15k input tokens (rubric + resume data) and ~1k output tokens.
    # Actual cost varies with prompt caching hits and token counts.
    total_cases = len(judge_cal_data)
    console.print(
        f"\n{total_cases} cases scored by judge "
        f"(~${total_cases * 0.05:.2f} estimated cost)"
    )

    # Apply: write judge scores as human_scores into golden YAML files.
    # Uses text-based insertion (not YAML roundtrip) to preserve formatting.
    if apply_to_dir is not None:
        import re

        from curator.io_utils import atomic_text_write

        updated = 0
        for case_id, _name, _tier, dim_scores in judge_cal_data:
            golden_file = apply_to_dir / f"{case_id}.yaml"
            if not golden_file.exists():
                continue
            content = golden_file.read_text(encoding="utf-8")

            # Build the human_scores YAML block.
            lines = ["human_scores:"]
            lines.extend(
                f"  {dim}: {dim_scores[dim]}"
                for dim in JUDGE_DIMENSIONS
                if dim in dim_scores
            )
            new_block = "\n".join(lines)

            # Replace empty or existing human_scores block.
            if "human_scores: {}" in content:
                content = content.replace("human_scores: {}", new_block)
            else:
                content = re.sub(
                    r"human_scores:\n(?:  +.*\n)*",
                    new_block + "\n",
                    content,
                )
            atomic_text_write(golden_file, content)
            updated += 1

        console.print(
            f"\n[green]Updated human_scores in {updated} golden files "
            f"in {apply_to_dir}[/]"
        )


def _display_eval_report(
    console: Console,
    title: str,
    report: EvalReport,
) -> None:
    """Display an eval report as Rich tables."""
    from curator.eval.report import EvalMetricStatus

    # Category summary table.
    cat_table = Table(
        title=(
            f"Eval Report — {title} "
            f"(score: {report.aggregate_score:.0f}, "
            f"status: {report.status.name})"
        ),
    )
    cat_table.add_column("Category", style="cyan")
    cat_table.add_column("Score", justify="right")
    cat_table.add_column("Status")
    cat_table.add_column("Weight", justify="right")

    for cat in sorted(report.categories, key=lambda c: c.score):
        status_style = {
            EvalMetricStatus.PASS: "green",
            EvalMetricStatus.WARN: "yellow",
            EvalMetricStatus.FAIL: "red",
        }[cat.status]
        cat_table.add_row(
            cat.name,
            f"{cat.score:.0f}",
            f"[{status_style}]{cat.status.name}[/{status_style}]",
            f"{cat.weight:.0%}",
        )
    console.print(cat_table)

    # Metric detail table — FAIL/WARN only by default.
    show_pass = logger.level("DEBUG").no <= 10  # Verbose mode.
    detail_metrics = [
        m for m in report.metrics if show_pass or m.status != EvalMetricStatus.PASS
    ]

    if detail_metrics:
        console.print()
        detail_table = Table(title="Metric Details")
        detail_table.add_column("Metric", style="cyan")
        detail_table.add_column("Status")
        detail_table.add_column("Value", justify="right")
        detail_table.add_column("Detail")

        for m in detail_metrics:
            status_style = {
                EvalMetricStatus.PASS: "green",
                EvalMetricStatus.WARN: "yellow",
                EvalMetricStatus.FAIL: "red",
            }[m.status]
            detail_table.add_row(
                m.name,
                f"[{status_style}]{m.status.name}[/{status_style}]",
                str(m.value) if m.value is not None else "-",
                m.detail[:80] if m.detail else "",
            )
        console.print(detail_table)

    # Summary line.
    console.print(
        f"\n{len(report.metrics)} metrics evaluated, "
        f"aggregate: {report.aggregate_score:.0f}/100 "
        f"({report.status.name})"
    )


def _run_judge(
    ctx: EvalContext,
    settings: CuratorSettings,
    *,
    client: anthropic.Anthropic | None = None,
) -> Tier2Report:
    """Run Tier 2 LLM judge evaluation."""
    from curator.eval.judge import evaluate_tier2

    return evaluate_tier2(ctx, settings=settings, client=client)


def _display_tier2_report(console: Console, tier2: Tier2Report) -> None:
    """Display a Tier 2 judge report as a Rich table."""
    console.print()
    table = Table(
        title=(
            f"Tier 2 LLM Judge "
            f"(score: {tier2.aggregate_score:.0f}, "
            f"model: {tier2.model})"
        ),
    )
    table.add_column("Dimension", style="cyan")
    table.add_column("Group")
    table.add_column("Score", justify="right")
    table.add_column("Justification")

    for d in tier2.dimensions:
        # Color score: 4-5 green, 3 yellow, 1-2 red.
        if d.score >= 4:
            score_str = f"[green]{d.score}[/green]"
        elif d.score >= 3:
            score_str = f"[yellow]{d.score}[/yellow]"
        else:
            score_str = f"[red]{d.score}[/red]"

        table.add_row(
            d.name,
            d.group.replace("_", " "),
            score_str,
            d.justification[:80] if d.justification else "",
        )

    console.print(table)

    console.print(
        f"\n{len(tier2.dimensions)} dimensions, "
        f"aggregate: {tier2.aggregate_score:.0f}/100 "
        f"(tokens: {tier2.input_tokens:,} in, {tier2.output_tokens:,} out)"
    )
