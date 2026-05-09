"""Tests for ``curator.eval.from_profile_dir`` max_pages inference.

The fallback chain is: rendered PDF page count > curation_log.json
``max_pages`` > default 1. Validates the priority ordering and the
input-validation guards on the log read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from curator.eval import from_profile_dir
from tests.helpers import make_curation_dict


def _write_minimal_profile(
    profile_dir: Path,
    *,
    log_data: dict[str, Any] | None = None,
    pdf_bytes: bytes | None = None,
) -> Path:
    """Create the minimum on-disk shape ``from_profile_dir`` requires.

    - curated.yaml with a valid ``ResumeCuration``
    - data/ with empty per-section yamls
    - curation_log.json with whatever ``log_data`` is passed (or a
      minimal valid log if None)
    - resume.pdf with ``pdf_bytes`` when provided (caller passes a
      pre-rendered PDF; tests that exercise PDF inference share a
      single fixture pdf to avoid Typst dependency).
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    data_dir = profile_dir / "data"
    data_dir.mkdir(exist_ok=True)

    (profile_dir / "curated.yaml").write_text(
        yaml.safe_dump(make_curation_dict()), encoding="utf-8"
    )

    minimal_log = {"format_version": "2.3", "source": "api"}
    log_payload = log_data if log_data is not None else minimal_log
    (profile_dir / "curation_log.json").write_text(
        json.dumps(log_payload), encoding="utf-8"
    )

    # Empty section data so from_profile_dir loads cleanly.
    for section in ("work", "skills", "projects", "education", "certificates"):
        (data_dir / f"{section}.yaml").write_text(yaml.safe_dump([]))
    (data_dir / "interests.yaml").write_text(yaml.safe_dump({"hobbies": []}))
    (data_dir / "basics.yaml").write_text(
        yaml.safe_dump({"name": "Test", "email": "t@example.com"})
    )

    if pdf_bytes is not None:
        (profile_dir / "resume.pdf").write_bytes(pdf_bytes)

    return profile_dir


@pytest.fixture
def real_pdf_bytes() -> bytes:
    """Borrow a real PDF from the existing eval-pdf fixture set if any.

    Falls back to a minimal valid 1-page PDF stub generated with pypdf
    so tests can run without a real Typst compile.
    """
    # Borrow the smallest existing test PDF if present, else build one.
    candidates = list(Path("tests").rglob("*.pdf"))
    if candidates:
        return candidates[0].read_bytes()

    # Minimal one-page PDF via pypdf — sufficient for get_page_count.
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestFromProfileDirMaxPagesInference:
    """Priority chain: PDF reality > log intent > default 1."""

    def test_pdf_present_wins_over_log(
        self, tmp_path: Path, real_pdf_bytes: bytes
    ) -> None:
        # Log claims 3, PDF is 1 page → PDF wins.
        profile = _write_minimal_profile(
            tmp_path / "p",
            log_data={
                "format_version": "2.3",
                "source": "api",
                "max_pages": 3,
            },
            pdf_bytes=real_pdf_bytes,
        )
        ctx = from_profile_dir(profile)
        assert ctx.max_pages == 1  # actual PDF page count

    def test_log_used_when_pdf_absent(self, tmp_path: Path) -> None:
        profile = _write_minimal_profile(
            tmp_path / "p",
            log_data={
                "format_version": "2.3",
                "source": "static",
                "max_pages": 2,
            },
        )
        ctx = from_profile_dir(profile)
        assert ctx.max_pages == 2

    def test_default_when_neither_present(self, tmp_path: Path) -> None:
        # Pre-2.3 profile shape: log without max_pages, no PDF.
        profile = _write_minimal_profile(
            tmp_path / "p",
            log_data={"format_version": "2.2", "source": "api"},
        )
        ctx = from_profile_dir(profile)
        assert ctx.max_pages == 1

    @pytest.mark.parametrize(
        "bad_value",
        [
            "two",  # string
            -1,  # negative
            0,  # below range
            6,  # above range
            True,  # bool (subclass of int)
            [2],  # list
            {"max_pages": 2},  # dict
        ],
    )
    def test_malformed_log_max_pages_falls_through(
        self, tmp_path: Path, bad_value: Any
    ) -> None:
        profile = _write_minimal_profile(
            tmp_path / "p",
            log_data={
                "format_version": "2.3",
                "source": "api",
                "max_pages": bad_value,
            },
        )
        ctx = from_profile_dir(profile)
        # Falls through to default 1 cleanly (no exception).
        assert ctx.max_pages == 1
