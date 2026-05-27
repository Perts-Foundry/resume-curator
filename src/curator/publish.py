r"""Copy upload-ready artifacts from a profile to a destination directory.

The publish step exists because Windows browser file pickers refuse to
upload from ``\\wsl.localhost\...`` paths under Chromium's blocked-paths
policy (the error reads "this folder contains system files" but the
trigger is the UNC path, not file attributes). Copying the PDFs onto
the Windows drive sidesteps that.

Single source of truth for "what counts as an upload-ready artifact" is
:data:`curator.renderer.RENDER_PUBLISH_FILENAMES`, co-located with
the writer that produces them so adding a new shipped file is a
one-diff change. This module is a leaf (nothing in ``src/curator/``
imports it back), so importing from ``renderer`` is cycle-free.
"""

from __future__ import annotations

import shutil
from pathlib import Path  # noqa: TC003 -- runtime use in signature

from loguru import logger

from curator.exceptions import PublishError
from curator.renderer import RENDER_PUBLISH_FILENAMES


def publish_artifacts(profile_dir: Path, destination: Path) -> list[Path]:
    """Copy upload-ready files from ``profile_dir`` into ``destination``.

    Files listed in :data:`RENDER_PUBLISH_FILENAMES` are copied into
    ``<destination>/<profile_dir.name>/`` using :func:`shutil.copy2`
    with ``follow_symlinks=False`` (preserves mtime; does not chase
    symlinks). Files that don't exist in the source are skipped
    silently because the cover letter is optional, so a curate run
    without ``--cover-letter`` publishes only ``resume.pdf``.

    Existing destination files are overwritten; each overwrite logs at
    INFO so an accidental clobber (e.g. publishing a hand-renamed profile
    onto a previous run's output) is visible.

    Args:
        profile_dir: Source profile directory (e.g.
            ``profiles/2026-05-27-acme``). Must be an existing directory;
            only the filenames in :data:`RENDER_PUBLISH_FILENAMES` are
            looked up.
        destination: Publish root. ``~`` is expanded. Files land under a
            per-profile subdirectory ``<destination>/<profile_dir.name>/``
            so multiple publishes coexist without collision.

    Returns:
        The list of destination paths actually written, in the order of
        :data:`RENDER_PUBLISH_FILENAMES`.

    Raises:
        PublishError: If ``profile_dir`` is not a directory, if
            ``profile_dir.name`` would escape the destination root
            (e.g. ``..`` or ``.``), or if the destination cannot be
            created.
    """
    if not profile_dir.is_dir():
        msg = f"Source profile directory not found: {profile_dir.resolve()}"
        raise PublishError(msg)

    # Guard against profile names that would escape the publish root
    # (e.g. ``Path("..").name == ".."``). Resolve the candidate and
    # verify it lives under the destination root. Mirrors the
    # is_relative_to guard pattern used in renderer.py.
    dest_base = destination.expanduser().resolve()
    candidate = (dest_base / profile_dir.name).resolve()
    if not candidate.is_relative_to(dest_base):
        msg = (
            f"Refusing to publish: profile name {profile_dir.name!r} would "
            f"write outside the destination {dest_base}"
        )
        raise PublishError(msg)
    dest_root = candidate

    try:
        dest_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = f"Cannot create publish destination {dest_root}: {exc}"
        raise PublishError(msg) from exc

    copied: list[Path] = []
    for filename in RENDER_PUBLISH_FILENAMES:
        src = profile_dir / filename
        if not src.is_file():
            continue
        dst = dest_root / filename
        if dst.exists():
            logger.info("publish: overwriting existing {}", dst)
        # follow_symlinks=False: defense in depth so a symlink in the
        # source profile dir cannot redirect the copy to an off-target
        # file. The renderer never writes symlinks; this guards against
        # operator-error in hand-edited profiles.
        shutil.copy2(src, dst, follow_symlinks=False)
        copied.append(dst)

    logger.info("publish: copied {} file(s) to {}", len(copied), dest_root)
    return copied
