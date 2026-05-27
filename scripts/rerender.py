"""Re-render an existing curated.yaml through the renderer without an API call.

Usage:
    uv run python scripts/rerender.py profiles/<slug>/curated.yaml
    uv run python scripts/rerender.py --partial profiles/curation_partial-*.yaml
    uv run python scripts/rerender.py --raw profiles/curation_raw-*.json [--jd <path>]

Rebuilds a ``CurationResult`` from the saved YAML, reloads the portfolio,
and invokes ``renderer.render()`` so template/cascade changes take effect.

When ``--partial`` is passed, the input is treated as a side file produced
by the client when a cover letter validation failed on an otherwise
successful resume curation. The resume is rebuilt and re-rendered without
a cover letter; this is the recovery path that avoids a second paid call.

When ``--raw`` is passed, the input is treated as a side file produced by
the client when post-extract validation (Pydantic constraints in the
adapter OR resume ID-mismatch) failed. The raw JSON is fed back through
``_adapt_curation_dict`` so the user gets the original validation error
verbatim (which names the offending field). After hand-editing the JSON
to fix the field, a re-run renders the resume. The adapter needs portfolio
+ JD text for skill-keyword filling, so ``--raw`` accepts ``--jd <path>``
(or the JD is read from the input directory's ``job_description.txt``
sibling when not provided). If the input's profile dir contains a
``data/cover_letter.yaml`` sibling, the cover letter is loaded; otherwise
the rerender produces resume-only output.

When the curated.yaml directory also contains ``data/cover_letter.yaml``,
the cover letter is loaded and re-rendered alongside the resume. Useful
for iterating on the cover-letter Typst template.

Both ``--raw`` and ``--partial`` write rendered output into
``<output_dir>/<slug>/`` (default ``profiles/<slug>/``) per the
``CuratorSettings.output_dir`` resolution, NOT next to the input side
file; the renderer always controls its own profile-directory layout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from curator.client import CurationResult, _adapt_curation_dict
from curator.config import CuratorSettings
from curator.exceptions import APIResponseError
from curator.io_utils import MAX_TEXT_SIZE
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
        help="Path to curated.yaml (standard), curation_partial YAML "
        "(with --partial), or curation_raw JSON (with --raw).",
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
        "--raw",
        action="store_true",
        help=(
            "Treat the input as a curation_raw-*.json side file from a "
            "post-extract validation failure (Pydantic or ID-mismatch). "
            "Re-runs the adapter so the validation error points at the "
            "offending field; hand-edit the JSON and re-run to render."
        ),
    )
    parser.add_argument(
        "--jd",
        type=Path,
        default=None,
        help=(
            "Path to the job description text file. Used with --raw to "
            "fill skill keywords via JD relevance scoring. Falls back to "
            "<input-dir>/job_description.txt when omitted."
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
    raw: bool = args.raw
    pages: int = args.pages

    if partial and raw:
        sys.stderr.write("rerender: --partial and --raw are mutually exclusive\n")
        sys.exit(2)

    # Sanity-check the extension so a swapped flag prints a hint instead
    # of a confusing YAML/JSON parse error.
    if partial and curated_path.suffix.lower() == ".json":
        sys.stderr.write(
            "rerender: --partial expects a .yaml side file; got .json. "
            "Did you mean --raw?\n"
        )
        sys.exit(2)
    if raw and curated_path.suffix.lower() in {".yaml", ".yml"}:
        sys.stderr.write(
            "rerender: --raw expects a .json side file; got "
            f"{curated_path.suffix}. Did you mean --partial?\n"
        )
        sys.exit(2)

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

    if raw:
        # Match the io_utils size cap so an outsized JSON side file
        # (typically corruption or accidental dump of a transcript)
        # fails fast with a clear error instead of OOMing the process.
        raw_size = curated_path.stat().st_size
        if raw_size > MAX_TEXT_SIZE:
            sys.stderr.write(
                f"rerender: --raw input exceeds size limit "
                f"({raw_size} > {MAX_TEXT_SIZE} bytes): {curated_path.name}\n"
            )
            sys.exit(1)
        raw_data = json.loads(curated_path.read_text(encoding="utf-8"))
        if not isinstance(raw_data, dict):
            sys.stderr.write(
                f"rerender: --raw input must be a JSON object, got "
                f"{type(raw_data).__name__}\n"
            )
            sys.exit(1)
        settings = CuratorSettings(max_pages=pages)
        portfolio = load_portfolio(settings.portfolio_data_path)

        # Resolve JD text: --jd takes precedence, else look beside the
        # input file. The adapter requires JD text for keyword scoring;
        # empty string is acceptable (yields no JD-scored ordering) but
        # we surface the omission so the operator knows the rerender's
        # skill ordering differs from the original paid call.
        jd_path: Path | None = args.jd
        if jd_path is None:
            sibling = curated_path.parent / "job_description.txt"
            if sibling.is_file():
                jd_path = sibling
        jd_text = ""
        if jd_path is not None and jd_path.is_file():
            jd_text = jd_path.read_text(encoding="utf-8")
        else:
            sys.stderr.write(
                "rerender: --raw running without JD text; skill keyword "
                "ordering will use portfolio order rather than JD "
                "relevance. Pass --jd <path> to match the original "
                "paid-call ranking.\n"
            )

        with_cover_letter = "cover_letter" in raw_data
        try:
            curation, cover_letter = _adapt_curation_dict(
                raw_data,
                portfolio,
                with_cover_letter=with_cover_letter,
                request_id="rerender-raw",
                max_pages=pages,
                jd_text=jd_text,
            )
        except APIResponseError as exc:
            sys.stderr.write(
                f"rerender: --raw input still fails post-extract "
                f"validation. Edit the field named in the error below "
                f"and re-run.\n  {exc}\n"
            )
            sys.exit(1)

        result = CurationResult(
            curation=curation,
            model="rerender-raw",
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            cover_letter=cover_letter,
        )
        # Reuse the API path's default safety_net=True so any work
        # entries omitted from the curation get portfolio-order
        # highlights appended (matching original-call rendering).
        # render() handles cover-letter rendering when present.
        output = render(
            result, portfolio, jd_text or None, settings, safety_net=True
        )
        print(f"Re-rendered (raw recovery): {output.pdf_path}")
        print(f"Page count: {output.page_count}")
        print(f"Trim steps: {len(output.trim_log)}")
        if output.cover_letter_txt_path is not None:
            print(f"Cover letter paste-ready: {output.cover_letter_txt_path}")
        if output.cover_letter_pdf_path is not None:
            print(f"Cover letter PDF: {output.cover_letter_pdf_path}")
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
    except (OSError, yaml.YAMLError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"rerender failed: {exc}\n")
        sys.exit(1)
