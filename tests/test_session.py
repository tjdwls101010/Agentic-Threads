from __future__ import annotations

import builtins
import sys
from contextlib import contextmanager, nullcontext
from types import ModuleType, SimpleNamespace

import pytest

from agentic_threads import docids, errors, gql, session
from agentic_threads.auth import SessionCredential


def _credential(**overrides: object) -> SessionCredential:
    values: dict[str, object] = {
        "sessionid": "synthetic-session",
        "ds_user_id": "10001",
        "csrftoken": "synthetic-csrf",
        "user_agent": "synthetic-test-agent/1.0",
        "doc_ids": {"feed": "90000000000000001"},
        "features": {"synthetic_feature": True},
    }
    values.update(overrides)
    return SessionCredential(**values)  # type: ignore[arg-type]


class FakeReadClient:
    def __init__(self, responses: list[dict | Exception] | None = None):
        self.responses = list(responses or [])
        self.calls: list[tuple[str, dict[str, object], str]] = []
        self.closed = False

    def post(self, operation, variables, *, doc_id):
        self.calls.append((operation, dict(variables), doc_id))
        if not self.responses:
            raise AssertionError("unexpected session health-check request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


def test_query_data_merges_saved_values_over_defaults_without_mutation():
    credential = _credential(
        doc_ids={"feed": "90000000000000002"},
        features={
            "synthetic_feature": True,
            "__relay_internal__pv__BarcelonaIsLoggedInrelayprovider": False,
        },
    )

    merged_ids, merged_features = session.query_data_for(credential)

    assert merged_ids["feed"] == "90000000000000002"
    assert merged_ids["post"] == docids.DEFAULT_DOC_IDS["post"]
    assert merged_features["synthetic_feature"] is True
    assert merged_features["__relay_internal__pv__BarcelonaIsLoggedInrelayprovider"] is False
    merged_ids["feed"] = "mutated-copy"
    merged_features["synthetic_feature"] = False
    assert credential.doc_ids == {"feed": "90000000000000002"}
    assert credential.features["synthetic_feature"] is True


def test_health_check_uses_the_saved_account_profile_and_classifies_logged_in():
    read_client = FakeReadClient([{"data": {"user": {"pk": "10001"}}}])

    status = session.check_session_status(
        read_client,
        {"profile": "profile-doc"},
        {"synthetic_feature": True},
        "10001",
    )

    assert status is session.Status.LOGGED_IN
    operation, variables, doc_id = read_client.calls[0]
    assert operation == gql.PROFILE_OPERATION
    assert variables["userID"] == "10001"
    assert variables["synthetic_feature"] is True
    assert doc_id == "profile-doc"


@pytest.mark.parametrize(
    "body",
    [
        {"data": {"user": None}},
        {"data": {"user": {"pk": "different-user"}}},
    ],
    ids=["explicit-null", "account-mismatch"],
)
def test_health_check_classifies_explicitly_unavailable_or_wrong_account_as_expired(body):
    read_client = FakeReadClient([body])

    status = session.check_session_status(read_client, {}, None, "10001")

    assert status is session.Status.EXPIRED
    assert len(read_client.calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"data": {}},
        {"data": []},
        {"data": {"user": []}},
    ],
    ids=["missing-data", "missing-user", "wrong-data-type", "wrong-user-type"],
)
def test_health_check_propagates_profile_envelope_drift(body):
    read_client = FakeReadClient([body])

    with pytest.raises(errors.EnvelopeParseError, match=r"data\.user") as caught:
        session.check_session_status(read_client, {}, None, "10001")

    assert caught.value.exit_code == 4
    assert len(read_client.calls) == 1


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (errors.RateLimitedError(reset_at=123), session.Status.RATE_LIMITED),
        (errors.SessionExpiredError("expired"), session.Status.EXPIRED),
    ],
)
def test_health_check_maps_expected_wire_failures(failure, expected):
    read_client = FakeReadClient([failure])
    assert session.check_session_status(read_client, {}, None, "10001") is expected


def test_health_check_preserves_challenge_error_identity():
    failure = errors.ChallengeError("checkpoint")
    read_client = FakeReadClient([failure])

    with pytest.raises(errors.ChallengeError) as caught:
        session.check_session_status(read_client, {}, None, "10001")

    assert caught.value is failure


def test_run_status_builds_a_one_request_client_and_always_closes(monkeypatch, tmp_path):
    credential = _credential()
    fake = FakeReadClient()
    constructed: dict[str, object] = {}

    monkeypatch.setattr(session.auth, "load_session", lambda *args, **kwargs: credential)

    def make_client(received, *, max_requests):
        constructed.update(credential=received, max_requests=max_requests)
        return fake

    monkeypatch.setattr(session.client, "ReadClient", make_client)
    monkeypatch.setattr(
        session,
        "check_session_status",
        lambda *args: session.Status.LOGGED_IN,
    )

    status = session.run_status("synthetic", profile_dir_override=tmp_path)

    assert status is session.Status.LOGGED_IN
    assert constructed == {"credential": credential, "max_requests": 1}
    assert fake.closed is True


def test_run_status_closes_client_when_health_check_raises(monkeypatch):
    credential = _credential()
    fake = FakeReadClient()
    monkeypatch.setattr(session.auth, "load_session", lambda *args, **kwargs: credential)
    monkeypatch.setattr(session.client, "ReadClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(
        session,
        "check_session_status",
        lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )

    with pytest.raises(RuntimeError, match="synthetic failure"):
        session.run_status("synthetic")
    assert fake.closed is True


def test_run_status_propagates_profile_drift_after_one_request_and_closes(monkeypatch):
    credential = _credential()
    fake = FakeReadClient([{"data": {}}])
    monkeypatch.setattr(session.auth, "load_session", lambda *args, **kwargs: credential)
    monkeypatch.setattr(session.client, "ReadClient", lambda *args, **kwargs: fake)

    with pytest.raises(errors.EnvelopeParseError, match=r"data\.user") as caught:
        session.run_status("synthetic")

    assert caught.value.exit_code == 4
    assert len(fake.calls) == 1
    assert fake.closed is True


def test_doctor_reports_a_missing_session_without_constructing_a_client(monkeypatch):
    monkeypatch.setattr(
        session.auth,
        "load_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(errors.LoginRequiredError("no session")),
    )
    monkeypatch.setattr(
        session.client,
        "ReadClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not construct")),
    )

    ok, message = session.run_doctor("missing")

    assert ok is False
    assert message == "no session"


def test_doctor_rate_limited_first_probe_does_not_refresh_or_save(monkeypatch):
    credential = _credential()
    fake = FakeReadClient([errors.RateLimitedError(reset_at=123)])
    constructed: list[tuple[object, int]] = []
    monkeypatch.setattr(session.auth, "load_session", lambda *args, **kwargs: credential)

    def make_client(received, *, max_requests):
        constructed.append((received, max_requests))
        return fake

    monkeypatch.setattr(session.client, "ReadClient", make_client)
    monkeypatch.setattr(
        session.docids,
        "reanchor_via_main_js",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not refresh")),
    )
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not save")),
    )

    ok, message = session.run_doctor("synthetic", refresh=True)

    assert ok is False
    assert "rate_limited" in message
    assert constructed == [(credential, 1)]
    assert len(fake.calls) == 1
    assert fake.closed is True


def test_doctor_refresh_recovers_stale_profile_doc_id_with_one_fresh_retry(
    monkeypatch,
    tmp_path,
):
    stale_profile_doc_id = "90000000000000002"
    fresh_profile_doc_id = "90000000000000003"
    fresh_post_doc_id = "90000000000000004"
    saved_feed_doc_id = "90000000000000005"
    saved_followers_doc_id = "90000000000000006"
    credential = _credential(
        doc_ids={
            "profile": stale_profile_doc_id,
            "feed": saved_feed_doc_id,
            "followers": saved_followers_doc_id,
        }
    )
    first_probe = FakeReadClient([errors.PersistedOperationDriftError("stale profile doc ID")])
    retry_probe = FakeReadClient([{"data": {"user": {"pk": "10001"}}}])
    clients = [first_probe, retry_probe]
    constructed: list[tuple[object, int, FakeReadClient]] = []
    refresh_args: list[tuple[object, ...]] = []
    saved: dict[str, object] = {}
    monkeypatch.setattr(session.auth, "load_session", lambda *args, **kwargs: credential)

    def make_client(received, *, max_requests):
        fake = clients[len(constructed)]
        constructed.append((received, max_requests, fake))
        return fake

    def reanchor(*args):
        assert first_probe.closed is True
        assert len(first_probe.calls) == 1
        assert retry_probe.calls == []
        refresh_args.append(args)
        return {
            "profile": fresh_profile_doc_id,
            "post": fresh_post_doc_id,
        }

    def save(profile, received, *, profile_dir_override=None):
        assert retry_probe.closed is True
        assert len(retry_probe.calls) == 1
        saved.update(
            profile=profile,
            credential=received,
            profile_dir_override=profile_dir_override,
        )

    monkeypatch.setattr(session.client, "ReadClient", make_client)
    monkeypatch.setattr(session.docids, "reanchor_via_main_js", reanchor)
    monkeypatch.setattr(session.auth, "save_session", save)

    ok, message = session.run_doctor(
        "synthetic",
        profile_dir_override=tmp_path,
        refresh=True,
    )

    expected_doc_ids = dict(docids.DEFAULT_DOC_IDS)
    expected_doc_ids.update(
        {
            "profile": fresh_profile_doc_id,
            "post": fresh_post_doc_id,
            "feed": saved_feed_doc_id,
            "followers": saved_followers_doc_id,
        }
    )
    assert ok is True
    assert message.endswith("re-anchored 2 doc_id(s)")
    assert constructed == [
        (credential, 1, first_probe),
        (credential, 1, retry_probe),
    ]
    assert refresh_args == [
        (
            "synthetic-session",
            "10001",
            "synthetic-csrf",
            "synthetic-test-agent/1.0",
        )
    ]
    assert len(first_probe.calls) == 1
    assert first_probe.calls[0][2] == stale_profile_doc_id
    assert len(retry_probe.calls) == 1
    assert retry_probe.calls[0][2] == fresh_profile_doc_id
    assert first_probe.closed is True
    assert retry_probe.closed is True
    assert credential.doc_ids == expected_doc_ids
    assert saved == {
        "profile": "synthetic",
        "credential": credential,
        "profile_dir_override": tmp_path,
    }


