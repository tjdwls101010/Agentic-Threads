from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest

from agentic_threads import docids, errors

_SESSION_ID = "synthetic-session"
_USER_ID = "10001"
_CSRF_TOKEN = "synthetic-csrf"
_USER_AGENT = "synthetic-test-agent/1.0"
_FB_DTSG = "synthetic-dtsg:token"
_LSD = "synthetic-lsd-token"
_ROUTE_PATHS = (
    "/",
    "/@threads",
    "/@threads/replies",
    "/search?q=threads&serp_type=default",
)


def _home_html(
    *javascript_urls: str,
    tokens: bool = True,
    extra: str = "",
) -> str:
    parts = ["<html><head>"]
    if tokens:
        parts.extend(
            (
                f'<script>["DTSGInitialData",[],{{"token":{json.dumps(_FB_DTSG)}}}]</script>',
                f'<script>["LSD",[],{{"token":{json.dumps(_LSD)}}}]</script>',
            )
        )
    parts.extend(f'<script src="{url}"></script>' for url in javascript_urls)
    parts.extend((extra, "</head><body></body></html>"))
    return "\n".join(parts)


def _preloader(operation: str, query_id: object) -> dict[str, object]:
    return {
        "actorID": _USER_ID,
        "preloaderID": f"adp_{operation}RelayPreloader_synthetic",
        "queryID": query_id,
        "variables": {},
    }


def _route_stream(*preloaders: dict[str, object], prefixed: bool = True) -> str:
    payload = {
        "payload": {
            "payloads": {
                "/synthetic": {
                    "result": {
                        "exports": {
                            "preloaders": list(preloaders),
                        }
                    }
                }
            }
        }
    }
    prefix = "for (;;);" if prefixed else ""
    return prefix + json.dumps(payload, separators=(",", ":"))


def _invoke(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleeps: list[float] | None = None,
) -> tuple[dict[str, str], list[float]]:
    recorded_sleeps = [] if sleeps is None else sleeps
    monkeypatch.setattr(docids.time, "sleep", recorded_sleeps.append)
    result = docids.reanchor_via_main_js(
        _SESSION_ID,
        _USER_ID,
        _CSRF_TOKEN,
        _USER_AGENT,
        transport=httpx.MockTransport(handler),
    )
    return result, recorded_sleeps


def _failure_diagnostic(exception: BaseException) -> str:
    pending = [exception]
    seen: set[int] = set()
    diagnostics: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        diagnostics.extend(
            (
                str(current),
                repr(current),
                "".join(traceback.format_exception(current)),
                repr(getattr(current, "request", None)),
            )
        )
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)
    return "\n".join(diagnostics)


def test_browser_harvest_maps_exact_minimized_projections():
    assert docids.harvest_from_browser(
        [
            {
                "operation": "BarcelonaProfilePageDirectQuery",
                "doc_id": "71001",
            },
            {
                "operation": "BarcelonaFeedPaginationDirectQuery",
                "doc_id": "71002",
            },
        ]
    ) == {"profile": "71001", "feed": "71002"}


def test_browser_harvest_rejects_raw_nested_and_attribute_artifacts_without_traversal():
    operation = "BarcelonaProfilePageDirectQuery"
    raw_form = f"fb_api_req_friendly_name={operation}&doc_id=72001"

    class NestedProjection(dict):
        def items(self):
            raise AssertionError("nested projection must not be traversed")

    class AttributeArtifact:
        @property
        def body(self):
            raise AssertionError("artifact attributes must not be read")

    artifacts = [
        raw_form,
        raw_form.encode(),
        f"https://www.threads.net/graphql/query?{raw_form}",
        json.dumps({"operation": operation, "doc_id": "72001"}),
        {"request": NestedProjection(operation=operation, doc_id="72001")},
        {
            "headers": {"x-fb-friendly-name": operation},
            "body": "doc_id=72001",
        },
        {"operation": operation, "doc_id": "72001", "headers": {}},
        {"operationName": operation, "docId": "72001"},
        AttributeArtifact(),
    ]

    assert docids.harvest_from_browser(artifacts) == {}
    assert docids.harvest_from_browser(raw_form) == {}
    assert docids.harvest_from_browser(raw_form.encode()) == {}


