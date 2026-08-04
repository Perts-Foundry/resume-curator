"""Tests for the headless exception taxonomy in ``curator.exceptions``."""

from __future__ import annotations

from curator.exceptions import (
    APIError,
    CuratorError,
    HeadlessCLIError,
    HeadlessUsageLimitError,
)


class TestHeadlessCLIError:
    def test_is_api_error(self) -> None:
        err = HeadlessCLIError("claude binary not found")
        assert isinstance(err, APIError)
        assert isinstance(err, CuratorError)

    def test_message_preserved(self) -> None:
        err = HeadlessCLIError("subprocess timed out")
        assert str(err) == "subprocess timed out"


class TestHeadlessUsageLimitError:
    def test_is_api_error(self) -> None:
        err = HeadlessUsageLimitError("session limit hit")
        assert isinstance(err, APIError)
        assert isinstance(err, CuratorError)

    def test_reset_text_carried(self) -> None:
        err = HeadlessUsageLimitError("session limit hit", reset_text="3:45pm")
        assert err.reset_text == "3:45pm"
        assert str(err) == "session limit hit"

    def test_reset_text_defaults_to_none(self) -> None:
        err = HeadlessUsageLimitError("session limit hit")
        assert err.reset_text is None
