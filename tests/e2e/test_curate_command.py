"""End-to-end tests for the ``curator curate`` CLI command.

These tests invoke the full CLI pipeline via Typer's CliRunner:
    loader → prompt → (mocked) API → renderer → real Typst compilation

The Claude API is mocked at the CuratorClient class boundary. Everything
else — portfolio loading, YAML rendering, Typst PDF compilation — runs
for real.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pypdf import PdfReader

from curator.cli import app

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_output_dir(output_root: Path) -> Path:
    """Return the single output directory under the output root."""
    dirs = [d for d in output_root.iterdir() if d.is_dir()]
    assert len(dirs) == 1, f"Expected 1 output dir, found {len(dirs)}: {dirs}"
    return dirs[0]


def _find_output_dirs(output_root: Path) -> list[Path]:
    """Return all output directories under the output root, sorted."""
    return sorted(d for d in output_root.iterdir() if d.is_dir())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestCurateHappyPath:
    """Full pipeline: load portfolio → mock API → render YAML → real Typst PDF."""

    def test_exits_zero(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
    ) -> None:
        result, _ = invoke_curate(jd_file)
        assert result.exit_code == 0, result.output

    def test_creates_dated_output_dir(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        e2e_output_dir: Path,
    ) -> None:
        invoke_curate(jd_file)
        output_dir = _find_output_dir(e2e_output_dir)

        assert "acme-corp" in output_dir.name
        # Directory name starts with a date: YYYY-MM-DD
        parts = output_dir.name.split("-")
        assert len(parts[0]) == 4  # year
        assert parts[0].isdigit()

    def test_produces_valid_pdf(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        e2e_output_dir: Path,
    ) -> None:
        invoke_curate(jd_file)
        output_dir = _find_output_dir(e2e_output_dir)
        pdf = output_dir / "resume.pdf"

        assert pdf.exists()
        assert pdf.stat().st_size > 0
        assert pdf.read_bytes()[:5] == b"%PDF-"

    def test_pdf_contains_expected_text(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        e2e_output_dir: Path,
    ) -> None:
        invoke_curate(jd_file)
        output_dir = _find_output_dir(e2e_output_dir)
        reader = PdfReader(output_dir / "resume.pdf")
        text = "".join(page.extract_text() or "" for page in reader.pages)

        # Name and injected summary from curation
        assert "Jane Doe" in text
        # Work entry
        assert "Senior Engineer" in text
        # Skill
        assert "AWS" in text

    def test_writes_artifact_and_data_files(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        e2e_output_dir: Path,
    ) -> None:
        invoke_curate(jd_file)
        output_dir = _find_output_dir(e2e_output_dir)

        # Audit artifacts
        assert (output_dir / "curated.yaml").exists()
        assert (output_dir / "curation_log.json").exists()
        assert (output_dir / "job_description.txt").exists()
        assert (output_dir / "layout.yaml").exists()

        # Data files for all renderable sections
        data_dir = output_dir / "data"
        for section in (
            "basics",
            "work",
            "skills",
            "projects",
            "certificates",
            "education",
            "interests",
        ):
            assert (data_dir / f"{section}.yaml").exists(), f"Missing {section}.yaml"

    def test_yaml_contents_match_selections(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        e2e_output_dir: Path,
    ) -> None:
        invoke_curate(jd_file)
        output_dir = _find_output_dir(e2e_output_dir)
        data_dir = output_dir / "data"

        work = yaml.safe_load((data_dir / "work.yaml").read_text())
        assert len(work) == 1
        assert work[0]["id"] == "acme-senior-engineer"
        assert len(work[0]["highlights"]) == 1
        assert work[0]["highlights"][0]["id"] == "acme-deployed-k8s"

        skills = yaml.safe_load((data_dir / "skills.yaml").read_text())
        assert len(skills) == 1
        assert skills[0]["id"] == "cloud-aws"

    def test_injects_summary(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        e2e_output_dir: Path,
    ) -> None:
        invoke_curate(jd_file)
        output_dir = _find_output_dir(e2e_output_dir)

        basics = yaml.safe_load((output_dir / "data" / "basics.yaml").read_text())
        assert "founder of Perts Foundry LLC" in basics["summary"]

    def test_layout_matches_section_order(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        e2e_output_dir: Path,
    ) -> None:
        invoke_curate(jd_file)
        output_dir = _find_output_dir(e2e_output_dir)

        layout = yaml.safe_load((output_dir / "layout.yaml").read_text())
        assert layout["section_order"] == [
            "work",
            "skills",
            "projects",
            "certificates",
            "education",
            "interests",
        ]

    def test_displays_results_table(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
    ) -> None:
        result, _ = invoke_curate(jd_file)
        assert "acme-corp" in result.output

    def test_curation_log_has_metadata(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        e2e_output_dir: Path,
    ) -> None:
        invoke_curate(jd_file)
        output_dir = _find_output_dir(e2e_output_dir)

        log = json.loads((output_dir / "curation_log.json").read_text())
        assert log["format_version"] == "2.6"
        assert log["source"] == "api"
        assert log["model"] == "claude-sonnet-4-6-20260217"
        assert log["input_tokens"] == 5000
        assert log["output_tokens"] == 500
        assert log["max_pages"] >= 1
        assert "timestamp" in log

    def test_preserves_jd_text(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        e2e_output_dir: Path,
    ) -> None:
        invoke_curate(jd_file)
        output_dir = _find_output_dir(e2e_output_dir)

        saved_jd = (output_dir / "job_description.txt").read_text()
        assert "Senior Site Reliability Engineer" in saved_jd

    def test_verbose_flag(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        cli_runner: Any,
        curation_result: Any,
    ) -> None:
        """--verbose is a top-level option, placed before the subcommand."""
        from unittest.mock import patch

        from curator.cli import app

        with patch("curator.pipeline.CuratorClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.curate.return_value = curation_result
            result = cli_runner.invoke(
                app, ["--verbose", "curate", str(jd_file)], catch_exceptions=True
            )
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestCurateErrors:
    """Verify user-friendly error handling for common failure modes."""

    def test_missing_jd_file(
        self,
        portfolio_dir: Path,
        invoke_curate: Any,
    ) -> None:
        result, _ = invoke_curate(
            Path("/nonexistent/path.txt"),
            catch_exceptions=True,
        )
        assert result.exit_code != 0

    def test_empty_jd_file(
        self,
        portfolio_dir: Path,
        invoke_curate: Any,
        tmp_path: Path,
    ) -> None:
        empty = tmp_path / "empty.txt"
        empty.write_text("", encoding="utf-8")

        result, _ = invoke_curate(empty)
        assert result.exit_code == 1
        assert "Traceback" not in result.output

    def test_api_error_shows_user_message(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
    ) -> None:
        from curator.exceptions import APIError

        result, _ = invoke_curate(
            jd_file,
            side_effect=APIError("connection failed"),
        )
        assert result.exit_code == 1
        # Should show the error message, not a raw traceback
        assert "connection failed" in result.output
        assert "Traceback" not in result.output

    def test_invalid_config_shows_config_error(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        cli_runner: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pydantic ValidationError from settings surfaces as ConfigError."""
        monkeypatch.delenv("CURATOR_ANTHROPIC_API_KEY", raising=False)
        result = cli_runner.invoke(app, ["curate", str(jd_file)], catch_exceptions=True)
        assert result.exit_code == 1
        assert "Error" in result.output
        assert "Traceback" not in result.output

    def test_missing_portfolio(
        self,
        jd_file: Path,
        invoke_curate: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # portfolio_dir intentionally omitted — we override the path to test the error.
        monkeypatch.setenv("CURATOR_PORTFOLIO_PATH", "/nonexistent/portfolio")

        result, _ = invoke_curate(jd_file)
        assert result.exit_code == 1
        assert "Traceback" not in result.output

    def test_typst_compilation_failure(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        monkeypatch: pytest.MonkeyPatch,
        e2e_output_dir: Path,
    ) -> None:
        bad_template = e2e_output_dir / "bad.typ"
        bad_template.write_text("#invalid typst {{{{ syntax", encoding="utf-8")
        monkeypatch.setenv("CURATOR_TEMPLATE_PATH", str(bad_template))

        result, _ = invoke_curate(jd_file)
        assert result.exit_code == 1
        assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestCurateEdgeCases:
    """Edge cases that exercise less-common code paths."""

    def test_duplicate_output_dir_gets_suffix(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        e2e_output_dir: Path,
    ) -> None:
        result1, _ = invoke_curate(jd_file)
        assert result1.exit_code == 0, result1.output

        result2, _ = invoke_curate(jd_file)
        assert result2.exit_code == 0, result2.output

        dirs = _find_output_dirs(e2e_output_dir)
        assert len(dirs) == 2
        # Second directory should have a numeric suffix
        assert dirs[1].name.endswith("-2")


# ---------------------------------------------------------------------------
# No-PDF mode
# ---------------------------------------------------------------------------


class TestCurateNoPdf:
    """No-PDF invocations: API is mocked, Typst compilation is skipped."""

    def test_no_pdf_no_pdf_produced(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        e2e_output_dir: Path,
    ) -> None:
        """--no-pdf exits zero but produces no PDF file."""
        result, _ = invoke_curate(jd_file, extra_args=["--no-pdf"])
        assert result.exit_code == 0, result.output

        output_dir = _find_output_dir(e2e_output_dir)
        assert not (output_dir / "resume.pdf").exists()

    def test_no_pdf_audit_artifacts_present(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        e2e_output_dir: Path,
    ) -> None:
        """curated.yaml and curation_log.json exist after --no-pdf."""
        invoke_curate(jd_file, extra_args=["--no-pdf"])
        output_dir = _find_output_dir(e2e_output_dir)

        assert (output_dir / "curated.yaml").exists()
        assert (output_dir / "curation_log.json").exists()
        assert (output_dir / "job_description.txt").exists()
        assert (output_dir / "layout.yaml").exists()

    def test_no_pdf_output_message(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
    ) -> None:
        """CLI output contains the no-pdf notice instead of a PDF path."""
        result, _ = invoke_curate(jd_file, extra_args=["--no-pdf"])
        assert result.exit_code == 0, result.output
        assert "No-PDF mode" in result.output
        assert "PDF compilation skipped" in result.output


# ---------------------------------------------------------------------------
# Dry-run mode (zero-cost preview)
# ---------------------------------------------------------------------------


class TestCurateDryRun:
    """Dry-run preview: no API call, shows portfolio stats and cost estimate."""

    def test_dry_run_exits_zero(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
    ) -> None:
        """--dry-run exits zero and displays preview."""
        result, mock_client = invoke_curate(jd_file, extra_args=["--dry-run"])
        assert result.exit_code == 0, result.output
        mock_client.curate.assert_not_called()

    def test_dry_run_shows_preview(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
    ) -> None:
        """--dry-run output contains preview table with stats."""
        result, _ = invoke_curate(jd_file, extra_args=["--dry-run"])
        assert result.exit_code == 0, result.output
        assert "Dry run" in result.output
        assert "Estimated cost" in result.output

    def test_dry_run_no_output_dir(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        e2e_output_dir: Path,
    ) -> None:
        """--dry-run does not create a profile output directory."""
        invoke_curate(jd_file, extra_args=["--dry-run"])
        dirs = list(e2e_output_dir.iterdir())
        assert len(dirs) == 0

    def test_dry_run_and_no_pdf_mutually_exclusive(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
    ) -> None:
        """--dry-run and --no-pdf together produce an error."""
        result, _ = invoke_curate(jd_file, extra_args=["--dry-run", "--no-pdf"])
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output


# ---------------------------------------------------------------------------
# Cover letter e2e paths
# ---------------------------------------------------------------------------


class TestCurateCoverLetterOffPath:
    """Without --cover-letter, no cover letter artifacts are produced."""

    def test_no_cover_letter_artifacts_written(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        invoke_curate: Any,
        e2e_output_dir: Path,
    ) -> None:
        invoke_curate(jd_file)
        output_dir = _find_output_dir(e2e_output_dir)
        assert not (output_dir / "cover_letter.pdf").exists()
        assert not (output_dir / "data" / "cover_letter.yaml").exists()
        log = json.loads((output_dir / "curation_log.json").read_text())
        assert log["cover_letter"] == {"enabled": False}


class TestCurateCoverLetterOnPath:
    """With --cover-letter, both artifacts are written and stream is called once."""

    def test_writes_cover_letter_pdf_and_yaml(
        self,
        portfolio_dir: Path,
        jd_file: Path,
        e2e_output_dir: Path,
        cli_runner: Any,
        curation_result: Any,
    ) -> None:
        from dataclasses import replace
        from unittest.mock import patch

        from tests.helpers import valid_cover_letter

        letter = valid_cover_letter()
        result_with_cl = replace(curation_result, cover_letter=letter)

        with patch("curator.pipeline.CuratorClient") as mock_cls:
            mock_client = mock_cls.return_value.__enter__.return_value
            mock_client.curate.return_value = result_with_cl
            result = cli_runner.invoke(app, ["curate", str(jd_file), "--cover-letter"])
            assert result.exit_code == 0, result.output
            mock_client.curate.assert_called_once()
            assert mock_client.curate.call_args.kwargs["with_cover_letter"] is True

        output_dir = _find_output_dir(e2e_output_dir)
        assert (output_dir / "resume.pdf").exists()
        assert (output_dir / "cover_letter.pdf").exists()
        assert (output_dir / "data" / "cover_letter.yaml").exists()
        log = json.loads((output_dir / "curation_log.json").read_text())
        assert log["cover_letter"]["enabled"] is True
        assert "is_template" not in log["cover_letter"]
        assert log["cover_letter"]["word_count"] > 0


@pytest.mark.parametrize("pages", [1, 2])
def test_curate_pages_flag_threads_through_render_and_log(
    pages: int,
    portfolio_dir: Path,
    jd_file: Path,
    invoke_curate: Any,
    e2e_output_dir: Path,
) -> None:
    """End-to-end ``--pages`` parametrize: render and audit log both honor it.

    Pinned by AR-6. With real Typst + mocked API, a 1-page run and a
    2-page run produce different ``curation_log.json["max_pages"]``
    values and different actual PDF page counts (within reason — the
    cascade may converge identically on a small fixture, but the
    persisted ``max_pages`` is unconditional).
    """
    result, _ = invoke_curate(jd_file, extra_args=["--pages", str(pages)])
    assert result.exit_code == 0, result.output
    output_dir = _find_output_dir(e2e_output_dir)

    # Audit log records the requested page budget.
    log = json.loads((output_dir / "curation_log.json").read_text())
    assert log["max_pages"] == pages

    # PDF exists and is at most max_pages pages.
    pdf = output_dir / "resume.pdf"
    assert pdf.exists()
    reader = PdfReader(pdf)
    assert 1 <= len(reader.pages) <= pages