@pytest.mark.parametrize(
    ("failure", "refresh"),
    [
        (errors.EnvelopeParseError("generic envelope mismatch"), True),
        (errors.PersistedOperationDriftError("stale profile doc ID"), False),
    ],
    ids=["generic-envelope-with-refresh", "operation-drift-without-refresh"],
)
def test_doctor_propagates_nonrecoverable_envelope_errors_without_mutation(
    monkeypatch,
    failure,
    refresh,
):
    stale_profile_doc_id = "90000000000000007"
    credential = _credential(doc_ids={"profile": stale_profile_doc_id})
    original_doc_ids = dict(credential.doc_ids)
    fake = FakeReadClient([failure])
    monkeypatch.setattr(session.auth, "load_session", lambda *args, **kwargs: credential)
    monkeypatch.setattr(session.client, "ReadClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(
        session.docids,
        "reanchor_via_main_js",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not refresh")),
    )
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not save")),
    )

    with pytest.raises(errors.EnvelopeParseError) as caught:
        session.run_doctor("synthetic", refresh=refresh)

    assert caught.value is failure
    assert len(fake.calls) == 1
    assert fake.calls[0][2] == stale_profile_doc_id
    assert fake.closed is True
    assert credential.doc_ids == original_doc_ids


def test_doctor_preserves_challenge_without_refresh_retry_or_save(monkeypatch):
    credential = _credential()
    failure = errors.ChallengeError("checkpoint")
    fake = FakeReadClient([failure])
    monkeypatch.setattr(session.auth, "load_session", lambda *args, **kwargs: credential)
    monkeypatch.setattr(session.client, "ReadClient", lambda *args, **kwargs: fake)
    monkeypatch.setattr(
        session.docids,
        "reanchor_via_main_js",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not refresh")),
    )
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not save")),
    )

    with pytest.raises(errors.ChallengeError) as caught:
        session.run_doctor("synthetic", refresh=True)

    assert caught.value is failure
    assert len(fake.calls) == 1
    assert fake.closed is True


@pytest.mark.parametrize(
    "fresh_doc_ids",
    [
        {},
        {"feed": "90000000000000011"},
        {"profile": "not-numeric"},
    ],
    ids=["empty", "missing-profile", "non-numeric-profile"],
)
def test_doctor_unusable_reanchor_fails_closed_without_retry_or_save(
    monkeypatch,
    fresh_doc_ids,
):
    credential = _credential(doc_ids={"profile": "90000000000000008"})
    original_doc_ids = dict(credential.doc_ids)
    first_probe = FakeReadClient([errors.PersistedOperationDriftError("stale profile doc ID")])
    constructed: list[tuple[object, int]] = []
    refresh_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(session.auth, "load_session", lambda *args, **kwargs: credential)

    def make_client(received, *, max_requests):
        if constructed:
            raise AssertionError("must not retry without a fresh profile doc ID")
        constructed.append((received, max_requests))
        return first_probe

    def reanchor(*args):
        assert first_probe.closed is True
        refresh_calls.append(args)
        return fresh_doc_ids

    monkeypatch.setattr(session.client, "ReadClient", make_client)
    monkeypatch.setattr(session.docids, "reanchor_via_main_js", reanchor)
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not save")),
    )

    ok, message = session.run_doctor("synthetic", refresh=True)

    assert ok is False
    assert "no usable profile doc ID" in message
    assert constructed == [(credential, 1)]
    assert len(refresh_calls) == 1
    assert first_probe.closed is True
    assert credential.doc_ids == original_doc_ids


@pytest.mark.parametrize(
    ("retry_response", "expected_status"),
    [
        ({"data": {"user": None}}, "expired"),
        (errors.PersistedOperationDriftError("profile doc ID is still stale"), None),
    ],
    ids=["expired", "envelope-drift"],
)
def test_doctor_failed_retry_is_bounded_and_does_not_save(
    monkeypatch,
    retry_response,
    expected_status,
):
    stale_profile_doc_id = "90000000000000009"
    fresh_profile_doc_id = "90000000000000010"
    credential = _credential(doc_ids={"profile": stale_profile_doc_id})
    original_doc_ids = dict(credential.doc_ids)
    first_probe = FakeReadClient([errors.PersistedOperationDriftError("stale profile doc ID")])
    retry_probe = FakeReadClient([retry_response])
    clients = [first_probe, retry_probe]
    constructed: list[tuple[object, int]] = []
    refresh_calls: list[object] = []
    monkeypatch.setattr(session.auth, "load_session", lambda *args, **kwargs: credential)

    def make_client(received, *, max_requests):
        fake = clients[len(constructed)]
        constructed.append((received, max_requests))
        return fake

    def reanchor(*args):
        refresh_calls.append(args)
        return {"profile": fresh_profile_doc_id}

    monkeypatch.setattr(session.client, "ReadClient", make_client)
    monkeypatch.setattr(session.docids, "reanchor_via_main_js", reanchor)
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not save")),
    )

    if expected_status is None:
        with pytest.raises(errors.EnvelopeParseError) as caught:
            session.run_doctor("synthetic", refresh=True)
        assert caught.value is retry_response
    else:
        ok, message = session.run_doctor("synthetic", refresh=True)
        assert ok is False
        assert expected_status in message

    assert constructed == [(credential, 1), (credential, 1)]
    assert len(refresh_calls) == 1
    assert len(first_probe.calls) == 1
    assert first_probe.calls[0][2] == stale_profile_doc_id
    assert len(retry_probe.calls) == 1
    assert retry_probe.calls[0][2] == fresh_profile_doc_id
    assert first_probe.closed is True
    assert retry_probe.closed is True
    assert credential.doc_ids == original_doc_ids


def test_doctor_refreshes_doc_ids_over_http_and_persists_them(monkeypatch, tmp_path):
    credential = _credential(
        doc_ids={
            "feed": "90000000000000003",
            "followers": "90000000000000004",
        }
    )
    fake = FakeReadClient()
    saved: dict[str, object] = {}
    refresh_args: list[object] = []
    constructed: list[tuple[object, int]] = []
    monkeypatch.setattr(session.auth, "load_session", lambda *args, **kwargs: credential)

    def make_client(received, *, max_requests):
        constructed.append((received, max_requests))
        return fake

    monkeypatch.setattr(session.client, "ReadClient", make_client)
    monkeypatch.setattr(
        session,
        "check_session_status",
        lambda *args: session.Status.LOGGED_IN,
    )

    def reanchor(*args):
        refresh_args.extend(args)
        return {
            "feed": "90000000000000005",
            "post": "90000000000000006",
        }

    def save(profile, received, *, profile_dir_override=None):
        saved.update(
            profile=profile,
            credential=received,
            profile_dir_override=profile_dir_override,
        )

    monkeypatch.setattr(session.docids, "reanchor_via_main_js", reanchor)
    monkeypatch.setattr(session.auth, "save_session", save)

    ok, message = session.run_doctor(
        "synthetic",
        profile_dir_override=tmp_path,
        refresh=True,
    )

    assert ok is True
    assert message.endswith("re-anchored 2 doc_id(s)")
    assert constructed == [(credential, 1)]
    assert refresh_args == [
        "synthetic-session",
        "10001",
        "synthetic-csrf",
        "synthetic-test-agent/1.0",
    ]
    assert credential.doc_ids["feed"] == "90000000000000005"
    assert credential.doc_ids["post"] == "90000000000000006"
    assert credential.doc_ids["followers"] == "90000000000000004"
    assert saved == {
        "profile": "synthetic",
        "credential": credential,
        "profile_dir_override": tmp_path,
    }
    assert fake.closed is True


@pytest.mark.parametrize(
    "failure_stage",
    ["first-probe", "refresh", "retry-probe", "save"],
)
def test_doctor_closes_every_constructed_client_before_exception_escapes(
    monkeypatch,
    failure_stage,
):
    credential = _credential(doc_ids={"profile": "90000000000000021"})
    failure = RuntimeError(f"{failure_stage} failure")
    logged_in = {"data": {"user": {"pk": credential.ds_user_id}}}
    if failure_stage == "first-probe":
        response_sets = [[failure]]
    elif failure_stage == "retry-probe":
        response_sets = [
            [errors.PersistedOperationDriftError("stale profile doc ID")],
            [failure],
        ]
    else:
        response_sets = [[logged_in]]
    clients: list[FakeReadClient] = []
    save_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def make_client(_credential, *, max_requests):
        assert max_requests == 1
        fake = FakeReadClient(response_sets[len(clients)])
        clients.append(fake)
        return fake

    def reanchor(*_args):
        assert all(fake.closed for fake in clients)
        if failure_stage == "refresh":
            raise failure
        return {"profile": "90000000000000022"}

    def save(*args, **kwargs):
        assert all(fake.closed for fake in clients)
        save_calls.append((args, kwargs))
        if failure_stage == "save":
            raise failure
        raise AssertionError("save must not be reached")

    monkeypatch.setattr(session.auth, "load_session", lambda *args, **kwargs: credential)
    monkeypatch.setattr(session.client, "ReadClient", make_client)
    monkeypatch.setattr(session.docids, "reanchor_via_main_js", reanchor)
    monkeypatch.setattr(session.auth, "save_session", save)

    with pytest.raises(RuntimeError) as caught:
        session.run_doctor("synthetic", refresh=True)

    assert caught.value is failure
    assert len(clients) == (2 if failure_stage == "retry-probe" else 1)
    assert all(fake.closed for fake in clients)
    assert len(save_calls) == (1 if failure_stage == "save" else 0)


@pytest.mark.parametrize(
    ("url", "html", "expected"),
    [
        ("https://www.threads.com/checkpoint/step", None, "checkpoint"),
        ("https://www.threads.com/challenge/step", None, "checkpoint"),
        ("https://www.instagram.com/accounts/login/", None, "login"),
        (
            "https://www.threads.com/accounts/login/",
            "challenge_required",
            "checkpoint",
        ),
        (
            "https://www.threads.com/accounts/login/",
            "CAA_LOGIN_FORM_DATA",
            "login",
        ),
        ("https://www.threads.com/", "challenge_required", "checkpoint"),
        ("https://www.threads.com/", "CAA_LOGIN_FORM_DATA", "login"),
        ("https://www.threads.com/", "ordinary page", None),
    ],
)
def test_detect_wall_uses_url_and_body_markers(url, html, expected):
    assert session.detect_wall(url, html) == expected


@pytest.mark.parametrize(
    ("url_value", "html_value"),
    [
        (RuntimeError("url unavailable"), '{"is_logged_in":true}'),
        (session._THREADS_HOME, RuntimeError("content unavailable")),
        (RuntimeError("url unavailable"), RuntimeError("content unavailable")),
        (None, '{"is_logged_in":true}'),
        (session._THREADS_HOME, b'{"is_logged_in":true}'),
    ],
    ids=[
        "url-read-fails",
        "content-read-fails",
        "both-reads-fail",
        "url-type-invalid",
        "content-type-invalid",
    ],
)
def test_page_wall_returns_unknown_when_either_browser_read_is_unusable(
    url_value,
    html_value,
):
    class FakePage:
        @property
        def url(self):
            if isinstance(url_value, Exception):
                raise url_value
            return url_value

        def content(self):
            if isinstance(html_value, Exception):
                raise html_value
            return html_value

    assert session._page_wall(FakePage()) == "unknown"


