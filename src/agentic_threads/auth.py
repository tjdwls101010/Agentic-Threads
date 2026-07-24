"""Authenticated session storage, cookie import, and identifier normalization."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlsplit

from . import config
from .errors import InvalidCookieError, InvalidIdentifierError, LoginRequiredError
from .redact import redact_cookie_parse_error

_PROFILE_DIR_MODE = 0o700
_SESSION_FILE_MODE = 0o600
SESSION_FILENAME = "session.json"
_PROFILE_DIRECTORY_ERROR = "profile storage directory is unavailable or unsafe"
_NO_SESSION_ERROR = "no session for selected profile: run `agentic-threads login`"

REQUIRED_COOKIE_NAMES = ("sessionid", "ds_user_id", "csrftoken")
_CookieValue = TypeVar("_CookieValue")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(repr=False)
class SessionCredential:
    """The complete read-session credential persisted for one profile."""

    sessionid: str
    ds_user_id: str
    csrftoken: str
    user_agent: str
    doc_ids: dict[str, str] | None = None
    features: dict[str, object] | None = None
    extracted_at: str = field(default_factory=_utc_now)

    @property
    def cookies(self) -> dict[str, str]:
        """Return only the three cookies approved for authenticated Threads reads."""
        return {
            "sessionid": self.sessionid,
            "ds_user_id": self.ds_user_id,
            "csrftoken": self.csrftoken,
        }

    def __repr__(self) -> str:
        return "SessionCredential(<redacted>)"

    def __str__(self) -> str:
        return "SessionCredential(<redacted>)"


def _open_profile_directory(path: Path, *, create: bool) -> int:
    """Open a verified real directory without following its final path component."""
    path = Path(path)
    if create:
        old_umask = os.umask(0o077)
        try:
            try:
                path.mkdir(parents=True)
            except FileExistsError:
                pass
            except OSError as exc:
                raise LoginRequiredError(_PROFILE_DIRECTORY_ERROR) from exc
        finally:
            os.umask(old_umask)

    try:
        entry_stat = os.lstat(path)
    except FileNotFoundError as exc:
        message = _PROFILE_DIRECTORY_ERROR if create else _NO_SESSION_ERROR
        raise LoginRequiredError(message) from exc
    except OSError as exc:
        raise LoginRequiredError(_PROFILE_DIRECTORY_ERROR) from exc
    if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISDIR(entry_stat.st_mode):
        raise LoginRequiredError(_PROFILE_DIRECTORY_ERROR)

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = -1
    try:
        fd = os.open(path, flags)
        opened_stat = os.fstat(fd)
        if not stat.S_ISDIR(opened_stat.st_mode) or (entry_stat.st_dev, entry_stat.st_ino) != (
            opened_stat.st_dev,
            opened_stat.st_ino,
        ):
            raise LoginRequiredError(_PROFILE_DIRECTORY_ERROR)
        os.fchmod(fd, _PROFILE_DIR_MODE)
    except LoginRequiredError:
        if fd >= 0:
            os.close(fd)
        raise
    except OSError as exc:
        if fd >= 0:
            os.close(fd)
        raise LoginRequiredError(_PROFILE_DIRECTORY_ERROR) from exc
    return fd


def ensure_profile_dir(path: Path) -> Path:
    """Create or harden a real credential directory with owner-only permissions."""
    directory_fd = _open_profile_directory(path, create=True)
    os.close(directory_fd)
    return Path(path)


def _create_session_temporary_file(directory_fd: int) -> tuple[int, str]:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    for _ in range(100):
        temporary_name = f".{SESSION_FILENAME}.{secrets.token_hex(16)}.tmp"
        try:
            fd = os.open(
                temporary_name,
                flags,
                _SESSION_FILE_MODE,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            continue
        return fd, temporary_name
    raise LoginRequiredError("saved session could not be written safely; log in again")


def save_session(
    profile: str,
    credential: SessionCredential,
    *,
    profile_dir_override: str | os.PathLike[str] | None = None,
) -> Path:
    """Atomically persist a credential in a verified 0700 directory as a 0600 file."""
    directory = config.profile_dir(profile, profile_dir_override=profile_dir_override)
    session_path = directory / SESSION_FILENAME
    payload = json.dumps(
        asdict(credential), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

    directory_fd = _open_profile_directory(directory, create=True)
    fd = -1
    temporary_name: str | None = None
    try:
        fd, temporary_name = _create_session_temporary_file(directory_fd)
        os.fchmod(fd, _SESSION_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            stream.write(payload)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(
            temporary_name,
            SESSION_FILENAME,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)

    return session_path


def load_session(
    profile: str,
    *,
    profile_dir_override: str | os.PathLike[str] | None = None,
) -> SessionCredential:
    """Load and validate a saved credential through a verified profile directory."""
    directory = config.profile_dir(profile, profile_dir_override=profile_dir_override)
    directory_fd = _open_profile_directory(directory, create=False)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    fd = -1
    try:
        try:
            fd = os.open(SESSION_FILENAME, flags, dir_fd=directory_fd)
        except FileNotFoundError as exc:
            raise LoginRequiredError(_NO_SESSION_ERROR) from exc
        except OSError as exc:
            raise LoginRequiredError(
                "saved session could not be opened safely; log in again"
            ) from exc

        try:
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise LoginRequiredError("saved session is not a regular file; log in again")
            os.fchmod(fd, _SESSION_FILE_MODE)
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                fd = -1
                try:
                    data = json.load(stream)
                except (UnicodeDecodeError, ValueError) as exc:
                    raise LoginRequiredError("saved session is invalid; log in again") from exc
        except LoginRequiredError:
            raise
        except OSError as exc:
            raise LoginRequiredError(
                "saved session could not be read safely; log in again"
            ) from exc
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(directory_fd)
    if not isinstance(data, dict):
        raise LoginRequiredError("saved session is invalid; log in again")
    required_fields = (*REQUIRED_COOKIE_NAMES, "user_agent", "extracted_at")
    if any(not isinstance(data.get(name), str) or not data[name] for name in required_fields):
        raise LoginRequiredError("saved session is incomplete; log in again")

    doc_ids = data.get("doc_ids")
    if doc_ids is not None and not _is_string_mapping(doc_ids):
        raise LoginRequiredError("saved session doc_ids are invalid; log in again")
    features = data.get("features")
    if features is not None and not isinstance(features, dict):
        raise LoginRequiredError("saved session features are invalid; log in again")

    return SessionCredential(
        sessionid=data["sessionid"],
        ds_user_id=data["ds_user_id"],
        csrftoken=data["csrftoken"],
        user_agent=data["user_agent"],
        doc_ids=dict(doc_ids) if doc_ids is not None else None,
        features=dict(features) if features is not None else None,
        extracted_at=data["extracted_at"],
    )


def _is_string_mapping(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    )


# Cookie import -----------------------------------------------------------------

_THREAD_DOMAIN_ROOTS = ("threads.com", "threads.net")
_NETSCAPE_HEADER_RE = re.compile(r"#\s*(?:Netscape|HTTP Cookie File)", re.IGNORECASE)
_CURL_HEADER_RE = re.compile(
    r"(?:^|\s)(?:-H|--header)(?:\s+|=)(?P<quote>['\"])(?P<header>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)


def _normalized_cookie_domain(domain: str) -> str | None:
    if not domain or not domain.isascii():
        return None
    normalized = domain.casefold()
    if normalized.startswith("."):
        normalized = normalized[1:]
    if (
        not normalized
        or normalized.startswith(".")
        or normalized.endswith(".")
        or ".." in normalized
        or any(not (character.isalnum() or character in ".-") for character in normalized)
    ):
        return None
    return normalized


def _is_threads_domain(domain: str) -> bool:
    normalized = _normalized_cookie_domain(domain)
    return normalized is not None and any(
        normalized == root or normalized.endswith(f".{root}") for root in _THREAD_DOMAIN_ROOTS
    )


def _insert_cookie(
    cookies: dict[str, _CookieValue],
    name: str,
    value: _CookieValue,
    *,
    conflict_context: str,
) -> None:
    if name in REQUIRED_COOKIE_NAMES and name in cookies and cookies[name] != value:
        raise InvalidCookieError(f"conflicting {conflict_context}")
    cookies[name] = value


def _cookie_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for name, value in pairs:
        if name in parsed and parsed[name] != value:
            raise InvalidCookieError("conflicting JSON cookie object key")
        parsed[name] = value
    return parsed


def _parse_cookie_records(records: Sequence[object], source: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for index, item in enumerate(records, start=1):
        if not isinstance(item, Mapping):
            raise InvalidCookieError(f"malformed {source} cookie entry {index}")
        name = item.get("name")
        if not isinstance(name, str):
            raise InvalidCookieError(f"malformed {source} cookie entry {index}")
        if name not in REQUIRED_COOKIE_NAMES:
            continue

        domain = item.get("domain")
        if not isinstance(domain, str) or _normalized_cookie_domain(domain) is None:
            raise InvalidCookieError(f"malformed {source} cookie domain at entry {index}")
        if not _is_threads_domain(domain):
            continue

        value = item.get("value")
        if not isinstance(value, str):
            raise InvalidCookieError(f"malformed {source} cookie entry {index}")
        _insert_cookie(
            cookies,
            name,
            value,
            conflict_context=f"{source} cookie at entry {index}",
        )
    return cookies


def _parse_cookie_json(text: str) -> dict[str, str] | None:
    try:
        data = json.loads(text, object_pairs_hook=_cookie_json_object)
    except InvalidCookieError:
        raise
    except ValueError:
        return None

    if isinstance(data, list):
        return _parse_cookie_records(data, "JSON")
    if not isinstance(data, dict):
        return None

    nested = data.get("cookies")
    if isinstance(nested, list):
        return _parse_cookie_records(nested, "JSON")
    if isinstance(nested, dict):
        return {
            name: value
            for name, value in nested.items()
            if isinstance(name, str) and isinstance(value, str)
        }
    if "name" in data or "value" in data:
        return _parse_cookie_records([data], "JSON")

    return {
        name: value
        for name, value in data.items()
        if isinstance(name, str) and isinstance(value, str)
    }


def _parse_cookie_netscape(text: str) -> dict[str, str] | None:
    entries: list[tuple[int, str]] = []
    for line_number, original_line in enumerate(text.splitlines(), start=1):
        line = original_line.strip("\r")
        if not line.strip():
            continue
        if line.startswith("#HttpOnly_"):
            line = line.removeprefix("#HttpOnly_")
        elif line.startswith("#"):
            continue
        entries.append((line_number, line))

    has_header = bool(_NETSCAPE_HEADER_RE.search(text))
    if not entries:
        return {} if has_header else None
    split_entries = [(line_number, line, line.split("\t")) for line_number, line in entries]
    if not has_header and not all(len(fields) == 7 for _, _, fields in split_entries):
        return None

    cookies: dict[str, str] = {}
    for line_number, line, fields in split_entries:
        if len(fields) != 7:
            raise InvalidCookieError(
                f"malformed Netscape cookie line {line_number} ({len(fields)} fields): "
                f"{redact_cookie_parse_error(line)}"
            )
        domain, name, value = fields[0], fields[5], fields[6]
        if _is_threads_domain(domain):
            _insert_cookie(
                cookies,
                name,
                value,
                conflict_context=f"Netscape cookie at line {line_number}",
            )
    return cookies


def _cookie_header_payloads(text: str) -> list[str]:
    payloads: list[str] = []
    for match in _CURL_HEADER_RE.finditer(text):
        header = match.group("header").strip()
        if header.lower().startswith("cookie:"):
            payloads.append(header.partition(":")[2].strip())

    for raw_line in text.splitlines():
        line = raw_line.strip().strip("'\"")
        if line.lower().startswith("cookie:"):
            payloads.append(line.partition(":")[2].strip())
    if payloads:
        return payloads

    line = text.strip()
    if line.lower().startswith("curl "):
        raise InvalidCookieError("cURL input does not contain a Cookie header")
    line = re.sub(
        r"^(?:-H|--header)(?:\s+|=)",
        "",
        line,
        flags=re.IGNORECASE,
    )
    line = line.strip().strip("'\"")
    line = re.sub(r"^Cookie\s*:\s*", "", line, flags=re.IGNORECASE)
    return [line]


def _parse_cookie_header(text: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for payload in _cookie_header_payloads(text):
        for index, part in enumerate(payload.split(";"), start=1):
            segment = part.strip()
            if not segment:
                continue
            if "=" not in segment:
                raise InvalidCookieError(
                    f"malformed cookie header segment: {redact_cookie_parse_error(segment)}"
                )
            name, _, value = segment.partition("=")
            name = name.strip()
            if not name:
                raise InvalidCookieError("malformed cookie header: empty cookie name")
            _insert_cookie(
                cookies,
                name,
                value.strip(),
                conflict_context=f"cookie header at segment {index}",
            )
    return cookies


def _required_cookies(cookies: Mapping[str, str]) -> dict[str, str]:
    missing = [
        name
        for name in REQUIRED_COOKIE_NAMES
        if not isinstance(cookies.get(name), str) or not cookies[name].strip()
    ]
    if missing:
        raise InvalidCookieError(
            f"cookie export is missing required cookie(s): {', '.join(missing)}"
        )
    return {name: cookies[name] for name in REQUIRED_COOKIE_NAMES}


def parse_cookie_file(path: Path) -> dict[str, str]:
    """Parse JSON, Netscape, or raw Cookie/cURL input and return required cookies."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidCookieError("cookie export is not valid UTF-8") from exc

    cookies = _parse_cookie_json(text)
    if cookies is None:
        cookies = _parse_cookie_netscape(text)
    if cookies is None:
        cookies = _parse_cookie_header(text)
    return _required_cookies(cookies)


