"""One non-mutating scrub path for diagnostic values and cookie-import errors.

Normalized output files are not redacted. Raw nodes, verbose diagnostics, and
error context must pass through this module before reaching a terminal or log.
"""

from __future__ import annotations

import re
from dataclasses import fields, is_dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_REDACTED = "[REDACTED]"
_TEXT_TRUNCATE_LEN = 40

# Stored normalized (case-folded, hyphens changed to underscores). ``redact``
# also catches token/cookie/user-id suffix families so future header spelling
# changes do not create a diagnostic leak.
_SENSITIVE_KEYS = frozenset(
    {
        "__user",
        "access_token",
        "auth",
        "authentication",
        "authorization",
        "av",
        "bearer",
        "c_user",
        "client_secret",
        "cookie",
        "cookies",
        "csrf_token",
        "csrftoken",
        "datr",
        "ds_user_id",
        "fb_dtsg",
        "id_token",
        "ig_did",
        "jazoest",
        "logged_in_user_id",
        "lsd",
        "mid",
        "password",
        "refresh_token",
        "rur",
        "sb",
        "secret",
        "session",
        "session_id",
        "sessionid",
        "token",
        "user_id",
        "userid",
        "viewer_id",
        "x_csrftoken",
        "x_fb_lsd",
        "x_ig_www_claim",
        "x_mid",
        "xs",
    }
)

_TEXT_KEYS = frozenset(
    {
        "accessibility_caption",
        "alt_text",
        "bio",
        "body",
        "biography",
        "caption",
        "description",
        "display_name",
        "display_text",
        "full_name",
        "message",
        "name",
        "query",
        "plaintext",
        "text",
        "title",
        "summary",
        "username",
    }
)


def _normalized_key(key: str) -> str:
    return key.casefold().replace("-", "_")


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = _normalized_key(key)
    if normalized in _SENSITIVE_KEYS:
        return True
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    if "token" in compact or "cookie" in compact:
        return True
    if compact.endswith(("userid", "viewerid", "actorid", "authorid", "ownerid")):
        return True
    return compact.endswith(("password", "secret"))


_RAW_ASSIGNMENT_RE = re.compile(
    r"""
    (?<![A-Za-z0-9_.-])
    (?P<key_literal>
        (?:
            (?P<key_quote>["'])
            (?P<quoted_key>[A-Za-z0-9_][A-Za-z0-9_.-]*)
            (?P=key_quote)
            |
            (?P<bare_key>[A-Za-z0-9_][A-Za-z0-9_.-]*)
        )
    )
    (?P<separator>\s*[:=]\s*)
    """,
    re.VERBOSE,
)
_BEARER_PREFIX_RE = re.compile(r"bearer\s+", re.IGNORECASE)
_BEARER_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?P<label>bearer)(?P<spacing>\s+)",
    re.IGNORECASE,
)
# Spaces remain inside unquoted values; ambiguous diagnostics redact to a structural boundary
# rather than retaining a possible secret suffix. Quotes are hard stops used to detect
# malformed values, not valid boundaries.
_UNQUOTED_VALUE_BOUNDARIES = frozenset("\r\n,;&)]}>")
_UNQUOTED_HARD_DELIMITERS = _UNQUOTED_VALUE_BOUNDARIES | frozenset("\"'")
_QUOTED_VALUE_BOUNDARIES = frozenset(",;&)]}>")
_COOKIE_VALUE_BOUNDARIES = frozenset("\r\n")


def _quoted_value_end(text: str, start: int) -> int | None:
    quote = text[start]
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == quote:
            end = index + 1
            if end == len(text) or text[end].isspace() or text[end] in _QUOTED_VALUE_BOUNDARIES:
                return end
            return None
        index += 1
    return None


def _unquoted_value_end(text: str, start: int) -> int:
    # A rejected sentinel prefix is value content; its closing bracket is not a delimiter.
    index = start + len(_REDACTED) if text.startswith(_REDACTED, start) else start
    while index < len(text) and text[index] not in _UNQUOTED_HARD_DELIMITERS:
        index += 1
    return index


