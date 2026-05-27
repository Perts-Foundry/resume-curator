"""Tests for slugify, priority_sort_key, and atomic write helpers in io_utils."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from curator.io_utils import (
    atomic_json_write,
    atomic_yaml_write,
    priority_sort_key,
    slugify,
)


class TestSlugify:
    """Deterministic slug normalization to ``ID_PATTERN``."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Acme Corp", "acme-corp"),
            ("ACME", "acme"),
            ("Café Résumé", "caf-r-sum"),
            ("A  B", "a-b"),
            ("-acme", "acme"),
            ("acme-", "acme"),
            ("--acme---corp--", "acme-corp"),
            ("1-company", "1-company"),
            ("Company#42", "company-42"),
            # Legal-entity suffix stripping (added 2026-05-17):
            ("Acme Inc.", "acme"),
            ("Acme LLC", "acme"),
            ("Anthropic, PBC", "anthropic"),
            ("Hugging Face Inc", "hugging-face"),
            ("Foobar Inc", "foobar"),
            ("Acme GmbH", "acme"),
            ("Acme Ltd", "acme"),
            ("Acme LLC Inc", "acme"),  # iterative strip
            ("Acme, Inc.", "acme"),  # trailing-punctuation variant
            # Negative cases: only TRAILING tokens are stripped.
            ("Inc Magazine", "inc-magazine"),
            ("Incentive", "incentive"),  # mid-word "inc" preserved
            ("Acme Co", "acme-co"),  # "co" NOT in suffix set
            # "corp" intentionally NOT in suffix set (too often the
            # public-facing brand name).
            ("Acme Corp Inc", "acme-corp"),
        ],
    )
    def test_basic_slugs(self, raw: str, expected: str) -> None:
        assert slugify(raw) == expected

    def test_pure_suffix_falls_back(self) -> None:
        # "LLC" alone becomes empty after suffix strip; fallback applies.
        assert slugify("LLC") == "general"

    def test_pure_suffix_honors_custom_fallback(self) -> None:
        assert slugify("Inc", fallback="anon") == "anon"

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
        for raw in [
            "Acme Corp",
            "A  B",
            "!!!",
            "-acme-",
            "long " * 50,
            # Suffix-bearing inputs: slugify(slugify(x)) must equal
            # slugify(x) — the stripped slug has no trailing suffix
            # token so the second pass is a no-op.
            "Acme Inc",
            "Hugging Face LLC",
            "Acme LLC Inc",
        ]:
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


class TestAtomicJsonWrite:
    """Parallel to atomic_yaml_write; raw recovery files depend on it."""

    def test_round_trips_dict(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        atomic_json_write(path, {"a": 1, "b": [2, 3], "c": "hi"})
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "a": 1,
            "b": [2, 3],
            "c": "hi",
        }

    def test_trailing_newline(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        atomic_json_write(path, {"a": 1})
        assert path.read_text(encoding="utf-8").endswith("\n")

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "deeper" / "out.json"
        atomic_json_write(path, {"x": 1})
        assert path.is_file()

    def test_preserves_non_ascii(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        atomic_json_write(path, {"name": "Café"})
        # ensure_ascii=False keeps the literal character on disk so the
        # raw-recovery file is human-editable without escape soup.
        assert "Café" in path.read_text(encoding="utf-8")

    def test_crash_cleans_temp_file(self, tmp_path: Path) -> None:
        """Failure mid-write removes the temp file and surfaces the error."""
        path = tmp_path / "out.json"
        with (
            patch("curator.io_utils.os.replace", side_effect=OSError("boom")),
            pytest.raises(OSError, match="boom"),
        ):
            atomic_json_write(path, {"a": 1})
        # No final file, no leftover temp file.
        assert not path.exists()
        leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert leftovers == []

    def test_idempotent_overwrite(self, tmp_path: Path) -> None:
        """A second write replaces the first cleanly (no append)."""
        path = tmp_path / "out.json"
        atomic_json_write(path, {"a": 1})
        atomic_json_write(path, {"b": 2})
        assert json.loads(path.read_text(encoding="utf-8")) == {"b": 2}


class TestAtomicYamlWrite:
    """Sister coverage for the YAML helper to document the contract."""

    def test_round_trips_dict(self, tmp_path: Path) -> None:
        import yaml

        path = tmp_path / "out.yaml"
        atomic_yaml_write(path, {"a": 1, "b": [2, 3]})
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
            "a": 1,
            "b": [2, 3],
        }

    def test_crash_cleans_temp_file(self, tmp_path: Path) -> None:
        path = tmp_path / "out.yaml"
        with (
            patch("curator.io_utils.os.replace", side_effect=OSError("boom")),
            pytest.raises(OSError, match="boom"),
        ):
            atomic_yaml_write(path, {"a": 1})
        assert not path.exists()
        leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert leftovers == []