@pytest.mark.parametrize(
    ("failing_read", "expected"),
    [("url", "unknown"), ("content", "checkpoint")],
)
def test_page_wall_requires_a_readable_origin_before_preserving_checkpoint_reads(
    failing_read,
    expected,
):
    failure = errors.ChallengeError(f"{failing_read} checkpoint")

    class FakePage:
        @property
        def url(self):
            if failing_read == "url":
                raise failure
            return session._THREADS_HOME

        def content(self):
            if failing_read == "content":
                raise failure
            return '{"is_logged_in":true}'

    assert session._page_wall(FakePage()) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://threads.com/", "clean"),
        ("https://threads.net/", "clean"),
        ("https://www.threads.com/search", "clean"),
        ("https://static.eu.threads.net/path", "clean"),
        ("https://threads.com:443/", "clean"),
        ("http://threads.com/", "unknown"),
        ("about:blank", "unknown"),
        ("https://example.com/", "unknown"),
        ("https://threads.com.attacker.test/", "unknown"),
        ("https://threads.net.attacker.test/", "unknown"),
        ("https://evilthreads.net/", "unknown"),
        ("https://threads.com@attacker.test/", "unknown"),
        ("https://attacker.test@threads.com/", "unknown"),
        ("https://.threads.com/", "unknown"),
        ("https://threads.com./", "unknown"),
        ("https://threads.com:444/", "unknown"),
        ("https://threads.com:not-a-port/", "unknown"),
        ("https://[threads.com/", "unknown"),
        ("https://threads.\ncom/", "unknown"),
    ],
    ids=[
        "com-root",
        "net-root",
        "com-subdomain",
        "net-nested-subdomain",
        "default-https-port",
        "http",
        "about-blank",
        "foreign-host",
        "com-suffix-confusion",
        "net-suffix-confusion",
        "net-prefix-confusion",
        "trusted-looking-userinfo",
        "userinfo-on-trusted-host",
        "empty-leading-label",
        "trailing-root-dot",
        "non-default-port",
        "malformed-port",
        "malformed-bracket-host",
        "control-character-host",
    ],
)
def test_page_wall_requires_a_trusted_https_threads_origin(url, expected):
    page = SimpleNamespace(url=url, content=lambda: '{"is_logged_in":true}')

    assert session._page_wall(page) == expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('{"is_logged_in":true}', "clean"),
        ('{"DTSGInitialData":{}}', "clean"),
        ('{"viewer_id":"10001"}', "clean"),
        ("", "unknown"),
        (" \n\t", "unknown"),
        ("Internal Server Error", "unknown"),
        ("<html><title>500 Error</title></html>", "unknown"),
        ("ordinary Threads page", "unknown"),
        ('{"is_logged_in":false}', "unknown"),
        ('{"is_logged_in":true} CAA_LOGIN_FORM_DATA', "unknown"),
        ('{"is_logged_in":true} challenge_required', "checkpoint"),
    ],
    ids=[
        "logged-in-flag",
        "dtsg-marker",
        "viewer-marker",
        "empty",
        "whitespace-only",
        "error-text",
        "error-document",
        "non-logged-in-content",
        "logged-out-marker",
        "login-wall-wins",
        "checkpoint-wall-wins",
    ],
)
def test_page_wall_requires_recognized_nonempty_logged_in_content(html, expected):
    page = SimpleNamespace(url=session._THREADS_HOME, content=lambda: html)

    assert session._page_wall(page) == expected


@pytest.mark.parametrize(
    ("url", "html", "expected"),
    [
        (
            "https://www.threads.com/checkpoint/step",
            '{"is_logged_in":true}',
            "checkpoint",
        ),
        (
            "https://www.threads.com/accounts/login/",
            '{"is_logged_in":true}',
            "unknown",
        ),
        (
            "https://www.threads.com/accounts/login/",
            '{"is_logged_in":true} challenge_required',
            "checkpoint",
        ),
        (
            "https://attacker.test/checkpoint/step",
            '{"is_logged_in":true} challenge_required',
            "unknown",
        ),
        ("https://www.threads.com/checkpoint/step", "", "checkpoint"),
    ],
    ids=[
        "trusted-checkpoint",
        "trusted-login-wall",
        "checkpoint-body-on-trusted-login-url",
        "foreign-checkpoint-lookalike",
        "empty-checkpoint-document",
    ],
)
def test_page_wall_requires_valid_page_evidence_before_wall_classification(
    url,
    html,
    expected,
):
    page = SimpleNamespace(url=url, content=lambda: html)

    assert session._page_wall(page) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://www.threads.com/checkpoint/step",
        "https://attacker.test/accounts/login/",
        "https://www.threads.com.attacker.test/challenge/step",
        "https://attacker.test@www.threads.com/checkpoint/step",
        "https://www.threads.com:444/checkpoint/step",
    ],
)
def test_page_wall_never_reads_challenge_body_before_origin_trust(url):
    reads: list[str] = []

    class FakePage:
        @property
        def url(self):
            reads.append("url")
            return url

        def content(self):
            reads.append("content")
            return "challenge_required"

    assert session._page_wall(FakePage()) == "unknown"
    assert reads == ["url"]


@pytest.mark.parametrize(
    "failed_navigation_index",
    range(4),
    ids=["home", "search", "public-profile", "post-permalink"],
)
def test_harvest_navigation_failed_goto_is_unknown_without_reading_stale_page(
    failed_navigation_index,
):
    post_url = "https://www.threads.com/@threads/post/synthetic"
    navigation_targets = [*session._HARVEST_NAV_URLS, post_url]

    class FakePage:
        def __init__(self):
            self.url = session._THREADS_HOME
            self.goto_calls: list[str] = []
            self.wait_calls = 0
            self.content_reads = 0
            self.selector_calls = 0

        def goto(self, url, *, wait_until, timeout):
            assert wait_until == "domcontentloaded"
            assert timeout == 20_000
            self.goto_calls.append(url)
            if len(self.goto_calls) - 1 == failed_navigation_index:
                raise RuntimeError("synthetic failed navigation")
            self.url = url

        def wait_for_timeout(self, milliseconds):
            assert milliseconds == 2_000
            self.wait_calls += 1

        def content(self):
            self.content_reads += 1
            return '{"is_logged_in":true}'

        def eval_on_selector(self, selector, expression):
            assert selector == 'a[href*="/post/"]'
            assert expression == "element => element.getAttribute('href')"
            self.selector_calls += 1
            return "/@threads/post/synthetic"

    page = FakePage()

    assert session._best_effort_harvest_navigation(page) == "unknown"
    assert page.goto_calls == navigation_targets[: failed_navigation_index + 1]
    assert page.wait_calls == failed_navigation_index
    assert page.content_reads == failed_navigation_index
    assert page.selector_calls == (1 if failed_navigation_index == 3 else 0)


def test_harvest_navigation_validates_redirect_origin_before_waiting_or_reading():
    class FakePage:
        url = session._THREADS_HOME

        def goto(self, *_args, **_kwargs):
            self.url = "https://www.threads.com.attacker.test/private"

        def wait_for_timeout(self, _milliseconds):
            raise AssertionError("untrusted redirects must not be waited on")

        def content(self):
            raise AssertionError("untrusted redirects must not be read")

        def eval_on_selector(self, *_args):
            raise AssertionError("untrusted redirects must not be evaluated")

    assert session._best_effort_harvest_navigation(FakePage()) == "unknown"


@pytest.mark.parametrize(
    ("href", "expected_post_url"),
    [
        (
            "/@threads/post/synthetic-relative",
            "https://www.threads.com/@threads/post/synthetic-relative",
        ),
        (
            "https://www.threads.net/@threads/post/synthetic-absolute",
            "https://www.threads.net/@threads/post/synthetic-absolute",
        ),
        (
            "https://threads.com:443/@threads/post/synthetic-port",
            "https://threads.com:443/@threads/post/synthetic-port",
        ),
    ],
    ids=["root-relative", "trusted-absolute", "explicit-https-port"],
)
def test_harvest_navigation_resolves_only_exact_trusted_post_permalinks(
    href,
    expected_post_url,
):
    class FakePage:
        def __init__(self):
            self.url = session._THREADS_HOME
            self.goto_calls: list[str] = []
            self.wait_calls = 0
            self.selector_calls = 0

        def goto(self, url, *, wait_until, timeout):
            assert wait_until == "domcontentloaded"
            assert timeout == 20_000
            self.goto_calls.append(url)
            self.url = url

        def wait_for_timeout(self, milliseconds):
            assert milliseconds == 2_000
            self.wait_calls += 1

        def content(self):
            return '{"is_logged_in":true}'

        def eval_on_selector(self, selector, expression):
            assert selector == 'a[href*="/post/"]'
            assert expression == "element => element.getAttribute('href')"
            self.selector_calls += 1
            return href

    page = FakePage()

    assert session._best_effort_harvest_navigation(page) == "clean"
    assert page.goto_calls == [*session._HARVEST_NAV_URLS, expected_post_url]
    assert page.wait_calls == len(session._HARVEST_NAV_URLS) + 1
    assert page.selector_calls == 1


@pytest.mark.parametrize(
    "href",
    [
        "//www.threads.com/@threads/post/synthetic-secret",
        "https://attacker.test/@threads/post/synthetic-secret",
        "https://www.threads.com.attacker.test/@threads/post/synthetic-secret",
        "https://www.threads.com@attacker.test/@threads/post/synthetic-secret",
        "https://attacker.test@www.threads.com/@threads/post/synthetic-secret",
        "http://www.threads.com/@threads/post/synthetic-secret",
        "https://www.threads.com:444/@threads/post/synthetic-secret",
        "/@threads/%70ost/synthetic-secret",
        "/@threads/post/synthetic-secret%2Fconfusion",
        "/@threads/post/synthetic-secret\ncontrol",
        r"https://www.threads.com\@attacker.test/@threads/post/synthetic-secret",
        "../@threads/post/synthetic-secret",
        "/@threads/profile/synthetic-secret",
    ],
    ids=[
        "protocol-relative",
        "untrusted-host",
        "trusted-suffix-confusion",
        "trusted-looking-userinfo",
        "userinfo-on-trusted-host",
        "plain-http",
        "untrusted-port",
        "encoded-post-segment",
        "encoded-path-confusion",
        "control-character",
        "backslash-authority-confusion",
        "dot-segment-confusion",
        "non-permalink-path",
    ],
)
def test_harvest_navigation_rejects_hostile_post_hrefs_without_navigation_or_leakage(
    href,
    capsys,
):
    class FakePage:
        def __init__(self):
            self.url = session._THREADS_HOME
            self.goto_calls: list[str] = []
            self.wait_calls = 0
            self.content_reads = 0

        def goto(self, url, *, wait_until, timeout):
            assert wait_until == "domcontentloaded"
            assert timeout == 20_000
            self.goto_calls.append(url)
            self.url = url

        def wait_for_timeout(self, milliseconds):
            assert milliseconds == 2_000
            self.wait_calls += 1

        def content(self):
            self.content_reads += 1
            return '{"is_logged_in":true}'

        def eval_on_selector(self, selector, expression):
            assert selector == 'a[href*="/post/"]'
            assert expression == "element => element.getAttribute('href')"
            return href

    page = FakePage()

    assert session._best_effort_harvest_navigation(page) == "clean"
    assert page.goto_calls == list(session._HARVEST_NAV_URLS)
    assert page.wait_calls == len(session._HARVEST_NAV_URLS)
    assert page.content_reads == len(session._HARVEST_NAV_URLS) + 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_harvest_navigation_preserves_typed_navigation_checkpoint():
    failure = errors.ChallengeError("synthetic navigation checkpoint")

    class FakePage:
        def goto(self, *_args, **_kwargs):
            raise failure

        def wait_for_timeout(self, _milliseconds):
            raise AssertionError("failed navigation must skip its wait")

        def content(self):
            raise AssertionError("typed checkpoint must not inspect a stale page")

        def eval_on_selector(self, *_args):
            raise AssertionError("typed checkpoint must stop navigation")

    assert session._best_effort_harvest_navigation(FakePage()) == "checkpoint"