@pytest.mark.parametrize(
    ("operation", "doc_id"),
    [
        ("UnknownThreadsOperation", "73001"),
        (True, "73001"),
        (1, "73001"),
        ("BarcelonaProfilePageDirectQuery", True),
        ("BarcelonaProfilePageDirectQuery", 73001),
        ("BarcelonaProfilePageDirectQuery", ""),
        ("BarcelonaProfilePageDirectQuery", "-1"),
        ("BarcelonaProfilePageDirectQuery", "73.001"),
        ("BarcelonaProfilePageDirectQuery", "７３００１"),
        ("BarcelonaProfilePageDirectQuery", " 73001"),
    ],
)
def test_browser_harvest_rejects_unknown_non_string_and_malformed_values(operation, doc_id):
    assert docids.harvest_from_browser([{"operation": operation, "doc_id": doc_id}]) == {}


def test_route_definition_contract_returns_only_exact_direct_pairs(monkeypatch, capsys):
    route_payloads = {
        "/": [_preloader("BarcelonaHomeContentQuery", "90000")],
        "/@threads": [
            _preloader("BarcelonaProfilePageDirectQuery", "91001"),
            _preloader("BarcelonaProfileThreadsTabDirectQuery", "91002"),
        ],
        "/@threads/replies": [_preloader("BarcelonaProfileRepliesTabDirectQuery", "91003")],
        "/search?q=threads&serp_type=default": [_preloader("BarcelonaSearchResultsQuery", "91004")],
    }
    requests: list[httpx.Request] = []
    forms: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, text=_home_html())
        form = parse_qs(request.content.decode(), keep_blank_values=True)
        forms.append(form)
        route_url = form["route_url"][0]
        return httpx.Response(200, text=_route_stream(*route_payloads[route_url]))

    result, sleeps = _invoke(monkeypatch, handler)

    assert result == {
        "profile": "91001",
        "profile_threads": "91002",
        "profile_replies": "91003",
        "post_search": "91004",
    }
    assert "feed" not in result
    assert all(value not in docids.DEFAULT_DOC_IDS.values() for value in result.values())
    assert [form["route_url"][0] for form in forms] == list(_ROUTE_PATHS)
    assert sleeps == [1.0] * 5

    required_fields = {
        "route_url",
        "routing_namespace",
        "__user",
        "fb_dtsg",
        "jazoest",
        "lsd",
        "__a",
        "__req",
        "__comet_req",
        "__crn",
    }
    expected_jazoest = "2" + str(sum(ord(character) for character in _FB_DTSG))
    for form in forms:
        assert set(form) == required_fields
        assert form["routing_namespace"] == ["barcelona_web"]
        assert form["__user"] == ["0"]
        assert form["fb_dtsg"] == [_FB_DTSG]
        assert form["jazoest"] == [expected_jazoest]
        assert form["lsd"] == [_LSD]
        assert form["__a"] == ["1"]
        assert form["__req"] == ["1"]
        assert form["__comet_req"] == ["29"]
        assert form["__crn"][0]

    route_request = requests[1]
    assert str(route_request.url) == "https://www.threads.com/ajax/route-definition/"
    assert route_request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert route_request.headers["x-csrftoken"] == _CSRF_TOKEN
    assert route_request.headers["x-fb-lsd"] == _LSD
    assert route_request.headers["x-ig-app-id"] == "238260118697367"
    assert route_request.headers["origin"] == "https://www.threads.com"
    assert route_request.headers["referer"] == "https://www.threads.com/"
    assert route_request.headers["user-agent"] == _USER_AGENT
    assert route_request.headers["sec-fetch-dest"] == "empty"
    assert route_request.headers["sec-fetch-mode"] == "cors"
    assert route_request.headers["sec-fetch-site"] == "same-origin"
    assert f"sessionid={_SESSION_ID}" in route_request.headers["cookie"]
    assert f"ds_user_id={_USER_ID}" in route_request.headers["cookie"]
    assert _FB_DTSG not in repr(result)
    assert _LSD not in repr(result)
    captured = capsys.readouterr()
    assert _FB_DTSG not in captured.out + captured.err
    assert _LSD not in captured.out + captured.err


