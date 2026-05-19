"""Tests for curator.jd_scorer."""

from __future__ import annotations

from curator.jd_scorer import score_keywords_for_jd


class TestScoreKeywordsForJd:
    def test_returns_top_n_keywords(self) -> None:
        jd = "We use Kubernetes and Docker for container orchestration."
        keywords = ["Kubernetes", "Docker", "Terraform", "Helm", "ArgoCD"]
        result = score_keywords_for_jd(jd, keywords, top_n=3)
        assert len(result) == 3
        # Kubernetes and Docker should rank ahead of unmentioned tools.
        assert "Kubernetes" in result
        assert "Docker" in result

    def test_zero_top_n_returns_empty(self) -> None:
        assert score_keywords_for_jd("anything", ["a", "b"], top_n=0) == []

    def test_empty_keywords_returns_empty(self) -> None:
        assert score_keywords_for_jd("anything", [], top_n=5) == []

    def test_empty_jd_returns_portfolio_order(self) -> None:
        # With no JD signal every keyword scores zero; portfolio order
        # determines the top-N.
        result = score_keywords_for_jd("", ["alpha", "beta", "gamma"], top_n=2)
        assert result == ["alpha", "beta"]

    def test_portfolio_order_is_tie_break(self) -> None:
        # All three keywords appear once in the JD; portfolio order
        # decides which two come back when top_n=2.
        jd = "alpha and beta and gamma all matter"
        result = score_keywords_for_jd(jd, ["gamma", "beta", "alpha"], top_n=2)
        # Tie-break by portfolio order means 'gamma' (first) and
        # 'beta' (second) win.
        assert result == ["gamma", "beta"]

    def test_keyword_with_higher_jd_frequency_ranks_first(self) -> None:
        jd = "Python Python Python is used widely. Some Java too."
        result = score_keywords_for_jd(jd, ["Java", "Python"], top_n=2)
        # Python appears 3x, Java 1x; Python should rank first despite
        # being second in portfolio order.
        assert result[0] == "Python"

    def test_case_insensitive_matching(self) -> None:
        jd = "We use KUBERNETES in production."
        result = score_keywords_for_jd(jd, ["kubernetes", "Helm"], top_n=2)
        assert result[0] == "kubernetes"

    def test_multi_word_keyword_matches_full_string(self) -> None:
        jd = "Hands-on experience with Container Registry deployments."
        result = score_keywords_for_jd(
            jd, ["Container Registry", "Docker", "Helm"], top_n=3
        )
        assert result[0] == "Container Registry"

    def test_word_boundaries_prevent_substring_pollution(self) -> None:
        # 'go' should not match 'going' or 'goal'.
        jd = "We are going to reach our goals with grit."
        result = score_keywords_for_jd(jd, ["go", "grit"], top_n=2)
        # Both keywords are emitted (top_n preserves at least these
        # two), but 'grit' should outrank 'go' since 'go' has zero
        # matches (no word-boundary hit) while 'grit' has one.
        assert result[0] == "grit"

    def test_returns_verbatim_input_strings(self) -> None:
        # The returned strings preserve original casing and spacing.
        result = score_keywords_for_jd(
            "We use AWS for cloud.", ["AWS", "Azure"], top_n=1
        )
        assert result == ["AWS"]

    def test_top_n_greater_than_keyword_count_returns_all(self) -> None:
        result = score_keywords_for_jd("any text", ["only-one"], top_n=10)
        assert result == ["only-one"]

    def test_tech_punctuation_preserved_in_tokens(self) -> None:
        # Tokens like c++, c#, .net, node.js stay intact for matching.
        jd = "Strong C++ background and Node.js experience required."
        result = score_keywords_for_jd(jd, ["C++", "Node.js", "Java"], top_n=3)
        # Both punctuation-containing keywords rank ahead of Java.
        assert result[0] in ("C++", "Node.js")
        assert result[2] == "Java"
