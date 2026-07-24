"""Paced, authenticated ``httpx`` transport for Threads GraphQL reads."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Mapping
from email.utils import parsedate_to_datetime

import httpx

from . import config, errors, gql
from .auth import SessionCredential

_AUTH_ERROR_CODES = frozenset({32, 102, 190, 401, 403, 1357001})
_RATE_LIMIT_ERROR_CODES = frozenset({4, 17, 613, 80004})
_CHALLENGE_ERROR_CODES = frozenset({368, 459})
_CHALLENGE_MARKERS = ("challenge", "checkpoint", "consent_required")
_AUTH_MARKERS = (
    "authenticate",
    "authentication",
    "csrf",
    "log in",
    "logged out",
    "login required",
    "session expired",
)
_RATE_LIMIT_MARKERS = ("rate limit", "too many request", "try again later")
_SOFT_LOCK_BODY_MARKERS = (
    "caa_login_form_data",
    "caafetaaymhpasswordentryquery",
    "login_required",
)

_AUTHENTICATION_FALSE_FIELDS = frozenset(
    {"authenticated", "isauthenticated", "isloggedin", "loggedin"}
)
# Pacing, budget reservation, and the wire call share one process-wide gate.
_REQUEST_GATE = threading.Lock()


class ReadClient:
    """A request-budgeted ``httpx.Client`` for read-only persisted queries."""

    def __init__(
        self,
        credential: SessionCredential,
        *,
        min_pause: float | None = None,
        max_requests: int | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float | httpx.Timeout = 30.0,
    ) -> None:
        if not credential.sessionid or not credential.ds_user_id or not credential.csrftoken:
            raise errors.LoginRequiredError("an authenticated Threads session is required")

        pause = config.DEFAULT_REQUEST_PAUSE if min_pause is None else min_pause
        self.min_pause = config.clamp_request_pause(float(pause))
        self.max_requests = config.DEFAULT_MAX_REQUESTS if max_requests is None else max_requests
        if self.max_requests < 0:
            raise ValueError("max_requests must be non-negative")

        self.requests_made = 0
        self.last_rate_limit_reset: int | None = None
        self._closed = False
        self._client = httpx.Client(
            cookies=credential.cookies,
            headers={
                "accept": "application/json, text/plain, */*",
                "content-type": "application/x-www-form-urlencoded",
                "origin": gql.THREADS_ORIGIN,
                "referer": f"{gql.THREADS_ORIGIN}/",
                "user-agent": credential.user_agent,
                "x-csrftoken": credential.csrftoken,
                "x-ig-app-id": gql.THREADS_WEB_APP_ID,
            },
            follow_redirects=False,
            timeout=timeout,
            transport=transport,
        )

    @property
    def remaining_requests(self) -> int:
        """Number of requests still available in this client's lifetime budget."""
        return max(0, self.max_requests - self.requests_made)

    def _throttle(self) -> None:
        """Apply the mandatory delay before every request, including the first."""
        self.min_pause = config.clamp_request_pause(float(self.min_pause))
        time.sleep(self.min_pause)

    def post(
        self,
        operation: str,
        variables: Mapping[str, object],
        *,
        doc_id: str,
    ) -> dict:
        """Execute one persisted read and return its parsed GraphQL envelope."""
        with _REQUEST_GATE:
            if self._closed:
                raise errors.SessionClosedError("the Threads read client is closed")
            if self.requests_made >= self.max_requests:
                raise errors.AgenticThreadsError(
                    f"request budget exhausted before {operation} ({self.max_requests} requests)"
                )
            if not operation or not doc_id:
                raise ValueError("operation and doc_id are required")

            body = {
                "doc_id": str(doc_id),
                "variables": json.dumps(dict(variables), ensure_ascii=False, separators=(",", ":")),
            }
            self._throttle()
            self.requests_made += 1
            try:
                response = self._client.post(
                    gql.GRAPHQL_URL,
                    data=body,
                    headers={"x-fb-friendly-name": operation},
                )
            except httpx.HTTPError as exc:
                failure_type = type(exc).__name__
            else:
                return self._handle(response, operation)
            raise errors.AgenticThreadsError(
                f"Threads request failed for {operation}: {failure_type}"
            )

    def _handle(self, response: httpx.Response, operation: str) -> dict:
        reset_at = _reset_at(response.headers)
        self.last_rate_limit_reset = reset_at

        raw_text = response.text
        if response.status_code in {400, 401, 403, 429}:
            try:
                error_body = response.json()
            except ValueError:
                error_body = None
            error_items = _meta_errors(error_body) if isinstance(error_body, Mapping) else []
            error_items.append({"message": raw_text})
            if response.status_code == 429:
                if _has_challenge_error(error_items):
                    raise errors.ChallengeError(
                        "Threads presented an account challenge; resolve it manually "
                        "and do not retry"
                    )
                raise errors.RateLimitedError(reset_at=reset_at)
            if response.status_code == 400:
                _raise_known_meta_error(error_items, reset_at=reset_at)
                if isinstance(error_body, Mapping):
                    _validate_errors_container(error_body, operation=operation)
            elif _has_challenge_error(error_items):
                raise errors.ChallengeError(
                    "Threads presented an account challenge; resolve it manually and do not retry"
                )

        if response.status_code in {401, 403}:
            raise errors.SessionExpiredError("Threads rejected the saved session")
        if response.status_code == 400:
            raise errors.PersistedOperationDriftError(
                f"{operation} returned HTTP 400; its operation identifier or request shape "
                "may have drifted"
            )
        if response.status_code != 200:
            raise errors.AgenticThreadsError(
                f"unexpected HTTP {response.status_code} for {operation}"
            )
        if not raw_text.strip():
            raise errors.SessionExpiredError("Threads returned an empty soft-lock response")

        try:
            body = response.json()
        except ValueError:
            body = None
            malformed_json = True
        else:
            malformed_json = False
        if malformed_json:
            lowered = raw_text.lower()
            if any(marker in lowered for marker in _CHALLENGE_MARKERS):
                raise errors.ChallengeError(
                    "Threads presented an account challenge; resolve it manually and do not retry"
                )
            if any(marker in lowered for marker in _SOFT_LOCK_BODY_MARKERS):
                raise errors.SessionExpiredError(
                    "Threads returned a logged-out or soft-locked response"
                )
            raise errors.EnvelopeParseError(f"{operation} returned a malformed JSON envelope")
        if body is None or body == {}:
            raise errors.SessionExpiredError(
                "Threads returned an empty response for the saved session"
            )
        if not isinstance(body, dict):
            raise errors.EnvelopeParseError(f"{operation} returned a non-object JSON envelope")

        _validate_errors_container(body, operation=operation)
        meta_errors = _meta_errors(body)
        if meta_errors:
            _raise_meta_error(meta_errors, reset_at=reset_at, operation=operation)
        if _has_explicit_authentication_downgrade(body):
            raise errors.SessionExpiredError(
                "Threads returned an explicitly logged-out or public response"
            )
        if "data" not in body:
            raise errors.EnvelopeParseError(f"{operation} response has no data envelope")
        return body

    def close(self) -> None:
        """Close the underlying connection pool; safe to call more than once."""
        with _REQUEST_GATE:
            if not self._closed:
                self._client.close()
                self._closed = True

    def __enter__(self) -> ReadClient:
        with _REQUEST_GATE:
            if self._closed:
                raise errors.SessionClosedError("the Threads read client is closed")
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _reset_at(headers: Mapping[str, str]) -> int | None:
    for name in ("x-rate-limit-reset", "x-ratelimit-reset"):
        value = headers.get(name)
        if value:
            try:
                reset_at = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(reset_at):
                try:
                    return int(reset_at)
                except (OverflowError, ValueError):
                    continue

    retry_after = headers.get("retry-after")
    if not retry_after:
        return None
    try:
        retry_seconds = float(retry_after)
    except (TypeError, ValueError, OverflowError):
        try:
            reset_at = parsedate_to_datetime(retry_after).timestamp()
            if not math.isfinite(reset_at):
                return None
            return math.ceil(reset_at)
        except (OSError, TypeError, ValueError, OverflowError):
            return None

    if not math.isfinite(retry_seconds):
        return None
    reset_at = time.time() + max(0.0, retry_seconds)
    if not math.isfinite(reset_at):
        return None
    try:
        return math.ceil(reset_at)
    except (OverflowError, ValueError):
        return None