def test_anti_json_line_stream_skips_malformed_lines_and_reads_plain_lines(monkeypatch):
    profile_line = _route_stream(_preloader("BarcelonaProfilePageDirectQuery", "92001"))
    replies_line = _route_stream(
        _preloader("BarcelonaProfileRepliesTabDirectQuery", "92002"),
        prefixed=False,
    )
    response_text = f"not-json\n{profile_line}\n{replies_line}\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_home_html())
        form = parse_qs(request.content.decode())
        if form["route_url"] == ["/"]:
            return httpx.Response(200, text=response_text)
        return httpx.Response(200, text="{}")

    result, _ = _invoke(monkeypatch, handler)

    assert result == {"profile": "92001", "profile_replies": "92002"}


def test_route_parser_rejects_unknown_near_match_malformed_and_cross_field_pairs(monkeypatch):
    profile_operation = "BarcelonaProfilePageDirectQuery"
    threads_operation = "BarcelonaProfileThreadsTabDirectQuery"
    invalid_preloaders = (
        _preloader("BarcelonaHomeContentQuery", "93001"),
        _preloader(f"{profile_operation}Substitute", "93002"),
        {
            "preloaderID": "adp_UnknownQueryRelayPreloader_synthetic",
            "queryID": "93003",
            "operation": profile_operation,
        },
        _preloader(profile_operation, "not-numeric"),
        _preloader(profile_operation, 93004),
        {"preloaderID": 93005, "queryID": "93005"},
        {
            "preloaderID": f"adp_{profile_operation}RelayPreloader_synthetic",
        },
        {"queryID": "93008"},
        {
            "preloaderID": (
                f"adp_{profile_operation}RelayPreloader_{threads_operation}RelayPreloader_synthetic"
            ),
            "queryID": "93006",
        },
    )
    outside_preloaders = json.dumps(
        {
            "payload": {
                "preloaderID": f"adp_{profile_operation}RelayPreloader_synthetic",
                "queryID": "93007",
            }
        }
    )
    response_text = _route_stream(*invalid_preloaders) + "\n" + outside_preloaders

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_home_html())
        form = parse_qs(request.content.decode())
        if form["route_url"] == ["/"]:
            return httpx.Response(200, text=response_text)
        return httpx.Response(200, text="{}")

    result, _ = _invoke(monkeypatch, handler)

    assert result == {}
    assert "feed" not in result


