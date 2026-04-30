"""Shared fixtures for golden dataset regression tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from curator.eval.golden import discover_golden_cases

if TYPE_CHECKING:
    import pytest


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Dynamically parametrize golden regression tests."""
    if "golden_case" in metafunc.fixturenames:
        cases = discover_golden_cases()
        if cases:
            metafunc.parametrize("golden_case", cases, ids=[c.meta.id for c in cases])
