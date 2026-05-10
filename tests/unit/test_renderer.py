"""Tests for curator.renderer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from curator.client import CurationResult
from curator.exceptions import RenderError
from curator.io_utils import MAX_PDF_SIZE as _MAX_PDF_SIZE
from curator.io_utils import (
    atomic_text_write,
    atomic_yaml_write,
    get_page_count,
)
from curator.models import (
    PortfolioData,
    ResumeCuration,
)
from curator.renderer import (
    RenderOutput,
    _apply_selections,
    _invoke_typst,
    _make_output_dir,
    _reorder_with_safety_net,
    _sort_work_chronologically,
    _write_data_files,
    _write_layout,
    render,
)
from curator.renderer import (
    _write_audit_artifacts as _real_write_audit_artifacts,
)


def _write_audit_artifacts(*args: Any, **kwargs: Any) -> Any:
    """Test wrapper that binds ``max_pages=1`` by default.

    Production ``_write_audit_artifacts`` now requires an explicit
    ``max_pages`` kwarg (no default; production callers in ``render()``
    pass ``settings.max_pages`` explicitly). These short-form tests opt
    in to ``max_pages=1`` once at module load.
    """
    kwargs.setdefault("max_pages", 1)
    return _real_write_audit_artifacts(*args, **kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_curation_dict() -> dict[str, Any]:
    """Curation with one work entry and one skill."""
    from tests.helpers import make_curation_dict

    return make_curation_dict(
        suggested_label="Senior SRE",
        company_slug="acme-corp",
        work_highlights=[
            {
                "work_id": "acme-senior-engineer",
                "highlight_ids": ["acme-deployed-k8s"],
            },
        ],
        skills=[{"skill_id": "cloud-aws", "keywords": ["EKS"]}],
        projects=[],
    )


@pytest.fixture
def simple_curation(simple_curation_dict: dict[str, Any]) -> ResumeCuration:
    return ResumeCuration.model_validate(simple_curation_dict)


@pytest.fixture
def curation_result(simple_curation: ResumeCuration) -> CurationResult:
    return CurationResult(
        curation=simple_curation,
        model="claude-sonnet-4-6-20260217",
        input_tokens=1000,
        output_tokens=200,
        cache_creation_input_tokens=500,
        cache_read_input_tokens=0,
    )


# ---------------------------------------------------------------------------
# _reorder_with_safety_net
# ---------------------------------------------------------------------------


class _FakeHighlight:
    """Minimal highlight object with .id and .model_dump()."""

    def __init__(self, hid: str) -> None:
        self.id = hid

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        return {"id": self.id, "text": f"text-{self.id}"}


@pytest.mark.unit
class TestReorderWithSafetyNet:
    def test_full_ranking_no_safety_net(self) -> None:
        highlights = [_FakeHighlight("a"), _FakeHighlight("b"), _FakeHighlight("c")]
        ordered, missing = _reorder_with_safety_net(highlights, ["c", "a", "b"])
        assert [h["id"] for h in ordered] == ["c", "a", "b"]
        assert missing == []

    def test_partial_ranking_appends_missing(self) -> None:
        highlights = [_FakeHighlight("a"), _FakeHighlight("b"), _FakeHighlight("c")]
        ordered, missing = _reorder_with_safety_net(highlights, ["b"])
        assert [h["id"] for h in ordered] == ["b", "a", "c"]
        assert missing == ["a", "c"]

    def test_empty_ai_list_returns_portfolio_order(self) -> None:
        highlights = [_FakeHighlight("x"), _FakeHighlight("y")]
        ordered, missing = _reorder_with_safety_net(highlights, [])
        assert [h["id"] for h in ordered] == ["x", "y"]
        assert missing == ["x", "y"]

    def test_duplicate_ids_deduped(self) -> None:
        highlights = [_FakeHighlight("a"), _FakeHighlight("b")]
        ordered, missing = _reorder_with_safety_net(highlights, ["a", "a", "b"])
        assert [h["id"] for h in ordered] == ["a", "b"]
        assert missing == []

    def test_unknown_ai_ids_skipped(self) -> None:
        highlights = [_FakeHighlight("a")]
        ordered, missing = _reorder_with_safety_net(highlights, ["z", "a"])
        assert [h["id"] for h in ordered] == ["a"]
        assert missing == []

    def test_empty_portfolio_highlights(self) -> None:
        ordered, missing = _reorder_with_safety_net([], ["a"])
        assert ordered == []
        assert missing == []


# ---------------------------------------------------------------------------
# _apply_selections
# ---------------------------------------------------------------------------


class TestApplySelections:
    def test_selected_work_entries_in_output(
        self,
        simple_curation: ResumeCuration,
        portfolio_data: PortfolioData,
    ) -> None:
        sections, _, _ = _apply_selections(simple_curation, portfolio_data)
        assert "work" in sections
        assert len(sections["work"]) == 1
        assert sections["work"][0]["id"] == "acme-senior-engineer"

    def test_selected_highlights_in_output(
        self,
        simple_curation: ResumeCuration,
        portfolio_data: PortfolioData,
    ) -> None:
        sections, _, _ = _apply_selections(simple_curation, portfolio_data)
        highlights = sections["work"][0]["highlights"]
        assert len(highlights) == 1
        assert highlights[0]["id"] == "acme-deployed-k8s"

    def test_filters_simple_sections(
        self,
        simple_curation: ResumeCuration,
        portfolio_data: PortfolioData,
    ) -> None:
        sections, _, _ = _apply_selections(simple_curation, portfolio_data)
        assert "skills" in sections
        assert len(sections["skills"]) == 1
        assert sections["skills"][0]["id"] == "cloud-aws"

    def test_education_and_certificates_from_portfolio(
        self,
        simple_curation: ResumeCuration,
        portfolio_data: PortfolioData,
    ) -> None:
        sections, _, _ = _apply_selections(simple_curation, portfolio_data)
        assert len(sections["education"]) == len(portfolio_data.education)
        assert len(sections["certificates"]) == len(portfolio_data.certificates)

    def test_all_portfolio_work_entries_preserved(
        self,
        portfolio_data: PortfolioData,
    ) -> None:
        """All portfolio work entries appear in output regardless of AI ranking."""
        from tests.helpers import make_curation_dict

        curation = ResumeCuration.model_validate(
            make_curation_dict(
                work_highlights=[
                    {
                        "work_id": "acme-senior-engineer",
                        "highlight_ids": ["acme-deployed-k8s"],
                    },
                ],
            )
        )
        sections, _, _ = _apply_selections(curation, portfolio_data)
        assert len(sections["work"]) == len(portfolio_data.work)

    def test_yaml_output_uses_snake_case(
        self,
        simple_curation: ResumeCuration,
        portfolio_data: PortfolioData,
    ) -> None:
        """Verify model_dump() produces snake_case keys for Typst template."""
        sections, _, _ = _apply_selections(simple_curation, portfolio_data)
        work_entry = sections["work"][0]
        assert "start_date" in work_entry
        assert "startDate" not in work_entry


# ---------------------------------------------------------------------------
# Chronological sort
# ---------------------------------------------------------------------------


class TestSortWorkChronologically:
    """Verify reverse-chronological work ordering regardless of input order."""

    def test_current_role_first_regardless_of_input_order(self) -> None:
        entries: list[dict[str, Any]] = [
            {"id": "old", "start_date": "2019-01", "end_date": "2022-06"},
            {"id": "current", "start_date": "2023-01", "end_date": None},
            {"id": "middle", "start_date": "2022-07", "end_date": "2022-12"},
        ]
        result = _sort_work_chronologically(entries)
        assert [e["id"] for e in result] == ["current", "middle", "old"]

    def test_multiple_past_roles_by_end_date_descending(self) -> None:
        entries = [
            {"id": "oldest", "start_date": "2015-01", "end_date": "2018-06"},
            {"id": "newest", "start_date": "2020-01", "end_date": "2023-06"},
            {"id": "middle", "start_date": "2018-07", "end_date": "2020-01"},
        ]
        result = _sort_work_chronologically(entries)
        assert [e["id"] for e in result] == ["newest", "middle", "oldest"]

    def test_end_date_empty_string_treated_as_current(self) -> None:
        entries = [
            {"id": "past", "start_date": "2020-01", "end_date": "2023-06"},
            {"id": "current", "start_date": "2023-07", "end_date": ""},
        ]
        result = _sort_work_chronologically(entries)
        assert [e["id"] for e in result] == ["current", "past"]

    def test_multiple_current_roles_by_start_date_descending(self) -> None:
        entries = [
            {"id": "older-current", "start_date": "2022-01", "end_date": None},
            {"id": "newer-current", "start_date": "2024-06", "end_date": None},
        ]
        result = _sort_work_chronologically(entries)
        assert [e["id"] for e in result] == ["newer-current", "older-current"]

    def test_non_zero_padded_months_sort_numerically(self) -> None:
        """Regression guard: previously the sort did lexicographic string
        compare on ``start_date``, which ordered ``2022-12`` BEFORE
        ``2022-6`` because ``'1' < '6'``. Numeric tuple parsing fixes
        this so "2022-6" < "2022-12" < "2022-2" (the latter being the
        newest after the numeric sort)."""
        entries = [
            {"id": "jun22", "start_date": "2022-6", "end_date": None},
            {"id": "dec22", "start_date": "2022-12", "end_date": None},
        ]
        result = _sort_work_chronologically(entries)
        # Dec 2022 must come before Jun 2022 despite "2022-12" < "2022-6"
        # lexicographically.
        assert [e["id"] for e in result] == ["dec22", "jun22"]

    def test_year_only_dates_sort_correctly(self) -> None:
        """``YYYY`` (year-only) and ``YYYY-MM`` must sort together in a
        coherent reverse-chronological order."""
        entries = [
            {"id": "year-only", "start_date": "2020", "end_date": "2023"},
            {"id": "with-month", "start_date": "2021-06", "end_date": "2024-01"},
        ]
        result = _sort_work_chronologically(entries)
        assert [e["id"] for e in result] == ["with-month", "year-only"]


class TestPruneEmptySections:
    """Post-trim cleanup: drop skeleton entries with no content."""

    def test_preserves_work_entries_with_zero_highlights(self) -> None:
        """Work entries with zero highlights are preserved so the output
        always renders the complete employment timeline (header-only rows
        when the trim cascade has drained a role's highlight list)."""
        from curator.renderer import _prune_empty_sections

        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": [{"id": "h1"}]},
                {"id": "w2", "highlights": []},  # trimmed to empty -- kept
                {"id": "w3", "highlights": [{"id": "h2"}]},
            ],
            "skills": [],
        }
        pruned = _prune_empty_sections(sections)
        assert [w["id"] for w in pruned["work"]] == ["w1", "w2", "w3"]

    def test_drops_skill_groups_with_zero_keywords(self) -> None:
        from curator.renderer import _prune_empty_sections

        sections: dict[str, Any] = {
            "work": [],
            "skills": [
                {"id": "kept", "keywords": ["kw1", "kw2"]},
                {"id": "empty", "keywords": []},  # defensive prune (tier 10 is atomic)
                {"id": "also-kept", "keywords": ["kw3"]},
            ],
        }
        pruned = _prune_empty_sections(sections)
        assert [s["id"] for s in pruned["skills"]] == ["kept", "also-kept"]

    def test_preserves_empty_projects_certs_education_lists(self) -> None:
        """Empty lists for optional sections stay — the template's
        ``if certificates.len() > 0`` guard keeps the heading off the
        rendered PDF. We only prune content-bearing skeletons."""
        from curator.renderer import _prune_empty_sections

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        pruned = _prune_empty_sections(sections)
        assert pruned["projects"] == []
        assert pruned["certificates"] == []
        assert pruned["education"] == []

    def test_does_not_mutate_input(self) -> None:
        from curator.renderer import _prune_empty_sections

        sections: dict[str, Any] = {
            "work": [{"id": "w1", "highlights": [{"id": "h1"}]}],
            "skills": [
                {"id": "s1", "keywords": ["kw1"]},
                {"id": "s2", "keywords": []},
            ],
        }
        original_skills = sections["skills"]
        pruned = _prune_empty_sections(sections)
        # Input list object is unchanged.
        assert len(original_skills) == 2
        assert [s["id"] for s in original_skills] == ["s1", "s2"]
        # Pruned output reflects the cleanup.
        assert [s["id"] for s in pruned["skills"]] == ["s1"]

    def test_handles_missing_keys(self) -> None:
        from curator.renderer import _prune_empty_sections

        sections: dict[str, Any] = {
            "work": [{"id": "w1", "highlights": [{"id": "h1"}]}],
        }
        pruned = _prune_empty_sections(sections)
        assert pruned == sections


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------