@pytest.mark.parametrize(
    ("current_url", "current_html", "expected"),
    [
        ("https://www.threads.com/@threads", '{"is_logged_in":true}', "clean"),
        ("https://www.threads.com/@threads", "ordinary page", "unknown"),
        ("about:blank", '{"is_logged_in":true}', "unknown"),
        ("https://foreign.example/", '{"is_logged_in":true}', "unknown"),
        (
            "https://www.threads.com/checkpoint/step",
            '{"is_logged_in":true}',
            "checkpoint",
        ),
    ],
    ids=["clean", "non-logged-in", "about-blank", "foreign", "checkpoint"],
)
def test_harvest_selector_failure_uses_current_page_clean_predicate(
    current_url,
    current_html,
    expected,
):
    class FakePage:
        def __init__(self):
            self.url = session._THREADS_HOME
            self.html = '{"is_logged_in":true}'
            self.goto_calls: list[str] = []
            self.content_reads = 0
            self.selector_calls = 0

        def goto(self, url, *, wait_until, timeout):
            assert wait_until == "domcontentloaded"
            assert timeout == 20_000
            self.goto_calls.append(url)
            self.url = url
            self.html = '{"is_logged_in":true}'

        def wait_for_timeout(self, milliseconds):
            assert milliseconds == 2_000

        def content(self):
            self.content_reads += 1
            return self.html

        def eval_on_selector(self, *_args):
            self.selector_calls += 1
            self.url = current_url
            self.html = current_html
            raise RuntimeError("synthetic selector lookup failure")

    page = FakePage()

    assert session._best_effort_harvest_navigation(page) == expected
    assert page.goto_calls == list(session._HARVEST_NAV_URLS)
    reads_after_selector = int(
        session._is_trusted_threads_page_url(current_url)
        and session.detect_wall(current_url) != "checkpoint"
    )
    assert page.content_reads == len(session._HARVEST_NAV_URLS) + reads_after_selector
    assert page.selector_calls == 1


def test_looks_logged_in_requires_all_cookies_and_a_positive_body_marker():
    cookies = ["sessionid", "ds_user_id", "csrftoken"]
    assert session.looks_logged_in('{"is_logged_in":true}', cookies) is True
    assert session.looks_logged_in('{"is_logged_in":true}', cookies[:-1]) is False
    assert session.looks_logged_in("ordinary page", cookies) is False
    assert (
        session.looks_logged_in(
            '{"is_logged_in":true} CAAFetaAYMHPasswordEntryQuery',
            cookies,
        )
        is False
    )


@pytest.mark.parametrize(
    "domain",
    ["threads.com", ".threads.net", "www.threads.com", "static.eu.threads.net"],
)
def test_cookie_jar_accepts_threads_roots_and_subdomains(domain):
    records = [
        {"name": name, "value": f"valid-{name}", "domain": domain}
        for name in session._REQUIRED_LOGIN_COOKIES
    ]

    assert session._cookie_jar(records) == {
        name: f"valid-{name}" for name in session._REQUIRED_LOGIN_COOKIES
    }


@pytest.mark.parametrize(
    "untrusted_domain",
    ["threads.com.attacker.test", "thread\u017f.com", "threads.com "],
)
@pytest.mark.parametrize("untrusted_first", [False, True])
def test_cookie_jar_ignores_unrelated_duplicates_regardless_of_order(
    untrusted_first,
    untrusted_domain,
):
    trusted = [
        {"name": name, "value": f"valid-{name}", "domain": ".threads.com"}
        for name in session._REQUIRED_LOGIN_COOKIES
    ]
    untrusted = [
        {"name": name, "value": f"evil-{name}", "domain": untrusted_domain}
        for name in session._REQUIRED_LOGIN_COOKIES
    ]
    records = untrusted + trusted if untrusted_first else trusted + untrusted

    assert session._cookie_jar(records) == {
        name: f"valid-{name}" for name in session._REQUIRED_LOGIN_COOKIES
    }


@pytest.mark.parametrize("reverse_duplicates", [False, True])
def test_cookie_jar_rejects_conflicting_trusted_duplicates_in_either_order(
    reverse_duplicates,
):
    secrets = (
        f"trusted-cookie-first-{reverse_duplicates}-unique",
        f"trusted-cookie-second-{reverse_duplicates}-unique",
    )
    duplicates = [
        {"name": "sessionid", "value": secrets[0], "domain": "threads.com"},
        {"name": "sessionid", "value": secrets[1], "domain": ".threads.net"},
    ]
    if reverse_duplicates:
        duplicates.reverse()
    records = [
        *duplicates,
        *[
            {"name": name, "value": f"valid-{name}", "domain": "www.threads.com"}
            for name in session._REQUIRED_LOGIN_COOKIES
            if name != "sessionid"
        ],
    ]

    cookies = session._cookie_jar(records)

    assert cookies == {}
    assert all(secret not in repr(cookies) for secret in secrets)


def test_cookie_jar_coalesces_identical_trusted_duplicates():
    records = [
        {"name": "sessionid", "value": "valid-sessionid", "domain": "threads.com"},
        {"name": "sessionid", "value": "valid-sessionid", "domain": ".threads.net"},
        *[
            {"name": name, "value": f"valid-{name}", "domain": "www.threads.com"}
            for name in session._REQUIRED_LOGIN_COOKIES
            if name != "sessionid"
        ],
    ]

    assert session._cookie_jar(records) == {
        name: f"valid-{name}" for name in session._REQUIRED_LOGIN_COOKIES
    }


def test_cookie_jar_fails_closed_without_domain_metadata():
    records = [
        {"name": name, "value": f"unscoped-{name}"} for name in session._REQUIRED_LOGIN_COOKIES
    ]

    assert session._cookie_jar(records) == {}


def test_isolated_browser_cache_is_scoped_and_restores_environment(
    monkeypatch,
    tmp_path,
):
    cache = tmp_path / "browser-cache"
    monkeypatch.setattr(session.config, "browsers_dir", lambda: cache)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "pre-existing")

    with session._isolated_browser_cache():
        assert cache.is_dir()
        assert session.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(cache)

    assert session.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "pre-existing"


def test_build_stealth_session_imports_browser_dependency_only_when_called(
    monkeypatch,
    tmp_path,
):
    observed: dict[str, object] = {}

    class FakeStealthySession:
        def __init__(self, **kwargs):
            observed.update(kwargs)

    scrapling_module = ModuleType("scrapling")
    scrapling_module.__path__ = []
    fetchers_module = ModuleType("scrapling.fetchers")
    fetchers_module.StealthySession = FakeStealthySession
    scrapling_module.fetchers = fetchers_module
    monkeypatch.setitem(sys.modules, "scrapling", scrapling_module)
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", fetchers_module)
    browser_dir = tmp_path / "profiles" / "synthetic" / "browser"
    monkeypatch.setattr(session.config, "browser_profile_dir", lambda *args, **kwargs: browser_dir)

    built = session._build_stealth_session("synthetic", profile_dir_override=tmp_path)

    assert isinstance(built, FakeStealthySession)
    assert browser_dir.is_dir()
    assert observed == {
        "real_chrome": True,
        "headless": False,
        "user_data_dir": str(browser_dir),
        "init_script": str(session._STEALTH_INIT_SCRIPT),
    }


def _browser_request(
    *,
    url=gql.GRAPHQL_URL,
    method="POST",
    resource_type="xhr",
    post_data="",
    headers=None,
    frame_url=session._THREADS_HOME,
):
    return SimpleNamespace(
        url=url,
        method=method,
        resource_type=resource_type,
        frame=SimpleNamespace(url=frame_url),
        post_data=post_data,
        headers=dict(headers or {}),
    )


@pytest.mark.parametrize("resource_type", ["fetch", "xhr"])
def test_browser_request_projection_retains_only_a_valid_operation_and_doc_id(resource_type):
    private_body = "variables=%7B%22query%22%3A%22private-query-sentinel%22%7D"
    request = _browser_request(
        resource_type=resource_type,
        post_data=(
            f"fb_api_req_friendly_name={gql.PROFILE_OPERATION}"
            "&doc_id=22222222222222222"
            f"&{private_body}&fb_dtsg=private-token-sentinel"
        ),
        headers={
            "X-FB-Friendly-Name": gql.PROFILE_OPERATION,
            "Authorization": "private-authorization-sentinel",
            "Cookie": "private-cookie-sentinel",
        },
    )

    projection = session._request_artifact_for_harvest(request)

    assert projection == {
        "operation": gql.PROFILE_OPERATION,
        "doc_id": "22222222222222222",
    }
    projected = repr(projection)
    for private_value in (
        private_body,
        "private-query-sentinel",
        "private-token-sentinel",
        "private-authorization-sentinel",
        "private-cookie-sentinel",
        gql.GRAPHQL_URL,
    ):
        assert private_value not in projected


@pytest.mark.parametrize(
    "frame_url",
    [
        "https://attacker.test/private-document",
        "not a document URL",
        RuntimeError("private-unreadable-frame-origin-sentinel"),
    ],
    ids=["foreign", "malformed", "unreadable"],
)
def test_browser_request_projection_rejects_untrusted_frame_before_body_or_headers(
    frame_url,
):
    events: list[str] = []

    class GuardedFrame:
        @property
        def url(self):
            events.append("frame-url")
            if isinstance(frame_url, Exception):
                raise frame_url
            return frame_url

    class GuardedRequest:
        url = gql.GRAPHQL_URL
        method = "POST"
        resource_type = "xhr"
        frame = GuardedFrame()

        @property
        def post_data(self):
            events.append("post-data")
            return f"fb_api_req_friendly_name={gql.FEED_OPERATION}&doc_id=123"

        @property
        def headers(self):
            events.append("headers")
            return {"x-fb-friendly-name": gql.FEED_OPERATION}

    assert session._request_artifact_for_harvest(GuardedRequest()) is None
    assert events == ["frame-url"]


