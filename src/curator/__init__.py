"""resume-curator package."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def default_template_path() -> Path:
    """Return the on-disk path of the bundled Typst template.

    Resolves via :mod:`importlib.resources` so the lookup works for both
    editable installs and built wheels without walking ``__file__``.
    """
    resource = files("curator") / "templates" / "curated.typ"
    return Path(str(resource))


def default_cover_letter_template_path() -> Path:
    """Return the on-disk path of the bundled cover letter Typst template."""
    resource = files("curator") / "templates" / "cover_letter.typ"
    return Path(str(resource))


__all__ = ["default_cover_letter_template_path", "default_template_path"]
