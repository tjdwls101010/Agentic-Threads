"""Persisted-query defaults and conservative live doc-id re-anchoring."""

from __future__ import annotations

import html
import json
import re
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from functools import wraps
from urllib.parse import urljoin, urlsplit

import httpx

from . import config, errors, gql

# Re-verified against authenticated read responses on 2026-07-23. These rotate and
# are fallbacks only; a value harvested from the current session takes precedence.
DEFAULT_DOC_IDS: dict[str, str] = {
    "feed": "27850575591298078",
    "profile": "27315641728135541",
    "profile_threads": "28037669779182544",
    "profile_threads_page": "28437090222560814",
    "profile_replies": "27873042895625707",
    "profile_replies_page": "38287806247476832",
    "post": "27618162774505383",
    "post_replies": "27810629915236396",
    "post_search": "27495177273458101",
    "people_search": "27962697876655098",
    "followers": "27390125367306731",
    "following": "26705592482449608",
}

OPERATION_TO_KEY: dict[str, str] = {
    "BarcelonaFeedPaginationDirectQuery": "feed",
    "BarcelonaProfilePageDirectQuery": "profile",
    "BarcelonaProfileThreadsTabDirectQuery": "profile_threads",
    "BarcelonaProfileThreadsTabRefetchableDirectQuery": "profile_threads_page",
    "BarcelonaProfileRepliesTabDirectQuery": "profile_replies",
    "BarcelonaProfileRepliesTabRefetchableDirectQuery": "profile_replies_page",
    "BarcelonaPostColumnPageQuery": "post",
    "BarcelonaPostPageDirectQuery": "post_replies",
    "BarcelonaSearchResultsQuery": "post_search",
    "useBarcelonaAccountSearchGraphQLDataSourceQuery": "people_search",
    "BarcelonaFriendshipsFollowersTabQuery": "followers",
    "BarcelonaFriendshipsFollowingTabQuery": "following",
}

_DOC_ID_RE = re.compile(r"^[0-9]+$")


def _valid_doc_id(value: object) -> str | None:
    if isinstance(value, str) and _DOC_ID_RE.fullmatch(value):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    return None


def merge_doc_ids(harvested: Mapping[str, object] | None = None) -> dict[str, str]:
    """Fill missing live values from shipped defaults without mutating either input."""
    merged = dict(DEFAULT_DOC_IDS)
    if not harvested:
        return merged
    for supplied_key, supplied_value in harvested.items():
        key = OPERATION_TO_KEY.get(supplied_key, supplied_key)
        doc_id = _valid_doc_id(supplied_value)
        if key in DEFAULT_DOC_IDS and doc_id is not None:
            merged[key] = doc_id
    return merged


def harvest_from_browser(
    projections: Iterable[object] | Mapping[str, object] | None,
) -> dict[str, str]:
    """Map exact minimized browser projections to their logical doc-ID keys.

    Each accepted mapping contains only a known ``operation`` string and an
    ASCII-decimal ``doc_id`` string. Defaults are intentionally not added here,
    so a future package upgrade can replace a stale fallback that was never
    actually harvested for this session.
    """
    if projections is None or isinstance(projections, (str, bytes)):
        return {}
    if isinstance(projections, Mapping):
        candidates: Iterable[object] = (projections,)
    elif isinstance(projections, Iterable):
        candidates = projections
    else:
        return {}

    observed: dict[str, str] = {}
    for projection in candidates:
        if not isinstance(projection, Mapping):
            continue
        if set(projection) != {"operation", "doc_id"}:
            continue
        operation = projection["operation"]
        doc_id = projection["doc_id"]
        if (
            not isinstance(operation, str)
            or operation not in OPERATION_TO_KEY
            or not isinstance(doc_id, str)
            or _DOC_ID_RE.fullmatch(doc_id) is None
        ):
            continue
        observed[OPERATION_TO_KEY[operation]] = doc_id
    return observed


# Browser-free re-anchor ---------------------------------------------------------