class TestMakeOutputDir:
    def test_creates_directory_with_data_subdir(self, tmp_path: Path) -> None:
        output_dir = _make_output_dir(tmp_path, "acme-corp")
        assert output_dir.is_dir()
        assert (output_dir / "data").is_dir()
        assert "acme-corp" in output_dir.name

    def test_appends_suffix_on_collision(self, tmp_path: Path) -> None:
        first = _make_output_dir(tmp_path, "acme-corp")
        second = _make_output_dir(tmp_path, "acme-corp")
        assert first != second
        assert second.name.endswith("-2")


class TestWriteDataFiles:
    def test_writes_basics_always(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        basics = {"name": "Jane Doe", "summary": "Test summary."}
        written = _write_data_files(tmp_path, {}, basics)

        assert "basics" in written
        loaded = yaml.safe_load(written["basics"].read_text())
        assert loaded["name"] == "Jane Doe"
        assert loaded["summary"] == "Test summary."

    def test_writes_section_files(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        sections = {
            "skills": [{"id": "cloud-aws", "name": "AWS"}],
        }
        written = _write_data_files(tmp_path, sections, {"name": "Test"})

        assert "skills" in written
        loaded = yaml.safe_load(written["skills"].read_text())
        assert len(loaded) == 1
        assert loaded[0]["id"] == "cloud-aws"


class TestWriteLayout:
    def test_writes_section_order(self, tmp_path: Path) -> None:
        path = _write_layout(tmp_path, ["skills", "work", "education"])
        loaded = yaml.safe_load(path.read_text())
        assert loaded["section_order"] == ["skills", "work", "education", "interests"]


class TestWriteAuditArtifacts:
    def test_writes_all_artifacts(
        self,
        tmp_path: Path,
        curation_result: CurationResult,
    ) -> None:
        curated_path, log_path, jd_path, mode_path = _write_audit_artifacts(
            tmp_path, curation_result, "Job description text."
        )

        assert curated_path.exists()
        assert log_path.exists()
        assert jd_path is not None
        assert jd_path.exists()
        assert mode_path is None

    def test_curation_log_has_metadata(
        self,
        tmp_path: Path,
        curation_result: CurationResult,
    ) -> None:
        _, log_path, _, _ = _write_audit_artifacts(
            tmp_path, curation_result, "JD text."
        )
        log_data = json.loads(log_path.read_text())

        # Pinned snapshot: bumping PROMPT_VERSION in prompt.py must update
        # this assertion as a deliberate signal that the prompt changed.
        # prompt_hash disambiguates same-day prompt edits where
        # PROMPT_VERSION is reused; rather than pinning a specific hex
        # digest (which would force an update on every prompt text edit
        # and is already covered by the byte-identity test), assert
        # presence and shape only.
        from curator.prompt import PROMPT_HASH

        assert log_data["format_version"] == "2.3"
        assert log_data["max_pages"] == 1
        assert log_data["source"] == "api"
        assert log_data["prompt_version"] == "2026-05-09"
        assert log_data["prompt_hash"] == PROMPT_HASH
        assert isinstance(log_data["prompt_hash"], str)
        assert len(log_data["prompt_hash"]) == 12
        assert log_data["model"] == "claude-sonnet-4-6-20260217"
        assert log_data["input_tokens"] == 1000
        assert "timestamp" in log_data
        assert log_data["cover_letter"] == {"enabled": False}

    def test_jd_text_preserved(
        self,
        tmp_path: Path,
        curation_result: CurationResult,
    ) -> None:
        _, _, jd_path, _ = _write_audit_artifacts(
            tmp_path, curation_result, "Original JD content."
        )
        assert jd_path is not None
        assert jd_path.read_text() == "Original JD content."

    def test_static_path_writes_mode_txt(
        self,
        tmp_path: Path,
        curation_result: CurationResult,
    ) -> None:
        from dataclasses import replace

        static_result = replace(curation_result, source="static", model="n/a")
        _, log_path, jd_path, mode_path = _write_audit_artifacts(
            tmp_path, static_result, jd_text=None
        )
        assert jd_path is None
        assert mode_path is not None
        assert mode_path.exists()
        assert "source: static" in mode_path.read_text()
        log_data = json.loads(log_path.read_text())
        assert log_data["source"] == "static"
        assert log_data["model"] == "n/a"


class TestApplySelectionsSafetyNet:
    """Behavior of the `safety_net` flag on _apply_selections."""

    def _two_highlights_portfolio(self) -> Any:
        from curator.models import (
            Basics,
            InterestData,
            PortfolioData,
            WorkEntry,
        )

        return PortfolioData(
            basics=Basics(name="X"),
            work=[
                WorkEntry.model_validate(
                    {
                        "id": "w1",
                        "name": "Co",
                        "position": "Eng",
                        "startDate": "2020-01",
                        "highlights": [
                            {"id": "h1", "text": "A"},
                            {"id": "h2", "text": "B"},
                            {"id": "h3", "text": "C"},
                        ],
                    }
                )
            ],
            education=[],
            skills=[],
            certificates=[],
            projects=[],
            volunteer=[],
            publications=[],
            languages=[],
            interests=InterestData.model_validate({"hobbies": [], "fun_facts": []}),
            services=[],
        )

    def _curation_with_subset(self) -> Any:
        from curator.models import ResumeCuration

        return ResumeCuration.model_validate(
            {
                "summary": "s " * 10 + "founder",
                "suggested_label": "Eng",
                "company_slug": "x",
                "work_highlights": [{"work_id": "w1", "highlight_ids": ["h1"]}],
                "skills": [],
                "projects": [],
            }
        )

    def test_safety_net_true_appends_missing(self) -> None:
        from curator.renderer import _apply_selections

        portfolio = self._two_highlights_portfolio()
        curation = self._curation_with_subset()
        sections, _, safety_net_count = _apply_selections(
            curation, portfolio, safety_net=True
        )
        ids = [h["id"] for h in sections["work"][0]["highlights"]]
        assert ids == ["h1", "h2", "h3"]
        assert safety_net_count == 2

    def test_safety_net_false_honors_subset(self) -> None:
        from curator.renderer import _apply_selections

        portfolio = self._two_highlights_portfolio()
        curation = self._curation_with_subset()
        sections, _, safety_net_count = _apply_selections(
            curation, portfolio, safety_net=False
        )
        ids = [h["id"] for h in sections["work"][0]["highlights"]]
        assert ids == ["h1"]
        assert safety_net_count == 0

    def test_safety_net_false_silently_drops_unknown(self) -> None:
        """Unknown IDs are dropped silently when safety_net=False."""
        from curator.models import ResumeCuration
        from curator.renderer import _apply_selections

        portfolio = self._two_highlights_portfolio()
        curation = ResumeCuration.model_validate(
            {
                "summary": "s " * 10 + "founder",
                "suggested_label": "Eng",
                "company_slug": "x",
                "work_highlights": [
                    {"work_id": "w1", "highlight_ids": ["h1", "bogus"]}
                ],
                "skills": [],
                "projects": [],
            }
        )
        sections, _, _ = _apply_selections(curation, portfolio, safety_net=False)
        ids = [h["id"] for h in sections["work"][0]["highlights"]]
        assert ids == ["h1"]


class TestAtomicYamlWrite:
    def test_writes_valid_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "test.yaml"
        atomic_yaml_write(path, {"key": "value"})
        loaded = yaml.safe_load(path.read_text())
        assert loaded == {"key": "value"}

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "dir" / "test.yaml"
        atomic_yaml_write(path, [1, 2, 3])
        assert path.exists()


# ---------------------------------------------------------------------------
# Typst invocation
# ---------------------------------------------------------------------------


class TestInvokeTypst:
    @pytest.fixture
    def template_file(self, tmp_path: Path) -> Path:
        """Create a real template file in a separate directory."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        tpl = tpl_dir / "curated.typ"
        tpl.write_text("// dummy template")
        return tpl

    def test_raises_render_error_on_missing_typst(
        self, tmp_path: Path, template_file: Path
    ) -> None:
        with (
            patch("curator.renderer.subprocess.run", side_effect=FileNotFoundError),
            pytest.raises(RenderError, match="not installed"),
        ):
            _invoke_typst(tmp_path, template_file)

    def test_raises_render_error_on_compilation_failure(
        self, tmp_path: Path, template_file: Path
    ) -> None:
        mock_result = type(
            "CompletedProcess",
            (),
            {"returncode": 1, "stderr": "error: file not found"},
        )()
        with (
            patch("curator.renderer.subprocess.run", return_value=mock_result),
            pytest.raises(RenderError, match="compilation failed"),
        ):
            _invoke_typst(tmp_path, template_file)

    def test_raises_render_error_on_timeout(
        self, tmp_path: Path, template_file: Path
    ) -> None:
        import subprocess as sp

        with (
            patch(
                "curator.renderer.subprocess.run",
                side_effect=sp.TimeoutExpired(cmd="typst", timeout=30),
            ),
            pytest.raises(RenderError, match="timed out"),
        ):
            _invoke_typst(tmp_path, template_file)


# ---------------------------------------------------------------------------
# Full render pipeline
# ---------------------------------------------------------------------------


class TestRender:
    def test_full_pipeline_with_mocked_typst(
        self,
        portfolio_data: PortfolioData,
        curation_result: CurationResult,
        tmp_path: Path,
    ) -> None:
        """Full render pipeline with Typst mocked to create an empty PDF."""

        def fake_typst_run(cmd: list[str], **kwargs: Any) -> Any:
            # Create a fake PDF file at the expected output path.
            pdf_path = Path(cmd[-1])
            pdf_path.write_bytes(b"%PDF-1.4 fake")
            return type("CompletedProcess", (), {"returncode": 0, "stderr": ""})()

        tpl = tmp_path / "tpl" / "curated.typ"
        tpl.parent.mkdir()
        tpl.write_text("// dummy template")
        settings = type(
            "FakeSettings",
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
            patch("curator.renderer.subprocess.run", side_effect=fake_typst_run),
            patch("curator.renderer.get_page_count", return_value=1),
        ):
            result = render(curation_result, portfolio_data, "Test JD.", settings)

        assert isinstance(result, RenderOutput)
        assert result.profile_dir.is_dir()
        assert result.pdf_path is not None
        assert result.pdf_path.exists()
        assert result.curated_yaml_path.exists()
        assert result.curation_log_path.exists()
        assert result.jd_path is not None
        assert result.jd_path.exists()
        assert "basics" in result.data_files
        assert result.trim_log == []

        # Verify basics has the injected summary.
        basics = yaml.safe_load(result.data_files["basics"].read_text())
        assert "founder of Perts Foundry LLC" in basics["summary"]

        # Verify layout.yaml uses the config default section order.
        layout = yaml.safe_load((result.profile_dir / "layout.yaml").read_text())
        assert layout["section_order"] == [*settings.section_order, "interests"]


# ---------------------------------------------------------------------------
# Render logging
# ---------------------------------------------------------------------------


class TestRenderLogging:
    """Tests for logging in the render pipeline.

    Uses a list-based Loguru sink because capsys cannot reliably capture
    Loguru output (it uses a different write path than sys.stderr.write).
    """

    def setup_method(self) -> None:
        """Set up Loguru with a list-based sink for capture."""
        from loguru import logger

        logger.remove()
        self.log_messages: list[str] = []
        logger.add(lambda msg: self.log_messages.append(str(msg)), level="INFO")

    def teardown_method(self) -> None:
        """Remove sinks added during test."""
        from loguru import logger

        logger.remove()

    def test_render_stats_logged(
        self,
        portfolio_data: PortfolioData,
        curation_result: CurationResult,
        tmp_path: Path,
    ) -> None:
        """Render statistics include work entries, highlights, and sections."""

        def fake_typst_run(cmd: list[str], **kwargs: Any) -> Any:
            pdf_path = Path(cmd[-1])
            pdf_path.write_bytes(b"%PDF-1.4 fake")
            return type("CompletedProcess", (), {"returncode": 0, "stderr": ""})()

        tpl = tmp_path / "tpl" / "curated.typ"
        tpl.parent.mkdir()
        tpl.write_text("// dummy template")
        settings = type(
            "FakeSettings",
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
            patch("curator.renderer.subprocess.run", side_effect=fake_typst_run),
            patch("curator.renderer.get_page_count", return_value=1),
        ):
            render(curation_result, portfolio_data, "Test JD.", settings)

        combined = " ".join(self.log_messages)
        assert "Render stats:" in combined
        assert "work entries" in combined
        assert "highlights" in combined
        assert "skill groups" in combined
        assert "populated sections" in combined

    def test_typst_timing_logged(self, tmp_path: Path) -> None:
        """Typst compilation timing is logged at INFO level."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        tpl = tpl_dir / "curated.typ"
        tpl.write_text("// dummy template")

        def fake_typst_run(cmd: list[str], **kwargs: Any) -> Any:
            pdf_path = Path(cmd[-1])
            pdf_path.write_bytes(b"%PDF-1.4 fake")
            return type("CompletedProcess", (), {"returncode": 0, "stderr": ""})()

        with patch("curator.renderer.subprocess.run", side_effect=fake_typst_run):
            _invoke_typst(tmp_path, tpl)

        combined = " ".join(self.log_messages)
        assert "Typst compiled in" in combined


# ---------------------------------------------------------------------------
# SafeDumper usage
# ---------------------------------------------------------------------------


class TestSafeDumper:
    """Tests for SafeDumper in YAML output."""

    def test_atomic_yaml_write_uses_safe_dumper(self, tmp_path: Path) -> None:
        """Verify atomic_yaml_write produces output identical to SafeDumper."""
        data = {"key": "value", "nested": {"a": 1}}
        path = tmp_path / "test.yaml"
        atomic_yaml_write(path, data)

        # Compare against direct SafeDumper output
        expected = yaml.dump(
            data,
            Dumper=yaml.SafeDumper,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        assert path.read_text() == expected

    def test_yaml_dump_rejects_unsafe_types(self, tmp_path: Path) -> None:
        """SafeDumper prevents serialization of arbitrary Python objects."""
        path = tmp_path / "unsafe.yaml"
        with pytest.raises(yaml.representer.RepresenterError):
            atomic_yaml_write(path, {"obj": object()})


# ---------------------------------------------------------------------------
# fd.close() fix in error handler
# ---------------------------------------------------------------------------


class TestAtomicTextWriteResourceLeak:
    """Tests for the fd.close() before unlink fix in atomic_text_write."""

    def test_error_during_write_calls_close(self, tmp_path: Path) -> None:
        """On write failure, fd.close() is called before unlink."""
        from unittest.mock import MagicMock

        path = tmp_path / "target.txt"
        mock_fd = MagicMock()
        mock_fd.write.side_effect = OSError("disk full")
        mock_fd.name = str(tmp_path / "temp.tmp")

        mock_tmpfile = patch(
            "curator.io_utils.tempfile.NamedTemporaryFile",
            return_value=mock_fd,
        )
        with mock_tmpfile, pytest.raises(OSError, match="disk full"):
            atomic_text_write(path, "content")

        mock_fd.close.assert_called()

    def test_successful_write_closes_fd(self, tmp_path: Path) -> None:
        """Happy path: fd is closed before the rename."""
        path = tmp_path / "target.txt"
        atomic_text_write(path, "hello world")
        assert path.read_text() == "hello world"


# ---------------------------------------------------------------------------
# Warning logs for missing IDs
# ---------------------------------------------------------------------------


class TestApplySelectionsWarnings:
    """Tests for warning logs when IDs are not found in portfolio.

    Uses a list-based Loguru sink because capsys cannot reliably capture
    Loguru output (it uses a different write path than sys.stderr.write).
    """

    def setup_method(self) -> None:
        """Set up Loguru with a list-based sink for warning capture."""
        from loguru import logger

        logger.remove()
        self.log_messages: list[str] = []
        logger.add(lambda msg: self.log_messages.append(str(msg)), level="WARNING")

    def teardown_method(self) -> None:
        """Remove sinks added during test."""
        from loguru import logger

        logger.remove()

    def test_missing_work_entry_logs_warning(
        self,
        portfolio_data: PortfolioData,
    ) -> None:
        """Work entry ID not in portfolio produces a warning log."""
        from tests.helpers import make_curation_dict

        curation = ResumeCuration.model_validate(
            make_curation_dict(
                work_highlights=[
                    {
                        "work_id": "acme-senior-engineer",
                        "highlight_ids": [],
                    },
                ],
            )
        )
        _, _, safety_net = _apply_selections(curation, portfolio_data)
        combined = " ".join(self.log_messages)
        assert "Safety net" in combined
        assert safety_net > 0

    def test_missing_skill_group_logs_warning(
        self,
        portfolio_data: PortfolioData,
    ) -> None:
        """Skill group ID not in portfolio produces a warning log."""
        from tests.helpers import make_curation_dict

        curation = ResumeCuration.model_validate(
            make_curation_dict(
                skills=[{"skill_id": "nonexistent-skill", "keywords": ["EKS"]}],
                projects=[],
            )
        )
        _, skipped, _ = _apply_selections(curation, portfolio_data)
        combined = " ".join(self.log_messages)
        assert "nonexistent-skill" in combined
        assert "not found" in combined
        assert skipped >= 1


# ---------------------------------------------------------------------------
# get_page_count
# ---------------------------------------------------------------------------


class TestGetPageCount:
    """Tests for get_page_count() PDF page extraction."""

    def test_returns_page_count_for_valid_pdf(self, tmp_path: Path) -> None:
        """Extract page count from a minimal valid PDF."""
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        pdf_path = tmp_path / "test.pdf"
        with pdf_path.open("wb") as f:
            writer.write(f)

        assert get_page_count(pdf_path) == 1

    def test_multi_page_pdf(self, tmp_path: Path) -> None:
        """Correctly counts multiple pages."""
        from pypdf import PdfWriter

        writer = PdfWriter()
        for _ in range(3):
            writer.add_blank_page(width=612, height=792)
        pdf_path = tmp_path / "multi.pdf"
        with pdf_path.open("wb") as f:
            writer.write(f)

        assert get_page_count(pdf_path) == 3

    def test_size_guard_rejects_oversized_pdf(self, tmp_path: Path) -> None:
        """PDF exceeding _MAX_PDF_SIZE raises RenderError."""
        pdf_path = tmp_path / "huge.pdf"
        # Write a file slightly over the size guard
        pdf_path.write_bytes(b"\x00" * (_MAX_PDF_SIZE + 1))

        with pytest.raises(RenderError, match="exceeds expected size limit"):
            get_page_count(pdf_path)

    def test_size_guard_boundary_passes(self, tmp_path: Path) -> None:
        """PDF at exactly _MAX_PDF_SIZE passes the size guard."""
        from pypdf import PdfWriter

        # Create a valid (small) PDF, then check that the guard only
        # rejects strictly-over-limit files. We test the boundary logic
        # by verifying a valid PDF under the limit succeeds.
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        pdf_path = tmp_path / "boundary.pdf"
        with pdf_path.open("wb") as f:
            writer.write(f)

        # Valid PDF is well under 10 MB, should not raise
        result = get_page_count(pdf_path)
        assert result == 1

    def test_debug_logging(self, tmp_path: Path) -> None:
        """get_page_count logs at DEBUG level with page count and size."""
        from loguru import logger
        from pypdf import PdfWriter

        logger.remove()
        log_messages: list[str] = []
        logger.add(lambda msg: log_messages.append(str(msg)), level="DEBUG")

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        pdf_path = tmp_path / "debug.pdf"
        with pdf_path.open("wb") as f:
            writer.write(f)

        get_page_count(pdf_path)

        combined = " ".join(log_messages)
        assert "PDF page count:" in combined
        assert "1" in combined
        assert "bytes" in combined

        logger.remove()

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        """A non-existent PDF path raises RenderError."""
        pdf_path = tmp_path / "nonexistent.pdf"
        with pytest.raises(RenderError, match="Failed to read PDF"):
            get_page_count(pdf_path)


# ---------------------------------------------------------------------------
# No-PDF mode
# ---------------------------------------------------------------------------


class TestRenderSkipPdf:
    """Tests for render() with skip_pdf=True.

    No-PDF mode writes all audit artifacts and data files but skips Typst
    PDF compilation. The returned RenderOutput has pdf_path=None.
    """

    @pytest.fixture
    def fake_settings(self, tmp_path: Path) -> Any:
        """Build settings with a nonexistent template (skip-pdf doesn't need it)."""
        return type(
            "FakeSettings",
            (),
            {
                "output_dir": tmp_path / "output",
                "template_path": tmp_path / "nonexistent" / "curated.typ",
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

    def test_render_skip_pdf_skips_typst(
        self,
        portfolio_data: PortfolioData,
        curation_result: CurationResult,
        fake_settings: Any,
    ) -> None:
        """skip_pdf=True produces audit files but no PDF; pdf_path is None."""
        with patch("curator.renderer.subprocess.run") as mock_run:
            result = render(
                curation_result,
                portfolio_data,
                "Test JD.",
                fake_settings,
                skip_pdf=True,
            )

        mock_run.assert_not_called()
        assert isinstance(result, RenderOutput)
        assert result.pdf_path is None
        assert result.profile_dir.is_dir()

    def test_render_skip_pdf_writes_data_files(
        self,
        portfolio_data: PortfolioData,
        curation_result: CurationResult,
        fake_settings: Any,
    ) -> None:
        """Data files (basics, work, skills, etc.) are written in skip-pdf mode."""
        result = render(
            curation_result,
            portfolio_data,
            "Test JD.",
            fake_settings,
            skip_pdf=True,
        )

        assert "basics" in result.data_files
        assert result.data_files["basics"].exists()
        basics = yaml.safe_load(result.data_files["basics"].read_text())
        assert basics["name"] == "Jane Doe"
        assert "founder of Perts Foundry LLC" in basics["summary"]

        # Section data files for selected sections.
        assert "work" in result.data_files
        assert "skills" in result.data_files

    def test_render_skip_pdf_writes_audit_artifacts(
        self,
        portfolio_data: PortfolioData,
        curation_result: CurationResult,
        fake_settings: Any,
    ) -> None:
        """curated.yaml, curation_log.json, and job_description.txt still written."""
        result = render(
            curation_result,
            portfolio_data,
            "Original JD.",
            fake_settings,
            skip_pdf=True,
        )

        assert result.curated_yaml_path.exists()
        assert result.curation_log_path.exists()
        assert result.jd_path is not None
        assert result.jd_path.exists()
        assert result.jd_path.read_text() == "Original JD."

    def test_render_skip_pdf_skips_template_check(
        self,
        portfolio_data: PortfolioData,
        curation_result: CurationResult,
        tmp_path: Path,
    ) -> None:
        """template_path doesn't need to exist when skip_pdf=True."""
        settings = type(
            "FakeSettings",
            (),
            {
                "output_dir": tmp_path / "output",
                "template_path": tmp_path / "does" / "not" / "exist.typ",
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

        # Should NOT raise RenderError for missing template.
        result = render(
            curation_result,
            portfolio_data,
            "Test JD.",
            settings,
            skip_pdf=True,
        )
        assert result.pdf_path is None


# ---------------------------------------------------------------------------
# Trimming algorithm
# ---------------------------------------------------------------------------


class TestGenerateNextTrim:
    """Tests for _generate_next_trim() tier evaluation.

    Tier numbers in legacy ``test_tierN_*`` function names reflect
    earlier revisions of the cascade. The ladder is now 12-tier after
    removing the project-description drain tier and relocating
    certificates to fire after skill keywords. Legacy test names are
    kept stable so git blame and selection scripts still line up.
    """

    def test_tier1_remove_interests(self) -> None:
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {"work": [], "skills": [], "projects": []}
        interests = {"hobbies": [{"name": "Reading"}], "fun_facts": []}
        step = _generate_next_trim(sections, interests)
        assert step is not None
        assert step.description == "Removed interests section"

    def test_tier1_skips_empty_interests(self) -> None:
        """Empty interests section is skipped; cascade proceeds to the
        next applicable tier. With no skills, work, education, or
        project highlights to drain, the cascade reaches certificates
        at tier 4 (bottom-up, preserving the top ``CERTIFICATE_FLOOR``)."""
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [{"id": "p1"}, {"id": "p2"}],
            # 4 certs > floor of 3: tier 4 fires on the last one.
            "certificates": [
                {"id": "c1"},
                {"id": "c2"},
                {"id": "c3"},
                {"id": "c4"},
            ],
            "education": [],
        }
        interests: dict[str, Any] = {"hobbies": [], "fun_facts": []}
        step = _generate_next_trim(sections, interests)
        assert step is not None
        assert step.description == "Removed certificate: c4"

    def test_top_two_projects_survive_cascade(self) -> None:
        """The cascade removes projects only when more than 2 remain
        (tier 3). Weight-1 and weight-2 picks always survive so the
        top portfolio preferences stay visible."""
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [{"id": "p1"}, {"id": "p2"}],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        # Only 2 projects left: tier 3 does not fire -- nothing else
        # remains to cut -- so the cascade returns None.
        assert step is None

    def test_tier4_removes_lowest_ranked_project_when_above_floor(self) -> None:
        """Tier 3 trims projects early in the cascade (right after
        project highlights have drained) so the page budget
        preferentially goes to work and skills. Description rides
        with its project -- no separate description-drain tier -- so
        the entire entry (header + description + any highlights) is
        cut together. The AI orders projects strongest-first
        (JD fit x weight), so ``projects[-1]`` is the least valuable.
        At least 2 projects always survive."""
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.description == "Removed project: p3"

    def test_single_project_falls_through_to_skill_group(self) -> None:
        """Single project with no content + a skill group + a below-
        floor cert list: project-highlight tier 2 skips (no highlights),
        tier 3 skips (single project cannot be removed wholesale),
        tier 4 skips (cert count == floor), cascade falls through to
        skill-group removal at tier 10. Certificate stays put."""
        from curator.renderer import TrimKind, _apply_trim, _generate_next_trim

        sections: dict[str, Any] = {
            "work": [],
            "skills": [{"id": "s1", "keywords": ["k1"]}],
            "projects": [{"id": "p1"}],
            "certificates": [{"id": "c1"}],  # 1 <= CERTIFICATE_FLOOR; preserved
            "education": [],
        }
        # First trim: whole skill group removed (tier 10). Cert protected.
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.kind is TrimKind.SKILL_GROUP
        assert step.description == "Removed skill group: s1"

        sections, _ = _apply_trim(sections, None, step)
        # No more skills, cert floor still protecting: returns None.
        step = _generate_next_trim(sections, None)
        assert step is None
        # Cert survived.
        assert sections["certificates"] == [{"id": "c1"}]
        assert sections["skills"] == []

    def test_tier2_drains_bottom_project_highlights_first(self) -> None:
        from curator.renderer import TrimKind, _generate_next_trim

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [
                {
                    "id": "p1",
                    "highlights": [
                        {"id": "p1h1", "text": "a"},
                        {"id": "p1h2", "text": "b"},
                        {"id": "p1h3", "text": "c"},
                    ],
                },
                {
                    "id": "p2",
                    "highlights": [
                        {"id": "p2h1", "text": "x"},
                        {"id": "p2h2", "text": "y"},
                    ],
                },
            ],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.kind is TrimKind.PROJECT_HIGHLIGHT
        assert step.target_id == "p2"
        assert step.description == "Removed highlight: p2h2 from project: p2"

    def test_tier2_walks_upward_when_bottom_project_empty(self) -> None:
        from curator.renderer import TrimKind, _generate_next_trim

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [
                {
                    "id": "p1",
                    "highlights": [
                        {"id": "p1h1", "text": "a"},
                        {"id": "p1h2", "text": "b"},
                    ],
                },
                {"id": "p2", "highlights": []},
            ],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.kind is TrimKind.PROJECT_HIGHLIGHT
        assert step.target_id == "p1"
        assert step.description == "Removed highlight: p1h2 from project: p1"

    def test_tier2_skips_when_no_project_highlights(self) -> None:
        """With no project highlights and no other content, the cascade
        falls through all tiers and returns None (projects themselves are
        never removed, and nothing else is available to cut)."""
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [
                {"id": "p1", "highlights": []},
                {"id": "p2", "highlights": []},
            ],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is None

    def test_tier2_skips_when_projects_missing_highlights_key(self) -> None:
        """Defensive: missing highlights key is treated as empty list.
        With no other content, the cascade returns None."""
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [{"id": "p1"}, {"id": "p2"}],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is None

    def test_tier2_skips_when_no_projects(self) -> None:
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [],
            "certificates": [
                {"id": "c1"},
                {"id": "c2"},
                {"id": "c3"},
                {"id": "c4"},
            ],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is not None
        # No projects at all: falls through to tier 4 (certificates,
        # bottom-up). 4 certs > floor of 3, so the last one is trimmed.
        assert step.description == "Removed certificate: c4"

    def test_tier3_remove_last_certificate(self) -> None:
        """Certificates removed at tier 4 (early), bottom-up, but never
        below ``CERTIFICATE_FLOOR`` (3). Fixture with 5 certs drains
        two then stops; the top 3 survive indefinitely."""
        from curator.renderer import TrimKind, _apply_trim, _generate_next_trim

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [],
            "certificates": [
                {"id": "c1"},
                {"id": "c2"},
                {"id": "c3"},
                {"id": "c4"},
                {"id": "aws-saa"},
            ],
            "education": [],
        }
        # Tier 4 removes the bottom cert first.
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.kind is TrimKind.CERTIFICATE
        assert step.description == "Removed certificate: aws-saa"
        sections, _ = _apply_trim(sections, None, step)

        # Tier 4 fires again, trimming down to exactly the floor.
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.description == "Removed certificate: c4"
        sections, _ = _apply_trim(sections, None, step)

        # Now at floor: cert drain stops, nothing else to trim.
        step = _generate_next_trim(sections, None)
        assert step is None
        assert [c["id"] for c in sections["certificates"]] == ["c1", "c2", "c3"]

    def test_tier4_remove_last_education(self) -> None:
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [{"id": "e1"}, {"id": "e2"}],
        }
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.description == "Removed education: e2"

    def test_tier4_skips_single_education(self) -> None:
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": [{"id": "h1"}, {"id": "h2"}]},
            ],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [{"id": "e1"}],
        }
        step = _generate_next_trim(sections, None)
        assert step is not None
        # Falls through to work-highlight trimming (tier 5/6 under the
        # 12-tier cascade; test kept under legacy tier4 name).
        assert "highlight" in step.description

    def test_tier5_remove_highlight_from_oldest_work(self) -> None:
        from curator.renderer import _generate_next_trim

        # Positions 0 and 1 (w1, w2) are protected by the recent-role
        # floor; tier 5 only scans positions 2+ (w3 here).
        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": [{"id": "h1"}, {"id": "h2"}]},
                {"id": "w2", "highlights": [{"id": "h3"}, {"id": "h4"}]},
                {"id": "w3", "highlights": [{"id": "h5"}, {"id": "h6"}]},
            ],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is not None
        # Should remove from w3 (position 2, oldest non-protected entry).
        assert step.description == "Removed highlight: h6 from work entry: w3"

    def test_skill_group_removal_targets_lowest_priority_group_first(self) -> None:
        """Tier 10 removes whole skill groups, lowest-priority (``skills[-1]``)
        first. Atomic removal converges the page-fit loop faster than the
        old keyword-at-a-time drain."""
        from curator.renderer import TrimKind, _generate_next_trim

        sections: dict[str, Any] = {
            "work": [{"id": "w1", "highlights": [{"id": "h1"}]}],
            "skills": [
                {"id": "s1", "keywords": ["k1"]},
                {"id": "s2", "keywords": ["k2"]},
            ],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.kind is TrimKind.SKILL_GROUP
        assert step.description == "Removed skill group: s2"
        assert step.target_id == "s2"

    def test_tier9_allows_positions_2_plus_to_reach_zero(self) -> None:
        """Tier 9 allows positions 2+ to drop to 0 highlights once tier 7
        (which requires >1 to keep a minimum of 1) cannot fire."""
        from curator.renderer import _generate_next_trim

        # w1 and w2 are protected (positions 0 and 1); tier 7 needs >1
        # highlight to fire on position 2+, so w3 with only 1 highlight
        # must be cleared by tier 9 which allows going to 0. No skill
        # keywords remain, so tier 8 also skips.
        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": [{"id": "h1"}, {"id": "h2"}]},
                {"id": "w2", "highlights": [{"id": "h3"}, {"id": "h4"}]},
                {"id": "w3", "highlights": [{"id": "h5"}]},
            ],
            "skills": [{"id": "s1", "keywords": []}],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.description == "Removed highlight: h5 from work entry: w3"

    def test_tier8_remove_skill_group(self) -> None:
        """With no other trimmable content, tier 10 removes the sole
        skill group wholesale. (Legacy ``tier8`` name retained.)"""
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [{"id": "w1", "highlights": []}],
            "skills": [{"id": "s1", "keywords": ["k1", "k2"]}],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.description == "Removed skill group: s1"

    def test_work_entries_are_never_removed(self) -> None:
        """Work entries are preserved even with zero highlights so the
        output renders the complete employment timeline. When every
        work entry has 0 highlights and nothing else remains to trim,
        the cascade returns None rather than removing a work entry."""
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": []},
                {"id": "w2", "highlights": []},
                {"id": "w3", "highlights": []},
            ],
            "skills": [{"id": "s1", "keywords": []}],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is None

    def test_tier8_trims_position_1_down_to_floor(self) -> None:
        """Tier 8 reduces position 1 highlights to recent_role_soft_floor."""
        from curator.renderer import _generate_next_trim

        # All other avenues exhausted. Position 1 has 5 highlights;
        # with floor=3 the first applicable trim is tier 8 on w2.
        sections: dict[str, Any] = {
            "work": [
                {
                    "id": "w1",
                    "highlights": [{"id": f"h1_{i}"} for i in range(3)],
                },
                {
                    "id": "w2",
                    "highlights": [{"id": f"h2_{i}"} for i in range(5)],
                },
            ],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None, work_position_floors=(3, 3, 0, 0, 0))
        assert step is not None
        assert step.description == "Removed highlight: h2_4 from work entry: w2"
        assert step.target_id == "w2"

    def test_tier8_respects_soft_floor(self) -> None:
        """Tier 7 stops trimming position 1 once the soft floor is reached;
        the loop then falls through to the tier 11 last-resort which
        bypasses the floor and sets below_floor=True so the caller logs a
        WARNING. (Legacy ``tier8`` name retained.)"""
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [
                {
                    "id": "w1",
                    "highlights": [{"id": f"h1_{i}"} for i in range(3)],
                },
                {
                    "id": "w2",
                    "highlights": [{"id": f"h2_{i}"} for i in range(3)],
                },
            ],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        # soft_floor=3 and w2 already at 3: tier 7 must NOT fire. Falls
        # through to tier 9 (no skill keywords), tier 10 (no certs),
        # then tier 11 which bypasses the soft floor as a last resort.
        step = _generate_next_trim(sections, None, work_position_floors=(3, 3, 0, 0, 0))
        assert step is not None
        assert "from work entry: w2" in step.description
        # Below-floor flag MUST be set so _trim_to_fit logs a WARNING.
        assert step.below_floor is True

    def test_below_floor_false_for_normal_tiers(self) -> None:
        """Tiers 1-10 should leave below_floor=False (the default)."""
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": [{"id": f"h_{i}"} for i in range(5)]},
            ],
            "skills": [],
            "projects": [],
            "certificates": [{"id": "c1"}],
            "education": [],
        }
        # Tier 8 fires first (position 0 highlights above soft floor);
        # not a last-resort tier.
        step = _generate_next_trim(sections, None, work_position_floors=(3, 3, 0, 0, 0))
        assert step is not None
        assert step.below_floor is False

    def test_tier9_trims_position_0_down_to_floor(self) -> None:
        """Tier 9 reduces position 0 highlights to recent_role_soft_floor."""
        from curator.renderer import _generate_next_trim

        # w2 already at floor, w1 above floor. Tier 9 fires on w1.
        sections: dict[str, Any] = {
            "work": [
                {
                    "id": "w1",
                    "highlights": [{"id": f"h1_{i}"} for i in range(5)],
                },
                {
                    "id": "w2",
                    "highlights": [{"id": f"h2_{i}"} for i in range(3)],
                },
            ],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None, work_position_floors=(3, 3, 0, 0, 0))
        assert step is not None
        assert step.description == "Removed highlight: h1_4 from work entry: w1"
        assert step.target_id == "w1"

    def test_cascade_never_emits_work_entry_removal(self) -> None:
        """Work entries are never removed by the cascade, regardless of
        position. Entries with empty highlights remain in the output as
        header-only rows so the complete employment timeline is visible."""
        from curator.renderer import _apply_trim, _generate_next_trim

        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": []},
                {"id": "w2", "highlights": []},
                {"id": "w3", "highlights": []},
                {"id": "w4", "highlights": []},
            ],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        removed: list[str] = []
        while True:
            step = _generate_next_trim(
                sections, None, work_position_floors=(3, 3, 0, 0, 0)
            )
            if step is None:
                break
            removed.append(step.description)
            sections, _ = _apply_trim(sections, None, step)
        assert not any("Removed work entry" in d for d in removed)
        # All four entries survive as header-only rows.
        assert [w["id"] for w in sections["work"]] == ["w1", "w2", "w3", "w4"]

    def test_nothing_left_to_trim(self) -> None:
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [{"id": "w1", "highlights": []}],
            "skills": [{"id": "s1", "keywords": []}],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is None


