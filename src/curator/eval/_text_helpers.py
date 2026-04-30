"""Shared text extraction helpers for eval metric modules."""

from __future__ import annotations

from typing import Any


def collect_highlight_texts(section_data: dict[str, Any]) -> list[str]:
    """Collect all highlight text strings from work entries."""
    texts: list[str] = []
    for entry in section_data.get("work", []):
        if not isinstance(entry, dict):
            continue
        texts.extend(
            str(h["text"])
            for h in entry.get("highlights", [])
            if isinstance(h, dict) and h.get("text")
        )
    return texts