def _has_explicit_authentication_downgrade(body: Mapping[str, object]) -> bool:
    data = body.get("data")
    if isinstance(data, Mapping) and "viewer" in data and data["viewer"] is None:
        return True

    pending: list[object] = [body]
    while pending:
        item = pending.pop()
        if isinstance(item, Mapping):
            for key, value in item.items():
                normalized_key = (
                    "".join(character for character in key.casefold() if character.isalnum())
                    if isinstance(key, str)
                    else ""
                )
                if value is False and normalized_key in _AUTHENTICATION_FALSE_FIELDS:
                    return True
                pending.append(value)
        elif isinstance(item, list):
            pending.extend(item)
    return False


def _validate_errors_container(body: Mapping[str, object], *, operation: str) -> None:
    if "errors" not in body:
        return
    listed = body["errors"]
    if not isinstance(listed, list) or any(not isinstance(item, Mapping) for item in listed):
        raise errors.EnvelopeParseError(
            f"{operation} returned a malformed GraphQL errors container"
        )


def _meta_errors(body: Mapping[str, object]) -> list[Mapping[str, object]]:
    found: list[Mapping[str, object]] = []
    listed = body.get("errors")
    if isinstance(listed, list):
        found.extend(item for item in listed if isinstance(item, Mapping))

    singular = body.get("error")
    if isinstance(singular, Mapping):
        found.append(singular)
    elif singular is not None:
        found.append(
            {
                "code": singular,
                "message": body.get("errorSummary") or body.get("errorDescription") or "",
            }
        )
    return found


