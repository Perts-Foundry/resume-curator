"""Tests for curator.loader."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from curator.exceptions import PortfolioNotFoundError, PortfolioValidationError
from curator.loader import _SECTION_REGISTRY, load_portfolio
from curator.models import (
    Basics,
    CertificateEntry,
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


def _write_yaml(path: Path, data: object) -> None:
    """Write data as YAML to a file."""
    path.write_text(
        yaml.dump(data, Dumper=yaml.SafeDumper, default_flow_style=False),
        encoding="utf-8",
    )


@pytest.fixture
def portfolio_dir(
    tmp_path: Path,
    basics_dict: dict[str, object],
    work_entry_dict: dict[str, object],
    education_entry_dict: dict[str, object],
    skill_entry_dict: dict[str, object],
    certificate_entry_dict: dict[str, object],
    project_entry_dict: dict[str, object],
    volunteer_entry_dict: dict[str, object],
    publication_entry_dict: dict[str, object],
    language_entry_dict: dict[str, object],
    interest_data_dict: dict[str, object],
    service_entry_dict: dict[str, object],
) -> Path:
    """Create a complete portfolio data directory with all sections."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Single object (not wrapped in list)
    _write_yaml(data_dir / "basics.yaml", basics_dict)
    _write_yaml(data_dir / "interests.yaml", interest_data_dict)

    # Array sections (wrapped in list)
    _write_yaml(data_dir / "work.yaml", [work_entry_dict])
    _write_yaml(data_dir / "education.yaml", [education_entry_dict])
    _write_yaml(data_dir / "skills.yaml", [skill_entry_dict])
    _write_yaml(data_dir / "certificates.yaml", [certificate_entry_dict])
    _write_yaml(data_dir / "projects.yaml", [project_entry_dict])
    _write_yaml(data_dir / "volunteer.yaml", [volunteer_entry_dict])
    _write_yaml(data_dir / "publications.yaml", [publication_entry_dict])
    _write_yaml(data_dir / "languages.yaml", [language_entry_dict])
    _write_yaml(data_dir / "services.yaml", [service_entry_dict])

    return data_dir


