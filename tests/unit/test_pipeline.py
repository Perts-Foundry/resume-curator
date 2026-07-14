"""Tests for curator.pipeline single-call + renderer-side trimming."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from curator.pipeline import (
    PipelineResult,
    _summarize_pipeline_result,
    run_pipeline,
    run_static_pipeline,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_settings(
    tmp_path: Path,
    *,
    max_pages: int = 1,
    max_trim_iterations: int = 15,
) -> MagicMock:
    """Build a MagicMock settings with the fields pipeline.py reads."""
    settings = MagicMock()
    settings.portfolio_data_path = tmp_path / "data"
    settings.output_dir = tmp_path / "output"
    settings.template_path = tmp_path / "curated.typ"
    settings.max_pages = max_pages
    settings.max_trim_iterations = max_trim_iterations
    return settings


def _make_mock_result(
    *,
    input_tokens: int = 1000,
    output_tokens: int = 500,
) -> MagicMock:
    """Build a MagicMock CurationResult with token fields."""
    result = MagicMock()
    result.input_tokens = input_tokens
    result.output_tokens = output_tokens
    return result


def _make_mock_render_output(
    tmp_path: Path,
    *,
    trim_log: list[str] | None = None,
    page_count: int | None = 1,
    cover_letter_pdf_path: Path | None = None,
    cover_letter_yaml_path: Path | None = None,
    cover_letter_txt_path: Path | None = None,
) -> MagicMock:
    """Build a MagicMock RenderOutput with pdf_path, trim_log, page_count."""
    output = MagicMock()
    output.profile_dir = tmp_path / "output" / "acme-corp"
    output.pdf_path = tmp_path / "output" / "acme-corp" / "resume.pdf"
    output.trim_log = trim_log or []
    output.page_count = page_count
    output.cover_letter_pdf_path = cover_letter_pdf_path
    output.cover_letter_yaml_path = cover_letter_yaml_path
    output.cover_letter_txt_path = cover_letter_txt_path
    return output


# ---------------------------------------------------------------------------
# TestPipelineResult
# ---------------------------------------------------------------------------


class TestPipelineResult:
    def test_fields_present(self) -> None:
        result = PipelineResult(
            curation=MagicMock(),
            render_output=MagicMock(),
            portfolio=MagicMock(),
            skip_pdf=False,
            page_count=1,
            converged=True,
            total_input_tokens=2000,
            total_output_tokens=1000,
            trim_log=["Removed interests section"],
        )
        assert result.page_count == 1
        assert result.converged is True
        assert result.trim_log == ["Removed interests section"]

    def test_frozen(self) -> None:
        result = PipelineResult(
            curation=MagicMock(),
            render_output=MagicMock(),
            portfolio=MagicMock(),
            skip_pdf=False,
            page_count=1,
            converged=True,
            total_input_tokens=0,
            total_output_tokens=0,
        )
        with pytest.raises(AttributeError):
            result.converged = False  # type: ignore[misc]

    def test_trim_log_defaults_empty(self) -> None:
        result = PipelineResult(
            curation=MagicMock(),
            render_output=MagicMock(),
            portfolio=MagicMock(),
            skip_pdf=False,
            page_count=1,
            converged=True,
            total_input_tokens=0,
            total_output_tokens=0,
        )
        assert result.trim_log == []


# ---------------------------------------------------------------------------
# TestSkipPdf
# ---------------------------------------------------------------------------


class TestSkipPdf:
    """No-PDF mode calls curate once, renders with skip_pdf=True."""

    def test_skip_pdf_single_api_call(
        self,
        mocker: Any,
        tmp_path: Path,
    ) -> None:
        settings = _make_mock_settings(tmp_path)
        mock_portfolio = MagicMock()
        mocker.patch("curator.pipeline.load_portfolio", return_value=mock_portfolio)

        mock_result = _make_mock_result()
        mock_client = MagicMock()
        mock_client.curate.return_value = mock_result
        mock_client_cls = mocker.patch("curator.pipeline.CuratorClient")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_render_output = MagicMock()
        mock_render_output.pdf_path = None
        mock_render_output.trim_log = []
        mock_render_output.page_count = None
        mock_render = mocker.patch(
            "curator.pipeline.render", return_value=mock_render_output
        )

        result = run_pipeline(settings, "Job description.", skip_pdf=True)

        mock_client.curate.assert_called_once()
        mock_render.assert_called_once_with(
            mock_result,
            mock_portfolio,
            "Job description.",
            settings,
            skip_pdf=True,
            safety_net=True,
            jd_scan_record=None,
        )
        assert result.skip_pdf is True
        assert result.page_count is None
        assert result.converged is True

    def test_jd_scan_record_threaded_to_render(
        self,
        mocker: Any,
        tmp_path: Path,
    ) -> None:
        settings = _make_mock_settings(tmp_path)
        mocker.patch("curator.pipeline.load_portfolio")

        mock_client = MagicMock()
        mock_client.curate.return_value = _make_mock_result()
        mock_client_cls = mocker.patch("curator.pipeline.CuratorClient")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_render_output = MagicMock()
        mock_render_output.pdf_path = None
        mock_render_output.trim_log = []
        mock_render_output.page_count = None
        mock_render = mocker.patch(
            "curator.pipeline.render", return_value=mock_render_output
        )

        record = {"suspected": False, "mode": "ask", "action": "none"}
        run_pipeline(settings, "JD.", skip_pdf=True, jd_scan_record=record)

        assert mock_render.call_args.kwargs["jd_scan_record"] == record

    def test_skip_pdf_token_counts(
        self,
        mocker: Any,
        tmp_path: Path,
    ) -> None:
        settings = _make_mock_settings(tmp_path)
        mocker.patch("curator.pipeline.load_portfolio")

        mock_result = _make_mock_result(input_tokens=1500, output_tokens=750)
        mock_client = MagicMock()
        mock_client.curate.return_value = mock_result
        mock_client_cls = mocker.patch("curator.pipeline.CuratorClient")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_render_output = MagicMock()
        mock_render_output.pdf_path = None
        mock_render_output.trim_log = []
        mock_render_output.page_count = None
        mocker.patch("curator.pipeline.render", return_value=mock_render_output)

        result = run_pipeline(settings, "JD.", skip_pdf=True)

        assert result.total_input_tokens == 1500
        assert result.total_output_tokens == 750


# ---------------------------------------------------------------------------
# TestSingleApiCall
# ---------------------------------------------------------------------------


class TestSingleApiCall:
    """Pipeline always calls the API exactly once."""

    def test_single_api_call(
        self,
        mocker: Any,
        tmp_path: Path,
    ) -> None:
        settings = _make_mock_settings(tmp_path, max_pages=1)
        mocker.patch("curator.pipeline.load_portfolio")

        mock_result = _make_mock_result()
        mock_client = MagicMock()
        mock_client.curate.return_value = mock_result
        mock_client_cls = mocker.patch("curator.pipeline.CuratorClient")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mocker.patch(
            "curator.pipeline.render",
            return_value=_make_mock_render_output(tmp_path, page_count=1),
        )

        result = run_pipeline(settings, "JD.")

        mock_client.curate.assert_called_once()
        assert result.converged is True
        assert result.page_count == 1

    def test_token_counts_from_single_call(
        self,
        mocker: Any,
        tmp_path: Path,
    ) -> None:
        """Token counts come directly from the single API call (no accumulation)."""
        settings = _make_mock_settings(tmp_path, max_pages=1)
        mocker.patch("curator.pipeline.load_portfolio")

        mock_result = _make_mock_result(input_tokens=2000, output_tokens=800)
        mock_client = MagicMock()
        mock_client.curate.return_value = mock_result
        mock_client_cls = mocker.patch("curator.pipeline.CuratorClient")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mocker.patch(
            "curator.pipeline.render",
            return_value=_make_mock_render_output(tmp_path, page_count=1),
        )

        result = run_pipeline(settings, "JD.")

        assert result.total_input_tokens == 2000
        assert result.total_output_tokens == 800


# ---------------------------------------------------------------------------
# TestTrimConvergence
# ---------------------------------------------------------------------------


class TestTrimConvergence:
    """Renderer-side trimming results flow through PipelineResult."""

    def test_trim_log_from_renderer(
        self,
        mocker: Any,
        tmp_path: Path,
    ) -> None:
        """trim_log is propagated from render output to pipeline result."""
        settings = _make_mock_settings(tmp_path, max_pages=1)
        mocker.patch("curator.pipeline.load_portfolio")

        mock_result = _make_mock_result()
        mock_client = MagicMock()
        mock_client.curate.return_value = mock_result
        mock_client_cls = mocker.patch("curator.pipeline.CuratorClient")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        trim_entries = [
            "Removed interests section",
            "Removed skill group: infra",
            "Removed certificate: cka",
        ]
        mocker.patch(
            "curator.pipeline.render",
            return_value=_make_mock_render_output(
                tmp_path, trim_log=trim_entries, page_count=1
            ),
        )

        result = run_pipeline(settings, "JD.")

        assert result.trim_log == trim_entries
        assert result.converged is True
        assert result.page_count == 1

    def test_no_trims_needed(
        self,
        mocker: Any,
        tmp_path: Path,
    ) -> None:
        """When no trimming is needed, trim_log is empty."""
        settings = _make_mock_settings(tmp_path, max_pages=1)
        mocker.patch("curator.pipeline.load_portfolio")

        mock_result = _make_mock_result()
        mock_client = MagicMock()
        mock_client.curate.return_value = mock_result
        mock_client_cls = mocker.patch("curator.pipeline.CuratorClient")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mocker.patch(
            "curator.pipeline.render",
            return_value=_make_mock_render_output(tmp_path, page_count=1),
        )

        result = run_pipeline(settings, "JD.")

        assert result.trim_log == []
        assert result.converged is True

    def test_not_converged(
        self,
        mocker: Any,
        tmp_path: Path,
    ) -> None:
        """Pipeline reports converged=False when page count exceeds target."""
        settings = _make_mock_settings(tmp_path, max_pages=1)
        mocker.patch("curator.pipeline.load_portfolio")

        mock_result = _make_mock_result()
        mock_client = MagicMock()
        mock_client.curate.return_value = mock_result
        mock_client_cls = mocker.patch("curator.pipeline.CuratorClient")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mocker.patch(
            "curator.pipeline.render",
            return_value=_make_mock_render_output(
                tmp_path,
                trim_log=["Removed interests section"],
                page_count=2,
            ),
        )

        result = run_pipeline(settings, "JD.")

        assert result.converged is False
        assert result.page_count == 2
        # API still called exactly once
        mock_client.curate.assert_called_once()


# ---------------------------------------------------------------------------
# TestMultiPageTarget
# ---------------------------------------------------------------------------


class TestMultiPageTarget:
    """max_pages > 1 is respected."""

    def test_two_page_target_converges(
        self,
        mocker: Any,
        tmp_path: Path,
    ) -> None:
        settings = _make_mock_settings(tmp_path, max_pages=2)
        mocker.patch("curator.pipeline.load_portfolio")

        mock_result = _make_mock_result()
        mock_client = MagicMock()
        mock_client.curate.return_value = mock_result
        mock_client_cls = mocker.patch("curator.pipeline.CuratorClient")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mocker.patch(
            "curator.pipeline.render",
            return_value=_make_mock_render_output(tmp_path, page_count=2),
        )

        result = run_pipeline(settings, "JD.")

        assert result.converged is True
        assert result.page_count == 2


# ---------------------------------------------------------------------------
# TestStatusCallback
# ---------------------------------------------------------------------------


class TestStatusCallback:
    """The on_status callback is invoked at the right moments."""

    def test_callback_called_during_pipeline(
        self,
        mocker: Any,
        tmp_path: Path,
    ) -> None:
        settings = _make_mock_settings(tmp_path, max_pages=1)
        mocker.patch("curator.pipeline.load_portfolio")

        mock_client = MagicMock()
        mock_client.curate.return_value = _make_mock_result()
        mock_client_cls = mocker.patch("curator.pipeline.CuratorClient")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mocker.patch(
            "curator.pipeline.render",
            return_value=_make_mock_render_output(tmp_path, page_count=1),
        )

        status_messages: list[str] = []
        result = run_pipeline(
            settings, "JD.", on_status=lambda msg: status_messages.append(msg)
        )

        assert result.converged is True
        assert any("Loading portfolio" in m for m in status_messages)
        assert any("Curating resume" in m for m in status_messages)
        assert any("Rendering PDF" in m for m in status_messages)

    def test_skip_pdf_callback(
        self,
        mocker: Any,
        tmp_path: Path,
    ) -> None:
        settings = _make_mock_settings(tmp_path)
        mocker.patch("curator.pipeline.load_portfolio")

        mock_client = MagicMock()
        mock_client.curate.return_value = _make_mock_result()
        mock_client_cls = mocker.patch("curator.pipeline.CuratorClient")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mock_render_output = MagicMock()
        mock_render_output.pdf_path = None
        mock_render_output.trim_log = []
        mock_render_output.page_count = None
        mocker.patch("curator.pipeline.render", return_value=mock_render_output)

        status_messages: list[str] = []
        run_pipeline(settings, "JD.", skip_pdf=True, on_status=status_messages.append)

        assert any("Curating resume" in m for m in status_messages)
        assert any("Writing audit artifacts" in m for m in status_messages)

    def test_no_callback_does_not_crash(
        self,
        mocker: Any,
        tmp_path: Path,
    ) -> None:
        settings = _make_mock_settings(tmp_path, max_pages=1)
        mocker.patch("curator.pipeline.load_portfolio")

        mock_client = MagicMock()
        mock_client.curate.return_value = _make_mock_result()
        mock_client_cls = mocker.patch("curator.pipeline.CuratorClient")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mocker.patch(
            "curator.pipeline.render",
            return_value=_make_mock_render_output(tmp_path, page_count=1),
        )

        result = run_pipeline(settings, "JD.", on_status=None)
        assert result.converged is True

    def test_trim_status_callback(
        self,
        mocker: Any,
        tmp_path: Path,
    ) -> None:
        """Status callback reports trim count when trimming occurred."""
        settings = _make_mock_settings(tmp_path, max_pages=1)
        mocker.patch("curator.pipeline.load_portfolio")

        mock_client = MagicMock()
        mock_client.curate.return_value = _make_mock_result()
        mock_client_cls = mocker.patch("curator.pipeline.CuratorClient")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        mocker.patch(
            "curator.pipeline.render",
            return_value=_make_mock_render_output(
                tmp_path,
                trim_log=[
                    "Removed interests section",
                    "Removed skill group: infra",
                    "Removed certificate: cka",
                ],
                page_count=1,
            ),
        )

        status_messages: list[str] = []
        run_pipeline(settings, "JD.", on_status=status_messages.append)

        assert any("Trimmed 3 item(s)" in m for m in status_messages)


# ---------------------------------------------------------------------------
# TestRunStaticPipeline
# ---------------------------------------------------------------------------


class TestRunStaticPipeline:
    """run_static_pipeline skips the client and synthesizes locally."""

    def test_happy_path_no_api_call(self, tmp_path: Path, mocker: Any) -> None:
        settings = _make_mock_settings(tmp_path)

        static_result = _make_mock_result(input_tokens=0, output_tokens=0)
        static_result.source = "static"
        mocker.patch("curator.pipeline.load_portfolio", return_value=MagicMock())
        mocker.patch("curator.pipeline.build_static_result", return_value=static_result)
        mock_render = mocker.patch(
            "curator.pipeline.render",
            return_value=_make_mock_render_output(tmp_path, page_count=1),
        )
        # CuratorClient must not be constructed in static mode.
        mock_client = mocker.patch("curator.pipeline.CuratorClient")

        result = run_static_pipeline(settings, name="general")

        assert result.total_input_tokens == 0
        assert result.total_output_tokens == 0
        assert result.curation.source == "static"
        assert mock_client.call_count == 0
        # render() received jd_text=None.
        _, kwargs = mock_render.call_args
        assert kwargs["jd_text"] is None

    def test_skip_pdf_short_circuits(self, tmp_path: Path, mocker: Any) -> None:
        settings = _make_mock_settings(tmp_path)

        mocker.patch("curator.pipeline.load_portfolio", return_value=MagicMock())
        mocker.patch(
            "curator.pipeline.build_static_result",
            return_value=_make_mock_result(input_tokens=0, output_tokens=0),
        )
        mocker.patch(
            "curator.pipeline.render",
            return_value=_make_mock_render_output(tmp_path, page_count=None),
        )

        result = run_static_pipeline(settings, name="general", skip_pdf=True)

        assert result.skip_pdf is True
        assert result.converged is True  # no page check when skip_pdf

    def test_name_and_max_highlights_forwarded(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        settings = _make_mock_settings(tmp_path)

        mocker.patch("curator.pipeline.load_portfolio", return_value=MagicMock())
        build_mock = mocker.patch(
            "curator.pipeline.build_static_result",
            return_value=_make_mock_result(input_tokens=0, output_tokens=0),
        )
        mocker.patch(
            "curator.pipeline.render",
            return_value=_make_mock_render_output(tmp_path),
        )

        run_static_pipeline(settings, name="Acme Corp", max_highlights=3)

        _, kwargs = build_mock.call_args
        assert kwargs["name"] == "Acme Corp"
        assert kwargs["max_highlights_per_work"] == 3


class TestSummarizePipelineResult:
    """The shared tail helper produces a valid PipelineResult."""

    def test_converged_when_under_budget(self, tmp_path: Path) -> None:
        settings = _make_mock_settings(tmp_path, max_pages=2)
        curation = _make_mock_result(input_tokens=0, output_tokens=0)
        render_out = _make_mock_render_output(tmp_path, page_count=1)
        portfolio = MagicMock()

        result = _summarize_pipeline_result(
            curation=curation,
            render_output=render_out,
            portfolio=portfolio,
            skip_pdf=False,
            settings=settings,
            on_status=lambda _msg: None,
        )

        assert result.converged is True
        assert result.page_count == 1

    def test_not_converged_when_over_budget(self, tmp_path: Path) -> None:
        settings = _make_mock_settings(tmp_path, max_pages=1)
        curation = _make_mock_result()
        render_out = _make_mock_render_output(
            tmp_path, page_count=2, trim_log=["trim1", "trim2"]
        )
        portfolio = MagicMock()

        status: list[str] = []
        result = _summarize_pipeline_result(
            curation=curation,
            render_output=render_out,
            portfolio=portfolio,
            skip_pdf=False,
            settings=settings,
            on_status=status.append,
        )

        assert result.converged is False
        assert any("Trimmed 2 item(s)" in m for m in status)

    def test_skip_pdf_reports_converged(self, tmp_path: Path) -> None:
        settings = _make_mock_settings(tmp_path)
        curation = _make_mock_result(input_tokens=0, output_tokens=0)
        render_out = _make_mock_render_output(tmp_path, page_count=None)
        portfolio = MagicMock()

        result = _summarize_pipeline_result(
            curation=curation,
            render_output=render_out,
            portfolio=portfolio,
            skip_pdf=True,
            settings=settings,
            on_status=lambda _msg: None,
        )
        assert result.converged is True


# ---------------------------------------------------------------------------
# Cover letter flag threading + summary
# ---------------------------------------------------------------------------


class TestCoverLetterPipeline:
    def test_run_pipeline_threads_flag_to_client(
        self, mocker: Any, tmp_path: Path
    ) -> None:
        settings = _make_mock_settings(tmp_path)
        mocker.patch("curator.pipeline.load_portfolio", return_value=MagicMock())
        mock_result = _make_mock_result()
        mock_client_ctx = MagicMock()
        mock_client_ctx.__enter__ = MagicMock(return_value=mock_client_ctx)
        mock_client_ctx.__exit__ = MagicMock(return_value=False)
        mock_client_ctx.curate = MagicMock(return_value=mock_result)
        mocker.patch("curator.pipeline.CuratorClient", return_value=mock_client_ctx)
        mocker.patch(
            "curator.pipeline.render",
            return_value=_make_mock_render_output(tmp_path, page_count=1),
        )

        run_pipeline(settings, "jd", skip_pdf=True, with_cover_letter=True)

        mock_client_ctx.curate.assert_called_once()
        kwargs = mock_client_ctx.curate.call_args.kwargs
        assert kwargs["with_cover_letter"] is True

    def test_run_static_pipeline_threads_flag(
        self, mocker: Any, tmp_path: Path
    ) -> None:
        settings = _make_mock_settings(tmp_path)
        mocker.patch("curator.pipeline.load_portfolio", return_value=MagicMock())
        mock_result = _make_mock_result(input_tokens=0, output_tokens=0)
        mock_build = mocker.patch(
            "curator.pipeline.build_static_result", return_value=mock_result
        )
        mocker.patch(
            "curator.pipeline.render",
            return_value=_make_mock_render_output(tmp_path, page_count=1),
        )

        run_static_pipeline(
            settings, name="acme", skip_pdf=True, with_cover_letter=True
        )

        kwargs = mock_build.call_args.kwargs
        assert kwargs["with_cover_letter"] is True

    def test_summary_emits_cover_letter_line_when_pdf_present(
        self, tmp_path: Path
    ) -> None:
        settings = _make_mock_settings(tmp_path)
        curation = _make_mock_result()
        render_out = _make_mock_render_output(
            tmp_path,
            page_count=1,
            cover_letter_pdf_path=tmp_path / "cover_letter.pdf",
        )
        portfolio = MagicMock()
        status: list[str] = []
        _summarize_pipeline_result(
            curation=curation,
            render_output=render_out,
            portfolio=portfolio,
            skip_pdf=False,
            settings=settings,
            on_status=status.append,
        )
        assert any("Cover letter generated" in m for m in status)

    def test_summary_silent_when_no_cover_letter(self, tmp_path: Path) -> None:
        settings = _make_mock_settings(tmp_path)
        curation = _make_mock_result()
        render_out = _make_mock_render_output(tmp_path, page_count=1)
        portfolio = MagicMock()
        status: list[str] = []
        _summarize_pipeline_result(
            curation=curation,
            render_output=render_out,
            portfolio=portfolio,
            skip_pdf=False,
            settings=settings,
            on_status=status.append,
        )
        assert not any("Cover letter" in m for m in status)

    def test_summary_emits_paste_ready_line_when_txt_present(
        self, tmp_path: Path
    ) -> None:
        # New sidecar artifact gets a parallel status line. Always rides
        # alongside the PDF/YAML line so users see all three paths.
        settings = _make_mock_settings(tmp_path)
        curation = _make_mock_result()
        render_out = _make_mock_render_output(
            tmp_path,
            page_count=1,
            cover_letter_pdf_path=tmp_path / "cover_letter.pdf",
            cover_letter_txt_path=tmp_path / "cover_letter.txt",
        )
        portfolio = MagicMock()
        status: list[str] = []
        _summarize_pipeline_result(
            curation=curation,
            render_output=render_out,
            portfolio=portfolio,
            skip_pdf=False,
            settings=settings,
            on_status=status.append,
        )
        assert any("Cover letter paste-ready" in m for m in status)
        assert any("cover_letter.txt" in m for m in status)
