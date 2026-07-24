from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs

import httpx
import pytest

from agentic_threads import client, errors
from agentic_threads.auth import SessionCredential


def _credential(**overrides: object) -> SessionCredential:
    values: dict[str, object] = {
        "sessionid": "synthetic-session",
        "ds_user_id": "10001",
        "csrftoken": "synthetic-csrf",
        "user_agent": "synthetic-test-agent/1.0",
    }
    values.update(overrides)
    return SessionCredential(**values)  # type: ignore[arg-type]


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": {"ok": True}})


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    handler=_ok,
    **kwargs: object,
) -> tuple[client.ReadClient, list[float]]:
    sleeps: list[float] = []
    monkeypatch.setattr(client.time, "sleep", sleeps.append)
    read_client = client.ReadClient(
        _credential(),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )
    return read_client, sleeps


def test_request_floor_applies_before_first_and_every_request(monkeypatch, capsys):
    read_client, sleeps = _make_client(monkeypatch, min_pause=0)
    try:
        read_client.post("SyntheticQuery", {"page": 1}, doc_id="100")
        read_client.post("SyntheticQuery", {"page": 2}, doc_id="100")
    finally:
        read_client.close()

    assert sleeps == [1.0, 1.0]
    assert read_client.requests_made == 2
    assert "raised to 1.0s" in capsys.readouterr().err


def test_request_floor_reclamps_mutation_before_first_send(monkeypatch, capsys):
    read_client, sleeps = _make_client(monkeypatch, min_pause=2.25)
    read_client.min_pause = 0.25
    try:
        read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert sleeps == [1.0]
    assert read_client.min_pause == 1.0
    assert "raised to 1.0s" in capsys.readouterr().err


def test_request_floor_reclamps_mutation_before_later_send(monkeypatch, capsys):
    read_client, sleeps = _make_client(monkeypatch, min_pause=2.25)
    try:
        read_client.post("SyntheticQuery", {"page": 1}, doc_id="100")
        read_client.min_pause = 0.25
        read_client.post("SyntheticQuery", {"page": 2}, doc_id="100")
    finally:
        read_client.close()

    assert sleeps == [2.25, 1.0]
    assert read_client.min_pause == 1.0
    assert "raised to 1.0s" in capsys.readouterr().err


def test_larger_request_pause_is_preserved(monkeypatch):
    read_client, sleeps = _make_client(monkeypatch, min_pause=2.25)
    try:
        read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()
    assert sleeps == [2.25]


def test_post_sends_only_persisted_query_fields_and_authenticated_headers(monkeypatch):
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["body"] = parse_qs(request.content.decode("utf-8"), keep_blank_values=True)
        observed["headers"] = request.headers
        return httpx.Response(200, json={"data": {"ok": True}})

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        body = read_client.post(
            "SyntheticFriendlyName",
            {"query": "synthetic query", "cursor": None, "nested": {"enabled": True}},
            doc_id="987654321",
        )
    finally:
        read_client.close()

    assert body == {"data": {"ok": True}}
    assert observed["url"] == "https://www.threads.com/graphql/query"
    form = observed["body"]
    assert isinstance(form, dict)
    assert set(form) == {"doc_id", "variables"}
    assert form["doc_id"] == ["987654321"]
    assert json.loads(form["variables"][0]) == {
        "query": "synthetic query",
        "cursor": None,
        "nested": {"enabled": True},
    }

    headers = observed["headers"]
    assert isinstance(headers, httpx.Headers)
    assert headers["x-fb-friendly-name"] == "SyntheticFriendlyName"
    assert headers["x-csrftoken"] == "synthetic-csrf"
    assert headers["x-ig-app-id"] == "238260118697367"
    assert headers["origin"] == "https://www.threads.com"
    assert headers["referer"] == "https://www.threads.com/"
    assert headers["user-agent"] == "synthetic-test-agent/1.0"
    cookie = headers["cookie"]
    assert "sessionid=synthetic-session" in cookie
    assert "ds_user_id=10001" in cookie
    assert "csrftoken=synthetic-csrf" in cookie


def test_request_budget_is_lifetime_scoped_and_checked_before_sleep(monkeypatch):
    read_client, sleeps = _make_client(monkeypatch, max_requests=1)
    try:
        read_client.post("FirstQuery", {}, doc_id="1")
        assert read_client.remaining_requests == 0
        with pytest.raises(errors.AgenticThreadsError, match="request budget exhausted"):
            read_client.post("SecondQuery", {}, doc_id="2")
    finally:
        read_client.close()

    assert read_client.requests_made == 1
    assert sleeps == [1.0]


