"""Tests for ``scripts/ci/check_prompt_version.py``.

Two layers of coverage:

  - **Pure-function truth table** for :func:`check`: exercises the four
    (hash same/different) x (version same/different) combinations
    without any git plumbing.
  - **Helpers** for ``hash_blob``, ``extract_system_prompt_literal``,
    and ``extract_prompt_version``: confirm the regex-based extraction
    behaves correctly on actual source-shaped text.

The git-plumbing entry point (``main``) is intentionally not unit
tested with a real init'd repo; it is exercised by the CI run itself
on every PR. Adding a tmp_path + git init smoke test would duplicate
that signal.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci" / "check_prompt_version.py"


def _load_module() -> object:
    """Import the script as a module without going through the package init.

    The ``scripts/ci`` directory has an empty ``__init__.py`` to keep
    tooling happy, but the script itself is meant to be invoked
    directly (``uv run python scripts/ci/check_prompt_version.py``).
    Loading it via :mod:`importlib.util` here matches that runtime
    convention rather than relying on package import resolution.
    """
    spec = importlib.util.spec_from_file_location(
        "_check_prompt_version_under_test", SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestCheckTruthTable:
    """Pure-function (no git) truth table for ``check``."""

    def test_no_drift_no_bump_passes(self) -> None:
        mod = _load_module()
        assert mod.check("abc", "abc", "v1", "v1") == 0

    def test_bump_without_drift_passes(self) -> None:
        """Bumping the version without changing the prompt is legal
        (e.g., the author is acknowledging an upcoming change or
        recovering from a missed bump on a prior PR)."""
        mod = _load_module()
        assert mod.check("abc", "abc", "v1", "v2") == 0

    def test_drift_with_bump_passes(self) -> None:
        mod = _load_module()
        assert mod.check("abc", "def", "v1", "v2") == 0

    def test_drift_without_bump_fails(self) -> None:
        mod = _load_module()
        assert mod.check("abc", "def", "v1", "v1") == 1


class TestExtractPromptVersion:
    """Regex extraction of ``PROMPT_VERSION`` from prompt.py source."""

    def test_extracts_simple_assignment(self) -> None:
        mod = _load_module()
        src = 'PROMPT_VERSION: str = "2026-05-21"\n'
        assert mod.extract_prompt_version(src) == "2026-05-21"

    def test_returns_empty_on_missing(self) -> None:
        """Missing constant -> empty string (not an exception). Lets
        callers compare empty-vs-empty cleanly when comparing across
        revisions where the constant was added or removed."""
        mod = _load_module()
        assert mod.extract_prompt_version("# no version here\n") == ""

    def test_anchored_to_line_start(self) -> None:
        """A nested or commented-out occurrence shouldn't be picked up."""
        mod = _load_module()
        src = '# PROMPT_VERSION: str = "old-version"\nPROMPT_VERSION: str = "real"\n'
        assert mod.extract_prompt_version(src) == "real"


class TestExtractSystemPromptLiteral:
    """Regex extraction of ``_SYSTEM_PROMPT_TEXT`` body."""

    def test_extracts_short_literal(self) -> None:
        mod = _load_module()
        src = '_SYSTEM_PROMPT_TEXT = """hello world"""\n'
        assert mod.extract_system_prompt_literal(src) == "hello world"

    def test_extracts_multiline_literal(self) -> None:
        mod = _load_module()
        src = '_SYSTEM_PROMPT_TEXT = """\nline one\nline two\n"""\n'
        assert mod.extract_system_prompt_literal(src) == "\nline one\nline two\n"

    def test_returns_empty_on_missing(self) -> None:
        mod = _load_module()
        assert mod.extract_system_prompt_literal("# no prompt here\n") == ""

    def test_first_match_wins(self) -> None:
        """``prompt.py`` reassigns ``_SYSTEM_PROMPT_TEXT`` via
        ``.format()`` after the literal definition. The extractor must
        capture the first literal (the source body) and not the
        post-format reassignment."""
        mod = _load_module()
        src = (
            '_SYSTEM_PROMPT_TEXT = """raw body with {placeholder}"""\n'
            "_SYSTEM_PROMPT_TEXT = _SYSTEM_PROMPT_TEXT.format(placeholder='X')\n"
        )
        assert mod.extract_system_prompt_literal(src) == "raw body with {placeholder}"