def test_multiple_query_ids_for_one_logical_key_are_rejected(monkeypatch):
    response_text = _route_stream(
        _preloader("BarcelonaProfilePageDirectQuery", "94001"),
        _preloader("BarcelonaProfilePageDirectQuery", "94002"),
        _preloader("BarcelonaProfileThreadsTabDirectQuery", "94003"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_home_html())
        form = parse_qs(request.content.decode())
        if form["route_url"] == ["/@threads"]:
            return httpx.Response(200, text=response_text)
        return httpx.Response(200, text="{}")

    result, _ = _invoke(monkeypatch, handler)

    assert result == {"profile_threads": "94003"}


@pytest.mark.parametrize("phase", ["home", "route", "asset"])
@pytest.mark.parametrize(
    ("status_code", "expected_error", "expected_exit"),
    [
        (401, errors.SessionExpiredError, 2),
        (403, errors.SessionExpiredError, 2),
        (429, errors.RateLimitedError, 3),
    ],
)
def test_auth_and_rate_limit_failures_abort_exactly_at_the_failing_phase(
    monkeypatch,
    capsys,
    phase,
    status_code,
    expected_error,
    expected_exit,
):
    seen: list[str] = []
    sleeps: list[float] = []
    body_secret = "synthetic-response-body-secret"
    header_secret = "synthetic-response-header-secret"
    asset_query_secret = "synthetic-asset-query-secret"
    failing_asset = f"/failing.js?access_token={asset_query_secret}"

    def failed_response() -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={
                "set-cookie": f"private={header_secret}",
                "x-private-token": header_secret,
            },
            text=f"{body_secret} {_SESSION_ID} {_CSRF_TOKEN} {_FB_DTSG} {_LSD}",
        )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/":
            seen.append("home")
            if phase == "home":
                return failed_response()
            return httpx.Response(
                200,
                text=_home_html(
                    failing_asset,
                    "/must-not-fetch.js",
                    tokens=phase == "route",
                ),
            )
        if request.method == "POST":
            route_url = parse_qs(request.content.decode())["route_url"][0]
            seen.append(f"route:{route_url}")
            assert phase == "route"
            assert route_url == "/"
            return failed_response()
        seen.append(f"asset:{request.url.path}")
        assert phase == "asset"
        assert request.url.path == "/failing.js"
        assert asset_query_secret in str(request.url)
        return failed_response()

    with pytest.raises(expected_error) as caught:
        _invoke(monkeypatch, handler, sleeps=sleeps)

    assert type(caught.value) is expected_error
    assert caught.value.exit_code == expected_exit
    if expected_error is errors.RateLimitedError:
        assert caught.value.reset_at is None
    expected_seen = {
        "home": ["home"],
        "route": ["home", "route:/"],
        "asset": ["home", "asset:/failing.js"],
    }[phase]
    assert seen == expected_seen
    assert sleeps == [1.0] * len(expected_seen)
    assert caught.value.__cause__ is None
    diagnostic = _failure_diagnostic(caught.value)
    for secret in (
        body_secret,
        header_secret,
        asset_query_secret,
        failing_asset,
        _SESSION_ID,
        _USER_ID,
        _CSRF_TOKEN,
        _FB_DTSG,
        _LSD,
    ):
        assert secret not in diagnostic
    captured = capsys.readouterr()
    assert not captured.out
    assert not captured.err


