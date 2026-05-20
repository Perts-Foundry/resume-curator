"""Tests for curator.config."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from curator.config import CuratorSettings
from curator.exceptions import ConfigError

_TEST_KEY = SecretStr("test-key-not-real")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Remove CURATOR_ env vars and .env influence for test isolation."""
    monkeypatch.delenv("CURATOR_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CURATOR_MODEL", raising=False)
    monkeypatch.delenv("CURATOR_MAX_TOKENS", raising=False)
    monkeypatch.delenv("CURATOR_PORTFOLIO_PATH", raising=False)
    monkeypatch.delenv("CURATOR_EFFORT", raising=False)
    monkeypatch.delenv("CURATOR_CACHE_TTL", raising=False)
    monkeypatch.chdir(tmp_path)


def _settings(**overrides: Any) -> CuratorSettings:
    """Create settings with a test API key and optional overrides."""
    overrides.setdefault("anthropic_api_key", _TEST_KEY)
    return CuratorSettings(**overrides)


class TestCuratorSettings:
    def test_construction_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CURATOR_ANTHROPIC_API_KEY", "test-key-from-env")
        settings = CuratorSettings()
        assert isinstance(settings.anthropic_api_key, SecretStr)
        assert settings.require_api_key() == "test-key-from-env"

    def test_missing_api_key_defaults_to_none(self) -> None:
        settings = CuratorSettings()
        assert settings.anthropic_api_key is None

    def test_require_api_key_raises_config_error(self) -> None:
        settings = CuratorSettings()
        with pytest.raises(ConfigError, match="CURATOR_ANTHROPIC_API_KEY"):
            settings.require_api_key()

    def test_require_api_key_returns_value(self) -> None:
        settings = _settings()
        assert settings.require_api_key() == _TEST_KEY.get_secret_value()

    def test_secret_str_masking(self) -> None:
        settings = _settings(anthropic_api_key=SecretStr("super-secret-value"))
        assert "super-secret-value" not in str(settings.anthropic_api_key)
        assert settings.require_api_key() == "super-secret-value"

    def test_defaults(self) -> None:
        settings = _settings()
        # judge_model default flipped 2026-05-09 from claude-sonnet-4-6 to
        # claude-haiku-4-5 after the cross-model A/B (tolerances hold,
        # ~37% of Sonnet's per-call cost). curate `model` was likewise
        # flipped briefly that day but reverted in the same PR after the
        # v4 retest produced confident wrong-company cover letters; see
        # testing/results/haiku-eval/findings.md and the same-day design
        # log entry.
        assert settings.model == "claude-sonnet-4-6"
        assert settings.max_tokens == 4096
        assert settings.portfolio_path == Path("../professional-portfolio-source")
        assert settings.output_dir == Path("profiles")
        assert settings.max_pages == 2
        assert settings.max_trim_iterations == 150
        assert settings.api_max_retries == 5
        assert settings.effort is None
        assert settings.allow_api_spend is False
        assert settings.judge_model == "claude-haiku-4-5"
        assert settings.judge_effort is None
        # cache_ttl defaults to "1h" so multi-run application sessions
        # amortize the 2x write cost across reads.
        assert settings.cache_ttl == "1h"

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CURATOR_ANTHROPIC_API_KEY", "test-key-from-env")
        monkeypatch.setenv("CURATOR_MODEL", "claude-haiku-4-5")
        monkeypatch.setenv("CURATOR_MAX_TOKENS", "2048")
        settings = CuratorSettings()
        assert settings.model == "claude-haiku-4-5"
        assert settings.max_tokens == 2048

    def test_init_kwargs_override(self) -> None:
        settings = _settings(model="claude-opus-4-6", max_pages=2)
        assert settings.model == "claude-opus-4-6"
        assert settings.max_pages == 2

    def test_computed_portfolio_data_path(self) -> None:
        settings = _settings()
        assert settings.portfolio_data_path == Path(
            "../professional-portfolio-source/data"
        )

    def test_computed_portfolio_data_path_custom(self) -> None:
        settings = _settings(portfolio_path=Path("/custom/portfolio"))
        assert settings.portfolio_data_path == Path("/custom/portfolio/data")

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_tokens", 100),
            ("max_tokens", 99999),
            ("max_pages", 0),
            ("max_pages", 6),
            ("max_trim_iterations", 0),
            ("max_trim_iterations", 201),
            ("api_max_retries", 0),
            ("api_max_retries", 11),
            ("effort", ""),
            ("effort", "invalid"),
            ("effort", "extreme"),
        ],
    )
    def test_field_constraint_violations(self, field: str, value: object) -> None:
        with pytest.raises(ValidationError):
            _settings(**{field: value})

    @pytest.mark.parametrize("level", ["low", "medium", "high", "max"])
    def test_effort_valid_levels(self, level: str) -> None:
        settings = _settings(effort=level)
        assert settings.effort == level

    @pytest.mark.parametrize("ttl", ["5m", "1h"])
    def test_cache_ttl_valid_values(self, ttl: str) -> None:
        settings = _settings(cache_ttl=ttl)
        assert settings.cache_ttl == ttl

    @pytest.mark.parametrize("invalid", ["", "2h", "30s", "5min", "1H"])
    def test_cache_ttl_invalid_values_rejected(self, invalid: str) -> None:
        with pytest.raises(ValidationError):
            _settings(cache_ttl=invalid)

    def test_cache_ttl_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CURATOR_ANTHROPIC_API_KEY", "test-key-from-env")
        monkeypatch.setenv("CURATOR_CACHE_TTL", "5m")
        settings = CuratorSettings()
        assert settings.cache_ttl == "5m"

    def test_cache_ttl_cli_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # CLI surfaces as an init kwarg (see cli.py CuratorSettings(**overrides)),
        # which pydantic-settings ranks above env vars per its precedence
        # documentation. Env says 5m, init kwarg says 1h -> 1h wins.
        monkeypatch.setenv("CURATOR_ANTHROPIC_API_KEY", "test-key-from-env")
        monkeypatch.setenv("CURATOR_CACHE_TTL", "5m")
        settings = CuratorSettings(cache_ttl="1h")
        assert settings.cache_ttl == "1h"

    def test_effort_none_default(self) -> None:
        settings = _settings()
        assert settings.effort is None

    def test_effort_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CURATOR_ANTHROPIC_API_KEY", "test-key-from-env")
        monkeypatch.setenv("CURATOR_EFFORT", "high")
        settings = CuratorSettings()
        assert settings.effort == "high"

    def test_extra_env_vars_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CURATOR_ANTHROPIC_API_KEY", "test-key-from-env")
        monkeypatch.setenv("CURATOR_UNKNOWN_FIELD", "some-value")
        settings = CuratorSettings()
        assert settings.require_api_key() == "test-key-from-env"


