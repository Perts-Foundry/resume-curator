"""Shared test fixtures for resume-curator."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

# ---------------------------------------------------------------------------
# Typst availability
# ---------------------------------------------------------------------------

TYPST_AVAILABLE: bool = shutil.which("typst") is not None

# ---------------------------------------------------------------------------
# Snap-safe temp directory for Typst compilation
# ---------------------------------------------------------------------------

# Snap-installed Typst cannot access /tmp. This fixture creates a temp
# directory under $HOME/.cache/ which snap can access.
_GOLDEN_CACHE_BASE = Path.home() / ".cache" / "curator-golden-tests"


@pytest.fixture
def typst_safe_dir() -> Generator[Path, None, None]:
    """Temp directory accessible to both regular and snap-confined Typst."""
    _GOLDEN_CACHE_BASE.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(dir=_GOLDEN_CACHE_BASE))
    yield work_dir
    shutil.rmtree(work_dir, ignore_errors=True)