@pytest.mark.parametrize("phase", ["home", "route", "asset"])
@pytest.mark.parametrize(
    (
        "status_code",
        "wall_name",
        "wall_marker",
        "form_action",
        "expected_error",
        "expected_message",
    ),
    [
        pytest.param(
            200,
            "login",
            "CAA_LOGIN_FORM_DATA",
            "/accounts/login/",
            errors.SessionExpiredError,
            "Threads returned a logged-out or soft-locked response",
            id="200-login-html",
        ),
        pytest.param(
            200,
            "checkpoint",
            "challenge_required",
            "/challenge/",
            errors.ChallengeError,
            "Threads presented an account challenge; resolve it manually and do not retry",
            id="200-checkpoint-html",
        ),
    ],
)
def test_successful_html_wall_aborts_exactly_at_the_failing_phase(
    monkeypatch,
    capsys,
    phase,
    status_code,
    wall_name,
    wall_marker,
    form_action,
    expected_error,
    expected_message,
):
    seen: list[str] = []
    sleeps: list[float] = []
    body_secret = f"synthetic-{wall_name}-response-body-secret"
    header_secret = f"synthetic-{wall_name}-response-header-secret"
    asset_query_secret = f"synthetic-{wall_name}-asset-query-secret"
    failing_asset = f"/failing.js?access_token={asset_query_secret}"
    wall_html = "\n".join(
        (
            "<!doctype html><html><head>",
            f'<script>["DTSGInitialData",[],{{"token":{json.dumps(_FB_DTSG)}}}]</script>',
            f'<script>["LSD",[],{{"token":{json.dumps(_LSD)}}}]</script>',
            f'<script src="{failing_asset}"></script>',
            '<script src="/must-not-fetch.js"></script>',
            "</head><body>",
            f'<form action="{form_action}">{wall_marker} {body_secret} '
            f"{_SESSION_ID} {_CSRF_TOKEN}</form>",
            '<script>const query={name:"BarcelonaProfilePageDirectQuery",id:"95999"};</script>',
            "</body></html>",
        )
    )
    for parser_name in (
        "_extract_initial_tokens",
        "_add_route_definition_pairs",
        "_add_reanchor_pairs",
        "_discover_javascript_urls",
    ):
        parser = getattr(docids, parser_name)

        def reject_wall_text(text, *args, _parser=parser, **kwargs):
            assert body_secret not in text
            return _parser(text, *args, **kwargs)

        monkeypatch.setattr(docids, parser_name, reject_wall_text)

    def failed_response() -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={
                "content-type": "text/html; charset=utf-8",
                "set-cookie": f"private={header_secret}",
                "x-private-token": header_secret,
            },
            text=wall_html,
        )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/":
            seen.append("home")
            if phase == "home":
                return failed_response()
            return httpx.Response(
                200,
                text=_home_html(
                    failing_asset,
                    "/must-not-fetch.js",
                    tokens=phase == "route",
                ),
            )
        if request.method == "POST":
            route_url = parse_qs(request.content.decode())["route_url"][0]
            seen.append(f"route:{route_url}")
            assert phase == "route"
            assert route_url == "/"
            return failed_response()
        seen.append(f"asset:{request.url.path}")
        assert phase == "asset"
        assert request.url.path == "/failing.js"
        assert asset_query_secret in str(request.url)
        return failed_response()

    with pytest.raises(expected_error) as caught:
        _invoke(monkeypatch, handler, sleeps=sleeps)

    assert type(caught.value) is expected_error
    assert caught.value.exit_code == 2
    assert str(caught.value) == expected_message
    expected_seen = {
        "home": ["home"],
        "route": ["home", "route:/"],
        "asset": ["home", "asset:/failing.js"],
    }[phase]
    assert seen == expected_seen
    assert sleeps == [1.0] * len(expected_seen)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    diagnostic = _failure_diagnostic(caught.value)
    for secret in (
        body_secret,
        header_secret,
        asset_query_secret,
        failing_asset,
        _SESSION_ID,
        _USER_ID,
        _CSRF_TOKEN,
        _FB_DTSG,
        _LSD,
    ):
        assert secret not in diagnostic
    captured = capsys.readouterr()
    assert not captured.out
    assert not captured.err