_THREADS_HOME = f"{gql.THREADS_ORIGIN}/"
_ROUTE_DEFINITION_URL = f"{gql.THREADS_ORIGIN}/ajax/route-definition/"
_ROUTE_DEFINITIONS = (
    ("/", "comet.threads.BarcelonaHomeRouteV2"),
    ("/@threads", "comet.threads.BarcelonaProfileThreadsColumnRoute"),
    ("/@threads/replies", "comet.threads.BarcelonaProfileRepliesColumnRoute"),
    (
        "/search?q=threads&serp_type=default",
        "comet.threads.BarcelonaSearchResultsColumnRoute",
    ),
)
_ANTI_JSON_PREFIX = "for (;;);"
_INITIAL_TOKEN_PATTERNS = (
    re.compile(
        r'"DTSGInitialData"\s*,\s*\[\]\s*,\s*\{[^{}]{0,2048}?'
        r'"token"\s*:\s*("(?:\\.|[^"\\])*")'
    ),
    re.compile(
        r'"LSD"\s*,\s*\[\]\s*,\s*\{[^{}]{0,2048}?'
        r'"token"\s*:\s*("(?:\\.|[^"\\])*")'
    ),
)
_TRUSTED_ASSET_ROOTS = (
    "threads.com",
    "threads.net",
    "cdninstagram.com",
    "fbcdn.net",
)
_HTML_DOCUMENT_RE = re.compile(
    r"^(?:<!doctype\s+html(?:\s|>)|<html(?:\s|>))",
    re.IGNORECASE,
)
_HTML_FRAGMENT_RE = re.compile(r"^<(?:head|body|form)(?:\s|>)", re.IGNORECASE)
_CHECKPOINT_HTML_MARKERS = (
    "challenge_required",
    "checkpoint_required",
    "consent_required",
    "cometcheckpointrootquery",
    "checkpoint_url",
)
_LOGIN_HTML_MARKERS = (
    "caa_login_form_data",
    "caafetaaymhpasswordentryquery",
    "comet_headless_login",
    "login_required",
)
_CHECKPOINT_FORM_RE = re.compile(
    r"<form\b[^>]{0,2048}\baction\s*=\s*['\"]"
    r"[^'\"]*/(?:checkpoint|challenge)(?:[/?'\"])",
    re.IGNORECASE,
)
_LOGIN_FORM_RE = re.compile(
    r"<form\b[^>]{0,2048}\baction\s*=\s*['\"]"
    r"[^'\"]*/(?:accounts/)?login(?:[/?'\"])",
    re.IGNORECASE,
)
_LOGGED_OUT_HTML_RE = re.compile(
    r"['\"]is_logged_in['\"]\s*:\s*false",
    re.IGNORECASE,
)
_MAX_ASSETS = 64
_MAX_ASSET_DEPTH = 2
_QUOTED_RESOURCE_RE = re.compile(r"['\"]([^'\"\r\n]{1,2048})['\"]")
_OPERATION_ALTERNATION = "|".join(
    re.escape(operation) for operation in sorted(OPERATION_TO_KEY, key=len, reverse=True)
)
_PRELOADER_OPERATION_RE = re.compile(
    rf"(?<![A-Za-z0-9])({_OPERATION_ALTERNATION})"
    r"(?=(?:RelayPreloader)?(?:[^A-Za-z0-9]|$))"
)
_ID_FIELD = r"(?:id|doc_id|docId|query_id|queryId)"
_NAME_FIELD = r"(?:name|operation|operation_name|operationName)"
_PAIR_WINDOW = r"(?:[^{}]|\{[^{}]{0,160}\}){0,320}?"
_ID_THEN_OPERATION_RE = re.compile(
    rf"(?<![A-Za-z0-9_])['\"]?{_ID_FIELD}['\"]?(?![A-Za-z0-9_])"
    rf"\s*:\s*['\"]?([0-9]+)['\"]?{_PAIR_WINDOW}"
    rf"(?<![A-Za-z0-9_])['\"]?{_NAME_FIELD}['\"]?(?![A-Za-z0-9_])"
    rf"\s*:\s*['\"]?({_OPERATION_ALTERNATION})['\"]?",
    re.DOTALL,
)
_OPERATION_THEN_ID_RE = re.compile(
    rf"(?<![A-Za-z0-9_])['\"]?{_NAME_FIELD}['\"]?(?![A-Za-z0-9_])"
    rf"\s*:\s*['\"]?({_OPERATION_ALTERNATION})['\"]?{_PAIR_WINDOW}"
    rf"(?<![A-Za-z0-9_])['\"]?{_ID_FIELD}['\"]?(?![A-Za-z0-9_])"
    rf"\s*:\s*['\"]?([0-9]+)['\"]?",
    re.DOTALL,
)


def _normalized_artifact_text(text: str) -> str:
    return (
        html.unescape(text)
        .replace("\\u002F", "/")
        .replace("\\u003A", ":")
        .replace("\\u0026", "&")
        .replace("\\/", "/")
        .replace('\\"', '"')
        .replace("\\'", "'")
    )


def _add_reanchor_pairs(text: str, candidates: dict[str, set[str]]) -> None:
    normalized = _normalized_artifact_text(text)
    for doc_id, operation in _ID_THEN_OPERATION_RE.findall(normalized):
        candidates[OPERATION_TO_KEY[operation]].add(doc_id)
    for operation, doc_id in _OPERATION_THEN_ID_RE.findall(normalized):
        candidates[OPERATION_TO_KEY[operation]].add(doc_id)