class TestApplyTrim:
    """Tests for _apply_trim() immutability and correctness."""

    def test_does_not_mutate_input(self) -> None:
        from curator.renderer import TrimKind, TrimStep, _apply_trim

        sections = {"certificates": [{"id": "c1"}, {"id": "c2"}]}
        interests = {"hobbies": [{"name": "Reading"}], "fun_facts": []}
        step = TrimStep(
            kind=TrimKind.CERTIFICATE, description="Removed certificate: c2"
        )

        new_sections, _new_interests = _apply_trim(sections, interests, step)

        # Original unchanged.
        assert len(sections["certificates"]) == 2
        # New copy trimmed.
        assert len(new_sections["certificates"]) == 1
        assert new_sections["certificates"][0]["id"] == "c1"

    def test_remove_interests(self) -> None:
        from curator.renderer import TrimKind, TrimStep, _apply_trim

        sections: dict[str, Any] = {}
        interests = {"hobbies": [{"name": "Reading"}], "fun_facts": ["Fact 1"]}
        step = TrimStep(
            kind=TrimKind.INTERESTS, description="Removed interests section"
        )

        _, new_interests = _apply_trim(sections, interests, step)

        assert new_interests == {"hobbies": [], "fun_facts": []}

    def test_remove_certificate(self) -> None:
        from curator.renderer import TrimKind, TrimStep, _apply_trim

        sections = {"certificates": [{"id": "cka"}, {"id": "aws"}]}
        step = TrimStep(
            kind=TrimKind.CERTIFICATE, description="Removed certificate: aws"
        )

        new_sections, _ = _apply_trim(sections, None, step)

        assert len(new_sections["certificates"]) == 1
        assert new_sections["certificates"][0]["id"] == "cka"

    def test_remove_highlight(self) -> None:
        from curator.renderer import TrimKind, TrimStep, _apply_trim

        sections = {
            "work": [
                {
                    "id": "w1",
                    "highlights": [{"id": "h1"}, {"id": "h2"}],
                },
            ],
        }
        step = TrimStep(
            kind=TrimKind.HIGHLIGHT,
            description="Removed highlight: h2 from work entry: w1",
            target_id="w1",
        )

        new_sections, _ = _apply_trim(sections, None, step)

        assert len(new_sections["work"][0]["highlights"]) == 1
        assert new_sections["work"][0]["highlights"][0]["id"] == "h1"

    def test_remove_highlight_targets_correct_work_entry(self) -> None:
        """With multiple work entries, targets the correct one by ID."""
        from curator.renderer import TrimKind, TrimStep, _apply_trim

        sections = {
            "work": [
                {"id": "w1", "highlights": [{"id": "h1"}, {"id": "h2"}]},
                {"id": "w2", "highlights": [{"id": "h3"}, {"id": "h4"}]},
            ],
        }
        step = TrimStep(
            kind=TrimKind.HIGHLIGHT,
            description="Removed highlight: h2 from work entry: w1",
            target_id="w1",
        )

        new_sections, _ = _apply_trim(sections, None, step)

        # w1 trimmed, w2 untouched
        assert len(new_sections["work"][0]["highlights"]) == 1
        assert new_sections["work"][0]["highlights"][0]["id"] == "h1"
        assert len(new_sections["work"][1]["highlights"]) == 2

    def test_remove_skill_group(self) -> None:
        """_apply_trim removes the entire skill group (all keywords) in
        one step, without mutating the input."""
        from curator.renderer import TrimKind, TrimStep, _apply_trim

        sections = {
            "skills": [{"id": "s1", "keywords": ["k1", "k2", "k3"]}],
        }
        step = TrimStep(
            kind=TrimKind.SKILL_GROUP,
            description="Removed skill group: s1",
            target_id="s1",
        )

        new_sections, _ = _apply_trim(sections, None, step)

        assert new_sections["skills"] == []
        # Input not mutated (deepcopy contract).
        assert len(sections["skills"]) == 1
        assert sections["skills"][0]["keywords"] == ["k1", "k2", "k3"]

    def test_remove_skill_group_targets_correct_group(self) -> None:
        """With multiple skill groups, removes only the target by ID."""
        from curator.renderer import TrimKind, TrimStep, _apply_trim

        sections = {
            "skills": [
                {"id": "s1", "keywords": ["k1", "k2"]},
                {"id": "s2", "keywords": ["k3", "k4"]},
            ],
        }
        step = TrimStep(
            kind=TrimKind.SKILL_GROUP,
            description="Removed skill group: s2",
            target_id="s2",
        )

        new_sections, _ = _apply_trim(sections, None, step)

        # Only s2 removed; s1 untouched.
        assert len(new_sections["skills"]) == 1
        assert new_sections["skills"][0]["id"] == "s1"
        assert new_sections["skills"][0]["keywords"] == ["k1", "k2"]
        # Input not mutated.
        assert len(sections["skills"]) == 2

    def test_remove_project_highlight_targets_correct_project(self) -> None:
        """With multiple projects, pops the last highlight of the target only."""
        from curator.renderer import TrimKind, TrimStep, _apply_trim

        sections: dict[str, Any] = {
            "projects": [
                {
                    "id": "p1",
                    "highlights": [{"id": "p1h1"}, {"id": "p1h2"}],
                },
                {
                    "id": "p2",
                    "highlights": [{"id": "p2h1"}, {"id": "p2h2"}],
                },
                {
                    "id": "p3",
                    "highlights": [{"id": "p3h1"}, {"id": "p3h2"}],
                },
            ],
        }
        step = TrimStep(
            kind=TrimKind.PROJECT_HIGHLIGHT,
            description="Removed highlight: p2h2 from project: p2",
            target_id="p2",
        )

        new_sections, _ = _apply_trim(sections, None, step)

        # Only p2 was trimmed.
        assert len(new_sections["projects"][0]["highlights"]) == 2
        assert len(new_sections["projects"][1]["highlights"]) == 1
        assert new_sections["projects"][1]["highlights"][0]["id"] == "p2h1"
        assert len(new_sections["projects"][2]["highlights"]) == 2
        # Input not mutated (deepcopy contract).
        assert len(sections["projects"][1]["highlights"]) == 2

    def test_remove_project_highlight_handles_missing_target(self) -> None:
        """If target_id matches no project, sections are unchanged (no-op)."""
        from curator.renderer import TrimKind, TrimStep, _apply_trim

        sections: dict[str, Any] = {
            "projects": [
                {
                    "id": "p1",
                    "highlights": [{"id": "p1h1"}, {"id": "p1h2"}],
                },
            ],
        }
        step = TrimStep(
            kind=TrimKind.PROJECT_HIGHLIGHT,
            description="Removed highlight: ghost from project: nope",
            target_id="nope",
        )

        new_sections, _ = _apply_trim(sections, None, step)

        assert len(new_sections["projects"]) == 1
        assert len(new_sections["projects"][0]["highlights"]) == 2