@pytest.mark.parametrize(
    "separate_clients",
    [False, True],
    ids=["same-client", "multiple-clients"],
)
def test_simultaneous_starts_serialize_sleep_and_wire_calls(monkeypatch, separate_clients):
    activity_lock = threading.Lock()
    sleep_barrier = threading.Barrier(2)
    wire_barrier = threading.Barrier(2)
    sleep_durations: list[float] = []
    sleep_active = 0
    max_sleep_active = 0
    wire_calls = 0
    wire_active = 0
    max_wire_active = 0

    def gated_sleep(duration: float) -> None:
        nonlocal max_sleep_active, sleep_active
        with activity_lock:
            sleep_durations.append(duration)
            sleep_active += 1
            max_sleep_active = max(max_sleep_active, sleep_active)
        try:
            sleep_barrier.wait(timeout=0.2)
        except threading.BrokenBarrierError:
            pass
        finally:
            with activity_lock:
                sleep_active -= 1

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal max_wire_active, wire_active, wire_calls
        with activity_lock:
            wire_calls += 1
            wire_active += 1
            max_wire_active = max(max_wire_active, wire_active)
        try:
            wire_barrier.wait(timeout=0.2)
        except threading.BrokenBarrierError:
            pass
        finally:
            with activity_lock:
                wire_active -= 1
        return httpx.Response(200, json={"data": {"ok": True}})

    monkeypatch.setattr(client.time, "sleep", gated_sleep)
    first = client.ReadClient(
        _credential(),
        min_pause=1.0,
        transport=httpx.MockTransport(handler),
    )
    second = (
        client.ReadClient(
            _credential(),
            min_pause=1.0,
            transport=httpx.MockTransport(handler),
        )
        if separate_clients
        else first
    )
    start = threading.Barrier(3)

    def invoke(read_client: client.ReadClient, index: int) -> dict:
        start.wait(timeout=2)
        return read_client.post(f"SyntheticQuery{index}", {}, doc_id=str(index))

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(invoke, first, 1),
                executor.submit(invoke, second, 2),
            ]
            start.wait(timeout=2)
            returned = [future.result(timeout=3) for future in futures]
    finally:
        first.close()
        if second is not first:
            second.close()

    assert returned == [{"data": {"ok": True}}, {"data": {"ok": True}}]
    assert sleep_durations == [1.0, 1.0]
    assert max_sleep_active == 1
    assert wire_calls == 2
    assert max_wire_active == 1


def test_simultaneous_budget_reservations_allow_exactly_one_wire_call(monkeypatch):
    sleep_barrier = threading.Barrier(2)
    activity_lock = threading.Lock()
    sleep_calls = 0
    wire_calls = 0

    def gated_sleep(_duration: float) -> None:
        nonlocal sleep_calls
        with activity_lock:
            sleep_calls += 1
        try:
            sleep_barrier.wait(timeout=0.2)
        except threading.BrokenBarrierError:
            pass

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal wire_calls
        with activity_lock:
            wire_calls += 1
        return httpx.Response(200, json={"data": {"ok": True}})

    monkeypatch.setattr(client.time, "sleep", gated_sleep)
    read_client = client.ReadClient(
        _credential(),
        min_pause=1.0,
        max_requests=1,
        transport=httpx.MockTransport(handler),
    )
    start = threading.Barrier(3)

    def invoke(index: int):
        start.wait(timeout=2)
        try:
            return read_client.post(f"SyntheticQuery{index}", {}, doc_id=str(index))
        except errors.AgenticThreadsError as exc:
            return exc

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(invoke, 1), executor.submit(invoke, 2)]
            start.wait(timeout=2)
            outcomes = [future.result(timeout=3) for future in futures]
    finally:
        read_client.close()

    successes = [outcome for outcome in outcomes if isinstance(outcome, dict)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert successes == [{"data": {"ok": True}}]
    assert len(failures) == 1
    assert type(failures[0]) is errors.AgenticThreadsError
    assert "request budget exhausted" in str(failures[0])
    assert read_client.requests_made == 1
    assert sleep_calls == 1
    assert wire_calls == 1


def test_transport_failure_is_wrapped_without_reflecting_sensitive_values(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic transport failed", request=request)

    read_client, sleeps = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.AgenticThreadsError, match="ConnectError") as caught:
            read_client.post("SyntheticQuery", {"secret": "do-not-reflect"}, doc_id="100")
    finally:
        read_client.close()

    assert "do-not-reflect" not in str(caught.value)
    assert read_client.requests_made == 1
    assert sleeps == [1.0]
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_close_is_idempotent_and_prevents_later_requests(monkeypatch):
    read_client, sleeps = _make_client(monkeypatch)
    read_client.close()
    read_client.close()

    with pytest.raises(errors.SessionClosedError, match="closed"):
        read_client.post("SyntheticQuery", {}, doc_id="100")
    with pytest.raises(errors.SessionClosedError, match="closed"):
        read_client.__enter__()
    assert sleeps == []


def test_context_manager_closes_after_use(monkeypatch):
    read_client, _ = _make_client(monkeypatch)
    with read_client as entered:
        assert entered is read_client
        entered.post("SyntheticQuery", {}, doc_id="100")

    with pytest.raises(errors.SessionClosedError):
        read_client.post("SyntheticQuery", {}, doc_id="100")


@pytest.mark.parametrize("status_code", [401, 403])
def test_http_auth_failures_map_to_session_expired(monkeypatch, status_code):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="synthetic auth rejection")

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.SessionExpiredError) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is errors.SessionExpiredError
    assert caught.value.exit_code == 2


