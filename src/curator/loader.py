"""YAML portfolio loader.

Loads and validates portfolio data from the configured portfolio source
directory (see ``CURATOR_PORTFOLIO_PATH``). Each YAML file under ``data/``
maps to a Pydantic model for validation, then all sections are assembled
into a ``PortfolioData`` dataclass. See ``docs/portfolio-schema.md`` for
the directory layout and per-section schema.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

import yaml
from loguru import logger
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from pathlib import Path

from curator.exceptions import (
    PortfolioNotFoundError,
    PortfolioValidationError,
)
from curator.io_utils import load_yaml_safe
from curator.models import (
    Basics,
    CertificateEntry,
    CoverLetterCuration,
    EducationEntry,
    InterestData,
    LanguageEntry,
    PortfolioData,
    ProjectEntry,
    PublicationEntry,
    ServiceEntry,
    SkillEntry,
    VolunteerEntry,
    WorkEntry,
)


class _SectionSpec(NamedTuple):
    model: type[BaseModel]
    field: str
    is_list: bool


_SECTION_REGISTRY: dict[str, _SectionSpec] = {
    "basics": _SectionSpec(model=Basics, field="basics", is_list=False),
    "work": _SectionSpec(model=WorkEntry, field="work", is_list=True),
    "education": _SectionSpec(model=EducationEntry, field="education", is_list=True),
    "skills": _SectionSpec(model=SkillEntry, field="skills", is_list=True),
    "certificates": _SectionSpec(
        model=CertificateEntry, field="certificates", is_list=True
    ),
    "projects": _SectionSpec(model=ProjectEntry, field="projects", is_list=True),
    "volunteer": _SectionSpec(model=VolunteerEntry, field="volunteer", is_list=True),
    "publications": _SectionSpec(
        model=PublicationEntry, field="publications", is_list=True
    ),
    "languages": _SectionSpec(model=LanguageEntry, field="languages", is_list=True),
    "interests": _SectionSpec(model=InterestData, field="interests", is_list=False),
    "services": _SectionSpec(model=ServiceEntry, field="services", is_list=True),
    "cover-letter": _SectionSpec(
        model=CoverLetterCuration, field="cover_letter", is_list=False
    ),
}

# Sections that must exist as files (others default to empty list or None)
_REQUIRED_OBJECTS = {"basics"}


def _load_yaml_file(path: Path) -> Any:
    """Load a single YAML file, wrapping errors as portfolio exceptions.

    Delegates to ``io_utils.load_yaml_safe()`` for the actual loading
    and wraps generic exceptions into portfolio-specific types.

    Args:
        path: Path to the YAML file.

    Returns:
        The parsed YAML content (dict, list, or None for empty files).

    Raises:
        PortfolioNotFoundError: If the file cannot be read.
        PortfolioValidationError: If the YAML is malformed or oversized.
    """
    try:
        return load_yaml_safe(path)
    except OSError as e:
        msg = f"Cannot read portfolio file: {path}"
        raise PortfolioNotFoundError(msg) from e
    except ValueError as e:
        msg = f"Portfolio file exceeds size limit: {path.name}"
        raise PortfolioValidationError(msg) from e
    except yaml.YAMLError as e:
        msg = f"Malformed YAML in {path.name}"
        raise PortfolioValidationError(msg) from e


def _validate_section(name: str, raw: Any, spec: _SectionSpec) -> Any:
    """Validate raw YAML data against its Pydantic model.

    Args:
        name: Section name (for error messages).
        raw: Raw parsed YAML data.
        spec: Section specification with model type and shape info.

    Returns:
        Validated model instance (single object) or list of instances.

    Raises:
        PortfolioValidationError: If the data fails validation.
    """
    try:
        if spec.is_list:
            if raw is None:
                return []
            if not isinstance(raw, list):
                msg = f"Section '{name}' must be an array, got {type(raw).__name__}"
                raise PortfolioValidationError(msg)
            result = [spec.model.model_validate(item) for item in raw]
            logger.debug("Validated {} {} entries", len(result), name)
            return result

        # Single object (basics, interests)
        if raw is None:
            msg = f"Section '{name}' is empty but requires data"
            raise PortfolioValidationError(msg)
        if not isinstance(raw, dict):
            msg = f"Section '{name}' must be an object, got {type(raw).__name__}"
            raise PortfolioValidationError(msg)
        return spec.model.model_validate(raw)
    except ValidationError as e:
        msg = f"Validation failed for section '{name}'"
        raise PortfolioValidationError(msg) from e


def load_portfolio(data_path: Path) -> PortfolioData:
    """Load and validate all portfolio data from a YAML directory.

    Reads each section's YAML file, validates it through the corresponding
    Pydantic model, and assembles the results into a PortfolioData instance.

    Args:
        data_path: Path to the portfolio data directory containing YAML files.

    Returns:
        A fully validated PortfolioData instance with all sections.

    Raises:
        PortfolioNotFoundError: If the directory or required files are missing.
        PortfolioValidationError: If any YAML is malformed or fails validation.
    """
    if not data_path.is_dir():
        msg = f"Portfolio data directory not found: {data_path}"
        raise PortfolioNotFoundError(msg)

    # Check required files exist before loading anything
    for name in _REQUIRED_OBJECTS:
        required_file = data_path / f"{name}.yaml"
        if not required_file.exists():
            msg = f"Required portfolio file missing: {required_file.name}"
            raise PortfolioNotFoundError(msg)

    sections: dict[str, Any] = {}

    for name, spec in _SECTION_REGISTRY.items():
        file_path = data_path / f"{name}.yaml"

        if not file_path.exists():
            if spec.is_list:
                logger.debug("Optional section '{}' not found, defaulting to []", name)
                sections[spec.field] = []
            else:
                # Non-list optional section (interests)
                logger.debug("Optional section '{}' not found, skipping", name)
                sections[spec.field] = None
            continue

        raw = _load_yaml_file(file_path)
        sections[spec.field] = _validate_section(name, raw, spec)

    counts = {
        name: len(sections[spec.field])
        for name, spec in _SECTION_REGISTRY.items()
        if spec.is_list and sections[spec.field]
    }
    summary = ", ".join(f"{n}={c}" for n, c in counts.items())
    logger.info("Portfolio loaded: {}", summary)

    return PortfolioData(
        basics=sections["basics"],
        work=sections["work"],
        education=sections["education"],
        skills=sections["skills"],
        certificates=sections["certificates"],
        projects=sections["projects"],
        volunteer=sections["volunteer"],
        publications=sections["publications"],
        languages=sections["languages"],
        interests=sections["interests"],
        services=sections["services"],
        cover_letter=sections["cover_letter"],
    )
