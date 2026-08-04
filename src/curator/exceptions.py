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


class HeadlessCLIError(APIError):
    """Headless Claude Code subprocess failure.

    Raised when the ``claude -p`` subprocess cannot run or its output
    cannot be interpreted: missing binary, timeout, or malformed stdout.
    Inherits from ``APIError`` so existing API-error handling covers the
    headless backend without new except clauses.
    """


class HeadlessUsageLimitError(APIError):
    """Subscription usage limit reached on the headless backend.

    Carries the reset text reported by the CLI (e.g. ``3:45pm``) so
    callers can surface when usage resumes. Never auto-retried: the
    limit resets on a clock, not on backoff.
    """

    def __init__(self, message: str, *, reset_text: str | None = None) -> None:
        """Initialise with the error message and optional reset text.

        Args:
            message: Human-readable error description.
            reset_text: The reset time reported by the CLI, if parseable.
        """
        super().__init__(message)
        self.reset_text = reset_text


class RenderError(CuratorError):
    """Typst compilation or output writing failure."""


class JobDescriptionError(CuratorError):
    """Unreadable or invalid job description input."""


class JDInjectionError(JobDescriptionError):
    """Suspected prompt-injection content in the JD; run aborted pre-API.

    Raised by the CLI's ``--jd-scan`` action layer when the scan
    suspects an embedded injection and the resolved policy is to stop
    (mode ``fail``, an interactive abort, or mode ``ask`` on a
    non-interactive stdin). Always raised before any billable API call.
    """


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


class PublishError(CuratorError):
    """Publish step failure (no destination configured, unwritable, etc.)."""
