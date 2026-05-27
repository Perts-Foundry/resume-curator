r"""Copy upload-ready artifacts from a profile to a destination directory.

The publish step exists because Windows browser file pickers refuse to
upload from ``\\wsl.localhost\...`` paths under Chromium's blocked-paths
policy (the error reads "this folder contains system files" but the
trigger is the UNC path, not file attributes). Copying the PDFs onto
the Windows drive sidesteps that.

Single source of truth for "what counts as an upload-ready artifact" is
:data:`curator.renderer.RENDER_PUBLISH_FILENAMES` -- co-located with
the writer that produces them, so adding a new shipped file is a
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
    (preserves mtime). Files that don't exist in the source are skipped
    silently -- the cover letter is optional, so a curate run without
    ``--cover-letter`` publishes only ``resume.pdf``.

    Existing destination files are overwritten; each overwrite logs at
    INFO so an accidental clobber (e.g. publishing a hand-renamed profile
    onto a previous run's output) is visible.

    Args:
        profile_dir: Source profile directory (e.g.
            ``profiles/2026-05-27-acme``). Need not exist as a renderer
            output -- only the filenames in :data:`RENDER_PUBLISH_FILENAMES`
            are looked up.
        destination: Publish root. ``~`` is expanded. Files land under a
            per-profile subdirectory ``<destination>/<profile_dir.name>/``
            so multiple publishes coexist without collision.

    Returns:
        The list of destination paths actually written, in the order of
        :data:`RENDER_PUBLISH_FILENAMES`.

    Raises:
        PublishError: If the destination root cannot be created.
    """
    dest_root = destination.expanduser() / profile_dir.name
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
        shutil.copy2(src, dst)
        copied.append(dst)

    logger.info("publish: copied {} file(s) to {}", len(copied), dest_root)
    return copied
