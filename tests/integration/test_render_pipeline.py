"""Integration tests for the render pipeline.

Tests the full flow from curation result through rendering, with Typst
mocked. Validates that module boundaries work correctly together.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pypdf
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
from curator.renderer import _render_cover_letter, render
from tests.conftest import TYPST_AVAILABLE


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
        assert log["format_version"] == "2.5"
        assert log["source"] == "api"
        assert log["model"] == "claude-sonnet-4-6-20260217"
        assert log["input_tokens"] == 5000
        assert log["cache_creation_input_tokens"] == 3000
        assert log["max_pages"] >= 1

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


class TestSafetyNetCapEndToEnd:
    """``render()`` applies the per-entry safety-net cap end-to-end.

    Pins the cap-bounds-safety-net invariant at the function boundary
    we care about (``render`` consumed by pipeline), without a live
    API call. The unit tests cover ``_apply_selections`` directly; this
    one proves the cap survives the ``render(... , settings)``
    plumbing and the final ``data/work.yaml`` written to disk.
    """

    @staticmethod
    def _portfolio_with_overstocked_pos0() -> PortfolioData:
        # Single recent role with 20 portfolio highlights. Under
        # 2-page mode (work_position_floors[0] = 8), the per-entry cap
        # at chrono position 0 is ceil(8 * 1.5) = 12.
        return PortfolioData(
            basics=Basics(
                name="Jane Doe",
                label="DevOps Engineer",
                email="jane@example.com",
                summary="Original.",
            ),
            work=[
                WorkEntry.model_validate(
                    {
                        "id": "acme-devops",
                        "name": "Acme Corp",
                        "position": "DevOps Engineer",
                        "startDate": "2024-01",
                        "highlights": [
                            {"id": f"h{i}", "text": f"Highlight {i}."}
                            for i in range(20)
                        ],
                    }
                ),
            ],
            education=[],
            skills=[
                SkillEntry.model_validate(
                    {"id": "kubernetes", "name": "K8s", "keywords": ["EKS"]}
                ),
            ],
            certificates=[],
            projects=[],
            volunteer=[],
            publications=[],
            languages=[],
            interests=None,
            services=[],
        )

    @staticmethod
    def _curation_top_12_with_weight(weight: float) -> CurationResult:
        from tests.helpers import make_curation_dict

        # AI emits 12 highlight IDs (the cap) in a deliberately
        # different order from portfolio order so the assertion that
        # AI rank survives is meaningful.
        ai_ids = [f"h{i}" for i in (19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 9, 8)]
        curation = ResumeCuration.model_validate(
            make_curation_dict(
                suggested_label="Senior SRE",
                company_slug="gamma",
                work_highlights=[
                    {"work_id": "acme-devops", "highlight_ids": ai_ids},
                ],
                skills=[{"skill_id": "kubernetes", "keywords": ["EKS"]}],
                projects=[],
                work_highlight_weights={"acme-devops": weight},
            )
        )
        return CurationResult(
            curation=curation,
            model="claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=500,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )

    def _render_with_max_pages_2(
        self,
        curation: CurationResult,
        portfolio: PortfolioData,
        tmp_path: Path,
    ) -> Any:
        # Re-uses ``_render_with_mock``'s pattern but with max_pages=2
        # so the per-entry cap at pos 0 is 12, not 1-page mode's 5.
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
                "max_pages": 2,
                "max_trim_iterations": 15,
            },
        )()
        with (
            patch("curator.renderer.subprocess.run", side_effect=_fake_typst_run),
            patch("curator.renderer.get_page_count", return_value=2),
        ):
            return render(curation, portfolio, "Test JD.", settings)

    def test_weight_18_at_pos_zero_pinned_to_cap_with_ai_rank(
        self, tmp_path: Path
    ) -> None:
        portfolio = self._portfolio_with_overstocked_pos0()
        curation = self._curation_top_12_with_weight(weight=1.8)
        result = self._render_with_max_pages_2(curation, portfolio, tmp_path)

        # Inspect the on-disk work.yaml the renderer wrote; this is
        # what the Typst template consumes.
        work = yaml.safe_load(result.data_files["work"].read_text())
        kept_ids = [h["id"] for h in work[0]["highlights"]]
        # Cap binds at 12 even though weight 1.8 would otherwise lift
        # the effective floor to round(8 * 1.8) = 14.
        assert len(kept_ids) == 12
        # The 12 retained highlights are the AI's top 12 in AI order,
        # NOT the portfolio-order tail. Without this assertion the
        # safety-net silent override could return.
        expected = [f"h{i}" for i in (19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 9, 8)]
        assert kept_ids == expected


# ---------------------------------------------------------------------------
# Soft-hyphen ActualText regression (cover letter PDF)
#
# Background: Typst auto-hyphenation wraps each line-break hyphen in a
# /ActualText <FEFF00AD> marked-content section, so PDF copy operations
# emit U+00AD (SOFT HYPHEN). Web fonts that lack a U+00AD glyph render
# the codepoint as boxes when text is pasted into job-application forms.
# This test pair pins both directions: with hyphenate disabled (default
# template) markers must be absent; with hyphenate forced back on (patched
# template) markers must be present, proving the assertion harness works.
# ---------------------------------------------------------------------------


def _content_streams_have_soft_hyphen_actualtext(pdf_path: Path) -> bool:
    """Return True if any page's content stream contains the marker.

    Looks for the literal ``FEFF00AD`` byte sequence (UTF-16 BOM + soft
    hyphen) that Typst writes inside ``/ActualText <...>`` when it
    auto-hyphenates a line break. Tighter than scanning for
    ``/ActualText`` and ``00AD`` separately: PDF readers can legitimately
    emit ``/ActualText`` for unrelated reasons (ligatures, accessibility
    tags), and a stray ``00AD`` byte sequence elsewhere in the same page
    (font CID, coordinate, hex string) would otherwise produce a false
    positive against a benign tag.

    Uses pypdf's filter-aware stream walking so the assertion is robust
    against PDF encoding variations (FlateDecode chains, ObjStm, CRLF
    differences) that a manual regex would miss.
    """
    reader = pypdf.PdfReader(str(pdf_path))
    for page in reader.pages:
        content = page.get_contents()
        if content is None:
            continue
        # PDF /Contents may be an array of streams; pypdf concatenates via
        # ContentStream / EncodedStreamObject. get_data() returns decoded
        # bytes for the page's combined content.
        try:
            raw = content.get_data()
        except AttributeError:
            raw = bytes(content)
        if b"FEFF00AD" in raw.upper():
            return True
    return False


def _write_minimal_basics(output_dir: Path) -> None:
    """Write a minimal data/basics.yaml so the cover letter template loads.

    The template reads basics.name plus optional email/phone/location/url
    fields for the letterhead. Only ``name`` is mandatory; everything else
    is gated on default-aware lookups.
    """
    data_dir = output_dir / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "basics.yaml").write_text(
        yaml.safe_dump({"name": "Test Candidate"}),
        encoding="utf-8",
    )


@pytest.mark.integration
@pytest.mark.skipif(not TYPST_AVAILABLE, reason="Typst not installed")
class TestCoverLetterSoftHyphenRegression:
    """Negative + positive control around hyphenate: false in cover_letter.typ.

    The negative test compiles with the packaged template (hyphenate: false)
    and asserts no soft-hyphen ActualText markers and a single page on the
    high-water-mark fixture (close to the 360-word total cap; verified
    in-test). The positive test patches the template back to
    hyphenate: true and asserts the marker IS present, proving the
    assertion mechanism actually fires on the bad input.
    """

    # Minimum word count below which valid_cover_letter() no longer
    # exercises a meaningful page-fit assertion. If the shared helper
    # is shrunk for an unrelated test, this floor fires before the
    # geometry assertion does, pointing the failure at the fixture
    # edit rather than the template. Raised from 280 to 340 on
    # 2026-05-17 in lockstep with COVER_LETTER_WORD_MAX 300 -> 360 so
    # the fixture continues to sit "near the cap" and stress-test
    # cover-letter page geometry rather than coasting well below it.
    HIGH_WATER_MARK_FLOOR = 340

    def test_default_template_emits_no_soft_hyphen_markers(
        self, typst_safe_dir: Path
    ) -> None:
        import re as _re

        from curator import default_cover_letter_template_path
        from tests.helpers import valid_cover_letter

        _write_minimal_basics(typst_safe_dir)
        letter = valid_cover_letter()

        # Guard against shared-fixture drift: the page-fit assertion
        # below is meaningful only when the fixture is near the
        # 360-word cap. If a future contributor shrinks
        # valid_cover_letter() for an unrelated test, surface the
        # decoupling here instead of having the geometry assertion
        # mislead the reader.
        word_count = sum(
            len(_re.findall(r"\b\w+\b", text))
            for text in (
                letter.opening,
                *letter.body_paragraphs,
                letter.closing,
            )
        )
        assert word_count >= self.HIGH_WATER_MARK_FLOOR, (
            f"valid_cover_letter() word count is {word_count}, below the "
            f"high-water-mark floor of {self.HIGH_WATER_MARK_FLOOR}. "
            "The page-fit assertion below relies on the fixture being "
            "near the 360-word cap. Either restore the helper's length, "
            "or move this test to a local high-water-mark fixture."
        )

        _, pdf_path, pages = _render_cover_letter(
            typst_safe_dir,
            letter,
            default_cover_letter_template_path(),
            skip_pdf=False,
        )
        assert pdf_path is not None
        assert pdf_path.exists()
        assert pages == 1, (
            f"Cover letter rendered to {pages} pages at "
            f"{word_count} words. The cover letter has no trim "
            "cascade; either tighten word caps in rules.py or shrink "
            "template leading/size."
        )
        assert not _content_streams_have_soft_hyphen_actualtext(pdf_path), (
            "Cover letter PDF contains /ActualText soft-hyphen markers. "
            "Verify hyphenate: false is set in src/curator/templates/"
            "cover_letter.typ; web-form fonts render U+00AD as boxes."
        )

    def test_hyphenation_enabled_emits_soft_hyphen_markers(
        self, typst_safe_dir: Path
    ) -> None:
        """Positive control: prove the negative test isn't passing vacuously.

        Patches a copy of the packaged template to flip hyphenate back to
        true, renders with the patched copy, and asserts the marker IS
        present. If this test fails (no marker on a known-bad input), the
        assertion harness is broken and the negative test is meaningless.
        """
        from curator import default_cover_letter_template_path
        from tests.helpers import valid_cover_letter

        # Render to a sibling dir so the patched-template copy in
        # _render_cover_letter doesn't collide with the negative test's run.
        render_dir = typst_safe_dir / "positive_control"
        render_dir.mkdir()
        _write_minimal_basics(render_dir)

        # Place the patched template OUTSIDE render_dir; _render_cover_letter
        # copies the template into output_dir (under its basename), which
        # would be a same-file copy if we put it directly in render_dir.
        template_dir = typst_safe_dir / "patched_templates"
        template_dir.mkdir()
        patched_template = template_dir / "cover_letter_hyphenate_true.typ"
        original = default_cover_letter_template_path().read_text(encoding="utf-8")
        patched = original.replace("hyphenate: false", "hyphenate: true")
        assert patched != original, (
            "Failed to patch hyphenate flag; template format changed?"
        )
        patched_template.write_text(patched, encoding="utf-8")

        letter = valid_cover_letter()
        _, pdf_path, _ = _render_cover_letter(
            render_dir,
            letter,
            patched_template,
            skip_pdf=False,
        )
        assert pdf_path is not None
        assert pdf_path.exists()
        assert _content_streams_have_soft_hyphen_actualtext(pdf_path), (
            "Positive control failed: hyphenate: true should produce "
            "/ActualText <FEFF00AD> markers on the high-water-mark fixture. "
            "If this fails the negative-direction assertion above is also "
            "meaningless. Likely causes: Typst version dropped ActualText "
            "tagging, fixture no longer triggers hyphenation at this "
            "font/geometry, or pypdf stream walking changed."
        )
