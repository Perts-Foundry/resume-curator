"""Shared test utilities for resume-curator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from curator.eval.report import EvalMetricResult
    from curator.models import CoverLetterCuration

# ---------------------------------------------------------------------------
# Headless Claude Code CLI mock
# ---------------------------------------------------------------------------
# Canonical fake for the ``claude -p --output-format json`` envelope and the
# ``subprocess.run`` boundary. Lives here (not in a test module) because it is
# the shared mock of an external contract used by both tests/unit/test_headless
# and tests/unit/test_eval_judge; cross-module private imports break the suite
# convention.

#: Version string the fake reports for the ``claude --version`` probe that
#: ``headless._cli_version`` runs to annotate envelope-failure messages.
FAKE_CLI_VERSION = "2.1.226 (Claude Code)"

DEFAULT_HEADLESS_USAGE: dict[str, int] = {
    "input_tokens": 1200,
    "output_tokens": 640,
    "cache_creation_input_tokens": 900,
    "cache_read_input_tokens": 300,
}

#: A served-model key for the envelope's ``modelUsage`` map.
FAKE_SERVED_MODEL = "claude-opus-5-20260115"


def make_headless_envelope(
    structured_output: dict[str, Any] | None,
    *,
    subtype: str = "success",
    is_error: bool = False,
    result_text: str = "",
    usage: dict[str, int] | None = None,
    model_usage: dict[str, Any] | None = None,
    total_cost_usd: float | None = 1.23,
    session_id: str | None = "sess-test-abc",
) -> dict[str, Any]:
    """Build a ``claude -p --output-format json`` result envelope."""
    envelope: dict[str, Any] = {
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "result": result_text,
        "usage": dict(DEFAULT_HEADLESS_USAGE) if usage is None else usage,
        "modelUsage": {FAKE_SERVED_MODEL: {}} if model_usage is None else model_usage,
        "total_cost_usd": total_cost_usd,
        "session_id": session_id,
    }
    if structured_output is not None:
        envelope["structured_output"] = structured_output
    return envelope


class FakeClaudeRun:
    """Fake ``subprocess.run`` recording ``(cmd, kwargs)`` per model call.

    Also snapshots the ``--system-prompt-file`` content while the call is in
    flight, since the temp dir is gone by the time the test asserts.

    The ``claude --version`` probe that ``headless._cli_version`` runs to
    enrich envelope-failure messages is answered transparently with
    :data:`FAKE_CLI_VERSION` and is NOT recorded in ``calls``, so ``calls``
    stays a record of model invocations only and single-subprocess-per-curate
    invariants keep their meaning.
    """

    def __init__(
        self,
        envelope: dict[str, Any] | str,
        *,
        returncode: int = 0,
        stderr: str = "",
    ) -> None:
        self.envelope = envelope
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.system_prompt_contents: list[str] = []

    @staticmethod
    def _completed(*, returncode: int, stdout: str, stderr: str) -> Any:
        return type(
            "CompletedProcess",
            (),
            {"returncode": returncode, "stdout": stdout, "stderr": stderr},
        )()

    def __call__(self, cmd: list[str], **kwargs: Any) -> Any:
        if cmd[:2] == ["claude", "--version"]:
            return self._completed(returncode=0, stdout=FAKE_CLI_VERSION, stderr="")
        self.calls.append((cmd, kwargs))
        if "--system-prompt-file" in cmd:
            path = Path(cmd[cmd.index("--system-prompt-file") + 1])
            self.system_prompt_contents.append(path.read_text(encoding="utf-8"))
        stdout = (
            self.envelope
            if isinstance(self.envelope, str)
            else json.dumps(self.envelope)
        )
        return self._completed(
            returncode=self.returncode, stdout=stdout, stderr=self.stderr
        )


def valid_cover_letter_kwargs() -> dict[str, Any]:
    """Return kwargs for a word-count-compliant, forbidden-free cover letter.

    The letter passes both ``CoverLetterCuration`` structural validation
    and ``validate_cover_letter`` policy checks. Used by unit, integration,
    and e2e tests that need a valid cover letter without reinventing one.

    Tuned to ~340 words total so the fixture lands close to the
    ``COVER_LETTER_WORD_MAX = 360`` soft cap. The
    ``HIGH_WATER_MARK_FLOOR`` assertion in
    ``tests/integration/test_render_pipeline.py`` pins this band so a
    future cap bump notices when the fixture drifts away from "near
    the cap" and stops exercising the cover-letter cascade meaningfully.
    """
    opening = (
        "When I read the recent Beta Corp engineering blog post about their "
        "incident-review practices I recognized many of the same failure "
        "modes we addressed rebuilding the deployment pipeline at Acme over "
        "the past 18 months. My background in platform engineering, "
        "combined with eight years leading cross-team infrastructure "
        "rollouts at two public companies, positions me well for the Staff "
        "Reliability Engineer role you posted. This letter focuses on two "
        "experiences drawn directly from my portfolio that map closely to "
        "the specific requirements listed in the job description."
    )
    body_one = (
        "At Acme I designed and rolled out a multi-region Kubernetes "
        "platform that cut deployment latency from 45 minutes to 6 "
        "minutes across 120 services, with zero customer-visible outages "
        "across the nine-month rollout and consistent performance metrics "
        "every week through release windows. I coordinated 18 service "
        "owners through weekly standups and a live dashboard that tracked "
        "migration status per region in real time, and the developer-hour "
        "savings landed at roughly 300 hours per quarter "
        "organization-wide across the engineering team and platform group."
    )
    body_two = (
        "Before Acme, at Contoso, I introduced an internal observability "
        "toolkit that was adopted by 12 engineering teams within a single "
        "calendar year of the initial launch event in production. The "
        "work required partner interviews with every team lead, a "
        "prototype written in three weeks, then iteration through two "
        "rewrites guided by usage telemetry from production services and "
        "staging environments across regions. The project taught me how "
        "to pair operational rigor with developer ergonomics, a balance "
        "I would bring to Beta Corp on day one of the role."
    )
    closing = (
        "I would welcome a conversation about how my background in platform "
        "engineering and reliability aligns with the problems Beta Corp is "
        "solving this year across production traffic and deployment safety. "
        "I am especially interested in the team's recent work on regional "
        "failover strategy and the deployment-safety initiatives outlined "
        "in the recent engineering blog post. Thank you for your time, "
        "for considering my background, and for your thoughtful "
        "consideration of how my experience could contribute to the team."
    )
    return {
        "salutation": "Dear Hiring Manager,",
        "opening": opening,
        "body_paragraphs": [body_one, body_two],
        "closing": closing,
        "sign_off": "Sincerely",
    }


def valid_cover_letter() -> CoverLetterCuration:
    """Return a word-count-compliant ``CoverLetterCuration`` instance."""
    from curator.models import CoverLetterCuration

    return CoverLetterCuration(**valid_cover_letter_kwargs())


def body_paragraph_embedding(trigger: str) -> str:
    """Return an in-band body paragraph that embeds ``trigger`` at the end.

    Used by tests that need body_paragraphs[0] to contain a specific
    forbidden word, phrase, placeholder, or case fixture while staying
    inside the validator's per-paragraph word band. Asserts the result
    falls inside ``[COVER_LETTER_PARAGRAPH_WORD_MIN,
    COVER_LETTER_PARAGRAPH_WORD_MAX]`` so a future band tightening
    produces a clean test-time assertion rather than an opaque validator
    error far from the call site.
    """
    import re as _re

    from curator.rules import (
        COVER_LETTER_PARAGRAPH_WORD_MAX,
        COVER_LETTER_PARAGRAPH_WORD_MIN,
    )

    result = (
        "At Acme I led the migration of a 120-service Kubernetes fleet "
        "across three cloud regions, cutting deployment latency from 45 "
        "minutes to 6 minutes and eliminating weekly rollback incidents "
        "over the course of a nine-month rollout that touched every team. "
        "The effort required coordinating 18 service owners through live "
        "dashboards, weekly operational standups, and a shared migration "
        "checklist that made status visible to every stakeholder in the "
        "organization at all times. "
    ) + trigger
    word_count = len(_re.findall(r"\b\w+\b", result))
    assert (
        COVER_LETTER_PARAGRAPH_WORD_MIN <= word_count <= COVER_LETTER_PARAGRAPH_WORD_MAX
    ), (
        f"body_paragraph_embedding produced {word_count} words, outside "
        f"[{COVER_LETTER_PARAGRAPH_WORD_MIN}, "
        f"{COVER_LETTER_PARAGRAPH_WORD_MAX}]. Adjust the base text or "
        f"trigger to keep the paragraph in band."
    )
    return result


def make_curation_dict(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid ResumeCuration dict with overrides."""
    base: dict[str, Any] = {
        "summary": (
            "A seasoned platform engineer and founder of Perts Foundry LLC "
            "with 10 years of experience in cloud "
            "infrastructure, DevOps, and site reliability engineering. "
            "Delivered 99.9% uptime across distributed Kubernetes clusters "
            "serving 50k requests per second, reduced deployment cycle time "
            "by 70% through CI/CD pipeline automation, and drove $2M annual "
            "cost savings via infrastructure right-sizing "
            "and reserved capacity planning."
        ),
        "suggested_label": "Senior DevOps Engineer",
        "company_slug": "test-co",
        "work_highlights": [
            {
                "work_id": "acme-senior-engineer",
                "highlight_ids": [
                    "acme-deployed-k8s",
                    "acme-reduced-mttr",
                    "acme-ci-pipeline",
                ],
            },
        ],
        "skills": [
            {"skill_id": "cloud-aws", "keywords": ["EKS", "S3", "Lambda"]},
            {"skill_id": "devops", "keywords": ["Terraform", "Docker"]},
        ],
        "projects": ["infra-toolkit"],
    }
    base.update(overrides)
    return base