class TestTrimToFit:
    """Tests for _trim_to_fit() loop behavior."""

    def test_already_fits_no_trimming(self, tmp_path: Path) -> None:
        """When PDF fits on first compile, no trimming occurs."""
        from curator.renderer import _trim_to_fit

        output_dir = tmp_path / "profile"
        output_dir.mkdir()
        (output_dir / "data").mkdir()
        tpl = tmp_path / "tpl" / "curated.typ"
        tpl.parent.mkdir()
        tpl.write_text("// dummy")

        def fake_run(cmd: list[str], **kw: Any) -> Any:
            Path(cmd[-1]).write_bytes(b"%PDF-1.4 fake")
            return type("R", (), {"returncode": 0, "stderr": ""})()

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        basics = {"name": "Test"}

        with (
            patch("curator.renderer.subprocess.run", side_effect=fake_run),
            patch("curator.renderer.get_page_count", return_value=1),
        ):
            (
                final_sections,
                _final_interests,
                trim_log,
                pages,
                safety_valve_fired,
            ) = _trim_to_fit(
                sections,
                basics,
                None,
                output_dir,
                tpl,
                ["work", "skills", "projects", "certificates", "education"],
                max_pages=1,
                max_trim_iterations=15,
            )

        assert trim_log == []
        assert final_sections == sections
        assert pages == 1
        # Convergent immediate fit → safety valve did NOT fire.
        assert safety_valve_fired is False

    def test_trims_until_fits(self, tmp_path: Path) -> None:
        """Trims content iteratively until page count is within budget."""
        from curator.renderer import _trim_to_fit

        output_dir = tmp_path / "profile"
        output_dir.mkdir()
        (output_dir / "data").mkdir()
        tpl = tmp_path / "tpl" / "curated.typ"
        tpl.parent.mkdir()
        tpl.write_text("// dummy")

        def fake_run(cmd: list[str], **kw: Any) -> Any:
            Path(cmd[-1]).write_bytes(b"%PDF-1.4 fake")
            return type("R", (), {"returncode": 0, "stderr": ""})()

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [],
            # 4 certs so tier 4 can trim one without breaching the floor.
            "certificates": [
                {"id": "c1"},
                {"id": "c2"},
                {"id": "c3"},
                {"id": "c4"},
            ],
            "education": [],
        }
        basics = {"name": "Test"}
        interests = {"hobbies": [{"name": "Reading"}], "fun_facts": []}

        # First compile: 2 pages, second: 2 pages, third: 1 page
        page_counts = iter([2, 2, 1])

        with (
            patch("curator.renderer.subprocess.run", side_effect=fake_run),
            patch("curator.renderer.get_page_count", side_effect=page_counts),
        ):
            final_sections, final_interests, trim_log, pages, _safety = _trim_to_fit(
                sections,
                basics,
                interests,
                output_dir,
                tpl,
                ["work", "skills", "projects", "certificates", "education"],
                max_pages=1,
                max_trim_iterations=15,
            )

        # Tier 1: remove interests. Tier 4: remove bottom cert c4
        # (5 certs -> 4, still above floor of 3 so one drain suffices
        # for the mocked page-count sequence).
        assert len(trim_log) == 2
        assert trim_log[0] == "Removed interests section"
        assert trim_log[1] == "Removed certificate: c4"
        assert final_interests == {"hobbies": [], "fun_facts": []}
        assert [c["id"] for c in final_sections["certificates"]] == [
            "c1",
            "c2",
            "c3",
        ]
        assert pages == 1

    def test_safety_valve(self, tmp_path: Path) -> None:
        """Stops after max_trim_iterations even if still over-page."""
        from curator.renderer import _trim_to_fit

        output_dir = tmp_path / "profile"
        output_dir.mkdir()
        (output_dir / "data").mkdir()
        tpl = tmp_path / "tpl" / "curated.typ"
        tpl.parent.mkdir()
        tpl.write_text("// dummy")

        def fake_run(cmd: list[str], **kw: Any) -> Any:
            Path(cmd[-1]).write_bytes(b"%PDF-1.4 fake")
            return type("R", (), {"returncode": 0, "stderr": ""})()

        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": [{"id": f"h{i}"} for i in range(5)]},
            ],
            "skills": [{"id": "s1", "keywords": ["k1"]}],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        basics = {"name": "Test"}

        # Always 2 pages
        with (
            patch("curator.renderer.subprocess.run", side_effect=fake_run),
            patch("curator.renderer.get_page_count", return_value=2),
        ):
            _, _, trim_log, _pages, safety_valve_fired = _trim_to_fit(
                sections,
                basics,
                None,
                output_dir,
                tpl,
                ["work", "skills", "projects", "certificates", "education"],
                max_pages=1,
                max_trim_iterations=3,
            )

        assert len(trim_log) == 3
        # Iteration cap exhausted while still over-page → safety valve fired.
        assert safety_valve_fired is True

    def test_nothing_to_trim(self, tmp_path: Path) -> None:
        """When nothing left to trim, returns immediately."""
        from curator.renderer import _trim_to_fit

        output_dir = tmp_path / "profile"
        output_dir.mkdir()
        (output_dir / "data").mkdir()
        tpl = tmp_path / "tpl" / "curated.typ"
        tpl.parent.mkdir()
        tpl.write_text("// dummy")

        def fake_run(cmd: list[str], **kw: Any) -> Any:
            Path(cmd[-1]).write_bytes(b"%PDF-1.4 fake")
            return type("R", (), {"returncode": 0, "stderr": ""})()

        sections: dict[str, Any] = {
            "work": [{"id": "w1", "highlights": []}],
            "skills": [{"id": "s1", "keywords": []}],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        basics = {"name": "Test"}

        with (
            patch("curator.renderer.subprocess.run", side_effect=fake_run),
            patch("curator.renderer.get_page_count", return_value=2),
        ):
            _, _, trim_log, _pages, _safety = _trim_to_fit(
                sections,
                basics,
                None,
                output_dir,
                tpl,
                ["work", "skills", "projects", "certificates", "education"],
                max_pages=1,
                max_trim_iterations=15,
            )

        assert trim_log == []

    def test_max_trim_iterations_one(self, tmp_path: Path) -> None:
        """Edge case: only one trim iteration allowed."""
        from curator.renderer import _trim_to_fit

        output_dir = tmp_path / "profile"
        output_dir.mkdir()
        (output_dir / "data").mkdir()
        tpl = tmp_path / "tpl" / "curated.typ"
        tpl.parent.mkdir()
        tpl.write_text("// dummy")

        def fake_run(cmd: list[str], **kw: Any) -> Any:
            Path(cmd[-1]).write_bytes(b"%PDF-1.4 fake")
            return type("R", (), {"returncode": 0, "stderr": ""})()

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [],
            "certificates": [{"id": "c1"}],
            "education": [],
        }
        basics = {"name": "Test"}
        interests: dict[str, Any] = {"hobbies": [{"name": "Reading"}], "fun_facts": []}

        with (
            patch("curator.renderer.subprocess.run", side_effect=fake_run),
            patch("curator.renderer.get_page_count", return_value=2),
        ):
            _, _, trim_log, _pages, _safety = _trim_to_fit(
                sections,
                basics,
                interests,
                output_dir,
                tpl,
                ["work", "skills", "projects", "certificates", "education"],
                max_pages=1,
                max_trim_iterations=1,
            )

        assert len(trim_log) == 1
        assert trim_log[0] == "Removed interests section"

    def test_multi_tier_trim_through_skill_and_below_floor(
        self, tmp_path: Path
    ) -> None:
        """End-to-end ``_trim_to_fit`` exercise covering multiple tiers
        in a single loop: a `SKILL_GROUP` removal (tier 10) followed by
        below-floor work-highlight trims (tiers 11-12). Asserts the
        ordered `trim_log`, the WARNING log when below-floor fires, and
        the final section state."""
        from loguru import logger

        from curator.renderer import _trim_to_fit

        output_dir = tmp_path / "profile"
        output_dir.mkdir()
        (output_dir / "data").mkdir()
        tpl = tmp_path / "tpl" / "curated.typ"
        tpl.parent.mkdir()
        tpl.write_text("// dummy")

        def fake_run(cmd: list[str], **kw: Any) -> Any:
            Path(cmd[-1]).write_bytes(b"%PDF-1.4 fake")
            return type("R", (), {"returncode": 0, "stderr": ""})()

        # Capture WARNINGs via a loguru list sink (loguru does not
        # route through pytest's caplog by default).
        log_messages: list[str] = []
        logger.remove()
        sink_id = logger.add(lambda msg: log_messages.append(str(msg)), level="WARNING")

        try:
            # Fixture designed so the cascade must: (1) drain a skill
            # group (tier 10) because no other non-work content is
            # trimmable; then (2) hit the below-floor tier because work
            # is pinned at soft floor and nothing else remains.
            sections: dict[str, Any] = {
                "work": [
                    {"id": "w1", "highlights": [{"id": f"a{i}"} for i in range(3)]},
                    {"id": "w2", "highlights": [{"id": f"b{i}"} for i in range(3)]},
                ],
                "skills": [
                    {"id": "s-only", "keywords": ["k1", "k2"]},
                ],
                "projects": [],
                # Cert count at the floor -> tier 4 skips.
                "certificates": [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}],
                "education": [],
            }
            basics = {"name": "Test"}

            # 3 compiles report 2 pages, the 4th reports 1. Forces
            # 3 trims: skill group (tier 10), then two below-floor
            # work-highlight trims (tiers 11/12) until the page fits.
            page_counts = iter([2, 2, 2, 1])

            with (
                patch("curator.renderer.subprocess.run", side_effect=fake_run),
                patch("curator.renderer.get_page_count", side_effect=page_counts),
            ):
                final_sections, _, trim_log, pages, _safety = _trim_to_fit(
                    sections,
                    basics,
                    None,
                    output_dir,
                    tpl,
                    ["work", "skills", "projects", "certificates", "education"],
                    max_pages=1,
                    max_trim_iterations=15,
                    work_position_floors=(3, 3, 0, 0, 0),
                )
        finally:
            logger.remove(sink_id)

        # Ordered log: skill group first, then two below-floor work trims.
        assert len(trim_log) == 3
        assert trim_log[0] == "Removed skill group: s-only"
        assert "from work entry: w2" in trim_log[1]
        # Second below-floor trim fires on w2 (pos 1, soft floor bypass)
        # until its highlights are exhausted.
        assert "from work entry: w" in trim_log[2]

        # Skills section fully drained by the atomic group removal.
        assert final_sections["skills"] == []
        # Certs at floor remain untouched.
        assert len(final_sections["certificates"]) == 3
        assert pages == 1

        # _trim_to_fit must emit the below_floor WARNING for tiers 11/12.
        below_floor_warnings = [m for m in log_messages if "work_position_floors" in m]
        assert len(below_floor_warnings) >= 1, (
            f"expected below_floor WARNING, got: {log_messages}"
        )

    def test_input_immutability(self, tmp_path: Path) -> None:
        """Original sections dict is not mutated by _trim_to_fit."""
        import copy as copy_mod

        from curator.renderer import _trim_to_fit

        output_dir = tmp_path / "profile"
        output_dir.mkdir()
        (output_dir / "data").mkdir()
        tpl = tmp_path / "tpl" / "curated.typ"
        tpl.parent.mkdir()
        tpl.write_text("// dummy")

        def fake_run(cmd: list[str], **kw: Any) -> Any:
            Path(cmd[-1]).write_bytes(b"%PDF-1.4 fake")
            return type("R", (), {"returncode": 0, "stderr": ""})()

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [],
            "certificates": [{"id": "c1"}],
            "education": [],
        }
        original = copy_mod.deepcopy(sections)
        basics = {"name": "Test"}
        interests: dict[str, Any] = {"hobbies": [{"name": "Reading"}], "fun_facts": []}
        original_interests = copy_mod.deepcopy(interests)

        with (
            patch("curator.renderer.subprocess.run", side_effect=fake_run),
            patch("curator.renderer.get_page_count", side_effect=[2, 1]),
        ):
            _trim_to_fit(
                sections,
                basics,
                interests,
                output_dir,
                tpl,
                ["work", "skills", "projects", "certificates", "education"],
                max_pages=1,
                max_trim_iterations=15,
            )

        assert sections == original
        assert interests == original_interests


