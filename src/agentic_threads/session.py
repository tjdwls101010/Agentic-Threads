"""Session checks and optional headed-browser login for Agentic Threads.

Status and doctor checks stay on the base ``httpx`` read path.  Scrapling is
imported lazily only by the browser setup/login helpers.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus, urljoin, urlsplit

from . import auth, client, config, docids, errors, gql, parse

_GRAPHQL_RESOURCE_TYPES = frozenset({"fetch", "xhr"})
_SAFE_GRAPHQL_REQUEST_HEADER = "x-fb-friendly-name"
_MAX_GRAPHQL_FORM_BODY_BYTES = 64 * 1024
_MAX_GRAPHQL_FORM_FIELDS = 128
_MAX_GRAPHQL_DOC_ID_DIGITS = 64
_MAX_HARVEST_PROJECTIONS = len(docids.OPERATION_TO_KEY)
_STEALTH_INIT_SCRIPT = (Path(__file__).parent / "_stealth_init.js").resolve()
_THREADS_HOME = "https://www.threads.com/"
_THREADS_COOKIE_URLS = (_THREADS_HOME, "https://www.threads.net/")
_THREADS_COOKIE_DOMAINS = frozenset({"threads.com", "threads.net"})

# Fixed, public destinations broaden the operation harvest without inspecting or
# printing the signed-in account's content.  A post link on the fixed public
# profile is opened separately because permanent post URLs can be deleted.
_HARVEST_NAV_URLS = (
    "https://www.threads.com/",
    "https://www.threads.com/search?q=threads&serp_type=default",
    "https://www.threads.com/@threads",
)

_LOGIN_FORM_MARKERS = (
    "caa_login_form_data",
    "caafetaaymhpasswordentryquery",
    "comet_headless_login",
    '"is_logged_in":false',
)
_CHECKPOINT_MARKERS = (
    "checkpoint_required",
    "challenge_required",
    "cometcheckpointrootquery",
    '"checkpoint_url"',
)
_LOGGED_IN_MARKERS = (
    '"dtsginitialdata"',
    '"is_logged_in":true',
    '"viewer_id"',
)
_REQUIRED_LOGIN_COOKIES = frozenset(auth.REQUIRED_COOKIE_NAMES)


class Status(Enum):
    LOGGED_IN = "logged_in"
    EXPIRED = "expired"
    RATE_LIMITED = "rate_limited"


def query_data_for(
    credential: auth.SessionCredential,
) -> tuple[dict[str, str], dict[str, object]]:
    """Return per-operation doc IDs and features with saved values winning.

    Both maps are copied.  Older credentials therefore gain newly shipped
    defaults without mutating the credential or losing values harvested from
    the browser that created it.
    """

    merged_doc_ids = docids.merge_doc_ids(credential.doc_ids)
    merged_features = dict(gql.DEFAULT_FEATURES)
    merged_features.update(credential.features or {})
    return merged_doc_ids, merged_features


def check_session_status(
    read_client: client.ReadClient,
    doc_ids: Mapping[str, str],
    features: Mapping[str, object] | None,
    ds_user_id: str,
) -> Status:
    """Probe the saved account's own profile with one authenticated HTTP read."""

    try:
        body = read_client.post(
            gql.PROFILE_OPERATION,
            gql.profile_variables(ds_user_id, features=features),
            doc_id=doc_ids.get("profile", docids.DEFAULT_DOC_IDS["profile"]),
        )
    except errors.RateLimitedError:
        return Status.RATE_LIMITED
    except errors.SessionExpiredError:
        return Status.EXPIRED

    user = parse.extract_profile_user(body)
    if user is None:
        return Status.EXPIRED
    returned_id = user.get("pk") or user.get("id")
    if returned_id is None or str(returned_id) != str(ds_user_id):
        return Status.EXPIRED
    return Status.LOGGED_IN


def run_status(
    profile: str = config.DEFAULT_PROFILE_NAME,
    *,
    profile_dir_override: str | os.PathLike[str] | None = None,
) -> Status:
    """Load a profile and classify it with exactly one HTTP request."""

    credential = auth.load_session(profile, profile_dir_override=profile_dir_override)
    merged_doc_ids, features = query_data_for(credential)
    read_client = client.ReadClient(credential, max_requests=1)
    try:
        return check_session_status(
            read_client,
            merged_doc_ids,
            features,
            credential.ds_user_id,
        )
    finally:
        read_client.close()