class TestHashBlob:
    """Hash combines system-prompt literal + rules.py content."""

    def test_identical_inputs_identical_hash(self) -> None:
        mod = _load_module()
        prompt = '_SYSTEM_PROMPT_TEXT = """body"""\n'
        rules = "MIN = 0.5\n"
        assert mod.hash_blob(prompt, rules) == mod.hash_blob(prompt, rules)

    def test_prompt_change_changes_hash(self) -> None:
        mod = _load_module()
        rules = "MIN = 0.5\n"
        a = mod.hash_blob('_SYSTEM_PROMPT_TEXT = """body A"""\n', rules)
        b = mod.hash_blob('_SYSTEM_PROMPT_TEXT = """body B"""\n', rules)
        assert a != b

    def test_rules_change_changes_hash(self) -> None:
        """rules.py edits flow into the prompt via .format() so changes
        must invalidate the hash."""
        mod = _load_module()
        prompt = '_SYSTEM_PROMPT_TEXT = """body"""\n'
        a = mod.hash_blob(prompt, "MIN = 0.5\n")
        b = mod.hash_blob(prompt, "MIN = 0.7\n")
        assert a != b

    def test_cover_letter_block_excluded(self) -> None:
        """Cover-letter-only edits must NOT change the hash so the gate
        does not fire on cover-letter edits (per policy)."""
        mod = _load_module()
        rules = "MIN = 0.5\n"
        prompt_a = (
            '_SYSTEM_PROMPT_TEXT = """body"""\n'
            '_COVER_LETTER_PROMPT_BLOCK = """cover A"""\n'
        )
        prompt_b = (
            '_SYSTEM_PROMPT_TEXT = """body"""\n'
            '_COVER_LETTER_PROMPT_BLOCK = """cover B"""\n'
        )
        assert mod.hash_blob(prompt_a, rules) == mod.hash_blob(prompt_b, rules)


class TestActualSource:
    """End-to-end on the live prompt.py and rules.py.

    Confirms the regexes match the real source shape. If prompt.py is
    ever refactored such that the system-prompt or PROMPT_VERSION
    source shapes change, these tests fail loudly so the regex stays
    in sync.
    """

    def test_extracts_real_prompt_version(self) -> None:
        mod = _load_module()
        prompt_py = (REPO_ROOT / "src/curator/prompt.py").read_text()
        version = mod.extract_prompt_version(prompt_py)
        assert version, "expected non-empty PROMPT_VERSION"
        # Sanity: should look like a date string.
        assert "-" in version

    def test_extracts_real_system_prompt_literal(self) -> None:
        mod = _load_module()
        prompt_py = (REPO_ROOT / "src/curator/prompt.py").read_text()
        literal = mod.extract_system_prompt_literal(prompt_py)
        assert literal, "expected non-empty _SYSTEM_PROMPT_TEXT literal"
        # The literal carries .format placeholders; sanity-check one
        # of the known interpolations stays visible after extraction.
        assert "{weak_phrases}" in literal

    def test_real_hash_deterministic(self) -> None:
        mod = _load_module()
        prompt_py = (REPO_ROOT / "src/curator/prompt.py").read_text()
        rules_py = (REPO_ROOT / "src/curator/rules.py").read_text()
        h1 = mod.hash_blob(prompt_py, rules_py)
        h2 = mod.hash_blob(prompt_py, rules_py)
        assert h1 == h2
        assert len(h1) == 12  # first 12 hex chars of sha256