def _extract_initial_tokens(text: str) -> tuple[str, str] | None:
    normalized = html.unescape(text)
    tokens: list[str] = []
    for pattern in _INITIAL_TOKEN_PATTERNS:
        values: set[str] = set()
        for match in pattern.finditer(normalized):
            try:
                value = json.loads(match.group(1))
            except (TypeError, ValueError):
                continue
            if isinstance(value, str) and value:
                values.add(value)
        if len(values) != 1:
            return None
        tokens.append(next(iter(values)))
    return tokens[0], tokens[1]


def _iter_json_line_values(text: str) -> Iterator[object]:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        while line.startswith(_ANTI_JSON_PREFIX):
            line = line[len(_ANTI_JSON_PREFIX) :].lstrip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except (RecursionError, ValueError):
            continue


def _iter_preloader_records(value: object) -> Iterator[Mapping[str, object]]:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            if "preloaderID" in current and "queryID" in current:
                yield current
                continue
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def _iter_preloaders(payload: object) -> Iterator[Mapping[str, object]]:
    stack = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            for key, value in current.items():
                if key == "preloaders":
                    yield from _iter_preloader_records(value)
                else:
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)


def _preloader_operation(preloader_id: object) -> str | None:
    if not isinstance(preloader_id, str):
        return None
    operations = set(_PRELOADER_OPERATION_RE.findall(preloader_id))
    if len(operations) != 1:
        return None
    return next(iter(operations))


def _add_route_definition_pairs(text: str, candidates: dict[str, set[str]]) -> None:
    for payload in _iter_json_line_values(text):
        for preloader in _iter_preloaders(payload):
            operation = _preloader_operation(preloader.get("preloaderID"))
            query_id = preloader.get("queryID")
            if (
                operation is not None
                and isinstance(query_id, str)
                and _DOC_ID_RE.fullmatch(query_id)
            ):
                candidates[OPERATION_TO_KEY[operation]].add(query_id)


def _trusted_asset_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or not parts.hostname:
        return False
    host = parts.hostname.lower().rstrip(".")
    return any(host == root or host.endswith(f".{root}") for root in _TRUSTED_ASSET_ROOTS)


def _pace_request(request: httpx.Request) -> None:
    if not _trusted_asset_url(str(request.url)):
        raise httpx.RequestError("refused an untrusted re-anchor request", request=request)
    time.sleep(max(1.0, config.MIN_REQUEST_PAUSE_SECONDS))


def _reanchor_html_wall(response: httpx.Response) -> str | None:
    """Classify only structurally recognizable HTML login/checkpoint responses."""

    text = response.text
    leading = text.lstrip("\ufeff \t\r\n")
    content_type = response.headers.get("content-type", "").partition(";")[0].strip().casefold()
    if not _HTML_DOCUMENT_RE.match(leading) and not (
        content_type in {"text/html", "application/xhtml+xml"} and _HTML_FRAGMENT_RE.match(leading)
    ):
        return None

    lowered = text.casefold()
    if any(marker in lowered for marker in _CHECKPOINT_HTML_MARKERS):
        return "checkpoint"
    if _CHECKPOINT_FORM_RE.search(text):
        return "checkpoint"
    if any(marker in lowered for marker in _LOGIN_HTML_MARKERS):
        return "login"
    if _LOGGED_OUT_HTML_RE.search(text) or _LOGIN_FORM_RE.search(text):
        return "login"
    return None


def _classify_reanchor_response(
    response: httpx.Response,
    *,
    optional: bool,
) -> bool:
    """Return whether an optional artifact is absent, or raise a package-owned failure."""
    status_code = response.status_code
    if 200 <= status_code < 300:
        wall = _reanchor_html_wall(response)
        if wall == "checkpoint":
            raise errors.ChallengeError(
                "Threads presented an account challenge; resolve it manually and do not retry"
            )
        if wall == "login":
            raise errors.SessionExpiredError(
                "Threads returned a logged-out or soft-locked response"
            )
        return False
    if status_code in {401, 403}:
        raise errors.SessionExpiredError(
            "Threads rejected the saved session during doc-ID discovery"
        )
    if status_code == 429:
        raise errors.RateLimitedError("Threads rate-limited doc-ID discovery")
    if optional and status_code in {404, 410}:
        return True
    raise errors.AgenticThreadsError(f"doc-ID discovery failed with HTTP status {status_code}")


def _translate_reanchor_transport_failures(
    operation: Callable[..., dict[str, str]],
) -> Callable[..., dict[str, str]]:
    @wraps(operation)
    def translated(*args: object, **kwargs: object) -> dict[str, str]:
        failure: errors.AgenticThreadsError | None = None
        try:
            return operation(*args, **kwargs)
        except httpx.HTTPError as exc:
            failure = errors.AgenticThreadsError(
                f"doc-ID discovery transport failed with {type(exc).__name__}"
            )
        raise failure

    return translated