@pytest.mark.parametrize(
    ("url", "method", "resource_type"),
    [
        (f"{gql.GRAPHQL_URL}?__a=1", "POST", "xhr"),
        (gql.GRAPHQL_URL.replace("https://", "http://"), "POST", "xhr"),
        (
            gql.GRAPHQL_URL.replace("www.threads.com", "www.threads.com.attacker.test"),
            "POST",
            "xhr",
        ),
        (
            gql.GRAPHQL_URL.replace("www.threads.com", "attacker.test@www.threads.com"),
            "POST",
            "xhr",
        ),
        (
            gql.GRAPHQL_URL.replace("www.threads.com", "www.threads.com:444"),
            "POST",
            "xhr",
        ),
        (gql.GRAPHQL_URL, "GET", "xhr"),
        (gql.GRAPHQL_URL, "post", "xhr"),
        (gql.GRAPHQL_URL, "POST", "document"),
    ],
    ids=[
        "query-string",
        "http",
        "suffix-confusion",
        "userinfo",
        "non-default-port",
        "get",
        "noncanonical-method",
        "document",
    ],
)
def test_browser_request_projection_requires_the_exact_graphql_transport(
    url,
    method,
    resource_type,
):
    request = _browser_request(
        url=url,
        method=method,
        resource_type=resource_type,
        post_data=f"fb_api_req_friendly_name={gql.FEED_OPERATION}&doc_id=123",
        headers={"x-fb-friendly-name": gql.FEED_OPERATION},
    )

    assert session._request_artifact_for_harvest(request) is None


@pytest.mark.parametrize(
    "post_data",
    [
        "",
        "fb_api_req_friendly_name=UnknownPrivateOperation&doc_id=123",
        f"fb_api_req_friendly_name={gql.FEED_OPERATION}",
        f"fb_api_req_friendly_name={gql.FEED_OPERATION}&doc_id=",
        f"fb_api_req_friendly_name={gql.FEED_OPERATION}&doc_id=12x",
        f"fb_api_req_friendly_name={gql.FEED_OPERATION}&doc_id=１２３",
        f"fb_api_req_friendly_name={gql.FEED_OPERATION}&doc_id=123&doc_id=123",
        (
            f"fb_api_req_friendly_name={gql.FEED_OPERATION}"
            f"&fb_api_req_friendly_name={gql.FEED_OPERATION}&doc_id=123"
        ),
        f"fb_api_req_friendly_name={gql.FEED_OPERATION}&doc_id=%ZZ",
        f"fb_api_req_friendly_name={gql.FEED_OPERATION}&doc_id=%FF",
        f"fb_api_req_friendly_name={gql.FEED_OPERATION}&doc_id",
        '{"doc_id":"123"}',
        b"\xff",
        (
            f"fb_api_req_friendly_name={gql.FEED_OPERATION}&doc_id=123&padding="
            + ("x" * session._MAX_GRAPHQL_FORM_BODY_BYTES)
        ),
        "&".join(
            [
                f"fb_api_req_friendly_name={gql.FEED_OPERATION}",
                "doc_id=123",
                *[
                    f"ignored_{index}=value"
                    for index in range(session._MAX_GRAPHQL_FORM_FIELDS - 1)
                ],
            ]
        ),
        (
            f"fb_api_req_friendly_name={gql.FEED_OPERATION}&doc_id="
            + ("1" * (session._MAX_GRAPHQL_DOC_ID_DIGITS + 1))
        ),
    ],
    ids=[
        "empty",
        "unknown-operation",
        "missing-doc-id",
        "empty-doc-id",
        "nonnumeric-doc-id",
        "non-ascii-doc-id",
        "duplicate-doc-id",
        "duplicate-operation",
        "malformed-percent",
        "non-ascii-percent-value",
        "malformed-field",
        "json",
        "non-ascii-bytes",
        "body-size",
        "field-count",
        "doc-id-size",
    ],
)
def test_browser_request_projection_rejects_unbounded_or_malformed_form_bodies(post_data):
    request = _browser_request(
        post_data=post_data,
        headers={"x-fb-friendly-name": gql.FEED_OPERATION},
    )

    assert session._request_artifact_for_harvest(request) is None


def test_browser_request_projection_rejects_conflicting_friendly_names():
    request = _browser_request(
        post_data=f"fb_api_req_friendly_name={gql.PROFILE_OPERATION}&doc_id=123",
        headers={"x-fb-friendly-name": gql.FEED_OPERATION},
    )

    assert session._request_artifact_for_harvest(request) is None


def test_browser_request_projection_requires_a_known_listener_friendly_name():
    request = _browser_request(
        post_data=f"fb_api_req_friendly_name={gql.FEED_OPERATION}&doc_id=123"
    )

    assert session._request_artifact_for_harvest(request) is None


@pytest.mark.parametrize(
    "url_value",
    [
        "https://attacker.test/accounts/login/",
        "http://www.threads.com/accounts/login/",
        "https://attacker.test@www.threads.com/",
        "https://www.threads.com.attacker.test/",
        "https://www.threads.com:444/",
        RuntimeError("private-unreadable-origin-sentinel"),
    ],
    ids=["foreign", "http", "userinfo", "suffix-confusion", "non-default-port", "unreadable"],
)
@pytest.mark.parametrize(
    "html",
    ["CAA_LOGIN_FORM_DATA", '{"is_logged_in":true}', "challenge_required"],
    ids=["login-body", "logged-in-body", "challenge-body"],
)
def test_run_login_rejects_untrusted_page_matrix_before_prompt_or_body_access(
    monkeypatch,
    tmp_path,
    capsys,
    url_value,
    html,
):
    events: list[str] = []
    save_calls: list[object] = []

    class FakePage:
        @property
        def url(self):
            events.append("url")
            if isinstance(url_value, Exception):
                raise url_value
            return url_value

        def content(self):
            events.append("content")
            return html

        @property
        def context(self):
            raise AssertionError("untrusted pages must not expose cookies")

        def evaluate(self, _expression):
            raise AssertionError("untrusted pages must not evaluate JavaScript")

        def wait_for_timeout(self, _milliseconds):
            raise AssertionError("untrusted pages must not be waited on")

        def on(self, _event, _handler):
            raise AssertionError("untrusted pages must not attach listeners")

    page = FakePage()

    class FakeBrowser:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.closed = True

        def fetch(self, _url, *, page_action, timeout):
            assert timeout == 60_000
            page_action(page)
            return SimpleNamespace()

    browser = FakeBrowser()
    monkeypatch.setattr(session, "_isolated_browser_cache", nullcontext)
    monkeypatch.setattr(session, "_build_stealth_session", lambda *args, **kwargs: browser)
    monkeypatch.setattr(session.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        session.docids,
        "harvest_from_browser",
        lambda _artifacts: (_ for _ in ()).throw(AssertionError("must not harvest")),
    )
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: save_calls.append((args, kwargs)),
    )

    assert (
        session.run_login(
            "synthetic",
            profile_dir_override=tmp_path,
            timeout_seconds=1,
        )
        is False
    )

    assert browser.closed is True
    assert events == ["url"]
    assert save_calls == []
    captured = capsys.readouterr()
    assert "A browser window is open" not in captured.err
    assert "Timed out" not in captured.err
    assert "private-unreadable-origin-sentinel" not in captured.err


def test_run_login_validates_the_first_url_before_printing_or_classifying_body(
    monkeypatch,
    tmp_path,
):
    events: list[str] = []

    class FakePage:
        @property
        def url(self):
            events.append("url")
            return session._THREADS_HOME

        def content(self):
            events.append("content")
            return "CAA_LOGIN_FORM_DATA"

        def wait_for_timeout(self, milliseconds):
            assert milliseconds == 2_000
            events.append("wait")

    page = FakePage()

    class FakeBrowser:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def fetch(self, _url, *, page_action, timeout):
            assert timeout == 60_000
            page_action(page)
            return SimpleNamespace()

    def observed_print(message, *args, **kwargs):
        del args, kwargs
        events.append("prompt" if message.startswith("A browser window") else "timeout")

    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(builtins, "print", observed_print)
    monkeypatch.setattr(session, "_isolated_browser_cache", nullcontext)
    monkeypatch.setattr(session, "_build_stealth_session", lambda *args, **kwargs: FakeBrowser())
    monkeypatch.setattr(session.time, "monotonic", lambda: next(ticks, 2.0))
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not save")),
    )

    assert (
        session.run_login(
            "synthetic",
            profile_dir_override=tmp_path,
            timeout_seconds=1,
        )
        is False
    )
    assert events == ["url", "prompt", "content", "url", "wait", "timeout"]


def test_run_login_revalidates_logged_in_page_before_cookie_access(
    monkeypatch,
    tmp_path,
):
    events: list[str] = []
    urls = iter(
        [
            session._THREADS_HOME,
            "https://www.threads.com.attacker.test/private",
        ]
    )

    class FakePage:
        @property
        def url(self):
            events.append("url")
            return next(urls)

        def content(self):
            events.append("content")
            return '{"is_logged_in":true}'

        @property
        def context(self):
            raise AssertionError("redirected foreign pages must not expose cookies")

        def evaluate(self, _expression):
            raise AssertionError("redirected foreign pages must not evaluate JavaScript")

        def wait_for_timeout(self, _milliseconds):
            raise AssertionError("redirected foreign pages must not be waited on")

    page = FakePage()

    class FakeBrowser:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def fetch(self, _url, *, page_action, timeout):
            assert timeout == 60_000
            page_action(page)
            return SimpleNamespace()

    monkeypatch.setattr(session, "_isolated_browser_cache", nullcontext)
    monkeypatch.setattr(session, "_build_stealth_session", lambda *args, **kwargs: FakeBrowser())
    monkeypatch.setattr(session.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not save")),
    )

    assert (
        session.run_login(
            "synthetic",
            profile_dir_override=tmp_path,
            timeout_seconds=1,
        )
        is False
    )
    assert events == ["url", "content", "url"]


