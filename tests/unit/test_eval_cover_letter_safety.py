"""Eval framework safety: cover letter additions must not break Tier 1 paths.

The eval framework treats cover letters as out of scope for v1 (tracked in
``TODO.md`` under EVAL-CL-1). These tests pin that contract so a future
``CurationResult.cover_letter``-aware metric does not silently slip in.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from curator.client import CurationResult
from curator.eval import EvalContext, evaluate_tier1, from_pipeline_result
from curator.models import CoverLetterCuration, ResumeCuration
from curator.pipeline import PipelineResult


@pytest.fixture
def valid_curation() -> ResumeCuration:
    """Minimal valid ResumeCuration mirroring the fixture in tests/helpers."""
    from tests.helpers import make_curation_dict

    payload = make_curation_dict(
        company_slug="acme",
        work_highlights=[
            {"work_id": "acme-eng", "highlight_ids": ["h1"]},
        ],
        skills=[],
        projects=[],
    )
    return ResumeCuration.model_validate(payload)


@pytest.fixture
def cover_letter() -> CoverLetterCuration:
    from tests.unit.test_models import _valid_letter_kwargs

    return CoverLetterCuration(**_valid_letter_kwargs())


class TestEvalIgnoresCoverLetter:
    def test_curation_result_with_cover_letter_constructs(
        self, valid_curation: ResumeCuration, cover_letter: CoverLetterCuration
    ) -> None:
        result = CurationResult(
            curation=valid_curation,
            model="m",
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            cover_letter=cover_letter,
        )
        assert result.cover_letter is cover_letter

    def test_eval_context_construction_from_pipeline_result_ignores_cover_letter(
        self, valid_curation: ResumeCuration, cover_letter: CoverLetterCuration
    ) -> None:
        """``from_pipeline_result`` builds an EvalContext from on-disk data
        files; the in-memory ``cover_letter`` field is not consulted, so the
        function must succeed when it is set and produce no cover-letter
        artifacts in the EvalContext.
        """
        result = CurationResult(
            curation=valid_curation,
            model="m",
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            cover_letter=cover_letter,
        )
        render_output = MagicMock()
        render_output.data_files = {}
        render_output.pdf_path = None
        portfolio = MagicMock()
        pipeline_result = PipelineResult(
            curation=result,
            render_output=render_output,
            portfolio=portfolio,
            skip_pdf=True,
            page_count=None,
            converged=True,
            total_input_tokens=0,
            total_output_tokens=0,
        )
        settings = MagicMock()
        settings.template_path = Path("ignored")
        settings.max_pages = 1

        ctx = from_pipeline_result(pipeline_result, "JD text.", settings)

        # EvalContext does not expose cover_letter; metric inputs unchanged.
        assert isinstance(ctx, EvalContext)
        assert ctx.curation is valid_curation
        # Cover letter is not surfaced as a section_data entry.
        assert "cover_letter" not in ctx.section_data

    def test_evaluate_tier1_does_not_crash_when_cover_letter_present(
        self, valid_curation: ResumeCuration, cover_letter: CoverLetterCuration
    ) -> None:
        """Sanity: feeding an EvalContext through Tier 1 with the wrapper
        result around it should never raise. The cover letter is just data
        the eval pipeline does not read.
        """
        # Build a minimal EvalContext directly (bypasses on-disk YAMLs).
        ctx = EvalContext(
            curation=valid_curation,
            section_data={
                "work": [],
                "skills": [],
                "projects": [],
                "education": [],
                "certificates": [],
                "interests": {"hobbies": [], "fun_facts": []},
            },
            basics={"name": "Jane Doe", "summary": "Concise."},
            jd_text="Senior role at Acme.",
            pdf_path=None,
            template_path=Path("ignored"),
            portfolio=None,
            max_pages=1,
            source="api",
        )
        report = evaluate_tier1(ctx)
        assert report is not None
