"""Custom exception hierarchy for resume-curator."""

from __future__ import annotations


class CuratorError(Exception):
    """Base exception for all curator errors."""


class ConfigError(CuratorError):
    """Missing or invalid configuration."""


class PortfolioError(CuratorError):
    """Base exception for portfolio-related errors."""


class PortfolioNotFoundError(PortfolioError):
    """Portfolio source directory not found."""


class PortfolioValidationError(PortfolioError):
    """Portfolio data failed validation."""


class APISpendGuardError(CuratorError):
    """API spend not authorized.

    Raised when ``CURATOR_ALLOW_API_SPEND`` is not set to ``true``.
    Prevents surprise charges from automated workflows.
    """


class APIError(CuratorError):
    """Base exception for Anthropic API errors."""


class APIAuthError(APIError):
    """Authentication failure with the Anthropic API."""


class APIRateLimitError(APIError):
    """Rate limit exceeded on the Anthropic API."""


class APIResponseError(APIError):
    """Unexpected or invalid response from the Anthropic API."""


class APIRefusalError(APIError):
    """Claude refused to process the request (stop_reason: refusal).

    Inherits from APIError (not APIResponseError) so callers can
    distinguish refusals from malformed responses at the except level.
    """


class RenderError(CuratorError):
    """Typst compilation or output writing failure."""


class JobDescriptionError(CuratorError):
    """Unreadable or invalid job description input."""


class EvalError(CuratorError):
    """Evaluation framework error (metric computation, golden loading, etc.)."""


class CurationValidationError(CuratorError):
    """Curation IDs failed validation against the portfolio.

    Raised by ``validate_curation_ids`` when a ``ResumeCuration`` references
    unknown work entries, highlight IDs, skill groups, non-verbatim keywords,
    or project IDs. The API path re-wraps this as ``APIResponseError`` so
    existing API-error handling continues to work.
    """


class StaticModeError(CuratorError):
    """Static-mode synthesis failure (missing portfolio data, etc.)."""
