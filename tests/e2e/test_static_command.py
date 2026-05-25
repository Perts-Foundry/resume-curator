"""End-to-end tests for the ``curator static`` CLI command.

Invokes the full CLI via Typer's CliRunner: loader -> static synthesis ->
renderer -> real Typst compilation. No API call is made.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from pypdf import PdfReader

from curator.cli import app

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def _output_dir_for(output_root: Path, slug: str) -> Path:
    """Return the single output directory matching *slug*."""
    matches = [d for d in output_root.iterdir() if d.is_dir() and slug in d.name]
    assert len(matches) == 1, (
        f"Expected one output dir matching '{slug}', found {len(matches)}: {matches}"
    )
    return matches[0]


class TestStaticHappyPath:
    """Static command runs the real Typst compiler and produces a valid PDF."""

    def test_one_page_default(
        self,
        portfolio_dir: Path,
        cli_runner: Any,
        e2e_output_dir: Path,
    ) -> None:
        # Distinct --name avoids collisions with any other same-day e2e run.
        result = cli_runner.invoke(app, ["static", "--name", "static-happy"])
        assert result.exit_code == 0, result.output

        out_dir = _output_dir_for(e2e_output_dir, "static-happy")
        pdf = out_dir / "resume.pdf"
        assert pdf.exists()
        reader = PdfReader(pdf)
        assert len(reader.pages) == 1

    def test_multi_page_with_pages_flag(
        self,
        portfolio_dir: Path,
        cli_runner: Any,
        e2e_output_dir: Path,
    ) -> None:
        result = cli_runner.invoke(
            app, ["static", "--name", "static-3page", "--pages", "3"]
        )
        assert result.exit_code == 0, result.output

        out_dir = _output_dir_for(e2e_output_dir, "static-3page")
        pdf = out_dir / "resume.pdf"
        assert pdf.exists()
        reader = PdfReader(pdf)
        assert 1 <= len(reader.pages) <= 3

    def test_curation_log_marks_static_source(
        self,
        portfolio_dir: Path,
        cli_runner: Any,
        e2e_output_dir: Path,
    ) -> None:
        result = cli_runner.invoke(app, ["static", "--name", "static-source"])
        assert result.exit_code == 0, result.output

        out_dir = _output_dir_for(e2e_output_dir, "static-source")
        log = json.loads((out_dir / "curation_log.json").read_text())
        assert log["source"] == "static"
        assert log["model"] == "n/a"
        assert log["input_tokens"] == 0
        assert log["output_tokens"] == 0
        assert (out_dir / "mode.txt").exists()
        assert not (out_dir / "job_description.txt").exists()


class TestStaticCoverLetterCommand:
    """``curator static --cover-letter`` renders the portfolio letter verbatim."""

    def test_happy_path_writes_pdf_and_yaml(
        self,
        portfolio_dir: Path,
        cli_runner: Any,
        e2e_output_dir: Path,
    ) -> None:
        import yaml as _yaml

        result = cli_runner.invoke(
            app, ["static", "--name", "cl-happy", "--cover-letter"]
        )
        assert result.exit_code == 0, result.output

        out_dir = _output_dir_for(e2e_output_dir, "cl-happy")
        pdf = out_dir / "cover_letter.pdf"
        assert pdf.exists()
        reader = PdfReader(pdf)
        assert len(reader.pages) >= 1

        data = _yaml.safe_load((out_dir / "data" / "cover_letter.yaml").read_text())
        assert "is_template" not in data
        # No unfilled placeholder tokens in body text.
        body = " ".join(
            [
                data["salutation"],
                data["opening"],
                *data["body_paragraphs"],
                data["closing"],
            ]
        )
        for token in ("[COMPANY]", "[ROLE]", "[HIRING_MANAGER_NAME]", "[TAILOR:"):
            assert token not in body

        log = json.loads((out_dir / "curation_log.json").read_text())
        assert log["cover_letter"]["enabled"] is True
        assert "is_template" not in log["cover_letter"]

    def test_missing_cover_letter_file_raises(
        self,
        portfolio_dir: Path,
        cli_runner: Any,
    ) -> None:
        # Remove the cover-letter YAML that portfolio_dir wrote.
        (portfolio_dir / "cover-letter.yaml").unlink()
        result = cli_runner.invoke(
            app, ["static", "--name", "cl-missing", "--cover-letter"]
        )
        assert result.exit_code != 0
        assert "data/cover-letter.yaml" in result.output
        assert "COVER_LETTER_" in result.output

    def test_name_does_not_influence_cover_letter_body(
        self,
        portfolio_dir: Path,
        cli_runner: Any,
        e2e_output_dir: Path,
    ) -> None:
        import yaml as _yaml

        result = cli_runner.invoke(
            app, ["static", "--name", "cl-acme", "--cover-letter"]
        )
        assert result.exit_code == 0, result.output

        out_dir = _output_dir_for(e2e_output_dir, "cl-acme")
        data = _yaml.safe_load((out_dir / "data" / "cover_letter.yaml").read_text())
        body = " ".join([data["opening"], *data["body_paragraphs"], data["closing"]])
        assert "cl-acme" not in body.lower()

    def test_static_cover_letter_emits_three_artifacts(
        self,
        portfolio_dir: Path,
        cli_runner: Any,
        e2e_output_dir: Path,
    ) -> None:
        # Pin the three-artifact contract end-to-end through the CLI:
        # cover_letter.pdf, cover_letter.txt, data/cover_letter.yaml.
        # Catches a regression where the .txt write is wired in the
        # renderer but unhooked from the CLI status path or the public
        # render() integration.
        import yaml as _yaml

        from curator.models import Basics, CoverLetterCuration

        result = cli_runner.invoke(
            app, ["static", "--name", "cl-three-artifacts", "--cover-letter"]
        )
        assert result.exit_code == 0, result.output

        out_dir = _output_dir_for(e2e_output_dir, "cl-three-artifacts")
        assert (out_dir / "cover_letter.pdf").exists()
        assert (out_dir / "cover_letter.txt").exists()
        assert (out_dir / "data" / "cover_letter.yaml").exists()

        # The .txt must be paste-ready: paragraphs separated by blank
        # lines, no internal wrapping, plain ASCII hyphens preserved.
        txt = (out_dir / "cover_letter.txt").read_text(encoding="utf-8")
        assert txt.endswith("\n")
        assert "\n\n" in txt  # at least one blank-line separator
        # No tofu-box ancestor characters from the PDF clipboard heuristic.
        assert "\u00ad" not in txt  # SOFT HYPHEN
        assert "\u2011" not in txt  # NON-BREAKING HYPHEN (PDF-only rule)

        # Round-trip pin: the .txt content must equal the model serializer
        # applied to the YAML the CLI just wrote, with the signer name
        # from the rendered basics. Ties the CLI path, renderer, and
        # model serializer together; catches silent encoding or
        # normalization steps that would slip through the property-level
        # assertions above (e.g., a future contributor adding a Typst
        # post-processor that touches the .txt by accident).
        yaml_data = _yaml.safe_load(
            (out_dir / "data" / "cover_letter.yaml").read_text(encoding="utf-8")
        )
        basics_data = _yaml.safe_load(
            (out_dir / "data" / "basics.yaml").read_text(encoding="utf-8")
        )
        letter = CoverLetterCuration(
            salutation=yaml_data["salutation"],
            opening=yaml_data["opening"],
            body_paragraphs=yaml_data["body_paragraphs"],
            closing=yaml_data["closing"],
            sign_off=yaml_data["sign_off"],
        )
        basics = Basics.model_validate(basics_data)
        assert txt == letter.to_plaintext(basics.name)