def run_doctor(
    profile: str = config.DEFAULT_PROFILE_NAME,
    *,
    profile_dir_override: str | os.PathLike[str] | None = None,
    refresh: bool = False,
) -> tuple[bool, str]:
    """Check authentication and optionally refresh doc IDs without a browser."""

    try:
        credential = auth.load_session(profile, profile_dir_override=profile_dir_override)
    except errors.LoginRequiredError as exc:
        return False, str(exc)

    merged_doc_ids, features = query_data_for(credential)
    retry_after_refresh = False
    read_client = client.ReadClient(credential, max_requests=1)
    try:
        try:
            status = check_session_status(
                read_client,
                merged_doc_ids,
                features,
                credential.ds_user_id,
            )
        except errors.PersistedOperationDriftError:
            if not refresh:
                raise
            retry_after_refresh = True
    finally:
        read_client.close()

    if not retry_after_refresh and status is not Status.LOGGED_IN:
        return False, f"session check failed: {status.value} (run `agentic-threads login`)"

    message = "OK - authenticated round-trip succeeded"
    if refresh:
        fresh_doc_ids = docids.reanchor_via_main_js(
            credential.sessionid,
            credential.ds_user_id,
            credential.csrftoken,
            credential.user_agent,
        )
        if retry_after_refresh:
            fresh_profile_doc_id = fresh_doc_ids.get("profile")
            if (
                not isinstance(fresh_profile_doc_id, str)
                or not fresh_profile_doc_id.isascii()
                or not fresh_profile_doc_id.isdigit()
            ):
                return False, "doc ID refresh failed: no usable profile doc ID found"

        refreshed_doc_ids = dict(merged_doc_ids)
        refreshed_doc_ids.update(fresh_doc_ids)
        refreshed_doc_ids = docids.merge_doc_ids(refreshed_doc_ids)

        if retry_after_refresh:
            retry_client = client.ReadClient(credential, max_requests=1)
            try:
                status = check_session_status(
                    retry_client,
                    refreshed_doc_ids,
                    features,
                    credential.ds_user_id,
                )
            finally:
                retry_client.close()
            if status is not Status.LOGGED_IN:
                return False, f"session check failed: {status.value} (run `agentic-threads login`)"

        credential.doc_ids = refreshed_doc_ids
        auth.save_session(
            profile,
            credential,
            profile_dir_override=profile_dir_override,
        )
        message += f"; re-anchored {len(fresh_doc_ids)} doc_id(s)"
    return True, message


def detect_wall(url: str, html: str | None = None) -> str | None:
    """Return ``checkpoint``, ``login``, or ``None`` for a browser page."""

    lowered_url = url.casefold()
    if "/checkpoint/" in lowered_url or "/challenge/" in lowered_url:
        return "checkpoint"
    if html is not None:
        lowered_html = html.casefold()
        if any(marker in lowered_html for marker in _CHECKPOINT_MARKERS):
            return "checkpoint"
    if "/accounts/login" in lowered_url or "/login" in lowered_url:
        return "login"
    if html is None:
        return None
    if any(marker in lowered_html for marker in _LOGIN_FORM_MARKERS):
        return "login"
    return None


def looks_logged_in(html: str, cookie_names: Iterable[str]) -> bool:
    """Recognize a completed Threads login from both body and cookie state."""

    names = set(cookie_names)
    lowered_html = html.casefold()
    return (
        _REQUIRED_LOGIN_COOKIES.issubset(names)
        and any(marker in lowered_html for marker in _LOGGED_IN_MARKERS)
        and detect_wall("", html) is None
    )


def _is_trusted_threads_page_url(url: str) -> bool:
    if url != url.strip() or any(ord(character) < 32 or ord(character) == 127 for character in url):
        return False
    try:
        endpoint = urlsplit(url)
        hostname = endpoint.hostname
        port = endpoint.port
    except ValueError:
        return False
    if (
        endpoint.scheme.casefold() != "https"
        or not isinstance(hostname, str)
        or not hostname.isascii()
        or endpoint.username is not None
        or endpoint.password is not None
        or port not in (None, 443)
    ):
        return False

    expected_netloc = hostname if port is None else f"{hostname}:{port}"
    if endpoint.netloc.casefold() != expected_netloc.casefold():
        return False
    normalized = hostname.casefold()
    labels = normalized.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or any(not (character.isalnum() or character == "-") for character in label)
        for label in labels
    ):
        return False
    return any(
        normalized == root or normalized.endswith(f".{root}") for root in _THREADS_COOKIE_DOMAINS
    )


