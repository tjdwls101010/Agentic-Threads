from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentic_threads import errors, gql, retrieve

DOC_IDS = {
    "feed": "doc-feed",
    "profile_threads": "doc-profile-threads",
    "profile_threads_page": "doc-profile-threads-page",
    "profile_replies": "doc-profile-replies",
    "profile_replies_page": "doc-profile-replies-page",
    "post": "doc-post",
    "post_replies": "doc-post-replies",
    "post_search": "doc-post-search",
    "people_search": "doc-people-search",
    "followers": "doc-followers",
    "following": "doc-following",
}

JULY_1 = int(datetime(2026, 7, 1, 12, tzinfo=UTC).timestamp())
JUNE_30 = int(datetime(2026, 6, 30, 12, tzinfo=UTC).timestamp())
JUNE_29 = int(datetime(2026, 6, 29, 12, tzinfo=UTC).timestamp())
SINCE_JUNE_30 = datetime(2026, 6, 30, tzinfo=UTC)
UNTIL_JUNE_30 = datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC)


class FakeReadClient:
    """Return scripted GraphQL bodies and record the actual operation contract."""

    def __init__(self, responses: list[dict | Exception], *, max_requests: int = 500):
        self._responses = list(responses)
        self.max_requests = max_requests
        self.requests_made = 0
        self.calls: list[tuple[str, dict[str, object], str]] = []

    def post(
        self,
        operation: str,
        variables: dict[str, object],
        *,
        doc_id: str,
    ) -> dict:
        if not self._responses:
            raise AssertionError("FakeReadClient exhausted: an unexpected request was made")
        self.requests_made += 1
        self.calls.append((operation, dict(variables), doc_id))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _raw_post(
    post_id: str,
    taken_at: int | None = JULY_1,
    *,
    pinned: bool = False,
    reply_to_id: str | None = None,
    code: str | None = None,
) -> dict:
    info: dict[str, object] = {
        "is_reply": reply_to_id is not None,
        "pinned_post_info": {"is_pinned_to_profile": pinned},
    }
    if reply_to_id is not None:
        info["reply_to_id"] = reply_to_id
        info["root_post_id"] = reply_to_id
    post = {
        "pk": post_id,
        "code": f"S{post_id}" if code is None else code,
        "caption": {"text": f"synthetic post {post_id}"},
        "user": {
            "pk": f"9{post_id}",
            "username": f"synthetic_{post_id}",
            "full_name": "Synthetic Person",
        },
        "text_post_app_info": info,
    }
    if taken_at is not None:
        post["taken_at"] = taken_at
    return post


def _post_page(
    operation: str,
    posts: list[dict],
    *,
    cursor: str | None = None,
    has_next: bool = False,
) -> dict:
    items = [{"post": post} for post in posts]
    if operation == "feed":
        node = {"text_post_app_thread": {"thread_items": items}}
        root = {"data": {"feedData": {}}}
        connection = root["data"]["feedData"]
    elif operation in {
        "profile_threads",
        "profile_threads_page",
        "profile_replies",
        "profile_replies_page",
    }:
        node = {"thread_items": items}
        root = {"data": {"mediaData": {}}}
        connection = root["data"]["mediaData"]
    elif operation == "post_replies":
        node = {"thread_items": items}
        root = {"data": {"data": {}}}
        connection = root["data"]["data"]
    elif operation == "post_search":
        node = {"thread": {"thread_items": items}}
        root = {"data": {"searchResults": {}}}
        connection = root["data"]["searchResults"]
    else:
        raise AssertionError(f"unsupported synthetic post operation: {operation}")

    connection["edges"] = [{"node": node}]
    connection["page_info"] = {
        "end_cursor": cursor,
        "has_next_page": has_next,
    }
    return root


def _post_detail(post: dict | None) -> dict:
    return {"data": {"media": post}}


def _raw_user(user_id: str, username: str) -> dict:
    return {
        "pk": user_id,
        "username": username,
        "full_name": f"Synthetic {username}",
        "is_verified": False,
    }


def _user_page(
    operation: str,
    users: list[dict],
    *,
    cursor: str | None = None,
    has_next: bool = False,
) -> dict:
    connection = {
        "edges": [{"node": user} for user in users],
        "page_info": {"end_cursor": cursor, "has_next_page": has_next},
    }
    if operation == "people_search":
        return {"data": {"xdt_api__v1__users__search_connection": connection}}
    if operation in {"followers", "following"}:
        return {"data": {"user": {operation: connection}}}
    raise AssertionError(f"unsupported synthetic user operation: {operation}")


