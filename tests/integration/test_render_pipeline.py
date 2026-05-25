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
        assert log["format_version"] == "2.7"
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


def _decode_page_content_streams(pdf_path: Path) -> list[bytes]:
    """Return decoded content-stream bytes for each page of *pdf_path*.

    Uses pypdf's filter-aware stream walking so callers don't reimplement
    FlateDecode chain handling, ObjStm decoding, or CRLF quirks. Pages
    with no /Contents entry contribute an empty bytes object so the
    return list always lines up with the page index.

    Used by both the soft-hyphen ActualText regression
    (``_content_streams_contain_hex(pdf, "FEFF00AD")``) and the
    non-breaking-hyphen body-rule regression
    (``_content_streams_contain_hex(pdf, "2011")``). Keep the decoder
    in one place so future PDF tests share the filter-aware walk.
    """
    reader = pypdf.PdfReader(str(pdf_path))
    out: list[bytes] = []
    for page in reader.pages:
        content = page.get_contents()
        if content is None:
            out.append(b"")
            continue
        # PDF /Contents may be an array of streams; pypdf concatenates via
        # ContentStream / EncodedStreamObject. get_data() returns decoded
        # bytes for the page's combined content.
        try:
            out.append(content.get_data())
        except AttributeError:
            out.append(bytes(content))
    return out


def _content_streams_contain_hex(pdf_path: Path, hex_literal: str) -> bool:
    """Return True if any page's decoded content stream contains *hex_literal*.

    Comparison is case-insensitive (PDF hex strings may be either case).
    The literal must be the exact substring you'd see inside a PDF
    operator (e.g. ``"FEFF00AD"`` for an /ActualText soft-hyphen marker,
    ``"2011"`` for a U+2011 non-breaking hyphen inside a hex-encoded
    glyph run).
    """
    needle = hex_literal.upper().encode("ascii")
    return any(needle in raw.upper() for raw in _decode_page_content_streams(pdf_path))


def _content_streams_have_soft_hyphen_actualtext(pdf_path: Path) -> bool:
    """Return True if any page's content stream contains the soft-hyphen marker.

    Looks for the literal ``FEFF00AD`` byte sequence (UTF-16 BOM + soft
    hyphen) that Typst writes inside ``/ActualText <...>`` when it
    auto-hyphenates a line break. Tighter than scanning for
    ``/ActualText`` and ``00AD`` separately: PDF readers can legitimately
    emit ``/ActualText`` for unrelated reasons (ligatures, accessibility
    tags), and a stray ``00AD`` byte sequence elsewhere in the same page
    (font CID, coordinate, hex string) would otherwise produce a false
    positive against a benign tag.
    """
    return _content_streams_contain_hex(pdf_path, "FEFF00AD")


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

        artifacts = _render_cover_letter(
            typst_safe_dir,
            letter,
            default_cover_letter_template_path(),
            signer_name="Test Candidate",
            skip_pdf=False,
        )
        pdf_path = artifacts.pdf_path
        pages = artifacts.page_count
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
        # The body-scoped `#show "-": "\u{2011}"` rule (added 2026-05-25)
        # also defends against the soft-hyphen-on-copy bug by pre-empting
        # line breaks at hyphens. The positive control must defeat ALL
        # defenses in the template, otherwise the FEFF00AD assertion is
        # vacuous: with the show rule active, body hyphens become U+2011
        # before Typst's hyphenation algorithm runs, so even with
        # hyphenate: true no markers are emitted in the body.
        patched = patched.replace('#show "-": "\\u{2011}"\n', "")
        assert patched != original, (
            "Failed to patch hyphenate flag; template format changed?"
        )
        # Symmetric assertions: the hyphenate flip and the show-rule strip
        # must BOTH actually land. Either silent no-op would make the
        # FEFF00AD assertion vacuous (the strip protects against the body
        # show rule pre-empting auto-hyphenation; the flip protects
        # against the original hyphenate: false defense suppressing
        # ActualText markers entirely).
        assert "hyphenate: true" in patched, (
            "Failed to flip hyphenate flag to true; the `hyphenate: false` "
            "literal in cover_letter.typ likely drifted (whitespace, "
            "comma placement, quoting). Without the flip, no /ActualText "
            "FEFF00AD markers will appear and the assertion stays vacuous."
        )
        assert "hyphenate: false" not in patched, (
            "Patched template still contains `hyphenate: false`; the "
            "replace did not land or there's a second occurrence to strip."
        )
        assert '#show "-": "\\u{2011}"' not in patched, (
            "Failed to strip U+2011 show rule from positive-control "
            "template; the rule must be removed alongside the hyphenate "
            "flip or the FEFF00AD assertion stays vacuous."
        )
        patched_template.write_text(patched, encoding="utf-8")

        letter = valid_cover_letter()
        artifacts = _render_cover_letter(
            render_dir,
            letter,
            patched_template,
            signer_name="Test Candidate",
            skip_pdf=False,
        )
        pdf_path = artifacts.pdf_path
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