def _error_int(item: Mapping[str, object], *keys: str) -> int | None:
    for key in keys:
        value = item.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _error_text(item: Mapping[str, object]) -> str:
    values = (
        item.get("message"),
        item.get("description"),
        item.get("error_user_msg"),
        item.get("errorSummary"),
        item.get("type"),
    )
    return " ".join(str(value) for value in values if value).lower()


def _has_challenge_error(error_items: list[Mapping[str, object]]) -> bool:
    codes = {_error_int(item, "code", "error_code") for item in error_items}
    subcodes = {_error_int(item, "error_subcode", "subcode") for item in error_items}
    text = " ".join(
        str(value)
        for item in error_items
        for value in (_error_text(item), item.get("code"), item.get("error_code"))
        if value
    ).lower()
    return bool(
        codes & _CHALLENGE_ERROR_CODES
        or subcodes & _CHALLENGE_ERROR_CODES
        or any(marker in text for marker in _CHALLENGE_MARKERS)
    )


def _raise_known_meta_error(
    error_items: list[Mapping[str, object]],
    *,
    reset_at: int | None,
) -> None:
    if _has_challenge_error(error_items):
        raise errors.ChallengeError(
            "Threads presented an account challenge; resolve it manually and do not retry"
        )

    codes = {_error_int(item, "code", "error_code") for item in error_items}
    subcodes = {_error_int(item, "error_subcode", "subcode") for item in error_items}
    text = " ".join(_error_text(item) for item in error_items)
    if (
        codes & _AUTH_ERROR_CODES
        or subcodes & _AUTH_ERROR_CODES
        or any(marker in text for marker in _AUTH_MARKERS)
        or any(marker in text for marker in _SOFT_LOCK_BODY_MARKERS)
    ):
        raise errors.SessionExpiredError("Threads rejected or soft-locked the saved session")
    if codes & _RATE_LIMIT_ERROR_CODES or any(marker in text for marker in _RATE_LIMIT_MARKERS):
        raise errors.RateLimitedError(reset_at=reset_at)


def _raise_meta_error(
    error_items: list[Mapping[str, object]],
    *,
    reset_at: int | None,
    operation: str,
) -> None:
    _raise_known_meta_error(error_items, reset_at=reset_at)
    raise errors.EnvelopeParseError(f"{operation} returned a GraphQL error envelope")