def test_username_resolution_requires_an_exact_case_insensitive_match():
    client = FakeReadClient(
        [
            _user_page(
                "people_search",
                [
                    _raw_user("10", "synthetic_alice_fan"),
                    _raw_user("11", "SyNtHeTiC_AlIcE"),
                ],
            )
        ]
    )

    user_id = retrieve.resolve_user_id(
        client,
        DOC_IDS,
        None,
        "username",
        "@synthetic_alice",
    )

    assert user_id == "11"
    operation, variables, doc_id = client.calls[0]
    assert operation == gql.PEOPLE_SEARCH_OPERATION
    assert variables["query"] == "@synthetic_alice"
    assert doc_id == DOC_IDS["people_search"]


def test_username_resolution_rejects_only_partial_matches():
    client = FakeReadClient([_user_page("people_search", [_raw_user("10", "synthetic_alice_fan")])])
    with pytest.raises(errors.ProfileUnavailableError, match="exact search match"):
        retrieve.resolve_user_id(client, DOC_IDS, None, "username", "synthetic_alice")


def test_username_resolution_fails_closed_on_an_invalid_non_null_user_node():
    client = FakeReadClient(
        [_user_page("people_search", [_raw_user("alphabetic", "synthetic_alice")])]
    )

    with pytest.raises(errors.EnvelopeParseError, match="non-null user node"):
        retrieve.resolve_user_id(client, DOC_IDS, None, "username", "synthetic_alice")

    assert client.requests_made == 1


def test_numeric_user_id_resolution_is_request_free():
    client = FakeReadClient([])
    assert retrieve.resolve_user_id(client, DOC_IDS, None, "user_id", "12345") == "12345"
    assert client.requests_made == 0


def test_post_retrieval_fails_closed_on_an_invalid_non_null_post_node():
    invalid_post = _raw_post("1")
    invalid_post["pk"] = "alphabetic"
    client = FakeReadClient([_post_page("feed", [invalid_post])])

    with pytest.raises(errors.EnvelopeParseError, match="non-null post node"):
        retrieve.fetch_home(client, DOC_IDS, None)

    assert client.requests_made == 1


def test_post_retrieval_fails_closed_on_an_invalid_non_null_relationship_id():
    client = FakeReadClient([_post_page("feed", [_raw_post("1", reply_to_id="-1")])])

    with pytest.raises(errors.EnvelopeParseError, match="non-null post node"):
        retrieve.fetch_home(client, DOC_IDS, None)

    assert client.requests_made == 1


def test_home_cursor_pagination_deduplicates_until_true_eof():
    client = FakeReadClient(
        [
            _post_page(
                "feed",
                [_raw_post("1"), _raw_post("2")],
                cursor="CURSOR-1",
                has_next=True,
            ),
            _post_page("feed", [_raw_post("2"), _raw_post("3")]),
        ]
    )

    result = retrieve.fetch_home(client, DOC_IDS, None)

    assert [post.id for post in result.posts] == ["1", "2", "3"]
    assert result.raw_post_count == 4
    assert result.stop_reason == "feed_exhausted"
    assert result.requests_made == 2
    assert client.calls[1][1]["after"] == "CURSOR-1"


def test_home_repeated_cursor_fails_closed_without_disclosing_cursor():
    cursor = "SECRET-REPEATED-POST-CURSOR"
    client = FakeReadClient(
        [
            _post_page("feed", [_raw_post("1")], cursor=cursor, has_next=True),
            _post_page("feed", [_raw_post("2")], cursor=cursor, has_next=True),
        ]
    )

    with pytest.raises(errors.EnvelopeParseError) as exc_info:
        retrieve.fetch_home(client, DOC_IDS, None)

    message = str(exc_info.value)
    assert message == (
        "response envelope drift for 'feed': pagination cursor was already scheduled"
    )
    assert cursor not in message
    assert client.requests_made == 2
    assert [call[1].get("after") for call in client.calls] == [None, cursor]


def test_home_cursor_cycle_fails_closed_before_rescheduling_a_seen_cursor():
    cursor_one = "SECRET-POST-CURSOR-1"
    cursor_two = "SECRET-POST-CURSOR-2"
    client = FakeReadClient(
        [
            _post_page("feed", [_raw_post("1")], cursor=cursor_one, has_next=True),
            _post_page("feed", [_raw_post("2")], cursor=cursor_two, has_next=True),
            _post_page("feed", [_raw_post("3")], cursor=cursor_one, has_next=True),
        ]
    )

    with pytest.raises(errors.EnvelopeParseError) as exc_info:
        retrieve.fetch_home(client, DOC_IDS, None)

    message = str(exc_info.value)
    assert message == (
        "response envelope drift for 'feed': pagination cursor was already scheduled"
    )
    assert cursor_one not in message
    assert cursor_two not in message
    assert client.requests_made == 3
    assert [call[1].get("after") for call in client.calls] == [
        None,
        cursor_one,
        cursor_two,
    ]


def test_empty_page_with_advancing_cursor_is_not_eof():
    client = FakeReadClient(
        [
            _post_page("feed", [_raw_post("1")], cursor="C1", has_next=True),
            _post_page("feed", [], cursor="C2", has_next=True),
            _post_page("feed", [_raw_post("2")], cursor=None, has_next=False),
        ]
    )

    result = retrieve.fetch_home(client, DOC_IDS, None)

    assert [post.id for post in result.posts] == ["1", "2"]
    assert result.stop_reason == "feed_exhausted"
    assert result.requests_made == 3