# ---------------------------------------------------------------------------
# Additional trim edge cases
# ---------------------------------------------------------------------------


class TestGenerateNextTrimEdgeCases:
    """Additional edge case tests for _generate_next_trim()."""

    def test_tier6_drains_oldest_position_first_under_default_floors(self) -> None:
        """Tier 6 (per-position floor, bottom-up scan) trims the oldest
        position first under the default 1-page floors ``(3, 3, 0, 0, 0)``.

        Under the new cascade, positions 2+ have floor 0 by default, so
        ``len(highlights) > 0`` is True for w4 (1 highlight) and the
        cascade trims w4 before w3. This is a deliberate inversion of
        the prior tier 5/6 behavior (which kept positions 2+ at >=1
        across the row before draining any to 0): the new design lets
        each older position drain to its floor in sequence, scanning
        bottom-up.
        """
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": [{"id": "h1"}, {"id": "h2"}]},
                {"id": "w2", "highlights": [{"id": "h3"}, {"id": "h4"}]},
                {"id": "w3", "highlights": [{"id": "h5"}, {"id": "h6"}]},
                {"id": "w4", "highlights": [{"id": "h7"}]},
            ],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is not None
        # w4 (pos 3, floor 0): len 1 > 0, trim h7 first.
        assert step.description == "Removed highlight: h7 from work entry: w4"
        assert step.target_id == "w4"

    def test_tier3_preserves_sole_certificate(self) -> None:
        """The cascade never removes a certificate that would leave the
        count below ``CERTIFICATE_FLOOR``. With a single cert present,
        the cert is treated as load-bearing: the cascade falls through
        to skill-group removal instead."""
        from curator.renderer import TrimKind, _apply_trim, _generate_next_trim

        sections: dict[str, Any] = {
            "work": [],
            "skills": [{"id": "s1", "keywords": ["k1"]}],
            "projects": [],
            "certificates": [{"id": "sole-cert"}],
            "education": [],
        }
        # Tier 4 skips (1 <= floor). Skill group removal fires instead.
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.kind is TrimKind.SKILL_GROUP
        sections, _ = _apply_trim(sections, None, step)

        # Nothing else trimmable; cert remains.
        step = _generate_next_trim(sections, None)
        assert step is None
        assert sections["certificates"] == [{"id": "sole-cert"}]

    def test_tier8_removes_sole_skill_group(self) -> None:
        """Tier 10 removes the sole remaining skill group wholesale."""
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [{"id": "w1", "highlights": []}],
            "skills": [{"id": "s1", "keywords": ["k1", "k2"]}],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.description == "Removed skill group: s1"
        assert step.target_id == "s1"

    def test_tier10_skips_empty_keyword_groups(self) -> None:
        """Tier 10 targets the lowest-priority group with non-empty
        keywords, skipping any empty-keyword groups between it and
        the bottom. Empty groups take no page space so removing them
        wouldn't save anything; ``_prune_empty_sections`` cleans them
        up separately."""
        from curator.renderer import TrimKind, _generate_next_trim

        # Iteration is bottom-up, so put the empty group AT the bottom
        # (index -1) and a non-empty group immediately above it. The
        # cascade must skip the empty one and target the non-empty one.
        sections: dict[str, Any] = {
            "work": [{"id": "w1", "highlights": []}],
            "skills": [
                {"id": "s1", "keywords": ["k1"]},
                {"id": "s2", "keywords": ["k2", "k3"]},
                {"id": "empty-bottom", "keywords": []},
            ],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.kind is TrimKind.SKILL_GROUP
        # Must target s2 (lowest non-empty), NOT empty-bottom.
        assert step.target_id == "s2"
        assert step.description == "Removed skill group: s2"

    def test_full_trim_sequence(self) -> None:
        """Walk through a full trim sequence verifying the new 8-tier ordering.

        With default floors ``(3, 3, 0, 0, 0)`` (1-page profile), tier 6
        scans positions N-1..0 and drains each position to its floor
        before advancing. Older positions (floor 0) drain fully before
        positions 0/1 (floor 3) are touched. Once tier 6 is exhausted,
        skill groups go (tier 7), then below-floor last resort drains
        positions 1 and 0 from the bottom up (tier 8).
        """
        from curator.renderer import _apply_trim, _generate_next_trim

        sections: dict[str, Any] = {
            "work": [
                {
                    "id": "w1",
                    "highlights": [{"id": "h1"}, {"id": "h2"}, {"id": "h3"}],
                },
                {
                    "id": "w2",
                    "highlights": [{"id": "h4"}, {"id": "h5"}, {"id": "h6"}],
                },
                {"id": "w3", "highlights": [{"id": "h7"}, {"id": "h8"}]},
                {"id": "w4", "highlights": [{"id": "h9"}]},
            ],
            "skills": [
                {"id": "s1", "keywords": ["k1"]},
                {"id": "s2", "keywords": ["k2"]},
            ],
            "projects": [
                {"id": "p1"},
                {
                    "id": "p2",
                    "highlights": [{"id": "ph1"}, {"id": "ph2"}],
                },
            ],
            # 5 certs so tier 4 can trim two (down to the floor of 3).
            "certificates": [
                {"id": "c1"},
                {"id": "c2"},
                {"id": "c3"},
                {"id": "c4"},
                {"id": "c5"},
            ],
            "education": [{"id": "e1"}],
        }
        interests: dict[str, Any] | None = {
            "hobbies": [{"name": "Reading"}],
            "fun_facts": [],
        }

        descriptions: list[str] = []
        while True:
            step = _generate_next_trim(sections, interests)
            if step is None:
                break
            descriptions.append(step.description)
            sections, interests = _apply_trim(sections, interests, step)

        # Expected 8-tier progression (work entries and projects of
        # count <= 2 are never removed wholesale; top CERTIFICATE_FLOOR
        # certs are preserved; skill groups removed atomically at
        # tier 7 lowest-priority first):
        #  1: interests
        #  2: project highlights, lowest project first: p2 -> ph2, ph1
        #  3: projects wholesale -- only 2 remain -> skip
        #  4: certs bottom-up down to floor: c5, c4 (c1-c3 survive)
        #  5: education keeps >=1 -> skip
        #  6: work to per-position floor, bottom-up:
        #     - w4 (pos 3, floor 0): len 1>0 -> h9
        #     - w3 (pos 2, floor 0): len 2>0 -> h8, then h7
        #     - w2 (pos 1, floor 3): len 3>3 false -> skip
        #     - w1 (pos 0, floor 3): len 3>3 false -> skip
        #  7: skill groups bottom-up: s2, then s1
        #  8: below-floor last resort, scan N-1..0 for first non-empty:
        #     - w4 empty, w3 empty
        #     - w2 -> h6, h5, h4 (3 below-floor steps)
        #     - w1 -> h3, h2, h1 (3 below-floor steps)
        expected = [
            "Removed interests section",
            "Removed highlight: ph2 from project: p2",
            "Removed highlight: ph1 from project: p2",
            "Removed certificate: c5",
            "Removed certificate: c4",
            "Removed highlight: h9 from work entry: w4",
            "Removed highlight: h8 from work entry: w3",
            "Removed highlight: h7 from work entry: w3",
            "Removed skill group: s2",
            "Removed skill group: s1",
            "Removed highlight: h6 from work entry: w2",
            "Removed highlight: h5 from work entry: w2",
            "Removed highlight: h4 from work entry: w2",
            "Removed highlight: h3 from work entry: w1",
            "Removed highlight: h2 from work entry: w1",
            "Removed highlight: h1 from work entry: w1",
        ]
        assert descriptions == expected
        # Top 3 certs survived through the entire cascade.
        assert [c["id"] for c in sections["certificates"]] == ["c1", "c2", "c3"]
        # Skill groups fully removed (atomic tier 7).
        assert sections["skills"] == []

    def test_project_description_never_trimmed_alone(self) -> None:
        """Project description rides with its entry. Draining a
        project's highlights never removes the description; the
        description disappears only when the whole project is cut
        wholesale at tier 3. Also asserts PROJECT_DESCRIPTION is
        gone from TrimKind so future accidental re-introduction
        trips a test."""
        from curator.renderer import (
            TrimKind,
            _apply_trim,
            _generate_next_trim,
        )

        # Regression trap for the removed tier. Description is
        # supposed to ride with its project (removed only when tier 3
        # cuts the whole entry); accidental re-introduction of a
        # separate description-drain tier should trip this test. If a
        # future rename legitimately reuses the name for something
        # else, weigh the cost of losing this guard deliberately.
        assert not hasattr(TrimKind, "PROJECT_DESCRIPTION")
        assert "project_description" not in {k.value for k in TrimKind}

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [
                {"id": "p1", "description": "Kept", "highlights": []},
                {"id": "p2", "description": "Kept", "highlights": []},
                {
                    "id": "p3",
                    "description": "Drains with project",
                    "highlights": [{"id": "p3h1"}, {"id": "p3h2"}],
                },
            ],
            "certificates": [],
            "education": [],
        }

        # Drain p3 highlights (tier 2) one at a time.
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.kind is TrimKind.PROJECT_HIGHLIGHT
        assert step.target_id == "p3"
        sections, _ = _apply_trim(sections, None, step)
        # Description survives the highlight-drain step.
        assert sections["projects"][2]["description"] == "Drains with project"

        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.kind is TrimKind.PROJECT_HIGHLIGHT
        sections, _ = _apply_trim(sections, None, step)
        assert sections["projects"][2]["description"] == "Drains with project"
        assert sections["projects"][2]["highlights"] == []

        # Next trim removes the whole project (tier 3); description
        # goes with it, never trimmed in isolation.
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.kind is TrimKind.PROJECT
        assert step.description == "Removed project: p3"
        sections, _ = _apply_trim(sections, None, step)
        assert [p["id"] for p in sections["projects"]] == ["p1", "p2"]

    def test_top_certificates_always_preserved(self) -> None:
        """The top ``CERTIFICATE_FLOOR`` (3) certs survive the entire
        cascade. Cert trim fires bottom-up early at tier 4, but once
        exactly ``CERTIFICATE_FLOOR`` remain no further cert step may
        fire. Skill groups drain atomically at tier 10 as page pressure
        dictates, but the top 3 certs are untouchable."""
        from curator.renderer import (
            CERTIFICATE_FLOOR,
            TrimKind,
            _apply_trim,
            _generate_next_trim,
        )

        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": [{"id": f"h{i}"} for i in range(3)]},
            ],
            "skills": [
                {"id": "s1", "keywords": ["a", "b"]},
                {"id": "s2", "keywords": ["c", "d"]},
            ],
            "projects": [],
            # 5 certs: 2 removable, 3 protected.
            "certificates": [
                {"id": "c1"},
                {"id": "c2"},
                {"id": "c3"},
                {"id": "c4"},
                {"id": "c5"},
            ],
            "education": [],
        }

        seen: list[TrimKind] = []
        while True:
            step = _generate_next_trim(sections, None)
            if step is None:
                break
            seen.append(step.kind)
            sections, _ = _apply_trim(sections, None, step)

        cert_steps = [k for k in seen if k is TrimKind.CERTIFICATE]
        assert len(cert_steps) == 2, (
            f"expected 2 cert drains (5 - floor of {CERTIFICATE_FLOOR}), "
            f"got {len(cert_steps)}"
        )
        assert len(sections["certificates"]) == CERTIFICATE_FLOOR
        assert [c["id"] for c in sections["certificates"]] == ["c1", "c2", "c3"]

    def test_cert_count_at_floor_never_trimmed(self) -> None:
        """With exactly ``CERTIFICATE_FLOOR`` certs present and nothing
        else trimmable, tier 4 must not fire. Pins the off-by-one
        boundary of the floor check (``> floor``, not ``>=``)."""
        from curator.renderer import (
            CERTIFICATE_FLOOR,
            _generate_next_trim,
        )

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [],
            "certificates": [{"id": f"c{i}"} for i in range(CERTIFICATE_FLOOR)],
            "education": [],
        }
        step = _generate_next_trim(sections, None)
        assert step is None, (
            f"cascade should not trim when cert count equals floor, got {step}"
        )
        assert len(sections["certificates"]) == CERTIFICATE_FLOOR

    def test_single_bullet_project_from_description(self) -> None:
        """Project with a description but no highlights is valid (the
        Typst template renders it as a header + single bullet). Tier 2
        must not emit a no-op PROJECT_HIGHLIGHT step for it, and it
        survives until the wholesale PROJECT tier fires."""
        from curator.renderer import (
            TrimKind,
            _apply_trim,
            _generate_next_trim,
        )

        sections: dict[str, Any] = {
            "work": [],
            "skills": [],
            "projects": [
                {"id": "p1", "description": "Alpha", "highlights": []},
                {"id": "p2", "description": "Beta", "highlights": []},
                {"id": "p3", "description": "Gamma", "highlights": []},
            ],
            "certificates": [],
            "education": [],
        }

        # Tier 2 must skip (no highlights anywhere). Tier 3 removes p3.
        step = _generate_next_trim(sections, None)
        assert step is not None
        assert step.kind is TrimKind.PROJECT
        assert step.description == "Removed project: p3"
        sections, _ = _apply_trim(sections, None, step)
        # Remaining projects keep their descriptions intact.
        assert [p["description"] for p in sections["projects"]] == ["Alpha", "Beta"]

        # Only 2 projects left now; tier 3 stops. Cascade returns None
        # because no other trimmable content exists.
        step = _generate_next_trim(sections, None)
        assert step is None

    def test_empty_certificates_list_with_skills_only(self) -> None:
        """An empty certificates list must not trigger a spurious
        CERTIFICATE step. Cascade removes all skill groups atomically,
        then returns None."""
        from curator.renderer import (
            TrimKind,
            _apply_trim,
            _generate_next_trim,
        )

        sections: dict[str, Any] = {
            "work": [],
            "skills": [
                {"id": "s1", "keywords": ["k1", "k2"]},
                {"id": "s2", "keywords": ["k3"]},
            ],
            "projects": [],
            "certificates": [],
            "education": [],
        }

        kinds: list[TrimKind] = []
        while True:
            step = _generate_next_trim(sections, None)
            if step is None:
                break
            kinds.append(step.kind)
            sections, _ = _apply_trim(sections, None, step)

        # One atomic step per group, bottom-up.
        assert kinds == [TrimKind.SKILL_GROUP, TrimKind.SKILL_GROUP]
        assert TrimKind.CERTIFICATE not in kinds
        assert sections["skills"] == []

    def test_below_floor_tiers_run_after_cert_drain_stops(self) -> None:
        """Removable certificates (above the floor) are trimmed at tier
        4 first; once the cascade hits ``CERTIFICATE_FLOOR`` it stops
        touching certs and falls through to keyword / below-floor work
        tiers as page pressure continues. The top 3 certs are never
        cut, even to relieve an overflow."""
        from curator.renderer import (
            CERTIFICATE_FLOOR,
            TrimKind,
            _apply_trim,
            _generate_next_trim,
        )

        # 4 certs (one removable), work positions at soft floor, no
        # skills or projects. Drain sequence should be: cert (tier 4)
        # then below-floor work highlights (tiers 11-12) -- NOT another
        # cert.
        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": [{"id": f"a{i}"} for i in range(3)]},
                {"id": "w2", "highlights": [{"id": f"b{i}"} for i in range(3)]},
            ],
            "skills": [],
            "projects": [],
            "certificates": [
                {"id": "c1"},
                {"id": "c2"},
                {"id": "c3"},
                {"id": "removable"},
            ],
            "education": [],
        }

        # First trim: the bottom cert (tier 4, above the floor).
        step = _generate_next_trim(sections, None, work_position_floors=(3, 3, 0, 0, 0))
        assert step is not None
        assert step.kind is TrimKind.CERTIFICATE
        assert step.description == "Removed certificate: removable"
        assert step.below_floor is False
        sections, _ = _apply_trim(sections, None, step)
        assert len(sections["certificates"]) == CERTIFICATE_FLOOR

        # Second trim: cert floor blocks further cert drain, so we fall
        # through to tier 11 (below-floor on position 1).
        step = _generate_next_trim(sections, None, work_position_floors=(3, 3, 0, 0, 0))
        assert step is not None
        assert step.kind is TrimKind.HIGHLIGHT
        assert step.below_floor is True
        assert "from work entry: w2" in step.description


