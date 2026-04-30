"""Integration tests for the render pipeline.

Tests the full flow from curation result through rendering, with Typst
mocked. Validates that module boundaries work correctly together.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from curator.client import CurationResult
from curator.models import (
    Basics,
    CertificateEntry,
    EducationEntry,
    LanguageEntry,
    PortfolioData,
    ResumeCuration,
    SkillEntry,
    WorkEntry,
)
from curator.renderer import render


def _fake_typst_run(cmd: list[str], **kwargs: Any) -> Any:
    """Mock Typst that creates a fake PDF."""
    pdf_path = Path(cmd[-1])
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    return type("CompletedProcess", (), {"returncode": 0, "stderr": ""})()


@pytest.fixture
def integration_portfolio() -> PortfolioData:
    """Multi-entry portfolio for integration testing."""
    return PortfolioData(
        basics=Basics(
            name="Jane Doe",
            label="DevOps Engineer",
            email="jane@example.com",
            summary="Original summary.",
        ),
        work=[
            WorkEntry.model_validate(
                {
                    "id": "acme-devops",
                    "name": "Acme Corp",
                    "position": "DevOps Engineer",
                    "startDate": "2023-01",
                    "highlights": [
                        {
                            "id": "acme-k8s",
                            "text": "Deployed Kubernetes cluster.",
                        },
                        {
                            "id": "acme-ci",
                            "text": "Built CI/CD pipeline.",
                        },
                    ],
                }
            ),
            WorkEntry.model_validate(
                {
                    "id": "beta-sre",
                    "name": "Beta Inc",
                    "position": "SRE",
                    "startDate": "2021-06",
                    "endDate": "2022-12",
                    "highlights": [
                        {"id": "beta-monitoring", "text": "Set up monitoring."},
                    ],
                }
            ),
        ],
        education=[
            EducationEntry.model_validate(
                {"id": "umw-cs", "institution": "UMW", "area": "CS"}
            ),
        ],
        skills=[
            SkillEntry.model_validate(
                {"id": "kubernetes", "name": "Kubernetes", "keywords": ["EKS"]}
            ),
            SkillEntry.model_validate(
                {"id": "terraform", "name": "Terraform", "keywords": ["IaC"]}
            ),
        ],
        certificates=[
            CertificateEntry.model_validate(
                {"id": "cka", "name": "CKA", "date": "2023"}
            ),
        ],
        projects=[],
        volunteer=[],
        publications=[],
        languages=[
            LanguageEntry.model_validate({"id": "english", "language": "English"}),
        ],
        interests=None,
        services=[],
    )


@pytest.fixture
def integration_curation() -> CurationResult:
    """Curation selecting a subset of portfolio entries."""
    from tests.helpers import make_curation_dict

    curation = ResumeCuration.model_validate(
        make_curation_dict(
            suggested_label="Senior SRE",
            company_slug="gamma-inc",
            work_highlights=[
                {
                    "work_id": "acme-devops",
                    "highlight_ids": ["acme-k8s"],
                },
            ],
            skills=[{"skill_id": "kubernetes", "keywords": ["EKS"]}],
            projects=[],
        )
    )
    return CurationResult(
        curation=curation,
        model="claude-sonnet-4-6-20260217",
        input_tokens=5000,
        output_tokens=500,
        cache_creation_input_tokens=3000,
        cache_read_input_tokens=0,
    )


def _render_with_mock(
    curation: CurationResult,
    portfolio: PortfolioData,
    tmp_path: Path,
    jd_text: str = "Test JD.",
) -> Any:
    """Helper: render with mocked Typst."""
    tpl = tmp_path / "tpl" / "curated.typ"
    tpl.parent.mkdir(exist_ok=True)
    tpl.write_text("// dummy template")
    settings = type(
        "S",
        (),
        {
            "output_dir": tmp_path / "output",
            "template_path": tpl,
            "section_order": (
                "work",
                "skills",
                "projects",
                "certificates",
                "education",
            ),
            "max_pages": 1,
            "max_trim_iterations": 15,
        },
    )()
    with (
        patch("curator.renderer.subprocess.run", side_effect=_fake_typst_run),
        patch("curator.renderer.get_page_count", return_value=1),
    ):
        return render(curation, portfolio, jd_text, settings)


class TestRenderPipeline:
    """Full render pipeline integration tests."""

    def test_output_structure(
        self,
        integration_portfolio: PortfolioData,
        integration_curation: CurationResult,
        tmp_path: Path,
    ) -> None:
        result = _render_with_mock(
            integration_curation, integration_portfolio, tmp_path
        )

        assert result.pdf_path is not None
        assert result.pdf_path.exists()
        assert result.curated_yaml_path.exists()
        assert result.curation_log_path.exists()
        assert result.jd_path.exists()
        assert (result.profile_dir / "layout.yaml").exists()
        assert (result.profile_dir / "data" / "basics.yaml").exists()

    def test_summary_injection(
        self,
        integration_portfolio: PortfolioData,
        integration_curation: CurationResult,
        tmp_path: Path,
    ) -> None:
        result = _render_with_mock(
            integration_curation, integration_portfolio, tmp_path
        )

        basics = yaml.safe_load(result.data_files["basics"].read_text())
        assert "founder of Perts Foundry LLC" in basics["summary"]

    def test_work_filtering(
        self,
        integration_portfolio: PortfolioData,
        integration_curation: CurationResult,
        tmp_path: Path,
    ) -> None:
        result = _render_with_mock(
            integration_curation, integration_portfolio, tmp_path
        )

        work = yaml.safe_load(result.data_files["work"].read_text())
        assert len(work) == len(integration_portfolio.work)
        acme = next(w for w in work if w["id"] == "acme-devops")
        assert acme["highlights"][0]["id"] == "acme-k8s"

    def test_skills_filtering(
        self,
        integration_portfolio: PortfolioData,
        integration_curation: CurationResult,
        tmp_path: Path,
    ) -> None:
        result = _render_with_mock(
            integration_curation, integration_portfolio, tmp_path
        )

        skills = yaml.safe_load(result.data_files["skills"].read_text())
        assert len(skills) == 1
        assert skills[0]["id"] == "kubernetes"

    def test_section_order_in_layout(
        self,
        integration_portfolio: PortfolioData,
        integration_curation: CurationResult,
        tmp_path: Path,
    ) -> None:
        result = _render_with_mock(
            integration_curation, integration_portfolio, tmp_path
        )

        layout = yaml.safe_load((result.profile_dir / "layout.yaml").read_text())
        assert layout["section_order"] == [
            "work",
            "skills",
            "projects",
            "certificates",
            "education",
            "interests",
        ]

    def test_curation_log_metadata(
        self,
        integration_portfolio: PortfolioData,
        integration_curation: CurationResult,
        tmp_path: Path,
    ) -> None:
        result = _render_with_mock(
            integration_curation, integration_portfolio, tmp_path
        )

        log = json.loads(result.curation_log_path.read_text())
        assert log["format_version"] == "2.2"
        assert log["source"] == "api"
        assert log["model"] == "claude-sonnet-4-6-20260217"
        assert log["input_tokens"] == 5000
        assert log["cache_creation_input_tokens"] == 3000

    def test_snake_case_field_names(
        self,
        integration_portfolio: PortfolioData,
        integration_curation: CurationResult,
        tmp_path: Path,
    ) -> None:
        result = _render_with_mock(
            integration_curation, integration_portfolio, tmp_path
        )

        work = yaml.safe_load(result.data_files["work"].read_text())
        assert "start_date" in work[0]
        assert "startDate" not in work[0]

    def test_directory_naming(
        self,
        integration_portfolio: PortfolioData,
        integration_curation: CurationResult,
        tmp_path: Path,
    ) -> None:
        result = _render_with_mock(
            integration_curation, integration_portfolio, tmp_path
        )

        assert "gamma-inc" in result.profile_dir.name