def _resolve_trusted_threads_post_href(href: object) -> str | None:
    """Resolve one harvested permalink without letting the browser reinterpret it."""

    if (
        not isinstance(href, str)
        or not href
        or not href.isascii()
        or href != href.strip()
        or any(character.isspace() or ord(character) == 127 for character in href)
        or "%" in href
        or "\\" in href
        or href.startswith("//")
    ):
        return None

    try:
        endpoint = urlsplit(href)
    except ValueError:
        return None
    if "//" in endpoint.path or any(segment in {".", ".."} for segment in endpoint.path.split("/")):
        return None
    candidate = href if endpoint.scheme or endpoint.netloc else urljoin(_THREADS_HOME, href)
    if not _is_trusted_threads_page_url(candidate):
        return None

    try:
        path = urlsplit(candidate).path
    except ValueError:
        return None
    if (
        "/post/" not in path
        or path.endswith("/post/")
        or "//" in path
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        return None
    return candidate


@contextmanager
def _isolated_browser_cache() -> Iterator[None]:
    browser_cache = config.browsers_dir()
    browser_cache.mkdir(parents=True, exist_ok=True)
    previous = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = previous


def _build_stealth_session(
    profile: str,
    *,
    profile_dir_override: str | os.PathLike[str] | None = None,
):
    """Build the exact headed Scrapling session used only for login/harvest."""

    try:
        from scrapling.fetchers import StealthySession
    except ImportError as exc:
        raise errors.BrowserSetupError(
            "browser support is not installed; install agentic-threads[browser] and run setup"
        ) from exc

    browser_dir = config.browser_profile_dir(
        profile,
        profile_dir_override=profile_dir_override,
    )
    auth.ensure_profile_dir(browser_dir)
    return StealthySession(
        real_chrome=True,
        headless=False,
        user_data_dir=str(browser_dir),
        init_script=str(_STEALTH_INIT_SCRIPT),
    )


def _trusted_page_url_and_wall(page: Any) -> tuple[str | None, str]:
    """Read and classify a page URL without inspecting any other browser state."""

    try:
        page_url = page.url
    except Exception:
        return None, "unknown"
    if not isinstance(page_url, str) or not _is_trusted_threads_page_url(page_url):
        return None, "unknown"

    try:
        wall = detect_wall(page_url)
    except errors.ChallengeError:
        return page_url, "checkpoint"
    except Exception:
        return page_url, "unknown"
    return page_url, wall or "clean"


def _page_wall(page: Any) -> str:
    """Classify a trusted readable browser page as checkpoint, clean, or unknown."""

    page_url, url_wall = _trusted_page_url_and_wall(page)
    if url_wall in {"checkpoint", "unknown"}:
        return url_wall

    try:
        page_html = page.content()
    except errors.ChallengeError:
        return "checkpoint"
    except Exception:
        return "unknown"
    if not isinstance(page_html, str) or not page_html.strip():
        return "unknown"

    try:
        wall = detect_wall(page_url, page_html)
    except errors.ChallengeError:
        return "checkpoint"
    except Exception:
        return "unknown"
    if wall == "checkpoint":
        return "checkpoint"
    if wall is not None:
        return "unknown"
    if any(marker in page_html.casefold() for marker in _LOGGED_IN_MARKERS):
        return "clean"
    return "unknown"


def _best_effort_harvest_navigation(page: Any) -> str:
    for url in _HARVEST_NAV_URLS:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        except errors.ChallengeError:
            return "checkpoint"
        except Exception:
            return "unknown"

        _, url_wall = _trusted_page_url_and_wall(page)
        if url_wall != "clean":
            return "checkpoint" if url_wall == "checkpoint" else "unknown"
        try:
            page.wait_for_timeout(2_000)
        except errors.ChallengeError:
            return "checkpoint"
        except Exception:
            return "unknown"

        page_wall = _page_wall(page)
        if page_wall != "clean":
            return page_wall

    # The source profile is fixed and public; only its first permalink is used
    # for navigation.  Neither the href nor page content leaves this process.
    _, url_wall = _trusted_page_url_and_wall(page)
    if url_wall != "clean":
        return "checkpoint" if url_wall == "checkpoint" else "unknown"
    try:
        href = page.eval_on_selector(
            'a[href*="/post/"]',
            "element => element.getAttribute('href')",
        )
    except errors.ChallengeError:
        return "checkpoint"
    except Exception:
        return _page_wall(page)

    post_url = _resolve_trusted_threads_post_href(href)
    if post_url is not None:
        try:
            page.goto(post_url, wait_until="domcontentloaded", timeout=20_000)
        except errors.ChallengeError:
            return "checkpoint"
        except Exception:
            return "unknown"

        _, url_wall = _trusted_page_url_and_wall(page)
        if url_wall != "clean":
            return "checkpoint" if url_wall == "checkpoint" else "unknown"
        try:
            page.wait_for_timeout(2_000)
        except errors.ChallengeError:
            return "checkpoint"
        except Exception:
            return "unknown"
    return _page_wall(page)


def _is_threads_cookie_domain(domain: object) -> bool:
    if not isinstance(domain, str) or not domain.isascii():
        return False
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
        return False
    return any(
        normalized == root or normalized.endswith(f".{root}") for root in _THREADS_COOKIE_DOMAINS
    )


def _cookie_jar(records: Iterable[object]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    items: Iterable[object] = (records,) if isinstance(records, Mapping) else records
    for record in items:
        if not isinstance(record, Mapping):
            continue
        name = record.get("name")
        value = record.get("value")
        if (
            isinstance(name, str)
            and name in _REQUIRED_LOGIN_COOKIES
            and isinstance(value, str)
            and value
            and _is_threads_cookie_domain(record.get("domain"))
        ):
            if name in cookies and cookies[name] != value:
                return {}
            cookies[name] = value
    return cookies


def _request_artifact_for_harvest(request: Any) -> dict[str, str] | None:
    """Project one bounded trusted GraphQL request to an operation/doc-ID pair."""

    try:
        url = request.url
        method = request.method
        resource_type = request.resource_type
    except Exception:
        return None
    if not all(isinstance(value, str) for value in (url, method, resource_type)):
        return None
    if (
        url != gql.GRAPHQL_URL
        or method != "POST"
        or resource_type.casefold() not in _GRAPHQL_RESOURCE_TYPES
    ):
        return None

    try:
        frame_url = request.frame.url
    except Exception:
        return None
    if not isinstance(frame_url, str) or not _is_trusted_threads_page_url(frame_url):
        return None

    try:
        post_data = request.post_data
    except Exception:
        return None
    if isinstance(post_data, bytes):
        if not post_data or len(post_data) > _MAX_GRAPHQL_FORM_BODY_BYTES:
            return None
        try:
            form_body = post_data.decode("ascii")
        except UnicodeDecodeError:
            return None
    elif isinstance(post_data, str):
        if not post_data:
            return None
        try:
            encoded_body = post_data.encode("ascii")
        except UnicodeEncodeError:
            return None
        if len(encoded_body) > _MAX_GRAPHQL_FORM_BODY_BYTES:
            return None
        form_body = post_data
    else:
        return None

    if any(ord(character) < 32 or ord(character) == 127 for character in form_body):
        return None
    for index, character in enumerate(form_body):
        if character == "%" and (
            index + 2 >= len(form_body)
            or any(
                digit not in "0123456789abcdefABCDEF" for digit in form_body[index + 1 : index + 3]
            )
        ):
            return None

    fields = form_body.split("&")
    if not fields or len(fields) > _MAX_GRAPHQL_FORM_FIELDS:
        return None
    form_operations: list[str] = []
    form_doc_ids: list[str] = []
    for field in fields:
        if not field or "=" not in field:
            return None
        raw_name, raw_value = field.split("=", 1)
        try:
            name = unquote_plus(raw_name, encoding="ascii", errors="strict")
        except (UnicodeDecodeError, ValueError):
            return None
        if name not in {"fb_api_req_friendly_name", "doc_id"}:
            continue
        try:
            value = unquote_plus(raw_value, encoding="ascii", errors="strict")
        except (UnicodeDecodeError, ValueError):
            return None
        if name == "fb_api_req_friendly_name":
            form_operations.append(value)
        else:
            form_doc_ids.append(value)

    header_operations: list[str] = []
    try:
        request_headers = request.headers
        if isinstance(request_headers, Mapping):
            for name, value in request_headers.items():
                if isinstance(name, str) and name.casefold() == _SAFE_GRAPHQL_REQUEST_HEADER:
                    if not isinstance(value, str):
                        return None
                    header_operations.append(value)
    except Exception:
        return None

    if len(form_operations) > 1 or len(header_operations) != 1 or len(form_doc_ids) != 1:
        return None
    operations = [*form_operations, *header_operations]
    if (
        not operations
        or any(operation not in docids.OPERATION_TO_KEY for operation in operations)
        or len(set(operations)) != 1
    ):
        return None

    doc_id = form_doc_ids[0]
    if (
        not doc_id
        or len(doc_id) > _MAX_GRAPHQL_DOC_ID_DIGITS
        or not doc_id.isascii()
        or not doc_id.isdecimal()
    ):
        return None
    return {"operation": operations[0], "doc_id": doc_id}


def run_login(
    profile: str = config.DEFAULT_PROFILE_NAME,
    *,
    profile_dir_override: str | os.PathLike[str] | None = None,
    timeout_seconds: float = 300.0,
) -> bool:
    """Open a headed browser, poll for login, then save minimal credentials."""

    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    auth.ensure_profile_dir(config.profile_dir(profile, profile_dir_override=profile_dir_override))
    state: dict[str, object] = {
        "logged_in": False,
        "wall": None,
        "user_agent": None,
        "trusted_cookies": None,
    }
    captured_projections: dict[str, dict[str, str]] = {}
    request_listener_attempted = False
    request_listener_active = False

    def capture_graphql_request(request: Any) -> None:
        if not request_listener_active or len(captured_projections) >= _MAX_HARVEST_PROJECTIONS:
            return
        try:
            projection = _request_artifact_for_harvest(request)
            if projection is not None:
                captured_projections[projection["operation"]] = projection
        except Exception:
            # Request events can race redirects and context shutdown.
            pass

    def attach_request_listener(page: Any) -> None:
        nonlocal request_listener_active, request_listener_attempted
        if request_listener_attempted:
            return
        request_listener_attempted = True
        try:
            page.on("request", capture_graphql_request)
        except errors.ChallengeError:
            raise
        except Exception:
            # Login and fixed navigation remain useful when event capture is unavailable.
            captured_projections.clear()
            return
        request_listener_active = True

    def trusted_page_url(page: Any, *, allow_login: bool) -> str | None:
        page_url, url_wall = _trusted_page_url_and_wall(page)
        if url_wall == "checkpoint":
            state["wall"] = "checkpoint"
            return None
        if page_url is None or url_wall == "unknown":
            state["wall"] = "unknown"
            return None
        if not allow_login and url_wall != "clean":
            state["wall"] = "unknown"
            return None
        return page_url

    def wait_for_login(page: Any) -> None:
        prompt_printed = False
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            page_url = trusted_page_url(page, allow_login=True)
            if page_url is None:
                return
            if not prompt_printed:
                print(
                    "A browser window is open. Complete the Threads login there; "
                    f"this continues automatically (timeout {timeout_seconds:.0f}s).",
                    file=sys.stderr,
                )
                prompt_printed = True

            try:
                html = page.content()
            except errors.ChallengeError:
                state["wall"] = "checkpoint"
                return
            except Exception:
                state["wall"] = "unknown"
                return
            if not isinstance(html, str):
                state["wall"] = "unknown"
                return

            try:
                wall = detect_wall(page_url, html)
            except errors.ChallengeError:
                state["wall"] = "checkpoint"
                return
            except Exception:
                state["wall"] = "unknown"
                return
            if wall == "checkpoint":
                state["wall"] = wall
                return

            # Only a recognized login wall or logged-in page awaiting cookies is transient.
            if wall is None and not any(marker in html.casefold() for marker in _LOGGED_IN_MARKERS):
                state["wall"] = "unknown"
                return

            if wall is None:
                if trusted_page_url(page, allow_login=False) is None:
                    return
                try:
                    trusted_cookies = _cookie_jar(page.context.cookies(_THREADS_COOKIE_URLS))
                except errors.ChallengeError:
                    state["wall"] = "checkpoint"
                    return
                except Exception:
                    state["wall"] = "unknown"
                    return
                try:
                    logged_in = looks_logged_in(html, trusted_cookies)
                except errors.ChallengeError:
                    state["wall"] = "checkpoint"
                    return
                except Exception:
                    state["wall"] = "unknown"
                    return
                if logged_in:
                    if trusted_page_url(page, allow_login=False) is None:
                        return
                    try:
                        user_agent = page.evaluate("navigator.userAgent")
                    except errors.ChallengeError:
                        state["wall"] = "checkpoint"
                        return
                    except Exception:
                        state["wall"] = "unknown"
                        return
                    if not isinstance(user_agent, str) or not user_agent:
                        state["wall"] = "unknown"
                        return
                    state["user_agent"] = user_agent

                    if trusted_page_url(page, allow_login=False) is None:
                        return
                    try:
                        page.wait_for_timeout(2_000)
                    except errors.ChallengeError:
                        state["wall"] = "checkpoint"
                        return
                    except Exception:
                        state["wall"] = "unknown"
                        return

                    if trusted_page_url(page, allow_login=False) is None:
                        return
                    try:
                        attach_request_listener(page)
                    except errors.ChallengeError:
                        state["wall"] = "checkpoint"
                        return
                    try:
                        harvest_wall = _best_effort_harvest_navigation(page)
                    except errors.ChallengeError:
                        state["wall"] = "checkpoint"
                        return
                    except Exception:
                        state["wall"] = "unknown"
                        return
                    if harvest_wall != "clean":
                        state["wall"] = harvest_wall
                        return

                    if trusted_page_url(page, allow_login=False) is None:
                        return
                    try:
                        final_cookies = _cookie_jar(page.context.cookies(_THREADS_COOKIE_URLS))
                    except errors.ChallengeError:
                        state["wall"] = "checkpoint"
                        return
                    except Exception:
                        state["wall"] = "unknown"
                        return
                    try:
                        final_wall = _page_wall(page)
                    except errors.ChallengeError:
                        state["wall"] = "checkpoint"
                        return
                    except Exception:
                        state["wall"] = "unknown"
                        return
                    if final_wall != "clean":
                        state["wall"] = final_wall
                        return
                    if trusted_page_url(page, allow_login=False) is None:
                        return
                    if not _REQUIRED_LOGIN_COOKIES.issubset(final_cookies):
                        state["wall"] = "unknown"
                        return
                    state["trusted_cookies"] = final_cookies
                    state["logged_in"] = True
                    return

            if trusted_page_url(page, allow_login=True) is None:
                return
            try:
                page.wait_for_timeout(2_000)
            except errors.ChallengeError:
                state["wall"] = "checkpoint"
                return
            except Exception:
                state["wall"] = "unknown"
                return
        print("Timed out waiting for Threads login.", file=sys.stderr)

    try:
        with _isolated_browser_cache():
            with _build_stealth_session(
                profile,
                profile_dir_override=profile_dir_override,
            ) as browser:
                browser.fetch(
                    _THREADS_HOME,
                    page_action=wait_for_login,
                    timeout=60_000,
                )

        request_listener_active = False

        if state["wall"] == "checkpoint":
            raise errors.ChallengeError(
                "Threads presented an account checkpoint; resolve it manually and do not retry"
            )
        if not state["logged_in"]:
            return False

        # The callback retained only required cookies from URL-scoped, validated records.
        persisted_cookies = state["trusted_cookies"]
        if not isinstance(persisted_cookies, Mapping):
            return False
        cookies = {
            name: value
            for name, value in persisted_cookies.items()
            if (
                isinstance(name, str)
                and name in _REQUIRED_LOGIN_COOKIES
                and isinstance(value, str)
                and value
            )
        }
        if not _REQUIRED_LOGIN_COOKIES.issubset(cookies):
            return False
        user_agent = state["user_agent"]
        if not isinstance(user_agent, str) or not user_agent:
            return False

        try:
            harvested_doc_ids = docids.harvest_from_browser(captured_projections.values())
        finally:
            # Only projections exist here, but none survive credential construction.
            captured_projections.clear()
        credential = auth.SessionCredential(
            sessionid=cookies["sessionid"],
            ds_user_id=cookies["ds_user_id"],
            csrftoken=cookies["csrftoken"],
            user_agent=user_agent,
            doc_ids=harvested_doc_ids,
            features=dict(gql.DEFAULT_FEATURES),
        )
        auth.save_session(
            profile,
            credential,
            profile_dir_override=profile_dir_override,
        )
        return True
    finally:
        request_listener_active = False
        captured_projections.clear()


def run_setup(*, force: bool = False) -> None:
    """Run Scrapling's installer in-process inside the isolated browser cache."""

    with _isolated_browser_cache():
        try:
            from scrapling.cli import main as scrapling_main
        except ImportError as exc:
            raise errors.BrowserSetupError(
                "browser support is not installed; install agentic-threads[browser]"
            ) from exc

        arguments = ["install"]
        if force:
            arguments.append("--force")
        try:
            scrapling_main.main(
                args=arguments,
                prog_name="scrapling",
                standalone_mode=False,
            )
        except Exception as exc:
            raise errors.BrowserSetupError(
                f"Scrapling browser installation failed: {type(exc).__name__}"
            ) from exc