def test_http_400_unknown_error_remains_persisted_operation_drift(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "errors": [
                    {
                        "code": 999999,
                        "message": "synthetic-secret-response-evidence",
                    }
                ]
            },
        )

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(
            errors.PersistedOperationDriftError,
            match="operation identifier",
        ) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is errors.PersistedOperationDriftError
    assert isinstance(caught.value, errors.EnvelopeParseError)
    assert caught.value.exit_code == 4
    assert "synthetic-secret-response-evidence" not in str(caught.value)


def test_http_400_code_190_auth_outranks_rate_and_malformed_errors(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "errors": [
                    {"code": 613, "message": "synthetic-secret-response-evidence"},
                    {"code": 190, "message": "synthetic-secret-response-evidence"},
                    "synthetic-secret-response-evidence",
                ]
            },
        )

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.SessionExpiredError) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is errors.SessionExpiredError
    assert caught.value.exit_code == 2
    assert "synthetic-secret-response-evidence" not in str(caught.value)


def test_http_400_code_613_preserves_reset_before_malformed_errors(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            headers={"x-rate-limit-reset": "1900000002"},
            json={
                "errors": [
                    {"code": 613, "message": "synthetic-secret-response-evidence"},
                    "synthetic-secret-response-evidence",
                ]
            },
        )

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.RateLimitedError) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is errors.RateLimitedError
    assert caught.value.exit_code == 3
    assert caught.value.reset_at == 1_900_000_002
    assert read_client.last_rate_limit_reset == 1_900_000_002
    assert "synthetic-secret-response-evidence" not in str(caught.value)


def test_http_400_malformed_errors_outrank_unknown_drift(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "errors": [
                    {"code": 999999, "message": "synthetic-secret-response-evidence"},
                    "synthetic-secret-response-evidence",
                ]
            },
        )

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.EnvelopeParseError) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is errors.EnvelopeParseError
    assert caught.value.exit_code == 4
    assert not isinstance(caught.value, errors.PersistedOperationDriftError)
    assert "synthetic-secret-response-evidence" not in str(caught.value)


@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        (
            400,
            {
                "errors": [
                    {"code": 190, "message": "synthetic auth rejection"},
                    {"code": 613, "message": "synthetic rate-limit rejection"},
                    {"code": 459, "message": "synthetic account restriction"},
                    "synthetic-secret-response-evidence",
                ]
            },
        ),
        (
            401,
            {
                "error": {
                    "code": 190,
                    "error_subcode": 459,
                    "message": "synthetic auth rejection",
                }
            },
        ),
        (
            403,
            {
                "errors": [
                    {
                        "code": 403,
                        "subcode": 368,
                        "message": "synthetic auth rejection",
                    }
                ]
            },
        ),
    ],
)
def test_http_challenge_evidence_outranks_status_fallback(
    monkeypatch,
    status_code,
    payload,
):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.ChallengeError) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is errors.ChallengeError
    assert caught.value.exit_code == 2
    assert "synthetic-secret-response-evidence" not in str(caught.value)


def test_other_non_success_http_is_generic(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="synthetic server failure")

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.AgenticThreadsError, match="unexpected HTTP 500") as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is errors.AgenticThreadsError