def from_cookie_file(
    path: Path,
    profile: str = "default",
    *,
    profile_dir_override: str | os.PathLike[str] | None = None,
) -> SessionCredential:
    """Import a cookie export, persist only its required cookies, and return it."""
    cookies = parse_cookie_file(path)
    credential = SessionCredential(
        sessionid=cookies["sessionid"],
        ds_user_id=cookies["ds_user_id"],
        csrftoken=cookies["csrftoken"],
        user_agent=DEFAULT_USER_AGENT,
    )
    save_session(profile, credential, profile_dir_override=profile_dir_override)
    print(
        "agentic-threads: the imported cookie file still contains a live session; "
        "delete or secure it",
        file=sys.stderr,
    )
    return credential


# Identifier normalization ------------------------------------------------------

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_NUMERIC_ID_RE = re.compile(r"^[0-9]+$")
_SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_URL_PREFIXES = (
    "threads.com/",
    "www.threads.com/",
    "threads.net/",
    "www.threads.net/",
)


def _looks_like_threads_url(raw: str) -> bool:
    lowered = raw.lower()
    return "://" in raw or lowered.startswith(_URL_PREFIXES)


def _threads_url_identifier(raw: str) -> tuple[str, str]:
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise InvalidIdentifierError("malformed Threads URL") from exc
    if parts.scheme not in {"http", "https"}:
        raise InvalidIdentifierError("only http/https Threads URLs are accepted")
    host = parts.hostname or ""
    if not _is_threads_domain(host):
        raise InvalidIdentifierError("URL host must be threads.com or threads.net")
    if parts.username is not None or parts.password is not None:
        raise InvalidIdentifierError("Threads URLs must not contain credentials")

    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments:
        raise InvalidIdentifierError("Threads URL has no profile or post path")

    if len(segments) == 1:
        username = segments[0].removeprefix("@")
        if not _USERNAME_RE.fullmatch(username):
            raise InvalidIdentifierError("Threads profile URL contains an invalid username")
        return "username", username

    if len(segments) == 3 and segments[1].lower() == "post":
        username = segments[0].removeprefix("@")
        shortcode = segments[2]
        if not _USERNAME_RE.fullmatch(username):
            raise InvalidIdentifierError("Threads post URL contains an invalid username")
        _decode_shortcode(shortcode)
        return "shortcode", shortcode

    raise InvalidIdentifierError(
        "unsupported Threads URL path: expected /@username or /@username/post/shortcode"
    )


