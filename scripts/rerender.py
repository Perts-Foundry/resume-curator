"""Re-render an existing curated.yaml through the renderer without an API call.

Usage:
    uv run python scripts/rerender.py profiles/<slug>/curated.yaml
    uv run python scripts/rerender.py --partial profiles/curation_partial-*.yaml

Rebuilds a ``CurationResult`` from the saved YAML, reloads the portfolio,
and invokes ``renderer.render()`` so template/cascade changes take effect.

When ``--partial`` is passed, the input is treated as a side file produced
by the client when a cover letter validation failed on an otherwise
successful resume curation. The resume is rebuilt and re-rendered without
a cover letter; this is the recovery path that avoids a second paid call.

When the curated.yaml directory also contains ``data/cover_letter.yaml``,
the cover letter is loaded and re-rendered alongside the resume. Useful
for iterating on the cover-letter Typst template.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from curator.client import CurationResult
from curator.config import CuratorSettings
from curator.loader import load_portfolio
from curator.models import CoverLetterCuration, ResumeCuration
from curator.renderer import render


def _load_cover_letter(profile_dir: Path) -> CoverLetterCuration | None:
    path = profile_dir / "data" / "cover_letter.yaml"
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # Drop renderer-computed fields that are not part of the Pydantic schema.
    data.pop("word_count", None)
    data.pop("rendered_date", None)
    return CoverLetterCuration.model_validate(data)


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "curated_path",
        type=Path,
        help="Path to curated.yaml (standard) or curation_partial YAML "
        "(with --partial).",
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        help=(
            "Treat the input as a curation_partial-*.yaml side file from a "
            "failed cover-letter validation. Renders only the resume."
        ),
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=2,
        choices=[1, 2, 3, 4, 5],
        help=(
            "Target page count (1..5). Default 2 matches the global "
            "CuratorSettings default; pass --pages 1 to re-render at "
            "short-form caps. Useful for iterating on a 2-page layout "
            "from an existing 1-page curation without a paid API call."
        ),
    )
    args = parser.parse_args()

    curated_path: Path = args.curated_path
    partial: bool = args.partial
    pages: int = args.pages

    if partial:
        curated_data = yaml.safe_load(curated_path.read_text(encoding="utf-8"))
        curation = ResumeCuration.model_validate(curated_data)
        settings = CuratorSettings(max_pages=pages)
        portfolio = load_portfolio(settings.portfolio_data_path)
        result = CurationResult(
            curation=curation,
            model="rerender-partial",
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        output = render(result, portfolio, None, settings, safety_net=True)
        print(f"Re-rendered (partial recovery): {output.pdf_path}")
        print(f"Page count: {output.page_count}")
        print(f"Trim steps: {len(output.trim_log)}")
        return

    profile_dir = curated_path.parent
    curated_data = yaml.safe_load(curated_path.read_text(encoding="utf-8"))
    curation = ResumeCuration.model_validate(curated_data)

    cover_letter = _load_cover_letter(profile_dir)

    result = CurationResult(
        curation=curation,
        model="rerender",
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        cover_letter=cover_letter,
    )

    settings = CuratorSettings(max_pages=pages)
    portfolio = load_portfolio(settings.portfolio_data_path)
    jd_path = profile_dir / "job_description.txt"
    jd_text: str | None = None
    if jd_path.is_file():
        jd_text = jd_path.read_text(encoding="utf-8")

    output = render(result, portfolio, jd_text, settings)
    print(f"Re-rendered: {output.pdf_path}")
    print(f"Page count: {output.page_count}")
    print(f"Trim steps: {len(output.trim_log)}")
    if output.cover_letter_txt_path is not None:
        print(f"Cover letter paste-ready: {output.cover_letter_txt_path}")
    if output.cover_letter_pdf_path is not None:
        print(f"Cover letter PDF: {output.cover_letter_pdf_path}")


if __name__ == "__main__":
    try:
        _main()
    except (OSError, yaml.YAMLError) as exc:
        sys.stderr.write(f"rerender failed: {exc}\n")
        sys.exit(1)