@pytest.mark.parametrize(
    ("payload", "text"),
    [
        (
            {
                "errors": [
                    {"code": 368, "message": "synthetic-secret-response-evidence"},
                ]
            },
            None,
        ),
        (
            {
                "error": {
                    "code": 459,
                    "message": "synthetic-secret-response-evidence",
                }
            },
            None,
        ),
        (
            {
                "errors": [
                    {
                        "code": 613,
                        "error_subcode": 368,
                        "message": "synthetic-secret-response-evidence",
                    }
                ]
            },
            None,
        ),
        (
            {
                "error": {
                    "code": 613,
                    "subcode": 459,
                    "message": "synthetic-secret-response-evidence",
                }
            },
            None,
        ),
        (None, "challenge: synthetic-secret-response-evidence"),
        (None, "checkpoint: synthetic-secret-response-evidence"),
        (None, "consent_required: synthetic-secret-response-evidence"),
    ],
    ids=[
        "code-368",
        "code-459",
        "error-subcode-368",
        "subcode-459",
        "challenge-marker",
        "checkpoint-marker",
        "consent-required-marker",
    ],
)
def test_http_429_challenge_evidence_outranks_rate_limit(
    monkeypatch,
    payload,
    text,
):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if payload is not None:
            return httpx.Response(
                429,
                headers={"x-rate-limit-reset": "1900000000"},
                json=payload,
            )
        return httpx.Response(
            429,
            headers={"x-rate-limit-reset": "1900000000"},
            text=text,
        )

    read_client, sleeps = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.ChallengeError) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is errors.ChallengeError
    assert caught.value.exit_code == 2
    assert not isinstance(caught.value, errors.RateLimitedError)
    assert "synthetic-secret-response-evidence" not in str(caught.value)
    assert "synthetic-secret-response-evidence" not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert calls == 1
    assert sleeps == [1.0]


def test_http_429_preserves_absolute_reset_header(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"x-rate-limit-reset": "1900000000.75"},
            json={
                "error": {
                    "code": 613,
                    "message": "too many requests: synthetic-secret-response-evidence",
                }
            },
        )

    read_client, sleeps = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.RateLimitedError) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is errors.RateLimitedError
    assert caught.value.exit_code == 3
    assert caught.value.reset_at == 1_900_000_000
    assert read_client.last_rate_limit_reset == 1_900_000_000
    assert "synthetic-secret-response-evidence" not in str(caught.value)
    assert "synthetic-secret-response-evidence" not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sleeps == [1.0]


def test_retry_after_is_converted_to_an_absolute_ceiling(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "2.1"})

    read_client, _ = _make_client(monkeypatch, handler)
    monkeypatch.setattr(client.time, "time", lambda: 100.0)
    try:
        with pytest.raises(errors.RateLimitedError) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert caught.value.reset_at == 103


@pytest.mark.parametrize(
    ("header_name", "header_value"),
    [
        ("x-rate-limit-reset", "nan"),
        ("x-rate-limit-reset", "inf"),
        ("x-rate-limit-reset", "-inf"),
        ("x-rate-limit-reset", "1e10000"),
        ("retry-after", "nan"),
        ("retry-after", "inf"),
        ("retry-after", "-inf"),
        ("retry-after", "1e10000"),
    ],
)
def test_http_429_rejects_non_finite_and_overflowing_reset_headers(
    monkeypatch,
    header_name,
    header_value,
):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={header_name: header_value})

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.RateLimitedError) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is errors.RateLimitedError
    assert caught.value.exit_code == 3
    assert caught.value.reset_at is None
    assert read_client.last_rate_limit_reset is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"errors": [{"code": 190, "message": "synthetic expired"}]}, errors.SessionExpiredError),
        ({"error": {"code": 613, "message": "synthetic limit"}}, errors.RateLimitedError),
        ({"errors": [{"code": 368, "message": "synthetic challenge"}]}, errors.ChallengeError),
        (
            {"errors": [{"message": "synthetic unknown GraphQL failure"}]},
            errors.EnvelopeParseError,
        ),
    ],
)
def test_graphql_error_envelopes_map_by_semantics(monkeypatch, payload, expected):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, headers={"x-ratelimit-reset": "1900000001"})

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(expected) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is expected
    if expected is errors.RateLimitedError:
        assert caught.value.reset_at == 1_900_000_001
    if expected is errors.ChallengeError:
        assert type(caught.value) is errors.ChallengeError
        assert caught.value.exit_code == 2