def normalize_user_identifier(raw: str, *, by: str | None = None) -> tuple[str, str]:
    """Normalize a username, numeric user id, or Threads profile URL."""
    if by not in {None, "username", "id", "user_id"}:
        raise InvalidIdentifierError("--by must be 'username' or 'id'")

    value = raw.strip()
    if not value:
        raise InvalidIdentifierError("identifier is empty")
    if _looks_like_threads_url(value):
        kind, normalized = _threads_url_identifier(value)
        if kind != "username":
            raise InvalidIdentifierError("expected a Threads profile URL, not a post URL")
        return kind, normalized

    if value.startswith("@"):
        username = value[1:]
        if not _USERNAME_RE.fullmatch(username):
            raise InvalidIdentifierError("invalid Threads username")
        return "username", username
    if by == "username":
        if not _USERNAME_RE.fullmatch(value):
            raise InvalidIdentifierError("invalid Threads username")
        return "username", value
    if by in {"id", "user_id"}:
        if not _NUMERIC_ID_RE.fullmatch(value):
            raise InvalidIdentifierError("--by id requires a numeric user id")
        return "user_id", value
    if _NUMERIC_ID_RE.fullmatch(value):
        return "user_id", value
    if _USERNAME_RE.fullmatch(value):
        return "username", value
    raise InvalidIdentifierError(
        "invalid identifier: expected @username, username, numeric id, or Threads profile URL"
    )