def _complete_redacted_value_end(
    text: str,
    start: int,
    *,
    boundaries: frozenset[str] = _UNQUOTED_VALUE_BOUNDARIES,
) -> int | None:
    quote = text[start] if start < len(text) and text[start] in {'"', "'"} else None
    redacted_start = start + 1 if quote is not None else start
    if not text.startswith(_REDACTED, redacted_start):
        return None

    redacted_end = redacted_start + len(_REDACTED)
    if quote is not None:
        value_end = redacted_end + 1
        if redacted_end >= len(text) or text[redacted_end] != quote:
            return None
        if (
            value_end == len(text)
            or text[value_end].isspace()
            or text[value_end] in _QUOTED_VALUE_BOUNDARIES
        ):
            return value_end
        return None

    boundary = redacted_end
    while boundary < len(text) and text[boundary].isspace() and text[boundary] not in "\r\n":
        boundary += 1
    if boundary == len(text) or text[boundary] in boundaries:
        return redacted_end
    return None


def _assignment_key(match: re.Match[str]) -> str:
    return match.group("quoted_key") or match.group("bare_key")


def _render_redacted_assignment(match: re.Match[str], quote: str | None = None) -> str:
    separator = ":" if match.group("key_quote") and ":" in match.group("separator") else "="
    safe_value = _REDACTED if quote is None else f"{quote}{_REDACTED}{quote}"
    return f"{match.group('key_literal')}{separator}{safe_value}"


def _redact_sensitive_assignments(text: str) -> str:
    chunks: list[str] = []
    cursor = 0
    search_from = 0
    while match := _RAW_ASSIGNMENT_RE.search(text, search_from):
        key = _assignment_key(match)
        value_start = match.end()
        if not _is_sensitive_key(key):
            search_from = value_start
            continue
        cookie_value = _normalized_key(key) in {"cookie", "cookies"}
        bearer = _BEARER_PREFIX_RE.match(text, value_start)
        redacted_start = bearer.end() if bearer is not None else value_start
        redacted_end = _complete_redacted_value_end(
            text,
            redacted_start,
            boundaries=(_COOKIE_VALUE_BOUNDARIES if cookie_value else _UNQUOTED_VALUE_BOUNDARIES),
        )
        if redacted_end is not None:
            search_from = redacted_end
            continue

        quote: str | None = None
        malformed_quote = False
        value_end = value_start
        if value_start < len(text) and text[value_start] in {'"', "'"}:
            quote = text[value_start]
            quoted_end = _quoted_value_end(text, value_start)
            if quoted_end is None:
                malformed_quote = True
                value_end = len(text)
            else:
                value_end = quoted_end
        else:
            token_start = bearer.end() if bearer is not None else value_start
            if token_start < len(text) and text[token_start] in {'"', "'"}:
                quote = text[token_start]
                quoted_end = _quoted_value_end(text, token_start)
                if quoted_end is None:
                    malformed_quote = True
                    value_end = len(text)
                else:
                    value_end = quoted_end
            elif cookie_value:
                line_ending = re.search(r"[\r\n]", text[token_start:])
                value_end = (
                    token_start + line_ending.start() if line_ending is not None else len(text)
                )
            else:
                value_end = _unquoted_value_end(text, token_start)
                if value_end < len(text) and text[value_end] in {'"', "'"}:
                    malformed_quote = True
                    value_end = len(text)

        if value_end == value_start:
            search_from = value_start
            continue

        chunks.append(text[cursor : match.start()])
        chunks.append(_render_redacted_assignment(match, quote))
        cursor = value_end
        if malformed_quote:
            return "".join(chunks)
        search_from = value_end

    chunks.append(text[cursor:])
    return "".join(chunks)


