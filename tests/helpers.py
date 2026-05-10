"""Shared test utilities for resume-curator."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from curator.eval.report import EvalMetricResult
    from curator.models import CoverLetterCuration


def valid_cover_letter_kwargs() -> dict[str, Any]:
    """Return kwargs for a word-count-compliant, forbidden-free cover letter.

    The letter passes both ``CoverLetterCuration`` structural validation
    and ``validate_cover_letter`` policy checks. Used by unit, integration,
    and e2e tests that need a valid cover letter without reinventing one.
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
        "every week. I coordinated 18 service owners through weekly "
        "standups and a live dashboard that tracked migration status per "
        "region in real time, and the developer-hour savings landed at "
        "roughly 300 hours per quarter organization-wide across the "
        "engineering team and platform group."
    )
    body_two = (
        "Before Acme, at Contoso, I introduced an internal observability "
        "toolkit that was adopted by 12 engineering teams within a single "
        "calendar year of the initial launch. The work required partner "
        "interviews with every team lead, a prototype written in three "
        "weeks, then iteration through two rewrites guided by usage "
        "telemetry from production services and staging environments. "
        "The project taught me how to pair operational rigor with "
        "developer ergonomics, a balance I would bring to Beta Corp on "
        "day one of the role."
    )
    closing = (
        "I would welcome a conversation about how my background in platform "
        "engineering and reliability aligns with the problems Beta Corp is "
        "solving this year across production traffic and deployment safety. "
        "Thank you for your time and for your thoughtful consideration."
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