def test_run_login_polls_without_input_and_saves_minimal_credential(
    monkeypatch,
    tmp_path,
):
    cookie_records = [
        {
            "name": "sessionid",
            "value": "attacker-session-before",
            "domain": "unrelated.example",
        },
        {"name": "sessionid", "value": "new-session", "domain": ".threads.com"},
        {"name": "ds_user_id", "value": "20002", "domain": "www.threads.net"},
        {
            "name": "ds_user_id",
            "value": "attacker-user-after",
            "domain": "threads.net.attacker.test",
        },
        {"name": "csrftoken", "value": "new-csrf", "domain": "auth.threads.com"},
        {
            "name": "csrftoken",
            "value": "attacker-csrf-after",
            "domain": "unrelated.example",
        },
        {
            "name": "unrelated_cookie",
            "value": "must-not-persist",
            "domain": ".threads.com",
        },
    ]
    cookie_scopes: list[tuple[str, ...]] = []

    def browser_cookies(urls):
        cookie_scopes.append(tuple(urls))
        return cookie_records

    page = SimpleNamespace(
        url="https://www.threads.com/",
        content=lambda: '{"is_logged_in":true}',
        context=SimpleNamespace(cookies=browser_cookies),
        evaluate=lambda _expression: "browser-agent/2.0",
        wait_for_timeout=lambda _milliseconds: None,
    )

    class RawFallbackResponse:
        @property
        def captured_xhr(self):
            raise AssertionError("response.captured_xhr must never be read")

    response = RawFallbackResponse()
    saved: dict[str, object] = {}
    browser_calls: dict[str, object] = {}
    harvest_inputs: list[list[object]] = []

    class FakeBrowser:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def fetch(self, url, *, page_action, timeout):
            browser_calls.update(url=url, timeout=timeout)
            page_action(page)
            return response

    monkeypatch.setattr(
        builtins,
        "input",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("input() is forbidden")),
    )
    monkeypatch.setattr(session, "_isolated_browser_cache", nullcontext)
    monkeypatch.setattr(session, "_build_stealth_session", lambda *args, **kwargs: FakeBrowser())
    monkeypatch.setattr(session, "_best_effort_harvest_navigation", lambda received: "clean")
    monkeypatch.setattr(session.time, "monotonic", lambda: 0.0)

    def harvest_without_listener(artifacts):
        snapshot = list(artifacts)
        harvest_inputs.append(snapshot)
        return {}

    monkeypatch.setattr(session.docids, "harvest_from_browser", harvest_without_listener)

    def save(profile, credential, *, profile_dir_override=None):
        saved.update(
            profile=profile,
            credential=credential,
            profile_dir_override=profile_dir_override,
        )

    monkeypatch.setattr(session.auth, "save_session", save)

    result = session.run_login(
        "synthetic",
        profile_dir_override=tmp_path,
        timeout_seconds=1,
    )

    assert result is True
    assert browser_calls == {"url": session._THREADS_HOME, "timeout": 60_000}
    credential = saved["credential"]
    assert isinstance(credential, SessionCredential)
    assert credential.cookies == {
        "sessionid": "new-session",
        "ds_user_id": "20002",
        "csrftoken": "new-csrf",
    }
    assert credential.user_agent == "browser-agent/2.0"
    assert credential.doc_ids == {}
    assert credential.features == gql.DEFAULT_FEATURES
    assert saved["profile"] == "synthetic"
    assert saved["profile_dir_override"] == tmp_path
    assert cookie_scopes == [
        session._THREADS_COOKIE_URLS,
        session._THREADS_COOKIE_URLS,
    ]
    assert harvest_inputs == [[]]


def test_run_login_does_not_accept_required_cookies_from_unrelated_domains(
    monkeypatch,
    tmp_path,
):
    cookie_records = [
        {
            "name": "sessionid",
            "value": "attacker-session",
            "domain": "threads.com.attacker.test",
        },
        {
            "name": "ds_user_id",
            "value": "40004",
            "domain": "evilthreads.net",
        },
        {
            "name": "csrftoken",
            "value": "attacker-csrf",
            "domain": "unrelated.example",
        },
    ]
    cookie_scopes: list[object] = []

    def browser_cookies(urls=None):
        cookie_scopes.append(urls)
        return cookie_records

    page = SimpleNamespace(
        url=session._THREADS_HOME,
        content=lambda: '{"is_logged_in":true}',
        context=SimpleNamespace(cookies=browser_cookies),
        evaluate=lambda _expression: "browser-agent/2.0",
        wait_for_timeout=lambda _milliseconds: None,
    )
    response = SimpleNamespace()

    class FakeBrowser:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def fetch(self, _url, *, page_action, timeout):
            assert timeout == 60_000
            page_action(page)
            return response

    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(session, "_isolated_browser_cache", nullcontext)
    monkeypatch.setattr(session, "_build_stealth_session", lambda *args, **kwargs: FakeBrowser())
    monkeypatch.setattr(session, "_best_effort_harvest_navigation", lambda _page: "clean")
    monkeypatch.setattr(session.time, "monotonic", lambda: next(ticks, 2.0))
    monkeypatch.setattr(
        session.docids,
        "harvest_from_browser",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not harvest")),
    )
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not save")),
    )

    assert (
        session.run_login(
            "synthetic",
            profile_dir_override=tmp_path,
            timeout_seconds=1,
        )
        is False
    )
    assert cookie_scopes == [session._THREADS_COOKIE_URLS]


def test_run_login_harvests_only_bounded_projections_without_leaking_artifacts(
    monkeypatch,
    tmp_path,
    capsys,
):
    cookies = [
        {"name": "sessionid", "value": "saved-session", "domain": ".threads.com"},
        {"name": "ds_user_id", "value": "30003", "domain": "www.threads.net"},
        {"name": "csrftoken", "value": "saved-csrf", "domain": "auth.threads.com"},
        {
            "name": "unrelated_cookie",
            "value": "response-cookie-must-not-persist",
            "domain": ".threads.com",
        },
    ]
    operation_doc_ids = {
        operation: str(80000000000000000 + index)
        for index, operation in enumerate(docids.OPERATION_TO_KEY)
    }
    assert len(operation_doc_ids) == session._MAX_HARVEST_PROJECTIONS
    rejected_doc_id = "99999999999999999"
    rejected_body = (
        f"fb_api_req_friendly_name={gql.POST_OPERATION}&doc_id={rejected_doc_id}"
        "&variables=rejected-private-request-body"
    )
    events: list[str] = []
    overflow_reads: list[str] = []

    class PrivateRawResponse:
        @property
        def captured_xhr(self):
            raise AssertionError("response.captured_xhr must never be read")

        @property
        def request_headers(self):
            raise AssertionError("response headers must never be harvested")

    response = PrivateRawResponse()

    class BrokenRequest:
        @property
        def url(self):
            raise RuntimeError("private-disposed-request-sentinel")

    class OverflowRequest:
        @property
        def url(self):
            overflow_reads.append("url")
            return gql.GRAPHQL_URL

        method = "POST"
        resource_type = "xhr"
        post_data = (
            f"fb_api_req_friendly_name={gql.FEED_OPERATION}"
            "&doc_id=77777777777777777&variables=private-overflow-body"
        )
        headers = {"x-fb-friendly-name": gql.FEED_OPERATION}

    class FakePage:
        url = session._THREADS_HOME

        def __init__(self):
            self.context = SimpleNamespace(cookies=lambda _urls: cookies)
            self.request_handler = None

        def content(self):
            return '{"is_logged_in":true}'

        def evaluate(self, _expression):
            return "browser-agent/3.0"

        def wait_for_timeout(self, _milliseconds):
            return None

        def on(self, event, handler):
            assert self.request_handler is None
            events.append(f"listener:{event}")
            self.request_handler = handler

        def emit(self, request):
            assert self.request_handler is not None
            self.request_handler(request)

    page = FakePage()
    emitted_requests = [
        BrokenRequest(),
        _browser_request(
            url="https://unrelated.example/graphql/query",
            post_data=rejected_body,
            headers={"x-fb-friendly-name": gql.POST_OPERATION},
        ),
        _browser_request(
            url=f"{gql.GRAPHQL_URL}?__a=1",
            resource_type="fetch",
            post_data=rejected_body,
            headers={"x-fb-friendly-name": gql.POST_OPERATION},
        ),
    ]
    for index, (operation, live_doc_id) in enumerate(operation_doc_ids.items()):
        emitted_requests.append(
            _browser_request(
                resource_type="fetch" if index % 2 else "xhr",
                post_data=(
                    f"fb_api_req_friendly_name={operation}&doc_id={live_doc_id}"
                    f"&variables=private-body-sentinel-{index}"
                    "&fb_dtsg=private-form-token-sentinel"
                ),
                headers={
                    "X-FB-Friendly-Name": operation,
                    "Cookie": "private-cookie-header-sentinel",
                    "Authorization": "private-authorization-header-sentinel",
                },
            )
        )
    emitted_requests.append(OverflowRequest())

    def navigate_for_harvest(received_page):
        assert received_page is page
        events.append("navigation")
        assert events == ["listener:request", "navigation"]
        for request in emitted_requests:
            page.emit(request)
        return "clean"

    class FakeBrowser:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def fetch(self, _url, *, page_action, timeout):
            assert timeout == 60_000
            page_action(page)
            return response

    real_harvest = docids.harvest_from_browser
    harvest_calls: list[list[object]] = []
    harvest_views: list[object] = []

    def observe_harvest(artifacts):
        harvest_views.append(artifacts)
        snapshot = list(artifacts)
        harvest_calls.append(snapshot)
        assert all(
            isinstance(artifact, dict) and set(artifact) == {"operation", "doc_id"}
            for artifact in snapshot
        )
        projected = repr(snapshot)
        for private_value in (
            "private-body-sentinel",
            "private-form-token-sentinel",
            "private-cookie-header-sentinel",
            "private-authorization-header-sentinel",
            "rejected-private-request-body",
        ):
            assert private_value not in projected
        return real_harvest(snapshot)

    real_credential = session.auth.SessionCredential

    def build_credential(**kwargs):
        assert harvest_views
        assert list(harvest_views[-1]) == []
        return real_credential(**kwargs)

    ticks = iter((0.0, 0.0))
    monkeypatch.setattr(session, "_isolated_browser_cache", nullcontext)
    monkeypatch.setattr(
        session,
        "_build_stealth_session",
        lambda *args, **kwargs: FakeBrowser(),
    )
    monkeypatch.setattr(session, "_best_effort_harvest_navigation", navigate_for_harvest)
    monkeypatch.setattr(session.time, "monotonic", lambda: next(ticks, 2.0))
    monkeypatch.setattr(session.docids, "harvest_from_browser", observe_harvest)
    monkeypatch.setattr(session.auth, "SessionCredential", build_credential)

    assert (
        session.run_login(
            "synthetic",
            profile_dir_override=tmp_path,
            timeout_seconds=1,
        )
        is True
    )
    monkeypatch.setattr(session.auth, "SessionCredential", real_credential)

    expected_projections = [
        {"operation": operation, "doc_id": live_doc_id}
        for operation, live_doc_id in operation_doc_ids.items()
    ]
    assert events == ["listener:request", "navigation"]
    assert overflow_reads == []
    assert harvest_calls == [expected_projections]
    assert len(harvest_calls[0]) == session._MAX_HARVEST_PROJECTIONS
    assert list(harvest_views[0]) == []

    credential = session.auth.load_session(
        "synthetic",
        profile_dir_override=tmp_path,
    )
    assert credential.doc_ids == {
        docids.OPERATION_TO_KEY[operation]: live_doc_id
        for operation, live_doc_id in operation_doc_ids.items()
    }
    persisted = (tmp_path / "synthetic" / "session.json").read_text(encoding="utf-8")
    for live_doc_id in operation_doc_ids.values():
        assert live_doc_id in persisted
    captured_output = capsys.readouterr()
    assert captured_output.out == ""
    forbidden_values = (
        *operation_doc_ids,
        gql.GRAPHQL_URL,
        f"{gql.GRAPHQL_URL}?__a=1",
        "private-body-sentinel",
        "private-form-token-sentinel",
        "private-cookie-header-sentinel",
        "private-authorization-header-sentinel",
        "private-disposed-request-sentinel",
        "private-overflow-body",
        "response-cookie-must-not-persist",
        "rejected-private-request-body",
        rejected_doc_id,
    )
    for value in forbidden_values:
        assert value not in captured_output.err
        assert value not in persisted


