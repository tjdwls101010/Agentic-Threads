#!/usr/bin/env python3
"""Reject structural signs of real data in committed synthetic JSON fixtures.

The scanner is deliberately stdlib-only and only the script's fixed
``tests/fixtures/*.json`` scope is used by :func:`main`. It catches mechanical
leaks; it cannot determine whether arbitrary free text describes a real person,
so fixture diffs still require human review.

Diagnostics contain only the fixture filename and finding category. Suspect
values are never copied into output.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

CATEGORY_ORDER = (
    "unsafe-file",
    "invalid-json",
    "credential-key",
    "email",
    "phone",
    "hostname",
    "high-entropy-secret",
    "identifier",
)

_CREDENTIAL_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "auth_token",
        "authorization",
        "bearer",
        "client_secret",
        "cookie",
        "cookies",
        "csrf",
        "csrf_token",
        "csrftoken",
        "fb_dtsg",
        "jazoest",
        "lsd",
        "password",
        "passwd",
        "private_key",
        "secret",
        "session",
        "session_id",
        "sessionid",
    }
)
_CREDENTIAL_TERMINALS = frozenset(
    {"authorization", "cookie", "cookies", "password", "passwd", "secret", "token"}
)
_KEY_TERMINALS = frozenset({"access", "api", "client", "private", "secret", "signing"})
_SYNTHETIC_WORDS = frozenset({"dummy", "example", "fake", "fixture", "sample", "synthetic", "test"})
_HOST_KEYS = frozenset({"domain", "endpoint", "host", "hostname", "origin", "url"})

_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])"
    r"[A-Za-z0-9._%+\-]+@"
    r"(?P<domain>"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63})"
    r"(?![A-Za-z0-9.\-])"
)
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+\d{1,3}[\s.\-])?"
    r"(?:\(\d{2,4}\)|\d{2,4})[\s.\-]"
    r"\d{3,4}[\s.\-]\d{3,4}(?!\w)"
)
_URL_RE = re.compile(r"(?i)\b(?:https?|wss?)://[^\s\"'<>]+")
_BARE_HOST_RE = re.compile(
    r"(?i)^(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}\.?$"
)
_IPV4_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"
    r"(?:\.[A-Za-z0-9_\-]{8,})?\b"
)
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
)
_SIMPLE_SYNTHETIC_CODE_RE = re.compile(r"[a-z_]{2,20}\d{1,4}")


def shannon_entropy(value: str) -> float:
    """Return deterministic Shannon entropy in bits per character."""
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for character in value:
        counts[character] = counts.get(character, 0) + 1
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _key_words(key: str) -> tuple[str, ...]:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return tuple(part for part in re.split(r"[^a-z0-9]+", separated.lower()) if part)


def _normalised_key(key: str) -> str:
    return "_".join(_key_words(key))


def _credential_shaped_key(key: str) -> bool:
    if key.startswith("__relay_internal__pv__") and key.endswith("relayprovider"):
        return False
    words = _key_words(key)
    if not words:
        return False
    normalised = "_".join(words)
    if normalised in _CREDENTIAL_KEYS:
        return True
    if any(word in _CREDENTIAL_TERMINALS for word in words):
        return True
    return len(words) >= 2 and words[-1] == "key" and words[-2] in _KEY_TERMINALS


def _has_synthetic_marker(value: str) -> bool:
    words = {part for part in re.split(r"[^a-z0-9]+", value.lower()) if part}
    return bool(words & _SYNTHETIC_WORDS)


def _allowed_hostname(hostname: str | None) -> bool:
    if hostname is None:
        return False
    normalized = hostname.lower().rstrip(".")
    return normalized == "example.invalid" or normalized.endswith(".example.invalid")


def _valid_ipv4(hostname: str) -> bool:
    if not _IPV4_RE.fullmatch(hostname):
        return False
    return all(0 <= int(part) <= 255 for part in hostname.split("."))


def _hostname_from_candidate(candidate: str) -> str | None:
    try:
        parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
        return parsed.hostname
    except ValueError:
        return None


def _has_forbidden_hostname(key: str, value: str) -> bool:
    for match in _URL_RE.finditer(value):
        if not _allowed_hostname(_hostname_from_candidate(match.group(0))):
            return True

    stripped = value.strip().rstrip("/.")
    normalised_key = _normalised_key(key)
    host_shaped_key = normalised_key in _HOST_KEYS or any(
        normalised_key.endswith(f"_{suffix}") for suffix in _HOST_KEYS
    )
    if host_shaped_key:
        hostname = _hostname_from_candidate(stripped)
        if hostname and (_BARE_HOST_RE.fullmatch(hostname) or _valid_ipv4(hostname)):
            return not _allowed_hostname(hostname)
    if _BARE_HOST_RE.fullmatch(stripped) or _valid_ipv4(stripped):
        return not _allowed_hostname(stripped)
    return False


def _has_real_email(value: str) -> bool:
    return any(not _allowed_hostname(match.group("domain")) for match in _EMAIL_RE.finditer(value))


def _character_classes(value: str) -> int:
    return sum(
        (
            any(character.islower() for character in value),
            any(character.isupper() for character in value),
            any(character.isdigit() for character in value),
            any(character in "+/=_-" for character in value),
        )
    )


def _looks_like_secret(value: str) -> bool:
    if _JWT_RE.search(value):
        return True
    for match in _TOKEN_RE.finditer(value):
        candidate = match.group(0)
        if _has_synthetic_marker(candidate):
            continue
        entropy = shannon_entropy(candidate)
        if re.fullmatch(r"[0-9a-fA-F]{32,}", candidate):
            if entropy >= 3.5:
                return True
        elif _character_classes(candidate) >= 3 and entropy >= 4.0:
            return True
    return False


def _synthetic_identifier(key: str, value: object) -> bool:
    normalised_key = _normalised_key(key)
    is_cursor = normalised_key == "cursor" or normalised_key.endswith("_cursor")
    is_username = normalised_key in {"handle", "user_name", "username"}
    is_full_name = normalised_key in {"display_name", "full_name"}
    is_code = normalised_key in {"code", "shortcode"}
    is_id = normalised_key in {"id", "pk"} or normalised_key.endswith("_id")
    if not any((is_cursor, is_username, is_full_name, is_code, is_id)):
        return True
    if value is None:
        return True
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return False

    text = str(value).strip()
    if _has_synthetic_marker(text):
        return True
    if is_cursor or is_username or is_full_name:
        return False
    if is_code:
        return bool(_SIMPLE_SYNTHETIC_CODE_RE.fullmatch(text))
    return text.isdigit() and len(text) <= 9


def _scan_value(key: str, value: object, categories: set[str]) -> None:
    if isinstance(value, Mapping):
        for child_key in sorted(value):
            child_value = value[child_key]
            key_text = str(child_key)
            if _credential_shaped_key(key_text):
                categories.add("credential-key")
            if not _synthetic_identifier(key_text, child_value):
                categories.add("identifier")
            _scan_value(key_text, child_value, categories)
        return
    if isinstance(value, list):
        for item in value:
            _scan_value(key, item, categories)
        return
    if not isinstance(value, str):
        return
    if _has_real_email(value):
        categories.add("email")
    if _PHONE_RE.search(value):
        categories.add("phone")
    if _has_forbidden_hostname(key, value):
        categories.add("hostname")
    if _looks_like_secret(value):
        categories.add("high-entropy-secret")
    if _UUID_RE.search(value) and not _has_synthetic_marker(value):
        categories.add("identifier")


def _diagnostics(path: Path, categories: set[str]) -> list[str]:
    return [f"{path.name}: {category}" for category in CATEGORY_ORDER if category in categories]


def scan_file(path: Path) -> list[str]:
    """Scan one JSON file and return value-free diagnostics."""
    path = Path(path)
    categories: set[str] = set()
    try:
        path_status = path.lstat()
    except OSError:
        categories.add("unsafe-file")
        return _diagnostics(path, categories)
    if stat.S_ISLNK(path_status.st_mode) or not stat.S_ISREG(path_status.st_mode):
        categories.add("unsafe-file")
        return _diagnostics(path, categories)

    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            categories.add("unsafe-file")
            return _diagnostics(path, categories)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            payload = json.load(stream)
    except (OSError, UnicodeError):
        categories.add("unsafe-file")
        return _diagnostics(path, categories)
    except (json.JSONDecodeError, RecursionError):
        categories.add("invalid-json")
        return _diagnostics(path, categories)
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    _scan_value("", payload, categories)
    return _diagnostics(path, categories)


def fixture_paths(directory: Path | None = None) -> tuple[Path, ...]:
    """Return only direct ``*.json`` children in deterministic filename order."""
    root = FIXTURES_DIR if directory is None else Path(directory)
    if not root.is_dir():
        return ()
    return tuple(sorted(root.glob("*.json"), key=lambda path: path.name))


def scan_fixtures(directory: Path | None = None) -> list[str]:
    """Scan the fixed fixture scope (or an explicit directory for unit tests)."""
    findings: list[str] = []
    for path in fixture_paths(directory):
        findings.extend(scan_file(path))
    return findings


def main() -> int:
    paths = fixture_paths()
    findings = scan_fixtures()
    if findings:
        print("Fixture PII/secret scan FAILED:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        print(
            "Fixtures must be hand-authored synthetic JSON. Diagnostics intentionally "
            "omit suspect values; inspect the named category locally.",
            file=sys.stderr,
        )
        return 1
    print(
        f"Fixture PII/secret scan OK ({len(paths)} file(s) checked); "
        "free-text still requires human review."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
