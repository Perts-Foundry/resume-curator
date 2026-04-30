"""Tests for slugify and priority_sort_key helpers in io_utils."""

from __future__ import annotations

import pytest

from curator.io_utils import priority_sort_key, slugify


class TestSlugify:
    """Deterministic slug normalization to ``ID_PATTERN``."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Acme Corp", "acme-corp"),
            ("ACME", "acme"),
            ("Acme Inc.", "acme-inc"),
            ("Café Résumé", "caf-r-sum"),
            ("A  B", "a-b"),
            ("-acme", "acme"),
            ("acme-", "acme"),
            ("--acme---corp--", "acme-corp"),
            ("1-company", "1-company"),
            ("Company#42", "company-42"),
        ],
    )
    def test_basic_slugs(self, raw: str, expected: str) -> None:
        assert slugify(raw) == expected

    @pytest.mark.parametrize("raw", ["", "!!!", "---", "   "])
    def test_fallback_applies(self, raw: str) -> None:
        assert slugify(raw) == "general"

    def test_custom_fallback(self) -> None:
        assert slugify("!!!", fallback="anon") == "anon"

    def test_truncation_at_max_length(self) -> None:
        slug = slugify("a" * 100)
        assert len(slug) == 64

    def test_truncation_strips_trailing_hyphen(self) -> None:
        # 63 'a's + one non-alnum before the cutoff would leave "-" at idx 64
        raw = "a" * 63 + "-bbbbb"
        slug = slugify(raw)
        assert slug.endswith("a")
        assert len(slug) == 63

    def test_raw_input_cap_prevents_hang(self) -> None:
        slug = slugify("x" * 10_000)
        assert len(slug) == 64

    def test_idempotent(self) -> None:
        for raw in ["Acme Corp", "A  B", "!!!", "-acme-", "long " * 50]:
            once = slugify(raw)
            twice = slugify(once)
            assert once == twice

    def test_result_matches_id_pattern(self) -> None:
        import re

        from curator.models import ID_PATTERN

        pattern = re.compile(ID_PATTERN)
        for raw in ["Acme", "A B C", "1x", "---", "xyz-"]:
            assert pattern.match(slugify(raw))


class TestPrioritySortKey:
    """Stable sort key for entries with priority/weight fields."""

    def test_unset_sorts_last(self) -> None:
        items = [
            {"id": "a", "priority": 2},
            {"id": "b", "priority": None},
            {"id": "c", "priority": 1},
        ]
        sorted_items = sorted(items, key=priority_sort_key)
        assert [i["id"] for i in sorted_items] == ["c", "a", "b"]

    def test_stable_on_ties(self) -> None:
        items = [
            {"id": "a", "priority": 1},
            {"id": "b", "priority": 1},
            {"id": "c", "priority": None},
            {"id": "d", "priority": None},
        ]
        sorted_items = sorted(items, key=priority_sort_key)
        assert [i["id"] for i in sorted_items] == ["a", "b", "c", "d"]

    def test_custom_field_name(self) -> None:
        items = [
            {"id": "a", "weight": 2},
            {"id": "b", "weight": 1},
            {"id": "c"},
        ]
        sorted_items = sorted(items, key=lambda e: priority_sort_key(e, "weight"))
        assert [i["id"] for i in sorted_items] == ["b", "a", "c"]

    def test_works_on_objects(self) -> None:
        class Obj:
            def __init__(self, name: str, priority: int | None) -> None:
                self.name = name
                self.priority = priority

        items = [Obj("a", 2), Obj("b", None), Obj("c", 1)]
        sorted_items = sorted(items, key=priority_sort_key)
        assert [o.name for o in sorted_items] == ["c", "a", "b"]