def test_run_login_timeout_does_not_save_or_read_stdin(monkeypatch, tmp_path, capsys):
    listener_events: list[str] = []
    page = SimpleNamespace(
        wait_for_timeout=lambda _milliseconds: None,
        on=lambda event, _handler: listener_events.append(event),
    )
    response = SimpleNamespace()

    class FakeBrowser:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def fetch(self, _url, *, page_action, timeout):
            page_action(page)
            return response

    monkeypatch.setattr(
        builtins,
        "input",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("input() is forbidden")),
    )
    monkeypatch.setattr(session, "_isolated_browser_cache", nullcontext)
    monkeypatch.setattr(session, "_build_stealth_session", lambda *args, **kwargs: FakeBrowser())
    monkeypatch.setattr(session.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not save")),
    )

    assert (
        session.run_login(
            "synthetic",
            profile_dir_override=tmp_path,
            timeout_seconds=0,
        )
        is False
    )
    assert "Timed out" in capsys.readouterr().err
    assert listener_events == []


@pytest.mark.parametrize(
    ("failure_stage", "expected_calls"),
    [
        (
            "content",
            {"content": 1, "url": 1, "cookies": 0, "evaluate": 0, "wait": 0, "on": 0},
        ),
        (
            "url",
            {"content": 0, "url": 1, "cookies": 0, "evaluate": 0, "wait": 0, "on": 0},
        ),
        (
            "evaluate",
            {"content": 1, "url": 3, "cookies": 1, "evaluate": 1, "wait": 0, "on": 0},
        ),
        (
            "wait",
            {"content": 1, "url": 4, "cookies": 1, "evaluate": 1, "wait": 1, "on": 0},
        ),
    ],
)
def test_run_login_page_failures_are_terminal_bounded_and_never_saved(
    monkeypatch,
    tmp_path,
    capsys,
    failure_stage,
    expected_calls,
):
    cookie_records = [
        {"name": "sessionid", "value": "valid-session", "domain": ".threads.com"},
        {"name": "ds_user_id", "value": "80008", "domain": "threads.net"},
        {"name": "csrftoken", "value": "valid-csrf", "domain": "auth.threads.com"},
    ]
    calls = {"content": 0, "url": 0, "cookies": 0, "evaluate": 0, "wait": 0, "on": 0}
    failure = RuntimeError(f"synthetic-secret-{failure_stage}-failure")
    save_calls: list[object] = []

    def browser_cookies(_urls):
        calls["cookies"] += 1
        return cookie_records

    class FakePage:
        context = SimpleNamespace(cookies=browser_cookies)

        @property
        def url(self):
            calls["url"] += 1
            if failure_stage == "url":
                raise failure
            return session._THREADS_HOME

        def content(self):
            calls["content"] += 1
            if failure_stage == "content":
                raise failure
            return '{"is_logged_in":true}'

        def evaluate(self, _expression):
            calls["evaluate"] += 1
            if failure_stage == "evaluate":
                raise failure
            return "browser-agent/8.0"

        def wait_for_timeout(self, _milliseconds):
            calls["wait"] += 1
            if failure_stage == "wait":
                raise failure

        def on(self, _event, _handler):
            calls["on"] += 1

    page = FakePage()
    response = SimpleNamespace()

    class FakeBrowser:
        def __init__(self):
            self.closed = False
            self.fetch_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.closed = True
            return None

        def fetch(self, _url, *, page_action, timeout):
            assert timeout == 60_000
            self.fetch_calls += 1
            page_action(page)
            return response

    browser = FakeBrowser()
    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(session, "_isolated_browser_cache", nullcontext)
    monkeypatch.setattr(
        session,
        "_build_stealth_session",
        lambda *args, **kwargs: browser,
    )
    monkeypatch.setattr(session.time, "monotonic", lambda: next(ticks, 2.0))
    monkeypatch.setattr(
        session,
        "_best_effort_harvest_navigation",
        lambda _page: (_ for _ in ()).throw(AssertionError("must not navigate")),
    )
    monkeypatch.setattr(
        session.docids,
        "harvest_from_browser",
        lambda _artifacts: (_ for _ in ()).throw(AssertionError("must not harvest")),
    )
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: save_calls.append((args, kwargs)),
    )

    assert (
        session.run_login(
            "synthetic",
            profile_dir_override=tmp_path,
            timeout_seconds=1,
        )
        is False
    )

    assert browser.closed is True
    assert browser.fetch_calls == 1
    assert calls == expected_calls
    assert save_calls == []
    captured = capsys.readouterr()
    assert "synthetic-secret" not in captured.err
    assert "Timed out" not in captured.err


@pytest.mark.parametrize("challenge_evidence", ["body", "url"])
def test_run_login_trusted_challenge_precedes_later_page_reads(
    monkeypatch,
    tmp_path,
    capsys,
    challenge_evidence,
):
    failure = RuntimeError("synthetic-secret-sibling-read-failure")
    calls = {"content": 0, "url": 0, "wait": 0}
    save_calls: list[object] = []

    class FakePage:
        @property
        def url(self):
            calls["url"] += 1
            if challenge_evidence == "url":
                return "https://www.threads.com/checkpoint/step"
            return session._THREADS_HOME

        def content(self):
            calls["content"] += 1
            if challenge_evidence == "url":
                raise failure
            return "challenge_required"

        @property
        def context(self):
            raise AssertionError("challenge must precede cookie reads")

        def wait_for_timeout(self, _milliseconds):
            calls["wait"] += 1

    page = FakePage()
    response = SimpleNamespace()

    class FakeBrowser:
        def __init__(self):
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.closed = True
            return None

        def fetch(self, _url, *, page_action, timeout):
            assert timeout == 60_000
            page_action(page)
            return response

    browser = FakeBrowser()
    ticks = iter((0.0, 0.0))
    monkeypatch.setattr(session, "_isolated_browser_cache", nullcontext)
    monkeypatch.setattr(
        session,
        "_build_stealth_session",
        lambda *args, **kwargs: browser,
    )
    monkeypatch.setattr(session.time, "monotonic", lambda: next(ticks, 2.0))
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: save_calls.append((args, kwargs)),
    )

    with pytest.raises(errors.ChallengeError, match="do not retry") as caught:
        session.run_login(
            "synthetic",
            profile_dir_override=tmp_path,
            timeout_seconds=1,
        )

    assert browser.closed is True
    expected_content_reads = 1 if challenge_evidence == "body" else 0
    assert calls == {"content": expected_content_reads, "url": 1, "wait": 0}
    assert save_calls == []
    assert "synthetic-secret" not in str(caught.value)
    captured = capsys.readouterr()
    assert "synthetic-secret" not in captured.err


@pytest.mark.parametrize(
    ("non_clean_stage", "assessment"),
    [
        ("harvest", "unknown"),
        ("harvest", "checkpoint"),
        ("final", "unknown"),
        ("final", "checkpoint"),
    ],
    ids=[
        "harvest-unknown",
        "harvest-checkpoint",
        "final-unknown",
        "final-checkpoint",
    ],
)
def test_run_login_harvest_and_final_non_clean_states_close_without_saving(
    monkeypatch,
    tmp_path,
    non_clean_stage,
    assessment,
):
    cookies = [
        {"name": "sessionid", "value": "valid-session", "domain": ".threads.com"},
        {"name": "ds_user_id", "value": "60006", "domain": "www.threads.net"},
        {"name": "csrftoken", "value": "valid-csrf", "domain": "auth.threads.com"},
    ]
    cookie_reads: list[tuple[str, ...]] = []
    wall_events: list[str] = []
    save_calls: list[object] = []
    harvest_calls: list[object] = []

    def browser_cookies(urls):
        cookie_reads.append(tuple(urls))
        return cookies

    page = SimpleNamespace(
        url=session._THREADS_HOME,
        content=lambda: '{"is_logged_in":true}',
        context=SimpleNamespace(cookies=browser_cookies),
        evaluate=lambda _expression: "browser-agent/5.0",
        wait_for_timeout=lambda _milliseconds: None,
        on=lambda _event, _handler: None,
    )
    response = SimpleNamespace()

    class FakeBrowser:
        def __init__(self):
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.closed = True
            return None

        def fetch(self, _url, *, page_action, timeout):
            assert timeout == 60_000
            page_action(page)
            return response

    browser = FakeBrowser()

    def navigate_for_harvest(received_page):
        assert received_page is page
        wall_events.append("harvest")
        return assessment if non_clean_stage == "harvest" else "clean"

    def assess_final_page(received_page):
        assert received_page is page
        wall_events.append("final")
        return assessment

    def harvest_doc_ids(artifacts):
        harvest_calls.append(artifacts)
        return {}

    monkeypatch.setattr(session, "_isolated_browser_cache", nullcontext)
    monkeypatch.setattr(
        session,
        "_build_stealth_session",
        lambda *args, **kwargs: browser,
    )
    monkeypatch.setattr(session, "_best_effort_harvest_navigation", navigate_for_harvest)
    monkeypatch.setattr(session, "_page_wall", assess_final_page)
    monkeypatch.setattr(session.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(session.docids, "harvest_from_browser", harvest_doc_ids)
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: save_calls.append((args, kwargs)),
    )

    if assessment == "checkpoint":
        with pytest.raises(errors.ChallengeError, match="do not retry"):
            session.run_login(
                "synthetic",
                profile_dir_override=tmp_path,
                timeout_seconds=1,
            )
    else:
        assert (
            session.run_login(
                "synthetic",
                profile_dir_override=tmp_path,
                timeout_seconds=1,
            )
            is False
        )

    assert browser.closed is True
    assert save_calls == []
    assert harvest_calls == []
    assert wall_events == (["harvest"] if non_clean_stage == "harvest" else ["harvest", "final"])
    assert cookie_reads == [
        session._THREADS_COOKIE_URLS for _ in range(1 if non_clean_stage == "harvest" else 2)
    ]


