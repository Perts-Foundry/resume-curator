"""Fail CI when the curator system prompt drifts without a PROMPT_VERSION bump.

Compares ``PROMPT_VERSION`` and the system-prompt content (the literal
``_SYSTEM_PROMPT_TEXT`` string plus the full ``rules.py`` text whose
constants are ``.format()``-substituted into the prompt) between the
merge base of the current branch against ``origin/main`` and the
working tree. Exits non-zero when system-prompt content changed but
``PROMPT_VERSION`` did not.

Cover-letter-only edits are intentionally NOT gated by this check, per
the policy documented at :data:`curator.prompt.PROMPT_VERSION`. The
cover-letter rulebook lives in ``_COVER_LETTER_PROMPT_BLOCK`` and is
hashed separately into ``COVER_LETTER_PROMPT_HASH`` for audit but does
not require a version bump.

Design choices:

- **No imports of curator.prompt.** That module runs validators at
  import time and depends on sibling modules (``curator.models``,
  ``curator.rules``) at the imported revision. Importing the
  merge-base revision via subprocess is fragile and slow; reading the
  raw file text via ``git show`` is fast and robust.
- **Pure ``check`` function for unit-testability.** The git plumbing
  is kept separate so the comparison logic itself is exercised by a
  truth-table unit test without an init'd git repo.
- **Rules-file inclusion is over-conservative on purpose.** Any change
  to ``rules.py`` triggers the gate, even when the changed constant
  is not interpolated into the prompt. The false-positive rate is
  acceptable; the alternative (parsing which constants flow into the
  prompt) is brittle and silently misses new constants added between
  source and renderer.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SYSTEM_PROMPT_RE = re.compile(
    r'^_SYSTEM_PROMPT_TEXT = """(?P<body>.*?)"""\s*$',
    re.DOTALL | re.MULTILINE,
)
PROMPT_VERSION_RE = re.compile(
    r'^PROMPT_VERSION: str = "(?P<version>[^"]+)"\s*$',
    re.MULTILINE,
)


def check(
    old_system_hash: str,
    new_system_hash: str,
    old_version: str,
    new_version: str,
) -> int:
    """Return 0 (pass) or 1 (fail).

    Fails when the system-prompt blob hash changed and PROMPT_VERSION
    did not. The four-cell truth table:

    +------------------+----------+--------+----------+
    | hash changed?    | version  | result | meaning  |
    |                  | changed? |        |          |
    +==================+==========+========+==========+
    | no               | no       | pass   | no-op    |
    +------------------+----------+--------+----------+
    | no               | yes      | pass   | version  |
    |                  |          |        | bump w/o |
    |                  |          |        | content  |
    |                  |          |        | drift    |
    |                  |          |        | (legit)  |
    +------------------+----------+--------+----------+
    | yes              | yes      | pass   | drift +  |
    |                  |          |        | bump     |
    +------------------+----------+--------+----------+
    | yes              | no       | FAIL   | drift    |
    |                  |          |        | without  |
    |                  |          |        | bump     |
    +------------------+----------+--------+----------+
    """
    if old_system_hash == new_system_hash:
        return 0
    if old_version != new_version:
        return 0
    return 1


def extract_system_prompt_literal(prompt_py_text: str) -> str:
    """Extract the raw ``_SYSTEM_PROMPT_TEXT`` literal from prompt.py source.

    Matches the first triple-quoted string assigned to
    ``_SYSTEM_PROMPT_TEXT`` (before the ``.format()`` reassignment).
    Returns an empty string when the marker is absent (e.g., the file
    was renamed at this revision); a stable empty value is preferable
    to raising because callers compare hashes and a missing block is
    a meaningful "no prompt here" state.
    """
    match = SYSTEM_PROMPT_RE.search(prompt_py_text)
    if match is None:
        return ""
    return match.group("body")


def extract_prompt_version(prompt_py_text: str) -> str:
    """Extract the ``PROMPT_VERSION`` string literal from prompt.py source."""
    match = PROMPT_VERSION_RE.search(prompt_py_text)
    if match is None:
        return ""
    return match.group("version")


def hash_blob(prompt_py_text: str, rules_py_text: str) -> str:
    """Hash the system-prompt content for change detection.

    Combines:
      - The raw ``_SYSTEM_PROMPT_TEXT`` literal (pre-``.format()``)
      - The full ``rules.py`` content (whose constants flow into the
        prompt via ``.format()``)

    Cover-letter content is intentionally excluded so cover-letter-only
    edits do not trip the version-bump gate.
    """
    system_literal = extract_system_prompt_literal(prompt_py_text)
    combined = (system_literal + rules_py_text).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()[:12]


def _git_show(ref: str, path: str) -> str:
    """Return ``git show <ref>:<path>`` or empty string on failure.

    Empty-on-failure is intentional: a missing file at the merge base
    (e.g., the file was added on this branch) should compare equal to
    "no system prompt" rather than crash the check.
    """
    # Subprocess args are constants/local-context; calling `git` on PATH
    # in CI is intentional. Suppress S603/S607.
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "show", f"{ref}:{path}"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def _git_merge_base(other_ref: str) -> str | None:
    """Return the merge-base SHA against ``other_ref`` or ``None`` on failure."""
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "merge-base", "HEAD", other_ref],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on pass, 1 on drift-without-bump."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base",
        default="origin/main",
        help=(
            "Git ref to compare against (default: origin/main). The "
            "merge-base of HEAD against this ref is the comparison "
            "point. CI checkouts must use fetch-depth: 0 (or fetch "
            "the base ref explicitly) for the merge-base to resolve."
        ),
    )
    args = parser.parse_args(argv)

    merge_base = _git_merge_base(args.base)
    if merge_base is None:
        # Fail loud so a misconfigured CI run is visible. A shallow
        # checkout that cannot resolve the merge base must surface
        # rather than silently pass.
        print(
            f"error: could not resolve `git merge-base HEAD {args.base}`. "
            "Ensure the CI checkout uses fetch-depth: 0.",
            file=sys.stderr,
        )
        return 2

    old_prompt = _git_show(merge_base, "src/curator/prompt.py")
    old_rules = _git_show(merge_base, "src/curator/rules.py")
    new_prompt = (REPO_ROOT / "src/curator/prompt.py").read_text(encoding="utf-8")
    new_rules = (REPO_ROOT / "src/curator/rules.py").read_text(encoding="utf-8")

    old_hash = hash_blob(old_prompt, old_rules)
    new_hash = hash_blob(new_prompt, new_rules)
    old_version = extract_prompt_version(old_prompt)
    new_version = extract_prompt_version(new_prompt)

    result = check(old_hash, new_hash, old_version, new_version)
    if result != 0:
        print(
            "error: system-prompt content changed without bumping "
            "PROMPT_VERSION.\n"
            f"  merge-base: {merge_base}\n"
            f"  old hash:   {old_hash}\n"
            f"  new hash:   {new_hash}\n"
            f"  old PROMPT_VERSION: {old_version!r}\n"
            f"  new PROMPT_VERSION: {new_version!r}\n"
            "Bump PROMPT_VERSION in src/curator/prompt.py to acknowledge "
            "the drift. Cover-letter-only edits are exempt; see "
            "COVER_LETTER_PROMPT_HASH for the audit trail.",
            file=sys.stderr,
        )
    return result


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