def curation_to_wire_dict(obj: Any) -> dict[str, Any]:
    """Convert a Pydantic curation (or wrapper) to the wire-shape dict.

    Mirrors what the model emits under the 2026-05-18 hybrid skill design:
    ``work_highlights_by_id`` keyed by parent work_id, ``skills`` as an
    ordered array of portfolio group IDs (the adapter fills keywords), and a
    free-text ``company_name`` the adapter slugifies. Optional
    ``work_highlight_weights`` / ``trim_priority`` are emitted only when
    non-empty so default fixtures keep a compact shape.

    Shared here (not in a test module) because it is the canonical builder
    of the external wire contract, used by test_client, test_headless, and
    the pipeline integration tests.
    """
    from curator.models import ResumeCuration, ResumeCurationWithCoverLetter

    if isinstance(obj, ResumeCurationWithCoverLetter):
        return {
            "resume": curation_to_wire_dict(obj.resume),
            "cover_letter": obj.cover_letter.model_dump(mode="json"),
        }
    if isinstance(obj, ResumeCuration):
        wire: dict[str, Any] = {
            "summary": obj.summary,
            "suggested_label": obj.suggested_label,
            "company_name": obj.company_slug,
            "work_highlights_by_id": {
                wh.work_id: list(wh.highlight_ids) for wh in obj.work_highlights
            },
            "skills": [sr.skill_id for sr in obj.skills],
            "projects": list(obj.projects),
        }
        if obj.work_highlight_weights:
            wire["work_highlight_weights"] = dict(obj.work_highlight_weights)
        if obj.trim_priority:
            wire["trim_priority"] = list(obj.trim_priority)
        return wire
    if isinstance(obj, dict):
        return obj
    msg = f"unsupported curation type for wire-dict conversion: {type(obj).__name__}"
    raise TypeError(msg)