# ---------------------------------------------------------------------------
# Non-breaking hyphen (U+2011) substitution in the cover letter body
#
# Background: even with `hyphenate: false` preventing Typst from inserting
# /ActualText FEFF00AD markers (above), the cover letter PDF still exposes
# the user to tofu boxes when Chrome/Acrobat copy a line break that falls
# on an existing hyphen. Those readers heuristically rewrite "word-\nrest"
# into "word­rest" (SOFT HYPHEN), which web fonts lacking U+00AD
# render as boxes on paste.
#
# Defense (in `cover_letter.typ`): a body-scoped `#show "-": "\u{2011}"`
# rule replaces ASCII hyphens with U+2011 NON-BREAKING HYPHEN inside the
# salutation-through-name block. Typst cannot break a line at U+2011, so
# the reader heuristic never fires. Letterhead URL/email/phone retain
# ASCII `-` so they paste as resolvable identifiers.
# ---------------------------------------------------------------------------


def _extract_pdf_text(pdf_path: Path) -> str:
    """Return the concatenated text of every page via pdfplumber.

    Uses pdfplumber rather than pypdf for text extraction because
    pdfplumber's character iteration honors ToUnicode CMaps consistently
    across font subsets, including the non-BMP codepoints the U+2011
    substitution emits. Pages are joined by ``\\n``.
    """
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