@pytest.mark.parametrize(
    ("posts", "kwargs", "expected_reason", "expected_since_crossed"),
    (
        pytest.param(
            [
                _raw_post("90", JUNE_30, pinned=True),
                _raw_post("1", JUNE_30),
                _raw_post("2", JUNE_30),
                _raw_post("91", JUNE_30, pinned=True),
                _raw_post("92", JUNE_29, pinned=True),
                _raw_post("93", JULY_1, pinned=True),
                _raw_post("90", JUNE_30, pinned=True),
            ],
            {"limit": 1, "since": SINCE_JUNE_30, "until": UNTIL_JUNE_30},
            "limit_reached",
            False,
            id="limit",
        ),
        pytest.param(
            [
                _raw_post("90", JUNE_30, pinned=True),
                _raw_post("1", JUNE_30),
                _raw_post("2", JUNE_29),
                _raw_post("3", JUNE_30),
                _raw_post("91", JUNE_30, pinned=True),
                _raw_post("92", JUNE_29, pinned=True),
                _raw_post("93", JULY_1, pinned=True),
                _raw_post("90", JUNE_30, pinned=True),
            ],
            {"since": SINCE_JUNE_30, "until": UNTIL_JUNE_30},
            "since_crossed",
            True,
            id="since",
        ),
    ),
)
def test_profile_terminal_reason_keeps_unique_in_window_pins_limit_exempt(
    posts,
    kwargs,
    expected_reason,
    expected_since_crossed,
):
    client = FakeReadClient(
        [
            _post_page(
                "profile_threads",
                posts,
                cursor="unused-next-page",
                has_next=True,
            )
        ]
    )

    result = retrieve.fetch_profile(
        client,
        DOC_IDS,
        None,
        "user_id",
        "200",
        **kwargs,
    )

    assert [post.id for post in result.posts] == ["90", "1", "91"]
    assert result.raw_post_count == len(posts)
    assert result.stop_reason == expected_reason
    assert result.since_target_crossed is expected_since_crossed
    assert result.requests_made == 1
    assert client.requests_made == 1


def test_feed_pinned_row_consumes_the_ordinary_limit():
    client = FakeReadClient(
        [
            _post_page(
                "feed",
                [_raw_post("90", pinned=True), _raw_post("1")],
                cursor="unused-next-page",
                has_next=True,
            )
        ]
    )

    result = retrieve.fetch_home(client, DOC_IDS, None, limit=1)

    assert [post.id for post in result.posts] == ["90"]
    assert result.stop_reason == "limit_reached"
    assert result.requests_made == 1


def test_post_search_pinned_row_consumes_the_ordinary_limit():
    client = FakeReadClient(
        [
            _post_page(
                "post_search",
                [_raw_post("90", pinned=True), _raw_post("1")],
                cursor="unused-next-page",
                has_next=True,
            )
        ]
    )

    result = retrieve.search(client, DOC_IDS, None, "synthetic", limit=1)

    assert isinstance(result, retrieve.RetrieveResult)
    assert [post.id for post in result.posts] == ["90"]
    assert result.stop_reason == "limit_reached"
    assert result.requests_made == 1


@pytest.mark.parametrize(
    ("taken_at", "since", "until", "expected_ids"),
    (
        pytest.param(JUNE_29, SINCE_JUNE_30, None, [], id="before-since"),
        pytest.param(JUNE_30, SINCE_JUNE_30, None, ["90"], id="after-since"),
        pytest.param(JUNE_30, None, UNTIL_JUNE_30, ["90"], id="before-until"),
        pytest.param(JULY_1, None, UNTIL_JUNE_30, [], id="after-until"),
    ),
)
def test_pinned_post_date_window_matrix(
    taken_at,
    since,
    until,
    expected_ids,
):
    client = FakeReadClient([_post_page("feed", [_raw_post("90", taken_at, pinned=True)])])

    result = retrieve.fetch_home(
        client,
        DOC_IDS,
        None,
        since=since,
        until=until,
    )

    assert [post.id for post in result.posts] == expected_ids
    assert result.stop_reason == "feed_exhausted"
    assert result.since_target_crossed is False


@pytest.mark.parametrize(
    ("first_post", "kwargs"),
    (
        pytest.param(
            _raw_post("1", JUNE_30),
            {"limit": 1},
            id="after-limit",
        ),
        pytest.param(
            _raw_post("1", JUNE_29),
            {"since": SINCE_JUNE_30},
            id="after-since",
        ),
    ),
)
def test_malformed_post_after_terminal_reason_still_fails_closed(first_post, kwargs):
    client = FakeReadClient(
        [
            _post_page(
                "profile_threads",
                [first_post, _raw_post("not-numeric")],
                cursor="unused-next-page",
                has_next=True,
            )
        ]
    )

    with pytest.raises(errors.EnvelopeParseError, match="non-null post node"):
        retrieve.fetch_profile(
            client,
            DOC_IDS,
            None,
            "user_id",
            "200",
            **kwargs,
        )

    assert client.requests_made == 1