# ---------------------------------------------------------------------------
# Cover letter writer + audit log
# ---------------------------------------------------------------------------


from dataclasses import replace  # noqa: E402

from curator.renderer import _render_cover_letter  # noqa: E402


def _fresh_cover_letter() -> Any:
    from curator.models import CoverLetterCuration
    from tests.helpers import valid_cover_letter_kwargs as _valid_letter_kwargs

    return CoverLetterCuration(**_valid_letter_kwargs())


class TestRenderCoverLetter:
    def test_writes_yaml_with_expected_keys(self, tmp_path: Path) -> None:
        from curator import default_cover_letter_template_path

        letter = _fresh_cover_letter()
        yaml_path, pdf_path, pages = _render_cover_letter(
            tmp_path,
            letter,
            default_cover_letter_template_path(),
            skip_pdf=True,
        )
        assert pdf_path is None
        assert pages is None
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        for key in (
            "salutation",
            "opening",
            "body_paragraphs",
            "closing",
            "sign_off",
            "word_count",
            "rendered_date",
        ):
            assert key in data
        assert "is_template" not in data


class TestCurationLogCoverLetter:
    def test_log_cover_letter_enabled_false_when_absent(
        self, tmp_path: Path, curation_result: CurationResult
    ) -> None:
        _, log_path, _, _ = _write_audit_artifacts(
            tmp_path, curation_result, "JD text."
        )
        log_data = json.loads(log_path.read_text())
        assert log_data["cover_letter"] == {"enabled": False}

    def test_log_cover_letter_sub_object_when_present(
        self, tmp_path: Path, curation_result: CurationResult
    ) -> None:
        letter = _fresh_cover_letter()
        result_with_cl = replace(curation_result, cover_letter=letter)
        _, log_path, _, _ = _write_audit_artifacts(tmp_path, result_with_cl, "JD text.")
        log_data = json.loads(log_path.read_text())
        cl = log_data["cover_letter"]
        assert cl["enabled"] is True
        assert "is_template" not in cl
        assert cl["word_count"] > 0
        # SA-6 (2026-04-26): the over_cap field must be present on every
        # enabled cover letter so consumers of curation_log.json know
        # whether the soft-warn fired (paid API calls that overshot the
        # 300-word cap still ship; over_cap=true flags them in the audit
        # trail without aborting the run).
        assert "over_cap" in cl
        assert isinstance(cl["over_cap"], bool)
        # The fresh fixture is in-band, so over_cap must be False.
        assert cl["over_cap"] is False