@pytest.mark.integration
@pytest.mark.skipif(not TYPST_AVAILABLE, reason="Typst not installed")
class TestCoverLetterNonBreakingHyphens:
    """Pin the body-scoped U+2011 substitution in cover_letter.typ.

    Geometry-independent tests assert the substitution mechanism without
    coupling to the template's line-wrap positions, which drift with
    fixture word counts and font availability. The positive control
    compiles a checked-in template variant that omits the show rule,
    proving the negative tests aren't passing because the source already
    had no hyphens or because the assertion harness is broken.
    """

    def test_body_hyphens_substituted_with_u2011(self, typst_safe_dir: Path) -> None:
        """U+2011 appears in the body text wherever the source had `-`."""
        from curator import default_cover_letter_template_path
        from tests.helpers import valid_cover_letter

        _write_minimal_basics(typst_safe_dir)
        letter = valid_cover_letter()

        # Fixture sanity: the assertion below relies on specific compounds
        # being present in the body and closing. If the fixture loses any
        # of them, surface the decoupling here instead of letting the
        # substitution assertion below fail with a confusing diff.
        body_text = " ".join([letter.opening, *letter.body_paragraphs, letter.closing])
        expected_source_compounds = (
            "multi-region",  # body_paragraph_1
            "nine-month",  # body_paragraph_1
            "developer-hour",  # body_paragraph_1
            "deployment-safety",  # closing
        )
        for compound in expected_source_compounds:
            assert compound in body_text, (
                f"Fixture lost compound {compound!r}; the U+2011 "
                "substitution assertion below is no longer meaningful. "
                "Restore in tests/helpers.valid_cover_letter_kwargs() "
                "or update this list."
            )

        artifacts = _render_cover_letter(
            typst_safe_dir,
            letter,
            default_cover_letter_template_path(),
            signer_name="Test Candidate",
            skip_pdf=False,
        )
        assert artifacts.pdf_path is not None
        text = _extract_pdf_text(artifacts.pdf_path)

        # Property-level guarantee: U+2011 is the substitution destination,
        # so it MUST appear in the rendered text.
        assert "\u2011" in text, (
            "Cover letter body has no U+2011 codepoint. The "
            '`#show "-": "\\u{2011}"` rule in cover_letter.typ likely '
            "did not fire. Check the body-scoped content block in "
            "src/curator/templates/cover_letter.typ."
        )

        # Substitution covers every body hyphen across multiple paragraphs,
        # not just the first one or just the first paragraph. Catches a
        # regression where the show rule fires only at the first match,
        # is scoped to a single paragraph, or stops applying to ``closing``.
        for compound in expected_source_compounds:
            u2011_form = compound.replace("-", "\u2011")
            assert u2011_form in text, (
                f"Expected {compound!r} from the fixture to render as "
                f"{u2011_form!r} in the PDF; got plain ASCII. The show "
                "rule may have been scoped narrower than intended "
                "(first-match-only, or only one paragraph)."
            )

    def test_letterhead_retains_ascii_hyphens(self, typst_safe_dir: Path) -> None:
        """Letterhead URL/email/phone keep ASCII `-` so paste resolves them."""
        from curator import default_cover_letter_template_path
        from tests.helpers import valid_cover_letter

        # Letterhead fields with hyphens that MUST paste as ASCII so the
        # destination application can resolve them as a phone number,
        # mailto: target, and URL respectively.
        data_dir = typst_safe_dir / "data"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "basics.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "Test Candidate",
                    "email": "first-last@example.com",
                    "phone": "(555) 202-2179",
                    "url": "https://example-domain.test/about/",
                }
            ),
            encoding="utf-8",
        )
        letter = valid_cover_letter()

        artifacts = _render_cover_letter(
            typst_safe_dir,
            letter,
            default_cover_letter_template_path(),
            signer_name="Test Candidate",
            skip_pdf=False,
        )
        assert artifacts.pdf_path is not None
        text = _extract_pdf_text(artifacts.pdf_path)

        # Each letterhead identifier must paste as ASCII. The U+2011
        # substitution must NOT have reached these fields. Phone number
        # is the most user-visible (clipboard-paste-to-tel:).
        assert "(555) 202-2179" in text, (
            "Letterhead phone number was rewritten or dropped. The "
            "U+2011 show rule must be scoped to the body block in "
            "cover_letter.typ; check that the letterhead lives OUTSIDE "
            "the body content block."
        )
        assert "first-last@example.com" in text, (
            "Letterhead email local-part hyphen was rewritten. Same "
            "scoping concern as above."
        )
        assert "example-domain.test" in text, (
            "Letterhead URL slug hyphen was rewritten. Same scoping concern as above."
        )

    def test_positive_control_no_show_rule_emits_ascii_hyphens(
        self, typst_safe_dir: Path
    ) -> None:
        """Without the show rule, ASCII hyphens survive into the PDF.

        Proves the negative tests above aren't passing vacuously (e.g.
        because pdfplumber strips U+2011, or because the fixture has no
        hyphens to begin with). The variant template is checked in at
        ``tests/integration/templates/cover_letter_no_show_rule.typ``;
        if it ever silently mirrors the packaged template, the drift
        check fires before the substantive assertion.
        """
        from curator import default_cover_letter_template_path
        from tests.helpers import valid_cover_letter

        variant_path = (
            Path(__file__).parent / "templates" / "cover_letter_no_show_rule.typ"
        )
        packaged_path = default_cover_letter_template_path()

        # Drift check: variant must differ from packaged. A future
        # contributor that "fixes" the variant to match packaged would
        # silently turn the positive control vacuous.
        assert variant_path.read_text(encoding="utf-8") != packaged_path.read_text(
            encoding="utf-8"
        ), (
            "Positive-control template is identical to the packaged "
            "template. The variant must omit the "
            '`#show "-": "\\u{2011}"` line; see the file header for '
            "rationale."
        )

        _write_minimal_basics(typst_safe_dir)
        letter = valid_cover_letter()

        artifacts = _render_cover_letter(
            typst_safe_dir,
            letter,
            variant_path,
            signer_name="Test Candidate",
            skip_pdf=False,
        )
        assert artifacts.pdf_path is not None
        text = _extract_pdf_text(artifacts.pdf_path)

        # Without the show rule, body hyphens must survive as ASCII.
        assert "multi-region" in text, (
            "Positive control failed: without the show rule, "
            "'multi-region' should render as ASCII in the PDF text. "
            "Either pdfplumber is normalizing U+2011 -> '-' (which "
            "would invalidate the negative tests above) or Typst is "
            "applying an unexpected substitution. Investigate before "
            "trusting the negative tests."
        )
        # And U+2011 must NOT appear without the show rule.
        assert "\u2011" not in text, (
            "Positive control template emitted U+2011 despite omitting "
            "the show rule. Source of the U+2011 is unclear; review the "
            "variant and rule out font ligatures or Typst defaults."
        )