def test_javascript_wall_words_are_not_classified_without_an_html_body(monkeypatch):
    seen: list[str] = []
    marker_javascript = """
const wallWords = [
  "login",
  "challenge",
  "checkpoint",
  "login_required",
  "challenge_required",
  "checkpoint_required",
  "consent_required",
  "CAA_LOGIN_FORM_DATA",
  "CAAFetaAYMHPasswordEntryQuery",
  "CometCheckpointRootQuery",
  "checkpoint_url"
];
const state = {"is_logged_in": false};
const template = "<html>challenge_required CAA_LOGIN_FORM_DATA</html>";
const query = {name: "BarcelonaProfilePageDirectQuery", id: "96001"};
const nested = "/fresh.js";
"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            seen.append("home")
            return httpx.Response(200, text=_home_html("/markers.js", tokens=False))
        seen.append(f"asset:{request.url.path}")
        if request.url.path == "/markers.js":
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=marker_javascript,
            )
        assert request.url.path == "/fresh.js"
        return httpx.Response(
            200,
            headers={"content-type": "application/javascript"},
            text=('const query={name:"BarcelonaProfileThreadsTabDirectQuery",id:"96002"};'),
        )

    result, sleeps = _invoke(monkeypatch, handler)

    assert result == {"profile": "96001", "profile_threads": "96002"}
    assert seen == ["home", "asset:/markers.js", "asset:/fresh.js"]
    assert sleeps == [1.0, 1.0, 1.0]


@pytest.mark.parametrize("artifact_kind", ["route", "asset"])
@pytest.mark.parametrize("status_code", [404, 410])
def test_missing_artifact_status_continues_to_later_candidates(
    monkeypatch,
    artifact_kind,
    status_code,
):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/":
            seen.append("home")
            javascript_urls = () if artifact_kind == "route" else ("/missing.js", "/fresh.js")
            return httpx.Response(
                200,
                text=_home_html(
                    *javascript_urls,
                    tokens=artifact_kind == "route",
                ),
            )
        if request.method == "POST":
            route_url = parse_qs(request.content.decode())["route_url"][0]
            seen.append(f"route:{route_url}")
            assert artifact_kind == "route"
            if route_url == "/":
                return httpx.Response(status_code, text="stale route")
            if route_url == "/@threads":
                return httpx.Response(
                    200,
                    text=_route_stream(_preloader("BarcelonaProfilePageDirectQuery", "95001")),
                )
            return httpx.Response(200, text="{}")
        seen.append(f"asset:{request.url.path}")
        assert artifact_kind == "asset"
        if request.url.path == "/missing.js":
            return httpx.Response(status_code, text="stale asset")
        assert request.url.path == "/fresh.js"
        return httpx.Response(
            200,
            text='const query={name:"BarcelonaProfilePageDirectQuery",id:"95001"};',
        )

    result, sleeps = _invoke(monkeypatch, handler)

    assert result == {"profile": "95001"}
    expected_seen = (
        ["home", *(f"route:{route_path}" for route_path in _ROUTE_PATHS)]
        if artifact_kind == "route"
        else ["home", "asset:/missing.js", "asset:/fresh.js"]
    )
    assert seen == expected_seen
    assert sleeps == [1.0] * len(seen)


@pytest.mark.parametrize("phase", ["home", "route", "asset"])
@pytest.mark.parametrize("status_code", [400, 503])
def test_nonrecoverable_status_is_a_sanitized_package_error_without_follow_up(
    monkeypatch,
    capsys,
    phase,
    status_code,
):
    seen: list[str] = []
    sleeps: list[float] = []
    response_secret = "synthetic-nonrecoverable-response-secret"
    header_secret = "synthetic-nonrecoverable-header-secret"
    asset_query_secret = "synthetic-status-query-secret"
    failing_asset = f"/failing.js?access_token={asset_query_secret}"

    def failed_response() -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={"set-cookie": f"private={header_secret}"},
            text=f"{response_secret} {_SESSION_ID} {_CSRF_TOKEN}",
        )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/":
            seen.append("home")
            if phase == "home":
                return failed_response()
            return httpx.Response(
                200,
                text=_home_html(
                    failing_asset,
                    "/must-not-fetch.js",
                    tokens=phase == "route",
                ),
            )
        if request.method == "POST":
            route_url = parse_qs(request.content.decode())["route_url"][0]
            seen.append(f"route:{route_url}")
            assert phase == "route"
            assert route_url == "/"
        else:
            seen.append(f"asset:{request.url.path}")
            assert phase == "asset"
            assert request.url.path == "/failing.js"
            assert asset_query_secret in str(request.url)
        return failed_response()

    with pytest.raises(errors.AgenticThreadsError) as caught:
        _invoke(monkeypatch, handler, sleeps=sleeps)

    assert type(caught.value) is errors.AgenticThreadsError
    assert caught.value.exit_code == 1
    assert str(caught.value) == f"doc-ID discovery failed with HTTP status {status_code}"
    expected_seen = {
        "home": ["home"],
        "route": ["home", "route:/"],
        "asset": ["home", "asset:/failing.js"],
    }[phase]
    assert seen == expected_seen
    assert sleeps == [1.0] * len(expected_seen)
    assert caught.value.__cause__ is None
    diagnostic = _failure_diagnostic(caught.value)
    for secret in (
        response_secret,
        header_secret,
        asset_query_secret,
        failing_asset,
        _SESSION_ID,
        _USER_ID,
        _FB_DTSG,
        _LSD,
        _CSRF_TOKEN,
    ):
        assert secret not in diagnostic
    captured = capsys.readouterr()
    assert not captured.out
    assert not captured.err


@pytest.mark.parametrize("phase", ["home", "route", "asset"])
def test_transport_failure_is_a_sanitized_package_error_without_follow_up(
    monkeypatch,
    capsys,
    phase,
):
    seen: list[str] = []
    sleeps: list[float] = []
    failures: list[httpx.ConnectError] = []
    transport_secret = "synthetic-connect-message-secret"
    asset_query_secret = "synthetic-connect-query-secret"
    failing_asset = f"/failing.js?access_token={asset_query_secret}"
    failure_message = (
        f"{transport_secret}: https://www.threads.com{failing_asset}; "
        f"sessionid={_SESSION_ID}; csrftoken={_CSRF_TOKEN}"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/":
            seen.append("home")
            if phase != "home":
                return httpx.Response(
                    200,
                    text=_home_html(
                        failing_asset,
                        "/must-not-fetch.js",
                        tokens=phase == "route",
                    ),
                )
        elif request.method == "POST":
            route_url = parse_qs(request.content.decode())["route_url"][0]
            seen.append(f"route:{route_url}")
            assert phase == "route"
            assert route_url == "/"
        else:
            seen.append(f"asset:{request.url.path}")
            assert phase == "asset"
            assert request.url.path == "/failing.js"
            assert asset_query_secret in str(request.url)
        failure = httpx.ConnectError(failure_message, request=request)
        failures.append(failure)
        raise failure

    with pytest.raises(errors.AgenticThreadsError) as caught:
        _invoke(monkeypatch, handler, sleeps=sleeps)

    assert type(caught.value) is errors.AgenticThreadsError
    assert caught.value.exit_code == 1
    assert str(caught.value) == "doc-ID discovery transport failed with ConnectError"
    assert caught.value is not failures[0]
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    expected_seen = {
        "home": ["home"],
        "route": ["home", "route:/"],
        "asset": ["home", "asset:/failing.js"],
    }[phase]
    assert seen == expected_seen
    assert sleeps == [1.0] * len(expected_seen)
    diagnostic = _failure_diagnostic(caught.value)
    for secret in (
        failure_message,
        transport_secret,
        asset_query_secret,
        failing_asset,
        _SESSION_ID,
        _USER_ID,
        _FB_DTSG,
        _LSD,
        _CSRF_TOKEN,
    ):
        assert secret not in diagnostic
    captured = capsys.readouterr()
    assert not captured.out
    assert not captured.err


def test_missing_or_ambiguous_tokens_skip_routes_but_keep_js_fallback(monkeypatch):
    second_dtsg = "different-synthetic-dtsg"
    duplicate_module = (
        f'<script>["DTSGInitialData",[],{{"token":{json.dumps(second_dtsg)}}}]</script>'
    )
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.path == "/":
            return httpx.Response(
                200,
                text=_home_html("/fallback.js", extra=duplicate_module),
            )
        assert request.method == "GET"
        return httpx.Response(
            200,
            text=('const query={id:"96001",name:"BarcelonaProfileRepliesTabDirectQuery"};'),
        )

    result, sleeps = _invoke(monkeypatch, handler)

    assert result == {"profile_replies": "96001"}
    assert methods == ["GET", "GET"]
    assert sleeps == [1.0, 1.0]


@pytest.mark.parametrize("status_code", [404, 410])
def test_missing_home_status_is_fatal_without_follow_up(
    monkeypatch,
    capsys,
    status_code,
):
    seen: list[str] = []
    sleeps: list[float] = []
    response_secret = "synthetic-missing-home-secret"
    header_secret = "synthetic-missing-home-header-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method}:{request.url.path}")
        return httpx.Response(
            status_code,
            headers={"x-private-token": header_secret},
            text=f"{response_secret} {_FB_DTSG} {_LSD}",
        )

    with pytest.raises(errors.AgenticThreadsError) as caught:
        _invoke(monkeypatch, handler, sleeps=sleeps)

    assert type(caught.value) is errors.AgenticThreadsError
    assert caught.value.exit_code == 1
    assert str(caught.value) == f"doc-ID discovery failed with HTTP status {status_code}"
    assert seen == ["GET:/"]
    assert sleeps == [1.0]
    assert caught.value.__cause__ is None
    diagnostic = _failure_diagnostic(caught.value)
    for secret in (response_secret, header_secret, _FB_DTSG, _LSD):
        assert secret not in diagnostic
    captured = capsys.readouterr()
    assert not captured.out
    assert not captured.err


def test_request_floor_runs_before_first_and_every_route_and_asset_request(monkeypatch):
    sleeps: list[float] = []
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(docids.time, "sleep", sleeps.append)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        assert len(sleeps) == len(seen)
        if request.method == "GET" and request.url.path == "/":
            return httpx.Response(200, text=_home_html("/root.js"))
        if request.method == "POST":
            return httpx.Response(200, text="{}")
        if request.url.path == "/root.js":
            return httpx.Response(200, text='const next="/nested.js";')
        return httpx.Response(200, text="")

    result = docids.reanchor_via_main_js(
        _SESSION_ID,
        _USER_ID,
        _CSRF_TOKEN,
        _USER_AGENT,
        transport=httpx.MockTransport(handler),
    )

    assert result == {}
    assert seen == [
        ("GET", "/"),
        ("POST", "/ajax/route-definition/"),
        ("POST", "/ajax/route-definition/"),
        ("POST", "/ajax/route-definition/"),
        ("POST", "/ajax/route-definition/"),
        ("GET", "/root.js"),
        ("GET", "/nested.js"),
    ]
    assert sleeps == [1.0] * len(seen)


def test_asset_scan_is_trusted_cookie_scoped_and_capped(monkeypatch):
    trusted_urls = [
        f"https://static.cdninstagram.com/assets/bundle-{index}.js" for index in range(70)
    ]
    untrusted_url = "https://static.cdninstagram.com.evil.example/asset.js"
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.host == "www.threads.com":
            assert f"sessionid={_SESSION_ID}" in request.headers["cookie"]
            return httpx.Response(
                200,
                text=_home_html(*trusted_urls, untrusted_url, tokens=False),
            )
        assert request.url.host == "static.cdninstagram.com"
        assert "cookie" not in request.headers
        assert "x-csrftoken" not in request.headers
        assert "x-fb-lsd" not in request.headers
        return httpx.Response(200, text="")

    result, sleeps = _invoke(monkeypatch, handler)

    assert result == {}
    assert requested_urls[1:] == trusted_urls[: docids._MAX_ASSETS]
    assert untrusted_url not in requested_urls
    assert len(requested_urls) == 1 + docids._MAX_ASSETS
    assert sleeps == [1.0] * len(requested_urls)


def test_asset_scan_stops_at_bounded_depth(monkeypatch):
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(200, text=_home_html("/level-0.js", tokens=False))
        depth = int(request.url.path.removeprefix("/level-").removesuffix(".js"))
        return httpx.Response(200, text=f'const next="/level-{depth + 1}.js";')

    result, sleeps = _invoke(monkeypatch, handler)

    assert result == {}
    assert seen_paths == ["/", "/level-0.js", "/level-1.js", "/level-2.js"]
    assert sleeps == [1.0] * 4


def test_no_findings_returns_no_shipped_defaults(monkeypatch):
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if request.method == "GET":
            return httpx.Response(200, text=_home_html())
        return httpx.Response(200, text=_route_stream())

    result, sleeps = _invoke(monkeypatch, handler)

    assert result == {}
    assert requests == 5
    assert sleeps == [1.0] * requests