def _redact_bearer_values(text: str) -> str:
    chunks: list[str] = []
    cursor = 0
    search_from = 0
    while match := _BEARER_VALUE_RE.search(text, search_from):
        value_start = match.end()
        redacted_end = _complete_redacted_value_end(text, value_start)
        if redacted_end is not None:
            search_from = redacted_end
            continue

        quote: str | None = None
        malformed_quote = False
        if value_start < len(text) and text[value_start] in {'"', "'"}:
            quote = text[value_start]
            quoted_end = _quoted_value_end(text, value_start)
            if quoted_end is None:
                malformed_quote = True
                value_end = len(text)
            else:
                value_end = quoted_end
        else:
            value_end = _unquoted_value_end(text, value_start)
            if value_end < len(text) and text[value_end] in {'"', "'"}:
                malformed_quote = True
                value_end = len(text)

        if value_end == value_start:
            search_from = value_start
            continue

        safe_value = _REDACTED if quote is None else f"{quote}{_REDACTED}{quote}"
        chunks.append(text[cursor : match.start()])
        chunks.append(f"{match.group('label')} {safe_value}")
        cursor = value_end
        if malformed_quote:
            return "".join(chunks)
        search_from = value_end

    chunks.append(text[cursor:])
    return "".join(chunks)


_URL_RE = re.compile(r'https?://[^\s"\'<>]+', re.IGNORECASE)
_COOKIE_PARSE_PLACEHOLDER = "<redacted cookie data>"


def is_signed_media_url(url: str) -> bool:
    """Whether ``url`` is on an actual Threads/Meta signed CDN host."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    if host is None:
        return False
    host = host.rstrip(".").casefold()
    return (
        host == "cdninstagram.com"
        or host.endswith(".cdninstagram.com")
        or (host == "fbcdn.net" or host.endswith(".fbcdn.net"))
    )


def redact_url(url: str) -> str:
    """Strip only the signing query from a recognized CDN URL."""
    if not is_signed_media_url(url):
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))


def redact_text(value: str, max_len: int = _TEXT_TRUNCATE_LEN) -> str:
    """Bound free text in diagnostics while retaining useful structural context."""
    max_len = max(0, max_len)
    if len(value) <= max_len:
        return value
    return f"{value[:max_len]}...[redacted {len(value) - max_len} more chars]"


def redact_raw_text(text: str) -> str:
    """Scrub credentials and signed CDN URLs from an unstructured diagnostic blob."""
    text = _redact_sensitive_assignments(text)
    text = _redact_bearer_values(text)
    return _URL_RE.sub(lambda match: redact_url(match.group(0)), text)


def redact_cookie_parse_error(_raw_line: str) -> str:
    """Return a fixed placeholder instead of attacker-controlled cookie bytes."""
    return _COOKIE_PARSE_PLACEHOLDER


def redact(value: Any) -> Any:
    """Recursively scrub a value without mutating caller-owned containers."""
    return _redact(value, active=set())


def _redact(value: Any, *, active: set[int]) -> Any:
    if isinstance(value, dict):
        identity = id(value)
        if identity in active:
            return "[REDACTED CYCLE]"
        active.add(identity)
        try:
            result: dict[Any, Any] = {}
            for key, item in value.items():
                if _is_sensitive_key(key):
                    result[key] = _REDACTED
                    continue
                if isinstance(item, str):
                    scrubbed = redact_raw_text(item)
                    normalized = _normalized_key(key) if isinstance(key, str) else ""
                    result[key] = redact_text(scrubbed) if normalized in _TEXT_KEYS else scrubbed
                else:
                    result[key] = _redact(item, active=active)
            return result
        finally:
            active.remove(identity)

    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            return "[REDACTED CYCLE]"
        active.add(identity)
        try:
            return [_redact(item, active=active) for item in value]
        finally:
            active.remove(identity)

    if isinstance(value, tuple):
        identity = id(value)
        if identity in active:
            return "[REDACTED CYCLE]"
        active.add(identity)
        try:
            return tuple(_redact(item, active=active) for item in value)
        finally:
            active.remove(identity)

    if is_dataclass(value) and not isinstance(value, type):
        identity = id(value)
        if identity in active:
            return "[REDACTED CYCLE]"
        active.add(identity)
        try:
            projected = {
                field_info.name: getattr(value, field_info.name) for field_info in fields(value)
            }
            return _redact(projected, active=active)
        finally:
            active.remove(identity)

    if isinstance(value, str):
        return redact_raw_text(value)

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        identity = id(value)
        if identity in active:
            return "[REDACTED CYCLE]"
        active.add(identity)
        try:
            return _redact(to_dict(), active=active)
        finally:
            active.remove(identity)

    return value
