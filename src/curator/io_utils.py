"""Shared I/O utilities for resume-curator.

Atomic file writes, YAML safe loading, PDF page counting, slug/sort
helpers promoted from private helpers in ``renderer.py`` and ``loader.py``
for reuse across the curation pipeline and evaluation framework.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from pypdf import PdfReader

from curator.exceptions import CuratorError, RenderError
from curator.models import ID_PATTERN
from curator.rules import CORPORATE_SLUG_SUFFIXES

# ---------------------------------------------------------------------------
# Size guards
# ---------------------------------------------------------------------------

MAX_YAML_SIZE: int = 1_048_576  # 1 MiB — generous for portfolio/golden data
MAX_PDF_SIZE: int = 10 * 1024 * 1024  # 10 MB safety guard
MAX_TEXT_SIZE: int = 1_048_576  # 1 MiB — for JSON logs, JD text, templates

# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def load_yaml_safe(path: Path) -> Any:
    """Load a YAML file with safe_load and a size guard.

    Args:
        path: Path to the YAML file.

    Returns:
        The parsed YAML content (dict, list, or None for empty files).

    Raises:
        OSError: If the file cannot be read.
        ValueError: If the file exceeds the size limit.
        yaml.YAMLError: If the YAML is malformed.
    """
    logger.debug("Loading {}", path.name)
    content = path.read_text(encoding="utf-8")

    if len(content) > MAX_YAML_SIZE:
        msg = f"YAML file exceeds size limit ({MAX_YAML_SIZE} bytes): {path.name}"
        raise ValueError(msg)

    return yaml.safe_load(content)


# ---------------------------------------------------------------------------
# Atomic file write helpers
# ---------------------------------------------------------------------------


def atomic_yaml_write(path: Path, data: Any) -> None:
    """Write YAML data atomically to the given path."""
    content = yaml.dump(
        data,
        Dumper=yaml.SafeDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    atomic_text_write(path, content)


def atomic_json_write(path: Path, data: Any) -> None:
    """Write JSON data atomically to the given path."""
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    atomic_text_write(path, content)


def atomic_text_write(path: Path, content: str) -> None:
    """Write text content atomically using tempfile + os.replace.

    The temp file is created in the same directory as the target to ensure
    the rename is atomic (same filesystem).

    Resulting permissions: 0o644. ``tempfile.NamedTemporaryFile`` defaults
    to 0o600, which would silently mark every YAML/JSON/TXT artifact as
    owner-only-readable. That is inconsistent with the 0o644 PDFs Typst
    writes and surprising for tooling that walks the profile dir. We use
    ``os.fchmod`` on the open fd (not a umask read/restore dance) so
    there is no process-wide side effect. The brief existence of the
    ``.tmp`` file at 0o644 is acceptable: parent directory permissions
    already gate access where it matters (the log dir is 0o700; see
    ``cli.py``).
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    fd = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="w",
        dir=parent,
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    )
    try:
        fd.write(content)
        fd.flush()
        os.fsync(fd.fileno())
        os.fchmod(fd.fileno(), 0o644)
        fd.close()
        Path(fd.name).replace(path)
    except BaseException:
        # Close fd first to release the handle, then clean up temp file.
        # Calling close() on an already-closed fd is a safe no-op.
        fd.close()
        Path(fd.name).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Typst compilation
# ---------------------------------------------------------------------------

_TYPST_TIMEOUT: int = 30  # seconds


def compile_typst(
    root_dir: Path,
    template_path: Path,
    output_path: Path,
    *,
    timeout: int = _TYPST_TIMEOUT,
) -> None:
    """Run Typst compilation.

    Args:
        root_dir: Directory used as ``--root`` for Typst path resolution.
        template_path: Path to the ``.typ`` template (must be under *root_dir*).
        output_path: Destination path for the compiled PDF.
        timeout: Maximum compilation time in seconds.

    Raises:
        FileNotFoundError: If Typst is not installed or not on PATH.
        subprocess.TimeoutExpired: If compilation exceeds *timeout*.
        CuratorError: If Typst exits with a non-zero return code.
    """
    cmd = [
        "typst",
        "compile",
        "--root",
        str(root_dir),
        str(template_path),
        str(output_path),
    ]
    logger.debug("Typst compile: {} -> {}", template_path.name, output_path.name)
    # S603 safe: list-form args (no shell=True), all paths from controlled
    # sources (validated profile directories and project-owned templates).
    result = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        msg = f"Typst compilation failed (exit {result.returncode}): {stderr}"
        raise CuratorError(msg)


# ---------------------------------------------------------------------------
# PDF utilities
# ---------------------------------------------------------------------------


def get_page_count(pdf_path: Path) -> int:
    """Extract page count from a compiled PDF.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Number of pages in the PDF.

    Raises:
        RenderError: If the PDF exceeds the size guard or cannot be read.
    """
    try:
        size = pdf_path.stat().st_size
    except OSError as e:
        msg = f"Failed to read PDF for page count: {pdf_path}"
        raise RenderError(msg) from e
    if size > MAX_PDF_SIZE:
        msg = f"PDF exceeds expected size limit: {size} bytes"
        raise RenderError(msg)
    try:
        pages = len(PdfReader(pdf_path).pages)
    except Exception as e:
        msg = f"Failed to read PDF for page count: {pdf_path}"
        raise RenderError(msg) from e
    logger.debug("PDF page count: {} ({} bytes)", pages, size)
    return pages


# ---------------------------------------------------------------------------
# Sorting and slugging helpers
# ---------------------------------------------------------------------------

