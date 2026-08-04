"""E2E test fixtures for the curator CLI."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import Generator

import pytest
import yaml

from curator.cli import app
from curator.client import CurationResult
from curator.models import ResumeCuration

# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------

# Snap-installed Typst cannot access /tmp. Use a home-based directory for
# any files that Typst needs to read/write (the output directory tree).
_E2E_OUTPUT_BASE = Path.home() / ".cache" / "curator-e2e-tests"


@pytest.fixture
def e2e_output_dir() -> Generator[Path, None, None]:
    """Create a Typst-accessible temp directory for output files.

    Snap-confined Typst cannot access pytest's ``tmp_path`` (under /tmp).
    This fixture creates a temp directory under ``$HOME/.cache/`` which
    snap can access, and cleans it up after the test.
    """
    _E2E_OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    output_dir = Path(tempfile.mkdtemp(dir=_E2E_OUTPUT_BASE))
    yield output_dir
    shutil.rmtree(output_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _e2e_env(
    tmp_path: Path,
    e2e_output_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Isolate every E2E test from the host environment.

    * Clears stale CURATOR_* vars from os.environ
    * Changes CWD to tmp_path so pydantic-settings cannot find a .env file
    * Sets required CURATOR_* vars for a valid CuratorSettings() construction
    * Redirects XDG_STATE_HOME to isolate the always-on debug log sink
    * Uses a home-based output dir so snap Typst can access it
    """
    # Clear any host CURATOR_* vars that might leak in.
    for key in list(os.environ):
        if key.upper().startswith("CURATOR_"):
            monkeypatch.delenv(key)

    # Prevent pydantic-settings from reading the repo root .env file.
    monkeypatch.chdir(tmp_path)

    # Portfolio dir structure: <portfolio_path>/data/<section>.yaml
    portfolio_root = tmp_path / "portfolio"
    (portfolio_root / "data").mkdir(parents=True)

    monkeypatch.setenv("CURATOR_ANTHROPIC_API_KEY", "sk-ant-test-key-for-e2e")
    monkeypatch.setenv("CURATOR_ALLOW_API_SPEND", "true")
    monkeypatch.setenv("CURATOR_PORTFOLIO_PATH", str(portfolio_root))
    monkeypatch.setenv("CURATOR_OUTPUT_DIR", str(e2e_output_dir))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    # Pin both backends to the SDK path so E2E behavior cannot shift if
    # the defaults ever change; the suite mocks the Anthropic SDK, never
    # the headless subprocess.
    monkeypatch.setenv("CURATOR_BACKEND", "api")
    monkeypatch.setenv("CURATOR_JUDGE_BACKEND", "api")


# ---------------------------------------------------------------------------
# Portfolio YAML fixtures
# ---------------------------------------------------------------------------

_BASICS: dict[str, Any] = {
    "name": "Jane Doe",
    "label": "Software Engineer",
    "email": "jane@example.com",
    "summary": "Experienced engineer.",
    "location": {"countryCode": "US", "region": "CA"},
    "profiles": [
        {
            "network": "GitHub",
            "username": "janedoe",
            "url": "https://github.com/janedoe",
        },
    ],
}

_WORK: list[dict[str, Any]] = [
    {
        "id": "acme-senior-engineer",
        "name": "Acme Corp",
        "position": "Senior Engineer",
        "startDate": "2023-06",
        "endDate": "",
        "location": "Remote",
        "summary": "Led platform engineering.",
        "description": "Platform, Infrastructure",
        "highlights": [
            {
                "id": "acme-deployed-k8s",
                "text": "Deployed Kubernetes cluster serving 10k RPS.",
                "tags": ["infrastructure", "kubernetes"],
                "resume_variants": ["general", "devops"],
                "technologies": ["Kubernetes", "AWS"],
            },
        ],
        "tags": ["infrastructure", "platform"],
        "resume_variants": ["general", "devops"],
        "technologies": ["Kubernetes", "AWS", "Terraform"],
    },
]

_SKILLS: list[dict[str, Any]] = [
    {"id": "cloud-aws", "name": "AWS", "level": "Advanced", "keywords": ["EKS"]},
]

_EDUCATION: list[dict[str, Any]] = [
    {
        "id": "umw-bs-cs",
        "institution": "University of Mary Washington",
        "area": "Computer Science",
        "studyType": "Bachelor of Science",
        "startDate": "2014",
        "endDate": "2018",
    },
]

_CERTIFICATES: list[dict[str, Any]] = [
    {
        "id": "cka",
        "name": "CKA",
        "date": "2023",
        "type": "professional",
        "issuer": "CNCF",
    },
]

_PROJECTS: list[dict[str, Any]] = [
    {"id": "my-project", "name": "My Project", "description": "A project."},
]

_VOLUNTEER: list[dict[str, Any]] = [
    {"id": "spca", "organization": "SPCA", "position": "Volunteer"},
]

_PUBLICATIONS: list[dict[str, Any]] = [
    {"id": "tech-talk", "name": "Talk", "type": "presentation", "releaseDate": "2025"},
]

_LANGUAGES: list[dict[str, Any]] = [
    {"id": "english", "language": "English", "fluency": "Native"},
]