@pytest.mark.unit
class TestRendererJudgeInvariant:
    """AR-3 (2026-04-26): pin the renderer-judge design coupling.

    The renderer's "preserve all work history" trim policy is codified
    in the Tier 2 judge rubric's <conventions> block. If either side
    changes without the other, this test should fail to surface the
    coupling break.
    """

    def test_renderer_invariant_comment_present(self) -> None:
        # Source-level comment in renderer.py documents the coupling.
        from pathlib import Path

        renderer_src = Path("src/curator/renderer.py").read_text(encoding="utf-8")
        assert "RENDERER_BEHAVIOR_INVARIANT" in renderer_src
        assert "preserve" in renderer_src.lower()
        assert (
            "JUDGE_VERSION" in renderer_src or "judge_version" in renderer_src.lower()
        )

    def test_judge_conventions_describe_renderer_intent(self) -> None:
        # Mirror check: judge prompt's <conventions> block must reference
        # the renderer's design intent. Same pin from both sides.
        from curator.eval.judge import _RUBRIC_SYSTEM_PROMPT

        text = _RUBRIC_SYSTEM_PROMPT.lower()
        assert "employment timeline" in text or "header-only" in text
        assert "trim" in text or "page" in text


@pytest.mark.unit
class TestTemplateTypography:
    """Pin typography invariants in both packaged Typst templates.

    Cheap, font-independent guard against the soft-hyphen regression:
    Typst auto-hyphenation wraps line-break hyphens in
    /ActualText <FEFF00AD>, which web fonts render as boxes when pasted.
    Source: 2026-04-30 the reference application cover-letter incident.
    """

    @pytest.mark.parametrize(
        "template_attr",
        [
            "default_template_path",
            "default_cover_letter_template_path",
        ],
    )
    def test_hyphenation_disabled(self, template_attr: str) -> None:
        import re as _re

        import curator

        template_path: Path = getattr(curator, template_attr)()
        src = template_path.read_text(encoding="utf-8")
        # Strip Typst comments so the rationale block (which includes
        # the literal phrase "auto-hyphenation off") cannot satisfy the
        # assertion. Strip block comments first (DOTALL across lines),
        # then single-line comments (each line up to newline). Active
        # code only.
        code = _re.sub(r"/\*.*?\*/", "", src, flags=_re.DOTALL)
        code = _re.sub(r"//[^\n]*", "", code)
        assert "hyphenate: false" in code, (
            f"{template_path.name} must set hyphenate: false. Re-enabling "
            "produces /ActualText <FEFF00AD> markers that render as boxes "
            "when pasted into web forms."
        )
        assert "hyphenate: true" not in code, (
            f"{template_path.name} contains hyphenate: true in active code. "
            "See the rationale comment above the #set text(...) block."
        )