_SLUGIFY_INPUT_CAP: int = 256
_SLUG_PATTERN_RE = re.compile(ID_PATTERN)
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


def parse_partial_date(raw: Any) -> tuple[int, int]:
    """Parse a portfolio date string into a ``(year, month)`` tuple for sorting.

    Accepts ``YYYY``, ``YYYY-M``, ``YYYY-MM``, ``YYYY-MM-DD``, as well as
    integer years and empty/``None`` values. Returns ``(0, 0)`` for
    anything unparseable so that empty/malformed dates sort as
    oldest-first (they still end up after real dates under ``reverse=True``
    because the rest of the values are larger).

    Using a numeric tuple instead of a lexicographic string compare
    avoids bugs on non-zero-padded months (``2022-6`` would otherwise
    sort after ``2022-12``).
    """
    if raw is None or raw == "":
        return (0, 0)
    s = str(raw).strip()
    if not s:
        return (0, 0)
    parts = s.split("-", 2)
    try:
        year = int(parts[0])
    except ValueError:
        return (0, 0)
    month = 0
    if len(parts) > 1 and parts[1]:
        try:
            month = int(parts[1])
        except ValueError:
            month = 0
    return (year, month)


def sort_work_chronologically(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return work entries in reverse chronological order.

    Current roles (no ``end_date``) come first, ordered by ``start_date``
    descending. Past roles follow, ordered by ``end_date`` descending
    (then ``start_date`` descending as a tiebreaker).

    Sort keys are numeric ``(year, month)`` tuples parsed via
    :func:`parse_partial_date` to handle non-zero-padded month inputs
    correctly.

    Lives in :mod:`curator.io_utils` (not the renderer) because both
    :mod:`curator.renderer` (for the trim cascade's per-position
    floors) and :mod:`curator.client._adapt_curation_dict` (for the
    per-entry highlight emit cap) need the same chronological-position
    convention. Keeping the helper here ensures the cap and the
    cascade speak the same coordinates.
    """
    current: list[dict[str, Any]] = []
    past: list[dict[str, Any]] = []
    for entry in entries:
        end_date = entry.get("end_date") or ""
        if end_date:
            past.append(entry)
        else:
            current.append(entry)
    current.sort(
        key=lambda e: parse_partial_date(e.get("start_date")),
        reverse=True,
    )
    past.sort(
        key=lambda e: (
            parse_partial_date(e.get("end_date")),
            parse_partial_date(e.get("start_date")),
        ),
        reverse=True,
    )
    return current + past


def priority_sort_key(entry: Any, field_name: str = "priority") -> tuple[int, int]:
    """Sort key: entries with *field_name* set first (ascending), then unset.

    Works on both Pydantic models (attribute access) and plain dicts. Used by
    the renderer to sort certificates/education by ``priority`` and by static
    mode to sort projects by ``weight``.

    Args:
        entry: A dict or object with the sort field as attribute/key.
        field_name: Attribute/key name to look up (``priority`` or ``weight``).

    Returns:
        ``(0, value)`` when set so values sort ascending, ``(1, 0)`` otherwise
        so unset entries trail. Python's stable sort preserves portfolio order
        on ties.
    """
    if isinstance(entry, dict):
        value = entry.get(field_name)
    else:
        value = getattr(entry, field_name, None)
    if value is not None:
        return (0, value)
    return (1, 0)


def slugify(
    name: str,
    *,
    fallback: str = "general",
    max_length: int = 64,
) -> str:
    """Convert free text to kebab-case matching ``models.ID_PATTERN``.

    Lowercases, replaces non-alphanumerics with ``-``, collapses runs,
    strips leading/trailing ``-``, removes trailing legal-entity
    suffixes (``CORPORATE_SLUG_SUFFIXES``: inc, llc, ltd, gmbh, pbc)
    iteratively, then truncates to *max_length*. Raw input is capped
    at 256 chars before regex work to avoid pathological inputs.
    Returns *fallback* when the result is empty or does not match
    ``ID_PATTERN``.

    Suffix stripping is trailing-only: ``Acme Inc`` -> ``acme`` but
    ``Inc Magazine`` -> ``inc-magazine`` (the leading ``inc`` is not
    a legal-entity marker here). Multi-suffix tails strip iteratively:
    ``Acme LLC Inc`` -> ``acme``. Pure-suffix input
    (``slugify("LLC")``) drops to empty and returns *fallback*.

    Args:
        name: Free-text input.
        fallback: Value returned when the result is empty or invalid.
        max_length: Maximum output length (default 64 matches the
            ``company_slug`` field constraint).

    Returns:
        A slug matching ``ID_PATTERN`` (``^[a-z0-9][a-z0-9-]*$``).
    """
    capped = name[:_SLUGIFY_INPUT_CAP]
    slug = _NON_SLUG_CHARS_RE.sub("-", capped.lower()).strip("-")

    # Strip trailing legal-entity suffixes iteratively so multi-suffix
    # tails ("Acme LLC Inc") collapse to the brand. Done after the
    # kebab cast so the token boundary is unambiguous, and before the
    # length cap so a short brand under a long stripped tail still
    # uses the full budget for the brand itself.
    if "-" in slug:
        parts = slug.split("-")
        while parts and parts[-1] in CORPORATE_SLUG_SUFFIXES:
            parts.pop()
        slug = "-".join(parts)
    elif slug in CORPORATE_SLUG_SUFFIXES:
        slug = ""

    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    if not slug or not _SLUG_PATTERN_RE.match(slug):
        return fallback
    return slug
