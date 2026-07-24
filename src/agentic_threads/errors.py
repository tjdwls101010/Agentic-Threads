"""Typed failures and their command-line exit semantics."""

from __future__ import annotations


class AgenticThreadsError(Exception):
    """Base class for every expected Agentic Threads failure."""

    exit_code = 1


class LoginRequiredError(AgenticThreadsError):
    """No persisted authenticated session exists for the selected profile."""

    exit_code = 2


class SessionExpiredError(AgenticThreadsError):
    """Threads rejected or soft-locked a previously valid session."""

    exit_code = 2


class RateLimitedError(AgenticThreadsError):
    """Threads rate-limited a request, optionally until ``reset_at``."""

    exit_code = 3

    def __init__(
        self,
        message: str = "Threads rate limit reached",
        reset_at: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class EnvelopeParseError(AgenticThreadsError):
    """A GraphQL response no longer matches its anchored envelope shape."""

    exit_code = 4


class PersistedOperationDriftError(EnvelopeParseError):
    """An authenticated HTTP 400 indicates persisted-operation or request-shape drift."""


class ProfileUnavailableError(AgenticThreadsError):
    """The target profile is private, suspended, deleted, or otherwise unavailable."""

    exit_code = 5


class NotFoundError(AgenticThreadsError):
    """The target post does not exist or is unavailable."""

    exit_code = 5


class InvalidIdentifierError(AgenticThreadsError, ValueError):
    """A username, numeric id, shortcode, or Threads URL failed validation."""


class InvalidCookieError(AgenticThreadsError, ValueError):
    """Imported cookies do not contain a valid Threads session."""


class BrowserSetupError(AgenticThreadsError):
    """The optional browser dependency or isolated browser install is unavailable."""


class ChallengeError(AgenticThreadsError):
    """Meta presented an account checkpoint that must never be retried automatically."""

    exit_code = 2


class SessionClosedError(AgenticThreadsError):
    """A read or iterator was advanced after its client context closed."""