@pytest.mark.parametrize(
    ("payload", "expected", "exit_code"),
    [
        (
            {
                "error": "checkpoint_required",
                "errorSummary": "synthetic authentication failure",
            },
            errors.ChallengeError,
            2,
        ),
        ({"error": "challenge_required"}, errors.ChallengeError, 2),
        ({"error": "synthetic_unknown_failure"}, errors.EnvelopeParseError, 4),
    ],
)
def test_textual_singular_error_values_preserve_typed_semantics(
    monkeypatch,
    payload,
    expected,
    exit_code,
):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(expected) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is expected
    assert caught.value.exit_code == exit_code


@pytest.mark.parametrize(
    "payload",
    [
        {
            "data": {
                "viewer": None,
                "note": "synthetic-secret-response-evidence",
            }
        },
        {
            "data": {
                "items": [],
                "metadata": {"is_logged_in": False},
                "note": "synthetic-secret-response-evidence",
            }
        },
        {
            "data": {"items": []},
            "extensions": {
                "isAuthenticated": False,
                "note": "synthetic-secret-response-evidence",
            },
        },
    ],
    ids=["null-viewer", "logged-in-false", "authenticated-false"],
)
def test_2xx_explicit_authentication_downgrade_fails_closed(monkeypatch, payload):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.SessionExpiredError) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is errors.SessionExpiredError
    assert caught.value.exit_code == 2
    assert caught.value.__cause__ is None
    assert "synthetic-secret-response-evidence" not in str(caught.value)


def test_2xx_ordinary_authenticated_envelope_without_state_metadata_is_accepted(monkeypatch):
    payload = {
        "data": {
            "viewer": {"id": "synthetic-viewer"},
            "items": [],
        },
        "extensions": {"is_final": False},
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        assert read_client.post("SyntheticQuery", {}, doc_id="100") == payload
    finally:
        read_client.close()


@pytest.mark.parametrize(
    "malformed_errors",
    [
        None,
        {"message": "synthetic-secret-response-evidence"},
        "synthetic-secret-response-evidence",
        [
            {"code": 368, "message": "synthetic checkpoint"},
            "synthetic-secret-response-evidence",
        ],
    ],
)
def test_malformed_graphql_errors_containers_fail_closed_with_data(
    monkeypatch,
    malformed_errors,
):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"ok": True}, "errors": malformed_errors},
        )

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.EnvelopeParseError) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is errors.EnvelopeParseError
    assert caught.value.exit_code == 4
    assert "synthetic-secret-response-evidence" not in str(caught.value)


def test_empty_graphql_errors_list_with_data_is_accepted(monkeypatch):
    payload = {"data": {"ok": True}, "errors": []}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        assert read_client.post("SyntheticQuery", {}, doc_id="100") == payload
    finally:
        read_client.close()


@pytest.mark.parametrize(
    ("text", "expected", "exit_code"),
    [
        ("", errors.SessionExpiredError, 2),
        ("<html>CAAFetaAYMHPasswordEntryQuery</html>", errors.SessionExpiredError, 2),
        (
            "<html>CAAFetaAYMHPasswordEntryQuery checkpoint challenge</html>",
            errors.ChallengeError,
            2,
        ),
        (
            "this is not JSON: synthetic-secret-response-evidence",
            errors.EnvelopeParseError,
            4,
        ),
    ],
)
def test_malformed_and_marker_bodies_are_classified_without_reflection(
    monkeypatch,
    text,
    expected,
    exit_code,
):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=text, headers={"content-type": "text/html"})

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(expected) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()

    assert type(caught.value) is expected
    assert caught.value.exit_code == exit_code
    assert "synthetic-secret-response-evidence" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"extensions": {"synthetic": True}},
    ],
)
def test_non_object_or_missing_data_envelopes_raise_parse_error(monkeypatch, payload):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.EnvelopeParseError) as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
        assert type(caught.value) is errors.EnvelopeParseError
    finally:
        read_client.close()


def test_empty_json_object_is_treated_as_a_dead_session(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.SessionExpiredError):
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()


def test_unexpected_http_status_is_not_misclassified_as_auth(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="synthetic outage")

    read_client, _ = _make_client(monkeypatch, handler)
    try:
        with pytest.raises(errors.AgenticThreadsError, match="HTTP 503") as caught:
            read_client.post("SyntheticQuery", {}, doc_id="100")
    finally:
        read_client.close()
    assert not isinstance(caught.value, errors.SessionExpiredError)


def test_missing_credentials_and_invalid_budget_fail_before_transport():
    with pytest.raises(errors.LoginRequiredError):
        client.ReadClient(_credential(csrftoken=""))
    with pytest.raises(ValueError, match="non-negative"):
        client.ReadClient(_credential(), max_requests=-1)
