"""Configuration management for resume-curator."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from curator import default_cover_letter_template_path, default_template_path
from curator.exceptions import ConfigError
from curator.models import RENDERER_SECTIONS


class CuratorSettings(BaseSettings):
    """Application settings loaded from environment variables and .env file.

    Settings are resolved in priority order:
    1. CLI arguments (passed as init kwargs)
    2. Environment variables (CURATOR_ prefix)
    3. .env file
    4. Field defaults
    """

    model_config = SettingsConfigDict(
        env_prefix="CURATOR_",
        env_file=".env",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
    )

    # API settings
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        description="Anthropic API key (required for API calls)",
    )
    # Default to the alias `claude-sonnet-4-6` rather than a snapshot ID:
    # at the time of writing the snapshot `claude-sonnet-4-6-20260217` did
    # not yet exist on the Anthropic API and returned 404 against a default
    # `curator curate` invocation. Forks that want reproducibility against
    # a specific model release should override `CURATOR_MODEL` with the
    # snapshot ID once one is published.
    model: str = Field(
        default="claude-sonnet-4-6",
        description=(
            "Claude model identifier. Alias by default; override CURATOR_MODEL "
            "with a snapshot ID (e.g. claude-sonnet-4-6-20260217) for "
            "reproducibility against a frozen model release."
        ),
    )
    max_tokens: int = Field(default=4096, ge=256, le=8192)
    effort: Literal["low", "medium", "high", "max"] | None = Field(
        default=None,
        description="Effort level for response quality tuning",
    )

    # Paths
    portfolio_path: Path = Field(
        default=Path("../professional-portfolio-source"),
        description="Path to portfolio source repo root",
    )
    output_dir: Path = Field(
        default=Path("profiles"),
        description="Directory for per-job output",
    )
    template_path: Path = Field(
        default_factory=lambda: default_template_path(),
        description="Path to the Typst resume template",
    )
    cover_letter_template_path: Path = Field(
        default_factory=lambda: default_cover_letter_template_path(),
        description="Path to the Typst cover letter template",
    )

    # Page count enforcement
    max_pages: int = Field(
        default=1,
        ge=1,
        le=5,
        description=(
            "Maximum PDF page count (renderer trims if exceeded). "
            "Values 1-3 are typical for curate (JD-tailored); 4-5 supports "
            "static-mode multi-page resumes (executive/academic profiles)."
        ),
    )
    max_trim_iterations: int = Field(
        default=150,
        ge=1,
        le=200,
        description=(
            "Maximum trim iterations for page-fitting (renderer-side). "
            "Default 150 covers static-mode portfolios with large cert "
            "lists, dense skill groups, and many work highlights that "
            "can all need draining before convergence (observed ~84 on "
            "the current portfolio). Ceiling 200 leaves headroom for "
            "operators to raise the cap without a code change and keeps "
            "the safety valve observable as a distinct event. The "
            "renderer logs a WARNING when the loop crosses 15 iterations "
            "so pathological cases surface."
        ),
    )

    # Retry settings (SDK handles backoff strategy internally)
    api_max_retries: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Max retry attempts for Anthropic API calls (SDK built-in retries)",
    )

    # Cost guard — must be explicitly set to allow API spending.
    allow_api_spend: bool = Field(
        default=False,
        description=(
            "Set CURATOR_ALLOW_API_SPEND=true to allow Anthropic API calls. "
            "Prevents surprise charges from automated workflows."
        ),
    )

    # Resume layout
    section_order: tuple[str, ...] = Field(
        default=RENDERER_SECTIONS,
        description="Fixed section order for resume layout (auto-hides empty sections)",
    )

    @field_validator("section_order")
    @classmethod
    def _validate_section_order(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        valid = set(RENDERER_SECTIONS)
        seen: set[str] = set()
        for name in v:
            if name not in valid:
                msg = f"Invalid section name '{name}'; valid: {sorted(valid)}"
                raise ValueError(msg)
            if name in seen:
                msg = f"Duplicate section name '{name}' in section_order"
                raise ValueError(msg)
            seen.add(name)
        missing = valid - seen
        if missing:
            msg = f"section_order must include all sections; missing: {sorted(missing)}"
            raise ValueError(msg)
        return v

    # Tier 2 LLM judge settings
    judge_model: str = Field(
        default="claude-sonnet-4-6",
        description="Claude model for Tier 2 LLM judge evaluation",
    )
    judge_effort: Literal["low", "medium", "high", "max"] | None = Field(
        default=None,
        description="Effort level for judge response quality tuning",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def portfolio_data_path(self) -> Path:
        """Path to the portfolio data directory."""
        return self.portfolio_path / "data"

    def require_api_key(self) -> str:
        """Return the API key value, raising ConfigError if not set.

        Centralizes the null check so callers get a clean error instead of
        ``AttributeError`` on ``None.get_secret_value()``.
        """
        if self.anthropic_api_key is None:
            msg = (
                "CURATOR_ANTHROPIC_API_KEY is required for API calls. "
                "Set it in your environment or .env file."
            )
            raise ConfigError(msg)
        return self.anthropic_api_key.get_secret_value()
