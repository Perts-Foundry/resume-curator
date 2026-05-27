"""Tests for the ``scripts/rerender.py`` recovery helper.

The script is loaded via ``importlib`` rather than executed as a
subprocess so we can mock the heavy dependencies (loader, renderer,
Typst) and inspect the call wiring directly. The script's
``_main()`` does the real argparse + dispatch work; we drive it by
patching ``sys.argv`` and the IO boundaries.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from curator.exceptions import APIResponseError

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "rerender.py"
)


def _load_rerender() -> Any:
    """Import scripts/rerender.py as a module without polluting sys.path."""
    spec = importlib.util.spec_from_file_location("rerender_under_test", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def rerender_module() -> Any:
    return _load_rerender()


class TestExtensionSanityChecks:
    """A swapped flag should print an actionable hint rather than a stack trace."""

    def test_partial_rejects_json_input(
        self,
        rerender_module: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "curation_partial-test.json"
        path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["rerender", "--partial", str(path)])

        with pytest.raises(SystemExit) as exc:
            rerender_module._main()

        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "Did you mean --raw" in captured.err

    def test_raw_rejects_yaml_input(
        self,
        rerender_module: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "curation_raw-test.yaml"
        path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["rerender", "--raw", str(path)])

        with pytest.raises(SystemExit) as exc:
            rerender_module._main()

        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "Did you mean --partial" in captured.err

    def test_partial_and_raw_mutually_exclusive(
        self,
        rerender_module: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "x.json"
        path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(
            sys, "argv", ["rerender", "--partial", "--raw", str(path)]
        )

        with pytest.raises(SystemExit) as exc:
            rerender_module._main()

        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "mutually exclusive" in captured.err


class TestRawRecovery:
    """End-to-end --raw path with portfolio/render mocked."""

    def _wire_common_mocks(
        self,
        mocker: Any,
        rerender_module: Any,
    ) -> tuple[MagicMock, MagicMock]:
        portfolio = MagicMock(name="portfolio")
        load_portfolio = mocker.patch.object(
            rerender_module, "load_portfolio", return_value=portfolio
        )
        # CuratorSettings instantiation needs an API key; bypass real
        # config validation by replacing the class with a lightweight
        # stub.
        settings_stub = MagicMock(name="settings")
        settings_stub.portfolio_data_path = Path("/portfolio")
        settings_class = mocker.patch.object(
            rerender_module, "CuratorSettings", return_value=settings_stub
        )
        return load_portfolio, settings_class

    def test_raw_invalid_payload_surfaces_adapter_error(
        self,
        mocker: Any,
        rerender_module: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Adapter validation error is printed verbatim so the user can edit."""
        self._wire_common_mocks(mocker, rerender_module)
        mocker.patch.object(
            rerender_module,
            "_adapt_curation_dict",
            side_effect=APIResponseError("summary: String should have at most 750"),
        )

        raw_path = tmp_path / "curation_raw-test.json"
        raw_path.write_text(json.dumps({"summary": "x" * 800}), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["rerender", "--raw", str(raw_path)])

        with pytest.raises(SystemExit) as exc:
            rerender_module._main()
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "summary" in captured.err
        assert "750" in captured.err

    def test_raw_happy_path_invokes_render(
        self,
        mocker: Any,
        rerender_module: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Adapter success leads to render() and prints the output path."""
        self._wire_common_mocks(mocker, rerender_module)
        fake_curation = MagicMock(name="curation")
        mocker.patch.object(
            rerender_module,
            "_adapt_curation_dict",
            return_value=(fake_curation, None),
        )
        render_output = MagicMock()
        render_output.pdf_path = tmp_path / "resume.pdf"
        render_output.page_count = 2
        render_output.trim_log = []
        render_output.cover_letter_txt_path = None
        render_output.cover_letter_pdf_path = None
        render_mock = mocker.patch.object(
            rerender_module, "render", return_value=render_output
        )

        raw_path = tmp_path / "curation_raw-test.json"
        raw_path.write_text(json.dumps({"summary": "ok"}), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["rerender", "--raw", str(raw_path)])

        rerender_module._main()

        assert render_mock.called
        captured = capsys.readouterr()
        assert "raw recovery" in captured.out

    def test_raw_input_must_be_json_object(
        self,
        mocker: Any,
        rerender_module: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A JSON array (or anything non-object) is rejected with an actionable hint."""
        raw_path = tmp_path / "curation_raw-test.json"
        raw_path.write_text("[1, 2, 3]", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["rerender", "--raw", str(raw_path)])

        with pytest.raises(SystemExit) as exc:
            rerender_module._main()
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "must be a JSON object" in captured.err


class TestPartialRecovery:
    """Partial-recovery happy path. Closes the coverage gap on the
    pre-existing --partial branch noted in the design plan."""

    def test_partial_happy_path_invokes_render(
        self,
        mocker: Any,
        rerender_module: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        portfolio = MagicMock(name="portfolio")
        mocker.patch.object(
            rerender_module, "load_portfolio", return_value=portfolio
        )
        settings_stub = MagicMock(name="settings")
        settings_stub.portfolio_data_path = Path("/portfolio")
        mocker.patch.object(
            rerender_module, "CuratorSettings", return_value=settings_stub
        )

        fake_curation = MagicMock(name="curation")
        mocker.patch.object(
            rerender_module.ResumeCuration,
            "model_validate",
            return_value=fake_curation,
        )

        render_output = MagicMock()
        render_output.pdf_path = tmp_path / "resume.pdf"
        render_output.page_count = 2
        render_output.trim_log = []
        render_mock = mocker.patch.object(
            rerender_module, "render", return_value=render_output
        )

        partial_path = tmp_path / "curation_partial-test.yaml"
        partial_path.write_text("summary: hello\n", encoding="utf-8")
        monkeypatch.setattr(
            sys, "argv", ["rerender", "--partial", str(partial_path)]
        )

        rerender_module._main()

        assert render_mock.called
        # The --partial branch passes safety_net=True (explicit on-API
        # recovery semantics).
        assert render_mock.call_args.kwargs.get("safety_net") is True
        captured = capsys.readouterr()
        assert "partial recovery" in captured.out