def test_profile_since_is_inclusive_and_stops_before_the_crossing_post():
    client = FakeReadClient(
        [
            _post_page(
                "profile_threads",
                [
                    _raw_post("3", JULY_1),
                    _raw_post("2", JUNE_30),
                    _raw_post("1", JUNE_29),
                ],
            )
        ]
    )

    result = retrieve.fetch_profile(
        client,
        DOC_IDS,
        None,
        "user_id",
        "200",
        since=datetime(2026, 6, 30, tzinfo=UTC),
    )

    assert [post.id for post in result.posts] == ["3", "2"]
    assert result.stop_reason == "since_crossed"
    assert result.since_target_crossed is True


def test_unordered_feed_keeps_an_in_window_row_after_an_older_row():
    client = FakeReadClient(
        [_post_page("feed", [_raw_post("1", JUNE_29), _raw_post("2", JUNE_30)])]
    )

    result = retrieve.fetch_home(
        client,
        DOC_IDS,
        None,
        since=SINCE_JUNE_30,
    )

    assert [post.id for post in result.posts] == ["2"]
    assert result.stop_reason == "feed_exhausted"
    assert result.since_target_crossed is False


def test_unordered_post_search_keeps_an_in_window_row_after_an_older_row():
    client = FakeReadClient(
        [
            _post_page(
                "post_search",
                [_raw_post("1", JUNE_29), _raw_post("2", JUNE_30)],
            )
        ]
    )

    result = retrieve.search(
        client,
        DOC_IDS,
        None,
        "synthetic",
        since=SINCE_JUNE_30,
    )

    assert isinstance(result, retrieve.RetrieveResult)
    assert [post.id for post in result.posts] == ["2"]
    assert result.stop_reason == "no_next_page"
    assert result.since_target_crossed is False


def test_until_skips_newer_posts_and_continues_to_older_ones():
    client = FakeReadClient(
        [
            _post_page(
                "feed",
                [
                    _raw_post("3", JULY_1),
                    _raw_post("2", JUNE_30),
                    _raw_post("1", JUNE_29),
                ],
            )
        ]
    )

    result = retrieve.fetch_home(
        client,
        DOC_IDS,
        None,
        until=datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC),
    )

    assert [post.id for post in result.posts] == ["2", "1"]
    assert result.stop_reason == "feed_exhausted"


def test_missing_dates_do_not_trigger_date_boundaries():
    client = FakeReadClient([_post_page("feed", [_raw_post("4", None)])])
    result = retrieve.fetch_home(
        client,
        DOC_IDS,
        None,
        since=datetime(2026, 7, 1, tzinfo=UTC),
        until=datetime(2026, 7, 1, 23, 59, tzinfo=UTC),
    )
    assert [post.id for post in result.posts] == ["4"]
    assert result.stop_reason == "feed_exhausted"


def test_run_request_budget_returns_partial_results_without_an_extra_call():
    client = FakeReadClient([_post_page("feed", [_raw_post("1")], cursor="C1", has_next=True)])

    result = retrieve.fetch_home(client, DOC_IDS, None, max_requests=1)

    assert [post.id for post in result.posts] == ["1"]
    assert result.stop_reason == "max_requests"
    assert result.requests_made == 1
    assert client.requests_made == 1


def test_underlying_client_budget_is_also_respected():
    client = FakeReadClient(
        [_post_page("feed", [_raw_post("1")], cursor="C1", has_next=True)],
        max_requests=1,
    )
    result = retrieve.fetch_home(client, DOC_IDS, None, max_requests=10)
    assert result.stop_reason == "max_requests"
    assert client.requests_made == 1


def test_rate_limit_without_wait_returns_the_partial_page():
    client = FakeReadClient(
        [
            _post_page("feed", [_raw_post("1")], cursor="C1", has_next=True),
            errors.RateLimitedError(reset_at=105),
        ]
    )

    result = retrieve.fetch_home(client, DOC_IDS, None)

    assert [post.id for post in result.posts] == ["1"]
    assert result.stop_reason == "rate_limited"
    assert result.requests_made == 2


