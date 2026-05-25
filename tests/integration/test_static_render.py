"""Integration tests for the static-mode render pipeline.

Validates that the static-mode synthesis + renderer combination writes
the expected artifacts (per-section YAML, curation_log.json with
``source: "static"``, ``mode.txt`` instead of ``job_description.txt``)
and that the trim cascade still converges on aggressive page budgets.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from curator.models import (
    Basics,
    CertificateEntry,
    EducationEntry,
    LanguageEntry,
    PortfolioData,
    ProjectEntry,
    SkillEntry,
    WorkEntry,
)
from curator.renderer import render
from curator.static_mode import build_static_result


def _fake_typst_run(cmd: list[str], **kwargs: Any) -> Any:
    """Mock Typst that creates a tiny fake PDF."""
    pdf_path = Path(cmd[-1])
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    return type("CompletedProcess", (), {"returncode": 0, "stderr": ""})()


@pytest.fixture
def static_portfolio() -> PortfolioData:
    """Portfolio rich enough to exercise project sort and skill grouping."""
    return PortfolioData(
        basics=Basics(
            name="Jane Doe",
            label="DevOps Engineer",
            email="jane@example.com",
            summary="Concise portfolio summary.",
        ),
        work=[
            WorkEntry.model_validate(
                {
                    "id": "acme-devops",
                    "name": "Acme Corp",
                    "position": "DevOps Engineer",
                    "startDate": "2023-01",
                    "highlights": [
                        {"id": f"acme-h{i}", "text": f"Achieved {i}."} for i in range(5)
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
                        {"id": f"beta-h{i}", "text": f"Stabilized {i}."}
                        for i in range(3)
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
                {"id": "kubernetes", "name": "K8s", "keywords": ["EKS", "GKE"]}
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
        projects=[
            ProjectEntry.model_validate(
                {"id": "p-low", "name": "Low", "description": "L", "weight": 3}
            ),
            ProjectEntry.model_validate(
                {"id": "p-top", "name": "Top", "description": "T", "weight": 1}
            ),
            ProjectEntry.model_validate(
                {"id": "p-none", "name": "None", "description": "N"}
            ),
        ],
        volunteer=[],
        publications=[],
        languages=[
            LanguageEntry.model_validate({"id": "english", "language": "English"}),
        ],
        interests=None,
        services=[],
        cover_letter=_valid_cover_letter_for_fixture(),
    )


def _valid_cover_letter_for_fixture() -> Any:
    from tests.helpers import valid_cover_letter

    return valid_cover_letter()


def _settings(tmp_path: Path, *, max_pages: int = 1) -> Any:
    tpl = tmp_path / "tpl" / "curated.typ"
    tpl.parent.mkdir(exist_ok=True)
    tpl.write_text("// dummy template")
    cl_tpl = tmp_path / "tpl" / "cover_letter.typ"
    cl_tpl.write_text("// dummy cover letter template")
    return type(
        "S",
        (),
        {
            "output_dir": tmp_path / "output",
            "template_path": tpl,
            "cover_letter_template_path": cl_tpl,
            "section_order": (
                "work",
                "skills",
                "projects",
                "certificates",
                "education",
            ),
            "max_pages": max_pages,
            "max_trim_iterations": 50,
        },
    )()


def _render_static(
    portfolio: PortfolioData,
    tmp_path: Path,
    *,
    max_pages: int = 1,
    page_count_returned: int = 1,
    with_cover_letter: bool = False,
) -> Any:
    settings = _settings(tmp_path, max_pages=max_pages)
    static_result = build_static_result(
        portfolio, name="general", with_cover_letter=with_cover_letter
    )
    with (
        patch("curator.renderer.subprocess.run", side_effect=_fake_typst_run),
        patch("curator.renderer.get_page_count", return_value=page_count_returned),
    ):
        return render(static_result, portfolio, jd_text=None, settings=settings)


class TestStaticRenderArtifacts:
    """The static path writes the expected artifacts and source signals."""

    def test_writes_full_artifact_set(
        self, static_portfolio: PortfolioData, tmp_path: Path
    ) -> None:
        result = _render_static(static_portfolio, tmp_path)

        assert result.pdf_path is not None
        assert result.pdf_path.exists()
        assert result.curated_yaml_path.exists()
        assert result.curation_log_path.exists()
        # Static path: no job_description.txt; mode.txt instead.
        assert result.jd_path is None
        assert result.mode_path is not None
        assert result.mode_path.exists()
        assert (result.profile_dir / "layout.yaml").exists()
        assert (result.profile_dir / "data" / "basics.yaml").exists()

    def test_curation_log_carries_source_and_zero_tokens(
        self, static_portfolio: PortfolioData, tmp_path: Path
    ) -> None:
        result = _render_static(static_portfolio, tmp_path)

        log = json.loads(result.curation_log_path.read_text())
        assert log["format_version"] == "2.7"
        assert log["source"] == "static"
        assert log["model"] == "n/a"
        assert log["input_tokens"] == 0
        assert log["output_tokens"] == 0
        assert log["max_pages"] >= 1
        assert log["cover_letter"]["enabled"] is False
        # Static path: no API call, so cache fields are null. A log
        # reader cannot mistake a static run for a cache-miss API run.
        assert log["cache_ttl"] is None
        assert log["cache_outcome"] is None

    def test_mode_txt_descriptor(
        self, static_portfolio: PortfolioData, tmp_path: Path
    ) -> None:
        result = _render_static(static_portfolio, tmp_path)
        assert result.mode_path is not None
        contents = result.mode_path.read_text()
        assert "source: static" in contents
        assert "company_slug: general" in contents

    def test_aggressive_cascade_with_pages_one_converges(
        self, static_portfolio: PortfolioData, tmp_path: Path
    ) -> None:
        """`--pages 1` against a rich portfolio still converges (no crash).

        We mock Typst to report 1 page so we exercise the renderer plumbing
        without depending on real layout. The point is that ``render()``
        terminates and returns a populated ``RenderOutput``.
        """
        result = _render_static(
            static_portfolio, tmp_path, max_pages=1, page_count_returned=1
        )
        assert result.page_count == 1
        assert result.pdf_path is not None


class TestStaticCoverLetterIntegration:
    """Static pipeline with --cover-letter writes all expected artifacts."""

    def test_cover_letter_artifacts_written(
        self, static_portfolio: PortfolioData, tmp_path: Path
    ) -> None:
        result = _render_static(static_portfolio, tmp_path, with_cover_letter=True)
        assert result.cover_letter_pdf_path is not None
        assert result.cover_letter_pdf_path.exists()
        assert result.cover_letter_yaml_path is not None
        assert result.cover_letter_yaml_path.exists()
        # Paste-ready plaintext sidecar lands on the static path too —
        # both API and static paths share ``_render_cover_letter`` so the
        # contract is symmetric. Pinned here so a regression on one path
        # is caught even if the other is silent.
        assert result.cover_letter_txt_path is not None
        assert result.cover_letter_txt_path.exists()
        assert result.cover_letter_txt_path.name == "cover_letter.txt"
        assert static_portfolio.cover_letter is not None
        assert result.cover_letter_txt_path.read_text(encoding="utf-8") == (
            static_portfolio.cover_letter.to_plaintext(static_portfolio.basics.name)
        )

    def test_cover_letter_yaml_has_no_is_template_and_renders_verbatim(
        self, static_portfolio: PortfolioData, tmp_path: Path
    ) -> None:
        import yaml

        result = _render_static(static_portfolio, tmp_path, with_cover_letter=True)
        data = yaml.safe_load(result.cover_letter_yaml_path.read_text(encoding="utf-8"))
        assert "is_template" not in data
        assert "rendered_date" in data
        # Portfolio cover letter flows through verbatim.
        assert static_portfolio.cover_letter is not None
        assert data["salutation"] == static_portfolio.cover_letter.salutation
        assert data["opening"] == static_portfolio.cover_letter.opening

    def test_curation_log_records_cover_letter_details(
        self, static_portfolio: PortfolioData, tmp_path: Path
    ) -> None:
        result = _render_static(static_portfolio, tmp_path, with_cover_letter=True)
        log = json.loads(result.curation_log_path.read_text())
        cl = log["cover_letter"]
        assert cl["enabled"] is True
        assert "is_template" not in cl
        assert cl["word_count"] > 0


class TestStaticCoverLetterMissingContent:
    """Static --cover-letter against a portfolio missing cover_letter raises."""

    def test_missing_cover_letter_raises_static_mode_error(
        self, static_portfolio: PortfolioData, tmp_path: Path
    ) -> None:
        from dataclasses import replace

        from curator.exceptions import StaticModeError

        portfolio = replace(static_portfolio, cover_letter=None)
        with pytest.raises(StaticModeError, match=r"data/cover-letter\.yaml"):
            _render_static(portfolio, tmp_path, with_cover_letter=True)