def _discover_javascript_urls(text: str, base_url: str) -> list[str]:
    normalized = _normalized_artifact_text(text)
    discovered: list[str] = []
    seen: set[str] = set()
    for match in _QUOTED_RESOURCE_RE.finditer(normalized):
        candidate = match.group(1).strip()
        try:
            path = urlsplit(candidate).path.lower()
        except ValueError:
            continue
        if ".js" not in path and "/rsrc.php" not in path:
            continue
        if candidate.startswith("//"):
            candidate = f"https:{candidate}"
        absolute = urljoin(base_url, candidate)
        if absolute in seen or not _trusted_asset_url(absolute):
            continue
        seen.add(absolute)
        discovered.append(absolute)
    return discovered


def _thread_scoped_cookies(
    sessionid: str,
    ds_user_id: str,
    csrftoken: str,
) -> httpx.Cookies:
    cookies = httpx.Cookies()
    for domain in (".threads.com", ".threads.net"):
        cookies.set("sessionid", sessionid, domain=domain, path="/")
        cookies.set("ds_user_id", ds_user_id, domain=domain, path="/")
        cookies.set("csrftoken", csrftoken, domain=domain, path="/")
    return cookies


@_translate_reanchor_transport_failures
def reanchor_via_main_js(
    sessionid: str,
    ds_user_id: str,
    csrftoken: str,
    user_agent: str,
    *,
    timeout: float = 20.0,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, str]:
    """Find fresh known doc IDs through route definitions, HTML, and bounded JS.

    The result contains only unambiguous pairs found in current artifacts. It does
    not silently insert defaults and never starts a browser; callers apply
    :func:`merge_doc_ids` after deciding how to persist the fresh subset.
    """
    candidates = {key: set() for key in DEFAULT_DOC_IDS}
    cookies = _thread_scoped_cookies(sessionid, ds_user_id, csrftoken)
    with httpx.Client(
        cookies=cookies,
        headers={
            "accept": ("text/html,application/xhtml+xml,application/javascript,*/*;q=0.8"),
            "referer": _THREADS_HOME,
            "user-agent": user_agent,
        },
        event_hooks={"request": [_pace_request]},
        follow_redirects=True,
        timeout=timeout,
        transport=transport,
    ) as client:
        home_response = client.get(_THREADS_HOME, headers={"x-csrftoken": csrftoken})
        _classify_reanchor_response(home_response, optional=False)

        initial_tokens = _extract_initial_tokens(home_response.text)
        if initial_tokens is not None:
            fb_dtsg, lsd = initial_tokens
            jazoest = "2" + str(sum(ord(character) for character in fb_dtsg))
            route_headers = {
                "content-type": "application/x-www-form-urlencoded",
                "origin": gql.THREADS_ORIGIN,
                "referer": _THREADS_HOME,
                "user-agent": user_agent,
                "x-csrftoken": csrftoken,
                "x-fb-lsd": lsd,
                "x-ig-app-id": gql.THREADS_WEB_APP_ID,
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
            }
            for route_url, route_name in _ROUTE_DEFINITIONS:
                form = {
                    "route_url": route_url,
                    "routing_namespace": "barcelona_web",
                    "__user": "0",
                    "fb_dtsg": fb_dtsg,
                    "jazoest": jazoest,
                    "lsd": lsd,
                    "__a": "1",
                    "__req": "1",
                    "__comet_req": "29",
                    "__crn": route_name,
                }
                route_response = client.post(
                    _ROUTE_DEFINITION_URL,
                    data=form,
                    headers=route_headers,
                    follow_redirects=False,
                )
                if _classify_reanchor_response(route_response, optional=True):
                    continue
                _add_route_definition_pairs(route_response.text, candidates)

        _add_reanchor_pairs(home_response.text, candidates)
        queue = deque(
            (url, 0)
            for url in _discover_javascript_urls(
                home_response.text,
                str(home_response.url),
            )
        )
        fetched: set[str] = set()
        while queue and len(fetched) < _MAX_ASSETS:
            asset_url, depth = queue.popleft()
            if asset_url in fetched:
                continue
            fetched.add(asset_url)
            response = client.get(asset_url)
            if _classify_reanchor_response(response, optional=True):
                continue
            _add_reanchor_pairs(response.text, candidates)
            if depth >= _MAX_ASSET_DEPTH:
                continue
            for nested_url in _discover_javascript_urls(response.text, asset_url):
                if nested_url not in fetched:
                    queue.append((nested_url, depth + 1))

    return {key: next(iter(values)) for key, values in candidates.items() if len(values) == 1}