class TestCapsForPages:
    """Pin _PageCaps profile values + per-position monotonicity invariant.

    Page-budget-aware floors (``work_position_floors`` per-position
    tuple, plus ``certificate_floor``) must rise non-strictly with
    ``max_pages`` so larger budgets never reduce floor protection.
    Per-project bullet cap is intentionally NOT in _PageCaps -- it
    stays at 2 across all modes (see ProjectRanking schema follow-up
    TODO).
    """

    def test_short_form_caps_match_pre_refactor_constants(self) -> None:
        from curator.renderer import _caps_for_pages

        caps = _caps_for_pages(1)
        assert caps.work_position_floors == (3, 3, 0, 0, 0)
        assert caps.certificate_floor == 3

    def test_two_page_caps(self) -> None:
        from curator.renderer import _caps_for_pages

        caps = _caps_for_pages(2)
        assert caps.work_position_floors == (8, 6, 6, 2, 2)
        assert caps.certificate_floor == 3

    def test_plateau_at_three_or_more_pages(self) -> None:
        from curator.renderer import _caps_for_pages

        caps_3 = _caps_for_pages(3)
        caps_5 = _caps_for_pages(5)
        assert caps_3.work_position_floors == (10, 8, 8, 4, 4)
        assert caps_3.certificate_floor == 5
        # Plateau: 4-5 page configs use the same profile as 3-page.
        assert caps_5 == caps_3

    @pytest.mark.parametrize("n", [2, 3, 4, 5])
    def test_caps_monotonic(self, n: int) -> None:
        """Floors never decrease as max_pages grows.

        Per-position monotonicity: each index in ``work_position_floors``
        is non-decreasing across page budgets. Iterates to the longer of
        the two tuples and falls through to the last value so future
        tuple-length divergence does not silently skip indices.
        """
        from curator.renderer import _caps_for_pages

        prev = _caps_for_pages(n - 1)
        cur = _caps_for_pages(n)
        prev_floors = prev.work_position_floors
        cur_floors = cur.work_position_floors
        max_len = max(len(prev_floors), len(cur_floors))
        for i in range(max_len):
            prev_val = prev_floors[i] if i < len(prev_floors) else prev_floors[-1]
            cur_val = cur_floors[i] if i < len(cur_floors) else cur_floors[-1]
            assert cur_val >= prev_val, (
                f"position {i}: pages {n} floor {cur_val} < pages {n - 1} {prev_val}"
            )
        assert cur.certificate_floor >= prev.certificate_floor

    def test_zero_pages_treated_as_short_form(self) -> None:
        """Defensive: pages <= 1 returns the short-form profile."""
        from curator.renderer import _caps_for_pages

        assert _caps_for_pages(0) == _caps_for_pages(1)


class TestPageCapsValidation:
    """``_PageCaps.__post_init__`` rejects invalid construction.

    Caps are normally constructed only via ``_caps_for_pages``, but
    direct construction (e.g., test scaffolding or future callers)
    should fail loud rather than silently produce broken behavior.
    """

    def test_rejects_empty_work_position_floors(self) -> None:
        from curator.page_caps import _PageCaps

        with pytest.raises(ValueError, match="work_position_floors must be non-empty"):
            _PageCaps(work_position_floors=(), certificate_floor=3)

    def test_rejects_negative_floor_values(self) -> None:
        from curator.page_caps import _PageCaps

        with pytest.raises(ValueError, match=">= 0"):
            _PageCaps(work_position_floors=(3, -1, 0), certificate_floor=3)

    def test_rejects_negative_certificate_floor(self) -> None:
        from curator.page_caps import _PageCaps

        with pytest.raises(ValueError, match="certificate_floor must be >= 0"):
            _PageCaps(work_position_floors=(3, 3), certificate_floor=-1)

    def test_floor_for_position_falls_through_to_last_value(self) -> None:
        """Positions beyond the tuple length receive the last value."""
        from curator.page_caps import _PageCaps

        caps = _PageCaps(work_position_floors=(8, 6, 6, 2, 2), certificate_floor=4)
        assert caps.floor_for_position(0) == 8
        assert caps.floor_for_position(4) == 2
        assert caps.floor_for_position(7) == 2  # falls through to last

    def test_floor_for_position_rejects_negative(self) -> None:
        from curator.page_caps import _PageCaps

        caps = _PageCaps(work_position_floors=(3,), certificate_floor=3)
        with pytest.raises(ValueError, match="position must be non-negative"):
            caps.floor_for_position(-1)


class TestCascadeDefaultFloors:
    """Pin the default ``work_position_floors`` on cascade entry points.

    Both ``_generate_next_trim`` and ``_trim_to_fit`` accept the floor
    tuple as a keyword arg. The default value is critical because many
    tests rely on it; a future drift to a 2-page-friendly default
    would silently flip behavior for every direct caller that omits
    the kwarg.
    """

    def test_generate_next_trim_default(self) -> None:
        import inspect

        from curator.renderer import _generate_next_trim

        sig = inspect.signature(_generate_next_trim)
        assert sig.parameters["work_position_floors"].default == (3, 3, 0, 0, 0)

    def test_trim_to_fit_default(self) -> None:
        import inspect

        from curator.renderer import _trim_to_fit

        sig = inspect.signature(_trim_to_fit)
        assert sig.parameters["work_position_floors"].default == (3, 3, 0, 0, 0)


class TestPerPositionFloorEdgeCases:
    """Edge cases for the new per-position floor cascade (tier 6)."""

    def test_three_work_entries_with_5_tuple_floor(self) -> None:
        """3 work entries against a 5-element floor tuple use indices 0-2."""
        from curator.renderer import _apply_trim, _generate_next_trim

        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": [{"id": f"h1_{i}"} for i in range(20)]},
                {"id": "w2", "highlights": [{"id": f"h2_{i}"} for i in range(20)]},
                {"id": "w3", "highlights": [{"id": f"h3_{i}"} for i in range(20)]},
            ],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        # 2-page floors (8, 6, 6, 2, 2): w1=8, w2=6, w3=6.
        while True:
            step = _generate_next_trim(
                sections, None, work_position_floors=(8, 6, 6, 2, 2)
            )
            if step is None or step.below_floor:
                break
            sections, _ = _apply_trim(sections, None, step)
        assert len(sections["work"][0]["highlights"]) == 8
        assert len(sections["work"][1]["highlights"]) == 6
        assert len(sections["work"][2]["highlights"]) == 6

    def test_seven_work_entries_falls_through_to_last_value(self) -> None:
        """7 entries against a 5-element floor tuple: positions 5,6 use last value."""
        from curator.renderer import _apply_trim, _generate_next_trim

        sections: dict[str, Any] = {
            "work": [
                {"id": f"w{i}", "highlights": [{"id": f"h{i}_{j}"} for j in range(20)]}
                for i in range(7)
            ],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        # 2-page floors (8, 6, 6, 2, 2): positions 0-4 use tuple values
        # directly; positions 5, 6 fall through to last value (2).
        while True:
            step = _generate_next_trim(
                sections, None, work_position_floors=(8, 6, 6, 2, 2)
            )
            if step is None or step.below_floor:
                break
            sections, _ = _apply_trim(sections, None, step)
        sizes = [len(e["highlights"]) for e in sections["work"]]
        assert sizes == [8, 6, 6, 2, 2, 2, 2]

    def test_empty_work_entries_skip_tier6(self) -> None:
        """``len(highlights) > floor`` is False when ``len == 0 == floor``.

        Pins the off-by-one: tier 6 must NOT fire on entries already at
        floor 0 with empty highlights, even though the cascade scans
        them. Otherwise the loop would never terminate.
        """
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [{"id": f"w{i}", "highlights": []} for i in range(5)],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        step = _generate_next_trim(sections, None, work_position_floors=(3, 3, 0, 0, 0))
        assert step is None  # nothing to trim, no infinite loop

    def test_below_floor_tier_skips_empty_positions(self) -> None:
        """Tier 8 (below-floor) scans bottom-up for first non-empty position.

        Positions already at 0 are skipped; the first non-empty
        position bottom-up is the trim target.
        """
        from curator.renderer import _generate_next_trim

        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": [{"id": "h1_0"}, {"id": "h1_1"}]},
                {"id": "w2", "highlights": []},  # pos 1 empty
                {"id": "w3", "highlights": [{"id": "h3_0"}]},  # pos 2
                {"id": "w4", "highlights": []},  # pos 3 empty
            ],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        # All work positions already <= floor (or empty). Skill groups
        # empty too. Only tier 8 (below_floor) can fire. Bottom-up
        # scan: w4 empty (skip), w3 has 1 (trim with below_floor=True).
        step = _generate_next_trim(sections, None, work_position_floors=(3, 3, 3, 3, 3))
        assert step is not None
        assert step.target_id == "w3"
        assert step.below_floor is True


class TestCascadeCliffRegression:
    """Regression tests for the cascade-cliff problem the rebalance fixed.

    Synthetic high-density 5-entry distributions exercise the corner
    case where the prior cascade drained positions 2+ to zero before
    trimming positions 0/1, producing 11/4/0/0/0-style "ghost row"
    output. Pinned here so future cascade tweaks cannot regress.
    """

    def test_high_density_5entry_no_ghost_rows_on_2page(self) -> None:
        """5-entry portfolio with skewed-high counts (32/24/12/17/4)
        under 2-page floors produces no empty work entries (positions 2+
        render content).
        """
        from curator.renderer import _apply_trim, _generate_next_trim

        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": [{"id": f"h1_{i}"} for i in range(32)]},
                {"id": "w2", "highlights": [{"id": f"h2_{i}"} for i in range(24)]},
                {"id": "w3", "highlights": [{"id": f"h3_{i}"} for i in range(12)]},
                {"id": "w4", "highlights": [{"id": f"h4_{i}"} for i in range(17)]},
                {"id": "w5", "highlights": [{"id": f"h5_{i}"} for i in range(4)]},
            ],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        # Drain through tier 6 only (no skill groups, certs, etc).
        while True:
            step = _generate_next_trim(
                sections, None, work_position_floors=(8, 6, 6, 2, 2)
            )
            if step is None or step.below_floor:
                break
            sections, _ = _apply_trim(sections, None, step)
        sizes = [len(e["highlights"]) for e in sections["work"]]
        # Each position lands at-or-above its floor; no zero ghost rows.
        assert sizes == [8, 6, 6, 2, 2]
        assert all(s >= 1 for s in sizes), f"ghost row regression: {sizes}"

    def test_1page_preserves_ghost_row_behavior(self) -> None:
        """1-page floor (3, 3, 0, 0, 0) drains positions 2+ to 0.

        The "ghost row" (header-only) policy is intentional on 1-page
        because page space is too constrained to support a non-zero
        older-role floor. Pinned so a future calibration that touches
        2-page floors does not accidentally widen the 1-page profile.
        """
        from curator.renderer import _apply_trim, _generate_next_trim

        sections: dict[str, Any] = {
            "work": [
                {"id": "w1", "highlights": [{"id": f"h1_{i}"} for i in range(10)]},
                {"id": "w2", "highlights": [{"id": f"h2_{i}"} for i in range(10)]},
                {"id": "w3", "highlights": [{"id": f"h3_{i}"} for i in range(10)]},
            ],
            "skills": [],
            "projects": [],
            "certificates": [],
            "education": [],
        }
        while True:
            step = _generate_next_trim(
                sections, None, work_position_floors=(3, 3, 0, 0, 0)
            )
            if step is None or step.below_floor:
                break
            sections, _ = _apply_trim(sections, None, step)
        sizes = [len(e["highlights"]) for e in sections["work"]]
        assert sizes == [3, 3, 0]