class TestPageEnforcementSettings:
    """Tests for max_pages and max_trim_iterations fields."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Remove CURATOR_ env vars for test isolation."""
        monkeypatch.delenv("CURATOR_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CURATOR_MAX_PAGES", raising=False)
        monkeypatch.delenv("CURATOR_MAX_TRIM_ITERATIONS", raising=False)
        monkeypatch.chdir(tmp_path)

    @pytest.mark.parametrize("pages", [1, 2, 3, 4, 5])
    def test_max_pages_valid_range(self, pages: int) -> None:
        settings = _settings(max_pages=pages)
        assert settings.max_pages == pages

    @pytest.mark.parametrize("iters", [1, 25, 50, 100, 200])
    def test_max_trim_iterations_valid_range(self, iters: int) -> None:
        settings = _settings(max_trim_iterations=iters)
        assert settings.max_trim_iterations == iters

    def test_max_pages_init_override(self) -> None:
        settings = _settings(max_pages=2)
        assert settings.max_pages == 2

    def test_max_trim_iterations_init_override(self) -> None:
        settings = _settings(max_trim_iterations=10)
        assert settings.max_trim_iterations == 10

    def test_max_pages_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CURATOR_ANTHROPIC_API_KEY", "test-key-from-env")
        monkeypatch.setenv("CURATOR_MAX_PAGES", "2")
        settings = CuratorSettings()
        assert settings.max_pages == 2

    def test_max_trim_iterations_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CURATOR_ANTHROPIC_API_KEY", "test-key-from-env")
        monkeypatch.setenv("CURATOR_MAX_TRIM_ITERATIONS", "20")
        settings = CuratorSettings()
        assert settings.max_trim_iterations == 20


@pytest.mark.unit
class TestSectionOrder:
    def test_default_includes_all_sections(self) -> None:
        from curator.models import RENDERER_SECTIONS

        settings = _settings()
        assert settings.section_order == RENDERER_SECTIONS

    def test_reordered_accepted(self) -> None:
        order = (
            "education",
            "skills",
            "work",
            "projects",
            "certificates",
        )
        settings = _settings(section_order=order)
        assert settings.section_order == order

    def test_invalid_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid section name"):
            _settings(
                section_order=(
                    "work",
                    "skills",
                    "projects",
                    "certificates",
                    "bogus",
                ),
            )

    def test_duplicate_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate"):
            _settings(
                section_order=(
                    "work",
                    "skills",
                    "projects",
                    "certificates",
                    "work",
                ),
            )

    def test_incomplete_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must include all sections"):
            _settings(section_order=("work", "skills"))