@pytest.fixture
def basics_only_dir(tmp_path: Path, basics_dict: dict[str, object]) -> Path:
    """Create a portfolio directory with only basics.yaml."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_yaml(data_dir / "basics.yaml", basics_dict)
    return data_dir


# ---------------------------------------------------------------------------
# Happy path tests
# ---------------------------------------------------------------------------


class TestLoadPortfolio:
    def test_full_portfolio(self, portfolio_dir: Path) -> None:
        portfolio = load_portfolio(portfolio_dir)
        assert isinstance(portfolio.basics, Basics)
        assert portfolio.basics.name == "Jane Doe"
        assert len(portfolio.work) == 1
        assert len(portfolio.education) == 1
        assert len(portfolio.skills) == 1
        assert len(portfolio.certificates) == 1
        assert len(portfolio.projects) == 1
        assert len(portfolio.volunteer) == 1
        assert len(portfolio.publications) == 1
        assert len(portfolio.languages) == 1
        assert len(portfolio.services) == 1
        assert portfolio.interests is not None

    def test_basics_is_single_object(self, portfolio_dir: Path) -> None:
        portfolio = load_portfolio(portfolio_dir)
        assert isinstance(portfolio.basics, Basics)
        assert portfolio.basics.email == "jane@example.com"

    def test_array_section_types(self, portfolio_dir: Path) -> None:
        portfolio = load_portfolio(portfolio_dir)
        assert isinstance(portfolio.work[0], WorkEntry)
        assert isinstance(portfolio.education[0], EducationEntry)
        assert isinstance(portfolio.skills[0], SkillEntry)
        assert isinstance(portfolio.certificates[0], CertificateEntry)
        assert isinstance(portfolio.projects[0], ProjectEntry)
        assert isinstance(portfolio.volunteer[0], VolunteerEntry)
        assert isinstance(portfolio.publications[0], PublicationEntry)
        assert isinstance(portfolio.languages[0], LanguageEntry)
        assert isinstance(portfolio.services[0], ServiceEntry)

    def test_interests_loaded_as_object(self, portfolio_dir: Path) -> None:
        portfolio = load_portfolio(portfolio_dir)
        assert isinstance(portfolio.interests, InterestData)
        assert len(portfolio.interests.hobbies) == 1
        assert portfolio.interests.hobbies[0].name == "Learning"

    def test_optional_sections_default_to_empty(self, basics_only_dir: Path) -> None:
        portfolio = load_portfolio(basics_only_dir)
        assert portfolio.basics.name == "Jane Doe"
        assert portfolio.work == []
        assert portfolio.education == []
        assert portfolio.skills == []
        assert portfolio.certificates == []
        assert portfolio.projects == []
        assert portfolio.volunteer == []
        assert portfolio.publications == []
        assert portfolio.languages == []
        assert portfolio.services == []
        assert portfolio.interests is None

    def test_extra_yaml_files_ignored(self, portfolio_dir: Path) -> None:
        _write_yaml(portfolio_dir / "unknown_section.yaml", [{"foo": "bar"}])
        portfolio = load_portfolio(portfolio_dir)
        assert not hasattr(portfolio, "unknown_section")

    def test_empty_array_yaml(self, basics_only_dir: Path) -> None:
        _write_yaml(basics_only_dir / "work.yaml", [])
        portfolio = load_portfolio(basics_only_dir)
        assert portfolio.work == []

    def test_multiple_entries_per_section(
        self,
        basics_only_dir: Path,
        work_entry_dict: dict[str, object],
    ) -> None:
        second = dict(work_entry_dict)
        second["id"] = "other-job"
        _write_yaml(basics_only_dir / "work.yaml", [work_entry_dict, second])
        portfolio = load_portfolio(basics_only_dir)
        assert len(portfolio.work) == 2

    def test_frozen_result(self, portfolio_dir: Path) -> None:
        portfolio = load_portfolio(portfolio_dir)
        with pytest.raises(AttributeError):
            portfolio.work = []  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Error path tests
# ---------------------------------------------------------------------------


class TestLoadPortfolioErrors:
    def test_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(PortfolioNotFoundError, match="not found"):
            load_portfolio(tmp_path / "nonexistent")

    def test_file_instead_of_directory(self, tmp_path: Path) -> None:
        file_path = tmp_path / "not_a_dir"
        file_path.write_text("content")
        with pytest.raises(PortfolioNotFoundError, match="not found"):
            load_portfolio(file_path)

    def test_missing_basics_raises(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write_yaml(data_dir / "work.yaml", [])
        with pytest.raises(PortfolioNotFoundError, match="basics"):
            load_portfolio(data_dir)

    def test_malformed_yaml_raises(self, basics_only_dir: Path) -> None:
        bad_yaml = basics_only_dir / "work.yaml"
        bad_yaml.write_text(":\n  : :\n  bad", encoding="utf-8")
        with pytest.raises(PortfolioValidationError, match="Malformed YAML"):
            load_portfolio(basics_only_dir)

    def test_malformed_yaml_chains_cause(self, basics_only_dir: Path) -> None:
        bad_yaml = basics_only_dir / "work.yaml"
        bad_yaml.write_text(":\n  : :\n", encoding="utf-8")
        with pytest.raises(PortfolioValidationError) as exc_info:
            load_portfolio(basics_only_dir)
        assert isinstance(exc_info.value.__cause__, yaml.YAMLError)

    def test_basics_not_dict_raises(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write_yaml(data_dir / "basics.yaml", [{"name": "Jane"}])
        with pytest.raises(PortfolioValidationError, match="object"):
            load_portfolio(data_dir)

    def test_array_section_not_list_raises(self, basics_only_dir: Path) -> None:
        _write_yaml(basics_only_dir / "work.yaml", {"id": "test"})
        with pytest.raises(PortfolioValidationError, match="array"):
            load_portfolio(basics_only_dir)

    def test_validation_error_chains_cause(self, basics_only_dir: Path) -> None:
        _write_yaml(basics_only_dir / "work.yaml", [{"id": "test"}])
        with pytest.raises(PortfolioValidationError) as exc_info:
            load_portfolio(basics_only_dir)
        assert exc_info.value.__cause__ is not None

    def test_validation_error_includes_section_name(
        self, basics_only_dir: Path
    ) -> None:
        _write_yaml(basics_only_dir / "work.yaml", [{"id": "test"}])
        with pytest.raises(PortfolioValidationError, match="work"):
            load_portfolio(basics_only_dir)

    def test_invalid_basics_content(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write_yaml(data_dir / "basics.yaml", {"label": "No name field"})
        with pytest.raises(PortfolioValidationError, match="basics"):
            load_portfolio(data_dir)

    def test_empty_basics_file_raises(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "basics.yaml").write_text("", encoding="utf-8")
        with pytest.raises(PortfolioValidationError, match="basics"):
            load_portfolio(data_dir)

    def test_empty_list_section_file(self, basics_only_dir: Path) -> None:
        (basics_only_dir / "work.yaml").write_text("", encoding="utf-8")
        portfolio = load_portfolio(basics_only_dir)
        assert portfolio.work == []

    def test_oversized_file_raises(self, basics_only_dir: Path) -> None:
        huge = basics_only_dir / "work.yaml"
        huge.write_text("x" * 1_048_577, encoding="utf-8")
        with pytest.raises(PortfolioValidationError, match="size limit"):
            load_portfolio(basics_only_dir)


# ---------------------------------------------------------------------------
# Parametrized tests
# ---------------------------------------------------------------------------

_OPTIONAL_LIST_SECTIONS = [
    "work",
    "education",
    "skills",
    "certificates",
    "projects",
    "volunteer",
    "publications",
    "languages",
    "services",
]


class TestOptionalSections:
    @pytest.mark.parametrize("section", _OPTIONAL_LIST_SECTIONS)
    def test_section_independently_missing(
        self, portfolio_dir: Path, section: str
    ) -> None:
        (portfolio_dir / f"{section}.yaml").unlink()
        portfolio = load_portfolio(portfolio_dir)
        assert getattr(portfolio, section) == []

    def test_interests_independently_missing(self, portfolio_dir: Path) -> None:
        (portfolio_dir / "interests.yaml").unlink()
        portfolio = load_portfolio(portfolio_dir)
        assert portfolio.interests is None


_SECTIONS_WITH_INVALID_DATA: list[tuple[str, Any]] = [
    ("work", [{"id": "bad"}]),
    ("education", [{"id": "bad"}]),
    ("skills", [{"id": "bad"}]),
    ("certificates", [{"id": "bad"}]),
    ("projects", [{"id": "bad"}]),
    ("volunteer", [{"id": "bad"}]),
    ("publications", [{"id": "bad"}]),
    ("languages", [{"id": "bad"}]),
    ("services", [{"id": "bad"}]),
]


class TestInvalidSectionData:
    @pytest.mark.parametrize(
        ("section", "bad_data"),
        _SECTIONS_WITH_INVALID_DATA,
        ids=[s for s, _ in _SECTIONS_WITH_INVALID_DATA],
    )
    def test_invalid_data_raises(
        self, portfolio_dir: Path, section: str, bad_data: Any
    ) -> None:
        _write_yaml(portfolio_dir / f"{section}.yaml", bad_data)
        with pytest.raises(PortfolioValidationError, match=section):
            load_portfolio(portfolio_dir)


class TestRegistryConsistency:
    def test_registry_fields_match_portfolio_data(self) -> None:
        registry_fields = {spec.field for spec in _SECTION_REGISTRY.values()}
        dataclass_fields = set(PortfolioData.__dataclass_fields__)
        assert registry_fields == dataclass_fields


# ---------------------------------------------------------------------------
# Constant naming
# ---------------------------------------------------------------------------


class TestMaxYamlSizeConstant:
    """Tests for _MAX_YAML_SIZE constant naming and usage."""

    def test_constant_exists(self) -> None:
        from curator.io_utils import MAX_YAML_SIZE as _MAX_YAML_SIZE

        assert _MAX_YAML_SIZE == 1_048_576

    def test_constant_used_for_size_check(self, basics_only_dir: Path) -> None:
        """Oversized file is rejected using _MAX_YAML_SIZE."""
        from curator.io_utils import MAX_YAML_SIZE as _MAX_YAML_SIZE

        huge = basics_only_dir / "work.yaml"
        huge.write_text("x" * (_MAX_YAML_SIZE + 1), encoding="utf-8")
        with pytest.raises(PortfolioValidationError, match="size limit"):
            load_portfolio(basics_only_dir)

    def test_file_at_size_limit_accepted(self, basics_only_dir: Path) -> None:
        """File exactly at _MAX_YAML_SIZE is not rejected by size check."""
        from curator.io_utils import MAX_YAML_SIZE as _MAX_YAML_SIZE

        # Create YAML content that is exactly at the limit.
        # Content must be valid YAML to avoid parse errors.
        content = "data: " + "x" * (_MAX_YAML_SIZE - len("data: ") - 1) + "\n"
        work_file = basics_only_dir / "work.yaml"
        work_file.write_text(content, encoding="utf-8")
        # The file is at the limit, so it should not raise a size error.
        # It will fail validation (not a list) but NOT a size error.
        with pytest.raises(PortfolioValidationError, match="array"):
            load_portfolio(basics_only_dir)


# ---------------------------------------------------------------------------
# Cover letter section loading (optional object)
# ---------------------------------------------------------------------------


class TestCoverLetterLoading:
    """``data/cover-letter.yaml`` is an optional object section.

    Missing file → ``portfolio.cover_letter is None`` and the rest of the
    portfolio loads normally. Present-but-invalid files raise
    ``PortfolioValidationError`` at load time (not downstream).
    """

    def test_missing_file_yields_none(self, basics_only_dir: Path) -> None:
        portfolio = load_portfolio(basics_only_dir)
        assert portfolio.cover_letter is None
        # Basics still loaded.
        assert portfolio.basics.name == "Jane Doe"

    def test_valid_file_loads(self, basics_only_dir: Path) -> None:
        from tests.helpers import valid_cover_letter_kwargs

        _write_yaml(basics_only_dir / "cover-letter.yaml", valid_cover_letter_kwargs())
        portfolio = load_portfolio(basics_only_dir)
        assert portfolio.cover_letter is not None
        assert portfolio.cover_letter.sign_off == "Sincerely"

    def test_empty_file_raises(self, basics_only_dir: Path) -> None:
        (basics_only_dir / "cover-letter.yaml").write_text("", encoding="utf-8")
        with pytest.raises(PortfolioValidationError):
            load_portfolio(basics_only_dir)

    def test_malformed_yaml_chains_cause(self, basics_only_dir: Path) -> None:
        (basics_only_dir / "cover-letter.yaml").write_text(
            "salutation: [unclosed\n", encoding="utf-8"
        )
        with pytest.raises(PortfolioValidationError) as exc_info:
            load_portfolio(basics_only_dir)
        assert isinstance(exc_info.value.__cause__, yaml.YAMLError)

    def test_top_level_list_rejected(self, basics_only_dir: Path) -> None:
        _write_yaml(basics_only_dir / "cover-letter.yaml", ["not", "an", "object"])
        with pytest.raises(PortfolioValidationError, match="object"):
            load_portfolio(basics_only_dir)

    def test_oversized_file_rejected(self, basics_only_dir: Path) -> None:
        from curator.io_utils import MAX_YAML_SIZE as _MAX_YAML_SIZE

        (basics_only_dir / "cover-letter.yaml").write_text(
            "x" * (_MAX_YAML_SIZE + 1), encoding="utf-8"
        )
        with pytest.raises(PortfolioValidationError, match="size limit"):
            load_portfolio(basics_only_dir)

    def test_missing_required_field_rejected_at_load(
        self, basics_only_dir: Path
    ) -> None:
        from tests.helpers import valid_cover_letter_kwargs

        kwargs = valid_cover_letter_kwargs()
        del kwargs["sign_off"]
        _write_yaml(basics_only_dir / "cover-letter.yaml", kwargs)
        with pytest.raises(PortfolioValidationError):
            load_portfolio(basics_only_dir)

    def test_invalid_sign_off_rejected_at_load(self, basics_only_dir: Path) -> None:
        from tests.helpers import valid_cover_letter_kwargs

        kwargs = valid_cover_letter_kwargs()
        kwargs["sign_off"] = "Warmly"
        _write_yaml(basics_only_dir / "cover-letter.yaml", kwargs)
        with pytest.raises(PortfolioValidationError):
            load_portfolio(basics_only_dir)

    def test_registry_entry_uses_hyphenated_filename(self) -> None:
        spec = _SECTION_REGISTRY["cover-letter"]
        assert spec.field == "cover_letter"
        assert spec.is_list is False

    def test_unknown_field_rejected_at_load(self, basics_only_dir: Path) -> None:
        """Legacy / typo fields (e.g., is_template) must fail under extra=forbid."""
        from tests.helpers import valid_cover_letter_kwargs

        payload = valid_cover_letter_kwargs() | {"is_template": True}
        _write_yaml(basics_only_dir / "cover-letter.yaml", payload)
        with pytest.raises(PortfolioValidationError):
            load_portfolio(basics_only_dir)

    def test_literal_block_scalar_newlines_rejected(
        self, basics_only_dir: Path
    ) -> None:
        """Prose with embedded raw newlines (from YAML `|`) is rejected.

        The authoring guide directs users to `>-` (folded) block scalars
        for a reason: `CoverLetterCuration._no_control_chars` rejects any
        control char including `\\n`. This test pins that contract so a
        future validator relaxation does not silently pass a literal
        block scalar authoring mistake.
        """
        content = (basics_only_dir / "cover-letter.yaml").write_text(
            'salutation: "Dear Hiring Manager,"\n'
            "opening: |\n"
            "  Line one of opening.\n"
            "  Line two of opening.\n"
            "body_paragraphs:\n"
            "  - Single-line paragraph.\n"
            "  - Another single-line paragraph.\n"
            "closing: Thank you.\n"
            "sign_off: Sincerely\n",
            encoding="utf-8",
        )
        del content
        with pytest.raises(PortfolioValidationError):
            load_portfolio(basics_only_dir)

    @pytest.mark.parametrize(
        ("field", "bad_value"),
        [
            ("salutation", "Dear Hiring Manager"),  # missing trailing comma
            ("sign_off", "Sincerely,"),  # trailing comma
            ("sign_off", "Warmly"),  # not in allow-list
        ],
    )
    def test_structural_validator_failures_at_load(
        self, basics_only_dir: Path, field: str, bad_value: str
    ) -> None:
        """Pydantic class validators fire through the loader boundary."""
        from tests.helpers import valid_cover_letter_kwargs

        payload = valid_cover_letter_kwargs() | {field: bad_value}
        _write_yaml(basics_only_dir / "cover-letter.yaml", payload)
        with pytest.raises(PortfolioValidationError):
            load_portfolio(basics_only_dir)