_INTERESTS: dict[str, Any] = {
    "hobbies": [
        {"name": "Learning", "description": "Always learning.", "keywords": ["tech"]},
    ],
    "fun_facts": ["Interesting fact."],
}

_SERVICES: list[dict[str, Any]] = [
    {
        "id": "cloud-infra",
        "name": "Cloud Infrastructure",
        "slug": "cloud-infrastructure",
    },
]


def _cover_letter_section() -> dict[str, Any]:
    """Return a word-count-compliant cover letter dict for e2e fixtures."""
    from tests.helpers import valid_cover_letter_kwargs

    return valid_cover_letter_kwargs()


_PORTFOLIO_SECTIONS: dict[str, Any] = {
    "basics": _BASICS,
    "work": _WORK,
    "skills": _SKILLS,
    "education": _EDUCATION,
    "certificates": _CERTIFICATES,
    "projects": _PROJECTS,
    "volunteer": _VOLUNTEER,
    "publications": _PUBLICATIONS,
    "languages": _LANGUAGES,
    "interests": _INTERESTS,
    "services": _SERVICES,
}


@pytest.fixture
def portfolio_dir(tmp_path: Path) -> Path:
    """Write portfolio YAML files and return the data directory path.

    Includes ``cover-letter.yaml`` (hyphenated) in addition to the standard
    underscore-named sections, so tests exercising ``--cover-letter`` find
    a valid letter to render.
    """
    data_dir = tmp_path / "portfolio" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    for name, data in _PORTFOLIO_SECTIONS.items():
        (data_dir / f"{name}.yaml").write_text(
            yaml.safe_dump(data, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )

    # Hyphenated filename matches the loader's _SECTION_REGISTRY key.
    (data_dir / "cover-letter.yaml").write_text(
        yaml.safe_dump(
            _cover_letter_section(), default_flow_style=False, allow_unicode=True
        ),
        encoding="utf-8",
    )

    return data_dir


# ---------------------------------------------------------------------------
# Curation result fixture
# ---------------------------------------------------------------------------

_CURATION_DICT: dict[str, Any] = {
    "summary": (
        "Experienced SRE and founder of Perts Foundry LLC "
        "with 10 years of expertise in Kubernetes, "
        "cloud infrastructure, and site reliability engineering. "
        "Delivered 99.9% uptime across distributed clusters serving 50k RPS, "
        "reduced deployment time by 70% through CI/CD pipeline automation."
    ),
    "suggested_label": "Senior Site Reliability Engineer",
    "company_slug": "acme-corp",
    "work_highlights": [
        {
            "work_id": "acme-senior-engineer",
            "highlight_ids": ["acme-deployed-k8s"],
        },
    ],
    "skills": [{"skill_id": "cloud-aws", "keywords": ["EKS"]}],
    "projects": ["my-project"],
}


@pytest.fixture
def curation_result() -> CurationResult:
    """CurationResult with IDs aligned to the portfolio fixtures."""
    return CurationResult(
        curation=ResumeCuration.model_validate(_CURATION_DICT),
        model="claude-sonnet-4-6-20260217",
        input_tokens=5000,
        output_tokens=500,
        cache_creation_input_tokens=3000,
        cache_read_input_tokens=0,
    )


# ---------------------------------------------------------------------------
# JD and CLI fixtures
# ---------------------------------------------------------------------------

_JD_TEXT = """\
Senior Site Reliability Engineer at Acme Corp

We are looking for a Senior SRE to join our platform team. You will:
- Design and operate Kubernetes clusters at scale
- Build CI/CD pipelines and developer tooling
- Drive reliability improvements across our microservices

Requirements:
- 5+ years of experience with cloud infrastructure (AWS preferred)
- Strong Kubernetes and container orchestration skills
- Experience with Terraform and infrastructure-as-code
"""


@pytest.fixture
def jd_file(tmp_path: Path) -> Path:
    """Write a sample job description and return its path."""
    jd = tmp_path / "jd.txt"
    jd.write_text(_JD_TEXT, encoding="utf-8")
    return jd


@pytest.fixture
def cli_runner() -> Any:
    """Typer CliRunner instance."""
    from typer.testing import CliRunner

    return CliRunner()


# ---------------------------------------------------------------------------
# Invocation helper
# ---------------------------------------------------------------------------


@pytest.fixture
def invoke_curate(
    cli_runner: Any,
    curation_result: CurationResult,
) -> Any:
    """Return a callable that invokes ``curator curate`` with a mocked API.

    Returns ``(result, mock_client)`` so tests can assert on both the CLI
    output and what was passed to the mocked client.
    """

    def _invoke(
        jd_path: Path,
        *,
        catch_exceptions: bool = True,
        extra_args: list[str] | None = None,
        side_effect: Exception | None = None,
    ) -> tuple[Any, MagicMock]:
        with patch("curator.pipeline.CuratorClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            if side_effect:
                mock_client.curate.side_effect = side_effect
            else:
                mock_client.curate.return_value = curation_result
            args = ["curate", str(jd_path), *(extra_args or [])]
            result = cli_runner.invoke(app, args, catch_exceptions=catch_exceptions)
            return result, mock_client

    return _invoke
