"""JD Alignment metrics (25% category weight; 4 scored + 2 informational).

Evaluates how well the curated resume matches the job description
through keyword coverage, distribution, and match rates. Two of the six
metrics (``jd_match_rate``, ``acronym_expansion_pairs``) are marked
``informational=True`` because they measure portfolio-JD fit (a property
of the candidate's career) rather than curation quality; they remain
visible as informational signals but do not contribute to the category
aggregate. ``PortfolioFitReport`` collects them into a sidecar report.
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any

from curator.eval.report import EvalMetricResult, EvalMetricStatus
from curator.rules import ACRONYM_EXPANSIONS

if TYPE_CHECKING:
    from curator.models import PortfolioData, ResumeCuration

_CATEGORY = "jd_alignment"

ALIGNMENT_METRIC_NAMES: tuple[str, ...] = (
    "keyword_coverage",
    "keyword_count",
    "keyword_distribution",
    "job_title_present",
    "acronym_expansion_pairs",
    "jd_match_rate",
)

# Common English stopwords for keyword extraction.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "is",
        "was",
        "are",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "can",
        "could",
        "must",
        "not",
        "no",
        "nor",
        "so",
        "if",
        "then",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "we",
        "our",
        "they",
        "their",
        "you",
        "your",
        "i",
        "me",
        "my",
        "he",
        "she",
        "him",
        "her",
        "who",
        "whom",
        "which",
        "what",
        "where",
        "when",
        "how",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "only",
        "own",
        "same",
        "than",
        "too",
        "very",
        "just",
        "about",
        "above",
        "after",
        "again",
        "also",
        "am",
        "any",
        "because",
        "before",
        "between",
        "into",
        "over",
        "under",
        "up",
        "down",
        "out",
        "off",
        "through",
        "able",
        "using",
        "used",
        "experience",
        "work",
        "working",
        "team",
        "etc",
        "including",
        "well",
        "strong",
        "knowledge",
    }
)


def _normalize_text(text: str) -> str:
    """Normalize text for keyword extraction."""
    text = html.unescape(text)
    # Smart quotes → ASCII.
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    # Em/en dashes → space.
    text = text.replace("\u2014", " ").replace("\u2013", " ")
    return text.lower()


def extract_keywords(text: str) -> set[str]:
    """Extract keywords from text using simple tokenization.

    Handles slash-separated terms (CI/CD → ci/cd, ci, cd), generates
    bigrams (2-word n-grams), and filters stopwords. 3-grams were
    removed on 2026-04-10 because they inflated the denominator for
    ``jd_match_rate`` without adding matchable signal on the portfolio
    side; see the 2026-04-10 decision log entry.
    """
    if not text or not text.strip():
        return set()

    normalized = _normalize_text(text)
    # Split on whitespace and punctuation (keep slashes for compound terms).
    tokens = re.findall(r"[a-z0-9]+(?:[/.-][a-z0-9]+)*", normalized)

    keywords: set[str] = set()
    for token in tokens:
        if token in _STOPWORDS or len(token) <= 1:
            continue
        keywords.add(token)
        # Split slash-separated terms.
        if "/" in token:
            parts = token.split("/")
            for part in parts:
                if part not in _STOPWORDS and len(part) > 1:
                    keywords.add(part)

    # Generate 2-grams only; 3-grams inflate denominator without adding signal.
    clean_tokens = [t for t in tokens if t not in _STOPWORDS and len(t) > 1]
    for i in range(len(clean_tokens) - 1):
        bigram = " ".join(clean_tokens[i : i + 2])
        keywords.add(bigram)

    return keywords


def _build_portfolio_keywords(portfolio: PortfolioData) -> set[str]:
    """Build the set of all keywords available in the full portfolio."""
    keywords: set[str] = set()
    for skill in portfolio.skills:
        for kw in skill.keywords:
            keywords.add(kw.lower())
    for entry in portfolio.work:
        for tech in entry.technologies:
            keywords.add(tech.lower())
        for h in entry.highlights:
            for tech in h.technologies:
                keywords.add(tech.lower())
    for proj in portfolio.projects:
        for tech in proj.technologies:
            keywords.add(tech.lower())
        for kw in proj.keywords:
            keywords.add(kw.lower())
    for cert in portfolio.certificates:
        for tech in cert.technologies:
            keywords.add(tech.lower())
    return keywords


def _extract_resume_keywords(
    section_data: dict[str, Any],
    basics: dict[str, Any],
) -> set[str]:
    """Extract keywords that appear in the curated resume output."""
    keywords: set[str] = set()

    # Skills keywords.
    for entry in section_data.get("skills", []):
        if isinstance(entry, dict):
            for kw in entry.get("keywords", []):
                keywords.add(str(kw).lower())

    # Technologies from work entries.
    for entry in section_data.get("work", []):
        if isinstance(entry, dict):
            for tech in entry.get("technologies", []):
                keywords.add(str(tech).lower())
            for h in entry.get("highlights", []):
                if isinstance(h, dict):
                    for tech in h.get("technologies", []):
                        keywords.add(str(tech).lower())

    # Summary and label text keywords (rough extraction).
    for field in ("summary", "label"):
        if text := basics.get(field):
            for kw in extract_keywords(str(text)):
                keywords.add(kw)

    return keywords


def evaluate_alignment(
    jd_text: str,
    section_data: dict[str, Any],
    basics: dict[str, Any],
    curation: ResumeCuration,
    portfolio: PortfolioData | None = None,
) -> list[EvalMetricResult]:
    """Evaluate JD Alignment metrics."""
    results: list[EvalMetricResult] = []

    jd_keywords = extract_keywords(jd_text)
    resume_keywords = _extract_resume_keywords(section_data, basics)

    # Build portfolio keywords once for both keyword_coverage and jd_match_rate.
    portfolio_keywords = (
        _build_portfolio_keywords(portfolio) if portfolio is not None else None
    )

    # keyword_coverage — 60-80% of matchable keywords.
    if portfolio_keywords is not None:
        matchable = jd_keywords & portfolio_keywords
        if matchable:
            covered = matchable & resume_keywords
            coverage = len(covered) / len(matchable)
            if coverage >= 0.6:
                kc_status = EvalMetricStatus.PASS
            elif coverage >= 0.4:
                kc_status = EvalMetricStatus.WARN
            else:
                kc_status = EvalMetricStatus.FAIL
            results.append(
                EvalMetricResult(
                    name="keyword_coverage",
                    category=_CATEGORY,
                    status=kc_status,
                    value=round(coverage, 2),
                    detail=(
                        f"{len(covered)}/{len(matchable)} matchable "
                        f"keywords covered ({coverage:.0%})"
                    ),
                )
            )
        else:
            results.append(
                EvalMetricResult(
                    name="keyword_coverage",
                    category=_CATEGORY,
                    status=EvalMetricStatus.WARN,
                    value=0.0,
                    detail="No matchable keywords found (JD ∩ portfolio is empty)",
                )
            )
    else:
        results.append(
            EvalMetricResult(
                name="keyword_coverage",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="Portfolio not available — cannot compute matchable keywords",
            )
        )

    # keyword_count — >=15 relevant keywords on the resume. No upper bound:
    # more JD-keyword density on the resume is a net positive; capping at 25
    # was penalizing well-mirrored resumes when the portfolio had broad JD
    # coverage. The lower bound stays to flag undermined ATS matching.
    relevant = jd_keywords & resume_keywords
    kcount = len(relevant)
    if kcount >= 15:
        kcount_status = EvalMetricStatus.PASS
    elif kcount >= 10:
        kcount_status = EvalMetricStatus.WARN
    else:
        kcount_status = EvalMetricStatus.FAIL
    results.append(
        EvalMetricResult(
            name="keyword_count",
            category=_CATEGORY,
            status=kcount_status,
            value=kcount,
            detail=f"{kcount} JD keywords on resume (target: >=15)",
        )
    )

    # keyword_distribution — top-5 JD keywords appear across >=2 sections.
    # Approximate "top" by frequency in the JD text.
    jd_lower = _normalize_text(jd_text)
    keyword_freq = {kw: jd_lower.count(kw) for kw in jd_keywords if len(kw) > 2}
    top5 = sorted(keyword_freq, key=keyword_freq.get, reverse=True)[:5]  # type: ignore[arg-type]

    distributed = 0
    for kw in top5:
        sections_with_kw = 0
        # Check summary/label.
        summary_lower = str(basics.get("summary", "")).lower()
        label_lower = str(basics.get("label", "")).lower()
        if kw in summary_lower or kw in label_lower:
            sections_with_kw += 1
        # Check each section.
        for entries in section_data.values():
            if not isinstance(entries, list):
                continue
            section_text = " ".join(
                str(v)
                for entry in entries
                if isinstance(entry, dict)
                for v in entry.values()
                if isinstance(v, str)
            ).lower()
            if kw in section_text:
                sections_with_kw += 1
                break  # Count section once per keyword.
        if sections_with_kw >= 2:
            distributed += 1

    if top5:
        dist_rate = distributed / len(top5)
        if dist_rate >= 0.6:
            dist_status = EvalMetricStatus.PASS
        elif dist_rate >= 0.4:
            dist_status = EvalMetricStatus.WARN
        else:
            dist_status = EvalMetricStatus.FAIL
    else:
        dist_rate = 0.0
        dist_status = EvalMetricStatus.WARN
    results.append(
        EvalMetricResult(
            name="keyword_distribution",
            category=_CATEGORY,
            status=dist_status,
            value=distributed,
            detail=f"{distributed}/{len(top5)} top keywords appear in >=2 sections",
        )
    )

    # job_title_present — §1.3: label matches JD title.
    label_lower = curation.suggested_label.lower()
    jd_lower_text = jd_text.lower()
    # Check if any significant part of the label appears in the JD.
    label_parts = label_lower.split()
    title_match = label_lower in jd_lower_text
    if not title_match and len(label_parts) >= 2:
        for i in range(len(label_parts) - 1):
            bigram = f"{label_parts[i]} {label_parts[i + 1]}"
            if bigram in jd_lower_text:
                title_match = True
                break
    results.append(
        EvalMetricResult(
            name="job_title_present",
            category=_CATEGORY,
            status=EvalMetricStatus.PASS if title_match else EvalMetricStatus.WARN,
            value=title_match,
            detail=f"Label '{curation.suggested_label}' "
            + ("found in JD" if title_match else "not found in JD"),
        )
    )

    # acronym_expansion_pairs — common JD acronyms appear on the resume
    # (informational). A miss usually means the PORTFOLIO lacks work
    # involving that acronym, which is a portfolio-coverage signal
    # (what the candidate has done), not a curation failure. The metric
    # stays visible so portfolio expansion opportunities are surfaced,
    # but is marked ``informational=True`` so it does not drag the
    # jd_alignment score. Portfolio-fit signals feed PortfolioFitReport.
    jd_upper = jd_text.upper()
    pair_issues: list[str] = []
    for acronym, expansion in ACRONYM_EXPANSIONS.items():
        if acronym.upper() in jd_upper:
            resume_text_all = " ".join(
                str(v)
                for entries in section_data.values()
                if isinstance(entries, list)
                for entry in entries
                if isinstance(entry, dict)
                for v in entry.values()
                if isinstance(v, str)
            )
            resume_text_all += " " + str(basics.get("summary", ""))
            resume_lower = resume_text_all.lower()
            has_acronym = acronym.lower() in resume_lower
            has_expansion = expansion.lower() in resume_lower
            if has_acronym and not has_expansion:
                pass  # Acronym without expansion is OK for common terms.
            elif not has_acronym and not has_expansion:
                pair_issues.append(f"{acronym} missing from resume")
    if not pair_issues:
        aep_status = EvalMetricStatus.PASS
    elif len(pair_issues) <= 2:
        aep_status = EvalMetricStatus.WARN
    else:
        aep_status = EvalMetricStatus.FAIL
    results.append(
        EvalMetricResult(
            name="acronym_expansion_pairs",
            category=_CATEGORY,
            status=aep_status,
            value=len(pair_issues),
            detail=(
                "; ".join(pair_issues) + " (informational, portfolio-fit signal)"
                if pair_issues
                else "All JD acronyms present"
            ),
            informational=True,
        )
    )

    # jd_match_rate — portfolio-JD coverage (informational).
    # This measures how much of the JD's keyword space the candidate's
    # PORTFOLIO covers, which is a property of the candidate's career
    # history, not of the curator. Penalizing the curator for a portfolio
    # gap (e.g., "JD wants Datadog, portfolio has CloudWatch") scores the
    # wrong object. The metric stays visible as a portfolio-fit signal
    # and is marked ``informational=True`` so it is excluded from the
    # jd_alignment category aggregate and gets rolled into
    # ``PortfolioFitReport`` instead.
    #
    # Status is uniformly PASS: empirical Phase-1 testing across 10 JDs
    # showed match rates of 0-5% on every realistic case (JDs are dense
    # and portfolios are bounded by career history). The previous
    # 15%/8% PASS/WARN bands were aspirational and produced FAIL noise
    # on every run that obscured actionable signals. The numeric value
    # remains the actionable signal; consult the PortfolioFitReport
    # sidecar for the rate. (testing-protocol 2026-04-26)
    #
    # CONSUMER CONTRACT: read ``value`` (the float coverage rate),
    # NOT ``status`` (always PASS post-2026-04-27). This is a
    # transitional shape; the metric is queued to migrate from
    # informational-status to a typed ``PortfolioFitReport.coverage_rate``
    # field in a future ``EVAL_SCHEMA_VERSION`` bump. See TODO.md
    # ``[CALIBRATE-3]``. Do not copy this "always-PASS metric" pattern
    # for new metrics without first reviewing that ticket.
    if portfolio_keywords is not None:
        if jd_keywords:
            match_count = len(jd_keywords & portfolio_keywords)
            match_rate = match_count / len(jd_keywords)
            results.append(
                EvalMetricResult(
                    name="jd_match_rate",
                    category=_CATEGORY,
                    status=EvalMetricStatus.PASS,
                    value=round(match_rate, 2),
                    detail=(
                        f"{match_count}/{len(jd_keywords)} JD keywords "
                        f"in portfolio ({match_rate:.0%}; informational, "
                        "portfolio-JD fit signal)"
                    ),
                    informational=True,
                )
            )
        else:
            results.append(
                EvalMetricResult(
                    name="jd_match_rate",
                    category=_CATEGORY,
                    status=EvalMetricStatus.PASS,
                    value=0.0,
                    detail=(
                        "No JD keywords extracted (informational, "
                        "portfolio-JD fit signal)"
                    ),
                    informational=True,
                )
            )
    else:
        results.append(
            EvalMetricResult(
                name="jd_match_rate",
                category=_CATEGORY,
                status=EvalMetricStatus.WARN,
                value=None,
                detail="Portfolio not available — cannot compute JD match rate",
                informational=True,
            )
        )

    return results