def test_rate_limit_waits_until_reset_and_retries(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(retrieve.time, "time", lambda: 100.0)
    monkeypatch.setattr(retrieve.time, "sleep", sleeps.append)
    client = FakeReadClient(
        [
            errors.RateLimitedError(reset_at=105),
            _post_page("feed", [_raw_post("1")]),
        ]
    )

    result = retrieve.fetch_home(
        client,
        DOC_IDS,
        None,
        wait_on_limit=True,
        max_wait=5,
    )

    assert sleeps == [5.0]
    assert [post.id for post in result.posts] == ["1"]
    assert result.stop_reason == "feed_exhausted"
    assert result.requests_made == 2


def test_rate_limit_above_max_wait_does_not_sleep_or_retry(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(retrieve.time, "time", lambda: 100.0)
    monkeypatch.setattr(retrieve.time, "sleep", sleeps.append)
    client = FakeReadClient([errors.RateLimitedError(reset_at=106)])

    result = retrieve.fetch_home(
        client,
        DOC_IDS,
        None,
        wait_on_limit=True,
        max_wait=5,
    )

    assert result.stop_reason == "rate_limited"
    assert result.requests_made == 1
    assert sleeps == []


def test_social_graph_stops_after_three_pages_that_add_no_users():
    duplicate = _raw_user("1", "synthetic_one")
    client = FakeReadClient(
        [
            _user_page("followers", [duplicate], cursor="C1", has_next=True),
            _user_page("followers", [duplicate], cursor="C2", has_next=True),
            _user_page("followers", [], cursor="C3", has_next=True),
            _user_page("followers", [], cursor="C4", has_next=True),
        ]
    )

    result = retrieve.fetch_social_graph(
        client,
        DOC_IDS,
        None,
        "followers",
        "user_id",
        "100",
    )

    assert [user.id for user in result.users] == ["1"]
    assert result.stop_reason == "empty_pages"
    assert result.requests_made == 4


def test_shortcode_resolution_probes_null_candidates_then_accepts_a_verified_root(monkeypatch):
    monkeypatch.setattr(
        retrieve,
        "shortcode_to_post_id_candidates",
        lambda _code: ("400", "401", "402", "403"),
    )
    client = FakeReadClient(
        [
            _post_detail(None),
            _post_detail(None),
            _post_detail(None),
            _post_detail(_raw_post("403", code="synthetic-code")),
        ]
    )

    result = retrieve.fetch_post(
        client,
        DOC_IDS,
        None,
        "shortcode",
        "synthetic-code",
        replies=False,
    )

    assert [post.id for post in result.posts] == ["403"]
    assert result.posts[0].code == "synthetic-code"
    assert result.requests_made == 4
    assert result.stop_reason == "no_next_page"
    assert [call[1]["postID"] for call in client.calls] == ["400", "401", "402", "403"]
    assert all(call[0] == gql.POST_OPERATION for call in client.calls)


@pytest.mark.parametrize(
    "malformed_body",
    [
        pytest.param({}, id="missing-data"),
        pytest.param({"data": []}, id="non-mapping-data"),
        pytest.param({"data": {}}, id="missing-media"),
    ],
)
def test_shortcode_malformed_root_envelope_fails_after_one_request(monkeypatch, malformed_body):
    monkeypatch.setattr(
        retrieve,
        "shortcode_to_post_id_candidates",
        lambda _code: ("400", "401", "402", "403"),
    )
    client = FakeReadClient(
        [
            malformed_body,
            _post_detail(_raw_post("401", code="synthetic-code")),
        ]
    )

    with pytest.raises(errors.EnvelopeParseError, match=r"data\.media"):
        retrieve.fetch_post(
            client,
            DOC_IDS,
            None,
            "shortcode",
            "synthetic-code",
        )

    assert client.requests_made == 1
    assert [call[0] for call in client.calls] == [gql.POST_OPERATION]
    assert client.calls[0][1]["postID"] == "400"


def test_numeric_post_root_id_mismatch_is_drift_without_a_reply_request():
    client = FakeReadClient([_post_detail(_raw_post("501"))])

    with pytest.raises(errors.EnvelopeParseError, match="normalized root id"):
        retrieve.fetch_post(client, DOC_IDS, None, "post_id", "500")

    assert client.requests_made == 1
    assert [call[0] for call in client.calls] == [gql.POST_OPERATION]
    assert client.calls[0][1]["postID"] == "500"


def test_shortcode_candidate_id_mismatch_is_drift_without_more_requests(monkeypatch):
    monkeypatch.setattr(
        retrieve,
        "shortcode_to_post_id_candidates",
        lambda _code: ("400", "401", "402", "403"),
    )
    client = FakeReadClient([_post_detail(_raw_post("401", code="synthetic-code"))])

    with pytest.raises(errors.EnvelopeParseError, match="normalized root id"):
        retrieve.fetch_post(
            client,
            DOC_IDS,
            None,
            "shortcode",
            "synthetic-code",
        )

    assert client.requests_made == 1
    assert [call[0] for call in client.calls] == [gql.POST_OPERATION]
    assert client.calls[0][1]["postID"] == "400"


def test_shortcode_code_mismatch_is_drift_without_more_requests(monkeypatch):
    monkeypatch.setattr(
        retrieve,
        "shortcode_to_post_id_candidates",
        lambda _code: ("400", "401", "402", "403"),
    )
    client = FakeReadClient([_post_detail(_raw_post("400", code="other-code"))])

    with pytest.raises(errors.EnvelopeParseError, match="normalized root code"):
        retrieve.fetch_post(
            client,
            DOC_IDS,
            None,
            "shortcode",
            "synthetic-code",
        )

    assert client.requests_made == 1
    assert [call[0] for call in client.calls] == [gql.POST_OPERATION]
    assert client.calls[0][1]["postID"] == "400"


def test_numeric_post_no_replies_issues_only_root_operation_and_reports_one_request():
    client = FakeReadClient([_post_detail(_raw_post("500"))])

    result = retrieve.fetch_post(
        client,
        DOC_IDS,
        None,
        "post_id",
        "500",
        replies=False,
    )

    assert [post.id for post in result.posts] == ["500"]
    assert result.stop_reason == "no_next_page"
    assert result.requests_made == 1
    assert client.calls == [
        (
            gql.POST_OPERATION,
            gql.post_variables("500", features=None),
            DOC_IDS["post"],
        )
    ]


def test_pinned_permalink_root_consumes_limit_before_a_reply_request():
    client = FakeReadClient([_post_detail(_raw_post("500", pinned=True))])

    result = retrieve.fetch_post(
        client,
        DOC_IDS,
        None,
        "post_id",
        "500",
        limit=1,
    )

    assert [post.id for post in result.posts] == ["500"]
    assert result.stop_reason == "limit_reached"
    assert result.requests_made == 1
    assert client.calls == [
        (
            gql.POST_OPERATION,
            gql.post_variables("500", features=None),
            DOC_IDS["post"],
        )
    ]


def test_shortcode_with_four_empty_probes_is_not_found(monkeypatch):
    monkeypatch.setattr(
        retrieve,
        "shortcode_to_post_id_candidates",
        lambda _code: ("400", "401", "402", "403"),
    )
    client = FakeReadClient([_post_detail(None) for _ in range(4)])

    with pytest.raises(errors.NotFoundError):
        retrieve.fetch_post(
            client,
            DOC_IDS,
            None,
            "shortcode",
            "synthetic-code",
        )
    assert client.requests_made == 4


def test_numeric_post_default_issues_root_then_replies_and_reports_two_requests():
    root = _raw_post("500")
    reply = _raw_post("501", reply_to_id="500")
    client = FakeReadClient(
        [
            _post_detail(root),
            _post_page("post_replies", [root, reply]),
        ]
    )

    result = retrieve.fetch_post(client, DOC_IDS, None, "post_id", "500")

    assert [post.id for post in result.posts] == ["500", "501"]
    assert result.posts[0].is_reply is False
    assert result.posts[1].is_reply is True
    assert result.posts[1].reply_to_id == "500"
    assert result.stop_reason == "no_next_page"
    assert result.requests_made == 2
    assert [call[0] for call in client.calls] == [
        gql.POST_OPERATION,
        gql.POST_REPLIES_OPERATION,
    ]


def test_pinned_post_reply_consumes_the_shared_ordinary_limit():
    root = _raw_post("500")
    pinned_reply = _raw_post("501", pinned=True, reply_to_id="500")
    later_reply = _raw_post("502", reply_to_id="500")
    client = FakeReadClient(
        [
            _post_detail(root),
            _post_page(
                "post_replies",
                [root, pinned_reply, later_reply],
                cursor="unused-next-page",
                has_next=True,
            ),
        ]
    )

    result = retrieve.fetch_post(
        client,
        DOC_IDS,
        None,
        "post_id",
        "500",
        limit=2,
    )

    assert [post.id for post in result.posts] == ["500", "501"]
    assert result.stop_reason == "limit_reached"
    assert result.requests_made == 2
    assert [call[0] for call in client.calls] == [
        gql.POST_OPERATION,
        gql.POST_REPLIES_OPERATION,
    ]


def test_profile_threads_and_replies_share_dedup_state():
    thread = _raw_post("600")
    reply = _raw_post("601", reply_to_id="600")
    client = FakeReadClient(
        [
            _post_page("profile_threads", [thread]),
            _post_page("profile_replies", [thread, reply]),
        ]
    )

    result = retrieve.fetch_profile(
        client,
        DOC_IDS,
        None,
        "user_id",
        "200",
        replies=True,
    )

    assert [post.id for post in result.posts] == ["600", "601"]
    assert [call[0] for call in client.calls] == [
        gql.PROFILE_THREADS_OPERATION,
        gql.PROFILE_REPLIES_OPERATION,
    ]


def test_profile_replies_keep_in_window_pins_limit_exempt():
    reply = _raw_post("601", JUNE_30, reply_to_id="600")
    pinned_reply = _raw_post("602", JUNE_30, pinned=True, reply_to_id="600")
    later_reply = _raw_post("603", JUNE_30, reply_to_id="600")
    client = FakeReadClient(
        [
            _post_page("profile_threads", []),
            _post_page(
                "profile_replies",
                [reply, pinned_reply, later_reply],
                cursor="unused-next-page",
                has_next=True,
            ),
        ]
    )

    result = retrieve.fetch_profile(
        client,
        DOC_IDS,
        None,
        "user_id",
        "200",
        replies=True,
        limit=1,
        since=SINCE_JUNE_30,
    )

    assert [post.id for post in result.posts] == ["601", "602"]
    assert result.stop_reason == "limit_reached"
    assert result.since_target_crossed is False
    assert result.requests_made == 2
    assert [call[0] for call in client.calls] == [
        gql.PROFILE_THREADS_OPERATION,
        gql.PROFILE_REPLIES_OPERATION,
    ]


def test_cross_tab_duplicate_proves_both_since_boundaries_before_the_hard_cap():
    shared_new = _raw_post("610", JULY_1)
    shared_old = _raw_post("611", JUNE_29)
    client = FakeReadClient(
        [
            _post_page(
                "profile_threads",
                [shared_new, shared_old],
                cursor="THREADS-NEXT",
                has_next=True,
            ),
            _post_page(
                "profile_replies",
                [shared_new, shared_old],
                cursor="REPLIES-NEXT",
                has_next=True,
            ),
        ]
    )

    result = retrieve.fetch_profile(
        client,
        DOC_IDS,
        None,
        "user_id",
        "200",
        replies=True,
        since=datetime(2026, 6, 30, tzinfo=UTC),
        max_requests=2,
    )

    assert [post.id for post in result.posts] == ["610"]
    assert result.raw_post_count == 4
    assert result.stop_reason == "since_crossed"
    assert result.since_target_crossed is True
    assert result.requests_made == 2
    assert [call[0] for call in client.calls] == [
        gql.PROFILE_THREADS_OPERATION,
        gql.PROFILE_REPLIES_OPERATION,
    ]


def test_post_search_returns_posts_and_no_matches_is_distinct():
    matching = FakeReadClient([_post_page("post_search", [_raw_post("700"), _raw_post("701")])])
    result = retrieve.search(matching, DOC_IDS, None, "synthetic topic")
    assert isinstance(result, retrieve.RetrieveResult)
    assert [post.id for post in result.posts] == ["700", "701"]
    assert result.stop_reason == "no_next_page"
    assert matching.calls[0][0] == gql.POST_SEARCH_OPERATION
    assert matching.calls[0][1]["query"] == "synthetic topic"

    empty = FakeReadClient([_post_page("post_search", [])])
    no_matches = retrieve.search(empty, DOC_IDS, None, "nothing here")
    assert isinstance(no_matches, retrieve.RetrieveResult)
    assert no_matches.posts == []
    assert no_matches.stop_reason == "no_matches"


def test_people_search_returns_users_with_cursor_dedup():
    first = _raw_user("800", "synthetic_a")
    second = _raw_user("801", "synthetic_b")
    client = FakeReadClient(
        [
            _user_page("people_search", [first], cursor="C1", has_next=True),
            _user_page("people_search", [first, second]),
        ]
    )

    result = retrieve.search(
        client,
        DOC_IDS,
        None,
        "synthetic",
        search_type="people",
    )

    assert isinstance(result, retrieve.UserResult)
    assert [user.id for user in result.users] == ["800", "801"]
    assert result.stop_reason == "no_next_page"
    assert client.calls[1][1]["after"] == "C1"
    assert all(user.raw is None for user in result.users)
    assert all("raw" not in user.to_dict() for user in result.users)


def test_people_search_fails_closed_on_an_invalid_non_null_user_node():
    client = FakeReadClient(
        [_user_page("people_search", [_raw_user("alphabetic", "synthetic_invalid")])]
    )

    with pytest.raises(errors.EnvelopeParseError, match="non-null user node"):
        retrieve.search(
            client,
            DOC_IDS,
            None,
            "synthetic",
            search_type="people",
        )

    assert client.requests_made == 1


def test_people_search_raw_preserves_top_level_source_nodes():
    first = _raw_user("810", "synthetic_raw_a")
    second = _raw_user("811", "synthetic_raw_b")
    client = FakeReadClient([_user_page("people_search", [first, second])])

    result = retrieve.search(
        client,
        DOC_IDS,
        None,
        "synthetic raw",
        search_type="people",
        raw=True,
    )

    assert isinstance(result, retrieve.UserResult)
    assert result.users[0].raw is first
    assert result.users[0].to_dict()["raw"] is first
    assert result.users[1].raw is second
    assert result.users[1].to_dict()["raw"] is second


def test_people_search_repeated_cursor_fails_closed_without_disclosing_cursor():
    cursor = "SECRET-REPEATED-USER-CURSOR"
    client = FakeReadClient(
        [
            _user_page(
                "people_search",
                [_raw_user("820", "synthetic_a")],
                cursor=cursor,
                has_next=True,
            ),
            _user_page(
                "people_search",
                [_raw_user("821", "synthetic_b")],
                cursor=cursor,
                has_next=True,
            ),
        ]
    )

    with pytest.raises(errors.EnvelopeParseError) as exc_info:
        retrieve.search(
            client,
            DOC_IDS,
            None,
            "synthetic",
            search_type="people",
        )

    message = str(exc_info.value)
    assert message == (
        "response envelope drift for 'people_search': pagination cursor was already scheduled"
    )
    assert cursor not in message
    assert client.requests_made == 2
    assert [call[1].get("after") for call in client.calls] == [None, cursor]


def test_people_search_cursor_cycle_fails_closed_without_disclosing_cursors():
    cursor_one = "SECRET-USER-CURSOR-1"
    cursor_two = "SECRET-USER-CURSOR-2"
    client = FakeReadClient(
        [
            _user_page(
                "people_search",
                [_raw_user("820", "synthetic_a")],
                cursor=cursor_one,
                has_next=True,
            ),
            _user_page(
                "people_search",
                [_raw_user("821", "synthetic_b")],
                cursor=cursor_two,
                has_next=True,
            ),
            _user_page(
                "people_search",
                [_raw_user("822", "synthetic_c")],
                cursor=cursor_one,
                has_next=True,
            ),
        ]
    )

    with pytest.raises(errors.EnvelopeParseError) as exc_info:
        retrieve.search(
            client,
            DOC_IDS,
            None,
            "synthetic",
            search_type="people",
        )

    message = str(exc_info.value)
    assert message == (
        "response envelope drift for 'people_search': pagination cursor was already scheduled"
    )
    assert cursor_one not in message
    assert cursor_two not in message
    assert client.requests_made == 3
    assert [call[1].get("after") for call in client.calls] == [
        None,
        cursor_one,
        cursor_two,
    ]


@pytest.mark.parametrize(
    ("operation", "friendly_name"),
    [
        ("followers", gql.FOLLOWERS_OPERATION),
        ("following", gql.FOLLOWING_OPERATION),
    ],
)
def test_social_graph_operations_return_user_output(operation, friendly_name):
    raw_user = _raw_user("900", f"synthetic_{operation}")
    client = FakeReadClient([_user_page(operation, [raw_user])])

    result = retrieve.fetch_social_graph(
        client,
        DOC_IDS,
        None,
        operation,
        "user_id",
        "300",
        limit=10,
    )

    assert isinstance(result, retrieve.UserResult)
    assert [user.id for user in result.users] == ["900"]
    assert result.stop_reason == "no_next_page"
    assert client.calls[0][0] == friendly_name
    assert client.calls[0][2] == DOC_IDS[operation]
    assert client.calls[0][1]["userID"] == "300"
    assert result.users[0].raw is None
    assert "raw" not in result.users[0].to_dict()


@pytest.mark.parametrize("operation", ["followers", "following"])
def test_social_graph_raw_preserves_top_level_source_node(operation):
    raw_user = _raw_user("901", f"synthetic_raw_{operation}")
    client = FakeReadClient([_user_page(operation, [raw_user])])

    result = retrieve.fetch_social_graph(
        client,
        DOC_IDS,
        None,
        operation,
        "user_id",
        "300",
        raw=True,
    )

    assert result.users[0].raw is raw_user
    assert result.users[0].to_dict()["raw"] is raw_user


def test_zero_limit_is_request_free_across_post_and_user_surfaces():
    for invoke in (
        lambda fake: retrieve.fetch_home(fake, DOC_IDS, None, limit=0),
        lambda fake: retrieve.fetch_profile(fake, DOC_IDS, None, "username", "synthetic", limit=0),
        lambda fake: retrieve.fetch_post(fake, DOC_IDS, None, "shortcode", "synthetic", limit=0),
        lambda fake: retrieve.search(
            fake, DOC_IDS, None, "synthetic", search_type="people", limit=0
        ),
        lambda fake: retrieve.fetch_social_graph(
            fake, DOC_IDS, None, "following", "username", "synthetic", limit=0
        ),
    ):
        fake = FakeReadClient([])
        result = invoke(fake)
        assert result.stop_reason == "limit_reached"
        assert result.requests_made == 0


def test_invalid_bounds_and_wait_budget_fail_before_request():
    client = FakeReadClient([])
    with pytest.raises(ValueError, match="since must not be later"):
        retrieve.fetch_home(
            client,
            DOC_IDS,
            None,
            since=datetime(2026, 7, 2, tzinfo=UTC),
            until=datetime(2026, 7, 1, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="max_wait"):
        retrieve.fetch_home(client, DOC_IDS, None, max_wait=-1)
    with pytest.raises(ValueError, match="max_requests"):
        retrieve.fetch_home(client, DOC_IDS, None, max_requests=-1)
    assert client.requests_made == 0