@pytest.mark.parametrize(
    "final_cookie_records",
    [
        [
            {"name": "sessionid", "value": "final-session", "domain": ".threads.com"},
            {"name": "ds_user_id", "value": "60006", "domain": "threads.net"},
        ],
        [],
        [
            {"name": "sessionid", "value": "", "domain": ".threads.com"},
            {"name": "ds_user_id", "value": 60006, "domain": "threads.net"},
            {
                "name": "csrftoken",
                "value": "final-csrf",
                "domain": "threads.com.attacker.test",
            },
        ],
        None,
    ],
    ids=["incomplete", "empty", "malformed-records", "invalid-result-type"],
)
def test_run_login_rejects_unusable_final_cookie_sets_and_closes_without_saving(
    monkeypatch,
    tmp_path,
    final_cookie_records,
):
    initial_cookies = [
        {"name": "sessionid", "value": "initial-session", "domain": ".threads.com"},
        {"name": "ds_user_id", "value": "60006", "domain": "threads.net"},
        {"name": "csrftoken", "value": "initial-csrf", "domain": "auth.threads.com"},
    ]
    cookie_reads: list[tuple[str, ...]] = []
    save_calls: list[object] = []
    harvest_calls: list[object] = []

    def browser_cookies(urls):
        cookie_reads.append(tuple(urls))
        return initial_cookies if len(cookie_reads) == 1 else final_cookie_records

    page = SimpleNamespace(
        url=session._THREADS_HOME,
        content=lambda: '{"is_logged_in":true}',
        context=SimpleNamespace(cookies=browser_cookies),
        evaluate=lambda _expression: "browser-agent/7.0",
        wait_for_timeout=lambda _milliseconds: None,
        on=lambda _event, _handler: None,
    )
    response = SimpleNamespace()

    class FakeBrowser:
        def __init__(self):
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.closed = True
            return None

        def fetch(self, _url, *, page_action, timeout):
            assert timeout == 60_000
            page_action(page)
            return response

    browser = FakeBrowser()
    ticks = iter((0.0, 0.0, 2.0))

    def harvest_doc_ids(artifacts):
        harvest_calls.append(artifacts)
        return {}

    monkeypatch.setattr(session, "_isolated_browser_cache", nullcontext)
    monkeypatch.setattr(
        session,
        "_build_stealth_session",
        lambda *args, **kwargs: browser,
    )
    monkeypatch.setattr(session, "_best_effort_harvest_navigation", lambda _page: "clean")
    monkeypatch.setattr(session.time, "monotonic", lambda: next(ticks, 2.0))
    monkeypatch.setattr(session.docids, "harvest_from_browser", harvest_doc_ids)
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: save_calls.append((args, kwargs)),
    )

    assert (
        session.run_login(
            "synthetic",
            profile_dir_override=tmp_path,
            timeout_seconds=1,
        )
        is False
    )

    assert browser.closed is True
    assert save_calls == []
    assert harvest_calls == []
    assert cookie_reads == [session._THREADS_COOKIE_URLS, session._THREADS_COOKIE_URLS]


@pytest.mark.parametrize(
    "failure_stage",
    [
        "poll-checkpoint",
        "listener-checkpoint",
        "harvest-checkpoint",
        "final-checkpoint",
        "fetch",
        "doc-id-harvest",
        "credential",
        "save",
    ],
)
def test_run_login_closes_browser_before_any_exception_escapes(
    monkeypatch,
    tmp_path,
    failure_stage,
):
    cookies = [
        {"name": "sessionid", "value": "valid-session", "domain": ".threads.com"},
        {"name": "ds_user_id", "value": "70007", "domain": "www.threads.net"},
        {"name": "csrftoken", "value": "valid-csrf", "domain": "auth.threads.com"},
    ]
    failure = RuntimeError(f"{failure_stage} failure")
    save_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    real_credential = session.auth.SessionCredential
    artifact_views: list[object] = []
    retained_containers: list[dict[str, object]] = []

    class FakePage:
        url = session._THREADS_HOME
        context = SimpleNamespace(cookies=lambda _urls: cookies)
        request_handler = None

        def content(self):
            if failure_stage == "poll-checkpoint":
                raise errors.ChallengeError("synthetic checkpoint read")
            return '{"is_logged_in":true}'

        def evaluate(self, _expression):
            return "browser-agent/6.0"

        def wait_for_timeout(self, _milliseconds):
            return None

        def on(self, _event, handler):
            if failure_stage == "listener-checkpoint":
                raise errors.ChallengeError("synthetic listener checkpoint")
            self.request_handler = handler

    page = FakePage()
    response = SimpleNamespace()

    class FakeBrowser:
        def __init__(self):
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.closed = True
            return None

        def fetch(self, _url, *, page_action, timeout):
            assert timeout == 60_000
            if failure_stage == "fetch":
                raise failure
            page_action(page)
            return response

    browser = FakeBrowser()

    def navigate_for_harvest(received_page):
        assert received_page is page
        assert page.request_handler is not None
        page.request_handler(
            _browser_request(
                post_data=(
                    f"fb_api_req_friendly_name={gql.FEED_OPERATION}"
                    "&doc_id=66666666666666666&variables=private-failure-body"
                ),
                headers={"x-fb-friendly-name": gql.FEED_OPERATION},
            )
        )
        for cell in page.request_handler.__closure__ or ():
            value = cell.cell_contents
            if isinstance(value, dict) and gql.FEED_OPERATION in value:
                retained_containers.append(value)
        if failure_stage == "harvest-checkpoint":
            return "checkpoint"
        return "clean"

    def assess_final_page(received_page):
        assert received_page is page
        if failure_stage == "final-checkpoint":
            return "checkpoint"
        return "clean"

    def harvest_doc_ids(artifacts):
        artifact_views.append(artifacts)
        assert list(artifacts) == [{"operation": gql.FEED_OPERATION, "doc_id": "66666666666666666"}]
        assert browser.closed is True
        if failure_stage == "doc-id-harvest":
            raise failure
        return {}

    def build_credential(**kwargs):
        assert browser.closed is True
        assert all(list(artifacts) == [] for artifacts in artifact_views)
        if failure_stage == "credential":
            raise failure
        return real_credential(**kwargs)

    def save(*args, **kwargs):
        assert browser.closed is True
        assert all(list(artifacts) == [] for artifacts in artifact_views)
        save_calls.append((args, kwargs))
        if failure_stage == "save":
            raise failure
        raise AssertionError("save must not be reached")

    monkeypatch.setattr(session, "_isolated_browser_cache", nullcontext)
    monkeypatch.setattr(
        session,
        "_build_stealth_session",
        lambda *args, **kwargs: browser,
    )
    monkeypatch.setattr(session, "_best_effort_harvest_navigation", navigate_for_harvest)
    monkeypatch.setattr(session, "_page_wall", assess_final_page)
    monkeypatch.setattr(session.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(session.docids, "harvest_from_browser", harvest_doc_ids)
    monkeypatch.setattr(session.auth, "SessionCredential", build_credential)
    monkeypatch.setattr(session.auth, "save_session", save)

    if failure_stage.endswith("checkpoint"):
        with pytest.raises(errors.ChallengeError, match="do not retry"):
            session.run_login(
                "synthetic",
                profile_dir_override=tmp_path,
                timeout_seconds=1,
            )
    else:
        with pytest.raises(RuntimeError) as caught:
            session.run_login(
                "synthetic",
                profile_dir_override=tmp_path,
                timeout_seconds=1,
            )
        assert caught.value is failure

    assert browser.closed is True
    assert len(save_calls) == (1 if failure_stage == "save" else 0)
    listener_capture_stages = {
        "harvest-checkpoint",
        "final-checkpoint",
        "doc-id-harvest",
        "credential",
        "save",
    }
    harvest_stages = {"doc-id-harvest", "credential", "save"}
    assert len(retained_containers) == int(failure_stage in listener_capture_stages)
    assert len(artifact_views) == int(failure_stage in harvest_stages)
    assert all(container == {} for container in retained_containers)
    assert all(list(artifacts) == [] for artifacts in artifact_views)


def test_run_login_checkpoint_body_on_login_url_closes_without_retrying_or_saving(
    monkeypatch,
    tmp_path,
):
    content_reads: list[str] = []
    cookie_reads: list[tuple[str, ...]] = []
    wait_calls: list[int] = []
    listener_events: list[str] = []
    save_calls: list[object] = []

    def page_content():
        content_reads.append("content")
        return "challenge_required"

    def browser_cookies(urls):
        cookie_reads.append(tuple(urls))
        return []

    page = SimpleNamespace(
        url="https://www.threads.com/accounts/login/",
        content=page_content,
        context=SimpleNamespace(cookies=browser_cookies),
        wait_for_timeout=lambda milliseconds: wait_calls.append(milliseconds),
        on=lambda event, _handler: listener_events.append(event),
    )
    response = SimpleNamespace()

    class FakeBrowser:
        def __init__(self):
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.closed = True
            return None

        def fetch(self, _url, *, page_action, timeout):
            assert timeout == 60_000
            page_action(page)
            return response

    browser = FakeBrowser()
    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(session, "_isolated_browser_cache", nullcontext)
    monkeypatch.setattr(
        session,
        "_build_stealth_session",
        lambda *args, **kwargs: browser,
    )
    monkeypatch.setattr(session.time, "monotonic", lambda: next(ticks, 2.0))
    monkeypatch.setattr(
        session.auth,
        "save_session",
        lambda *args, **kwargs: save_calls.append((args, kwargs)),
    )

    with pytest.raises(errors.ChallengeError, match="do not retry"):
        session.run_login(
            "synthetic",
            profile_dir_override=tmp_path,
            timeout_seconds=1,
        )

    assert browser.closed is True
    assert content_reads == ["content"]
    assert cookie_reads == []
    assert wait_calls == []
    assert listener_events == []
    assert save_calls == []


def test_run_login_rejects_negative_timeout_before_browser_setup():
    with pytest.raises(ValueError, match="non-negative"):
        session.run_login("synthetic", timeout_seconds=-0.1)


def test_run_setup_calls_scrapling_cli_inside_isolated_cache(monkeypatch, tmp_path):
    calls: list[dict[str, object]] = []

    class FakeCommand:
        @staticmethod
        def main(**kwargs):
            calls.append(kwargs)

    scrapling_module = ModuleType("scrapling")
    scrapling_module.__path__ = []
    cli_module = ModuleType("scrapling.cli")
    cli_module.main = FakeCommand
    scrapling_module.cli = cli_module
    monkeypatch.setitem(sys.modules, "scrapling", scrapling_module)
    monkeypatch.setitem(sys.modules, "scrapling.cli", cli_module)
    cache = tmp_path / "isolated-browser-cache"
    monkeypatch.setattr(session.config, "browsers_dir", lambda: cache)

    session.run_setup(force=True)

    assert calls == [
        {
            "args": ["install", "--force"],
            "prog_name": "scrapling",
            "standalone_mode": False,
        }
    ]
    assert cache.is_dir()


def test_run_setup_wraps_installer_failures(monkeypatch):
    class FailingCommand:
        @staticmethod
        def main(**kwargs):
            raise RuntimeError("synthetic installer failure")

    scrapling_module = ModuleType("scrapling")
    scrapling_module.__path__ = []
    cli_module = ModuleType("scrapling.cli")
    cli_module.main = FailingCommand
    scrapling_module.cli = cli_module
    monkeypatch.setitem(sys.modules, "scrapling", scrapling_module)
    monkeypatch.setitem(sys.modules, "scrapling.cli", cli_module)

    @contextmanager
    def no_cache():
        yield

    monkeypatch.setattr(session, "_isolated_browser_cache", no_cache)
    with pytest.raises(errors.BrowserSetupError, match="RuntimeError"):
        session.run_setup()
