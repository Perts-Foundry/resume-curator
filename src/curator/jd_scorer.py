"""JD-relevance scoring for portfolio keywords.

This module provides a deterministic, dependency-light scoring function
used by the client adapter to fill skill-group keywords based on
relevance to the job description. The AI emits an ordered list of skill
group IDs (judgment: which groups matter for this JD); code fills each
group's keywords from portfolio data using this scorer (bookkeeping:
which keywords in the group are most relevant).

Why this lives in code rather than in the AI: keyword-level filtering
is a lexical matching task with a clear scoring signal. The AI's
judgment value is at the group level (semantic understanding of role
shape); within a group the question is "does this exact string appear
in the JD?" which a regex answers reliably at zero cost.

The scorer is deliberately simple: keyword-token presence in the JD
weighted by occurrence count, with portfolio order as a stable
tie-break. This is enough to surface the JD-relevant subset of each
portfolio group without introducing a new dependency or model.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[+#.][a-z0-9]+)*")


def _tokenize_jd(jd_text: str) -> list[str]:
    """Lowercase + token-split the JD.

    Keeps internal punctuation that commonly appears in tech tokens
    (e.g., ``c++``, ``c#``, ``.net``, ``node.js``) so a portfolio
    keyword like ``C++`` can be matched. Strips surrounding punctuation
    so words at sentence ends still match.
    """
    return _TOKEN_RE.findall(jd_text.lower())


def _keyword_score(keyword: str, jd_tokens: list[str], jd_lower: str) -> int:
    """Score a single portfolio keyword against the JD tokens.

    Two complementary signals: full-keyword substring presence (catches
    multi-word keywords like ``Container Registry``) and per-token
    matches (catches single-token keywords like ``Kubernetes``). Both
    are summed so longer keywords with multiple matching tokens score
    higher than incidental single-letter hits.
    """
    kw_lower = keyword.lower()
    if not kw_lower:
        return 0
    score = 0
    # Whole-keyword substring (case-insensitive). Bounded by word
    # boundaries on each side so 'go' doesn't match 'going'.
    pattern = r"(?<![a-z0-9])" + re.escape(kw_lower) + r"(?![a-z0-9])"
    score += len(re.findall(pattern, jd_lower)) * 3
    # Per-token contribution for multi-word keywords: each portfolio
    # keyword token that appears in the JD adds one. This catches
    # cases where the JD uses ``container orchestration`` and the
    # portfolio lists ``Container Registry`` (one shared token).
    kw_tokens = _TOKEN_RE.findall(kw_lower)
    if len(kw_tokens) > 1:
        jd_token_set = set(jd_tokens)
        score += sum(1 for t in kw_tokens if t in jd_token_set)
    return score


def score_keywords_for_jd(
    jd_text: str,
    keywords: list[str],
    *,
    top_n: int,
) -> list[str]:
    """Return the top_n keywords ranked by JD relevance, stable on ties.

    Args:
        jd_text: The job description text. May be empty.
        keywords: Portfolio keywords for a single skill group, in
            portfolio order. Returned subset preserves verbatim strings
            (never re-cased or trimmed).
        top_n: Maximum keywords to return. Pass 0 to return [].

    Returns:
        At most ``top_n`` keywords. Order: descending JD-relevance
        score, with portfolio order as the stable tie-break. Keywords
        with zero score are still returned (up to ``top_n``) ordered
        by portfolio position; this means a group emitted by the AI
        always renders at least its first ``top_n`` keywords even when
        the JD doesn't directly reference any of them. This is
        deliberate: the AI's group-level judgment is the authoritative
        signal that this group belongs in the resume; the scorer just
        picks the best representatives within it.
    """
    if top_n <= 0 or not keywords:
        return []
    jd_lower = jd_text.lower()
    jd_tokens = _tokenize_jd(jd_text) if jd_text else []
    # Annotate each keyword with (-score, portfolio_position, keyword)
    # so sorted() gives descending score with portfolio order as
    # tie-break.
    scored = [
        (-_keyword_score(kw, jd_tokens, jd_lower), idx, kw)
        for idx, kw in enumerate(keywords)
    ]
    scored.sort()
    return [kw for _, _, kw in scored[:top_n]]