def valid_wire_dict() -> dict[str, Any]:
    """Wire-shape curation dict whose IDs match the ``portfolio_data`` fixture.

    The IDs (``acme-senior-engineer`` / ``acme-deployed-k8s`` / ``cloud-aws``
    / ``my-project``) are the ones the shared ``portfolio_data`` fixture in
    tests/unit/conftest.py exposes, so the result validates through the full
    adapter + ID-validation ladder.
    """
    from curator.models import ResumeCuration

    curation = ResumeCuration.model_validate(
        make_curation_dict(
            company_slug="acme-corp",
            work_highlights=[
                {
                    "work_id": "acme-senior-engineer",
                    "highlight_ids": ["acme-deployed-k8s"],
                },
            ],
            skills=[{"skill_id": "cloud-aws", "keywords": ["EKS"]}],
            projects=["my-project"],
        )
    )
    return curation_to_wire_dict(curation)


def find_metric(results: list[EvalMetricResult], name: str) -> EvalMetricResult:
    """Find a metric result by name.

    Args:
        results: List of EvalMetricResult objects.
        name: The metric name to find.

    Returns:
        The matching metric result.

    Raises:
        AssertionError: If the metric is not found.
    """
    for r in results:
        if r.name == name:
            return r
    msg = f"Metric '{name}' not found in {[r.name for r in results]}"
    raise AssertionError(msg)