normalize_identifier = normalize_user_identifier


def normalize_post_identifier(raw: str) -> tuple[str, str]:
    """Normalize a numeric post id, bare shortcode, or Threads post permalink."""
    value = raw.strip()
    if not value:
        raise InvalidIdentifierError("post identifier is empty")
    if _looks_like_threads_url(value):
        kind, normalized = _threads_url_identifier(value)
        if kind != "shortcode":
            raise InvalidIdentifierError("expected a Threads post permalink, not a profile URL")
        return kind, normalized
    if _NUMERIC_ID_RE.fullmatch(value):
        return "post_id", value
    _decode_shortcode(value)
    return "shortcode", value


def _decode_shortcode(code: str) -> int:
    if not _SHORTCODE_RE.fullmatch(code):
        raise InvalidIdentifierError("invalid Threads post shortcode")
    padding = "=" * (-len(code) % 4)
    try:
        decoded = base64.b64decode(code + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidIdentifierError("invalid Threads post shortcode") from exc
    if not decoded:
        raise InvalidIdentifierError("invalid Threads post shortcode")
    return int.from_bytes(decoded, byteorder="big", signed=False)


def shortcode_to_post_id_candidates(code: str) -> tuple[str, ...]:
    """Return the four IDs encoded by a permalink shortcode's omitted low bits."""
    decoded = _decode_shortcode(code.strip())
    return tuple(str((decoded << 2) | low_bits) for low_bits in range(4))
