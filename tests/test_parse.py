import pytest

from agentic_threads import parse
from agentic_threads.errors import EnvelopeParseError

POST_CASES = (
    (
        "feed.json",
        "feed",
        ["1001"],
        "synthetic_cursor_feed_page_2",
        True,
    ),
    ("profile_threads.json", "profile_threads", ["2001"], None, False),
    ("profile_threads.json", "profile_threads_page", ["2001"], None, False),
    ("profile_replies.json", "profile_replies", ["3002"], None, False),
    ("profile_replies.json", "profile_replies_page", ["3002"], None, False),
    ("post_root.json", "post", ["4001"], None, False),
    ("post_replies.json", "post_replies", ["4001", "4002"], None, False),
    (
        "post_search.json",
        "post_search",
        ["201"],
        "synthetic-post-page-2",
        True,
    ),
)

USER_CASES = (
    ("account_search.json", "people_search", ["301", "302"], None, False),
    (
        "followers.json",
        "followers",
        ["401", "402"],
        "synthetic-followers-page-2",
        True,
    ),
    ("following.json", "following", ["501"], None, False),
)

_ABSENT = object()


def _connection_response(
    walker: str,
    *,
    edges: object = _ABSENT,
    page_info: object = _ABSENT,
) -> dict:
    connection = {}
    if edges is not _ABSENT:
        connection["edges"] = edges
    if page_info is not _ABSENT:
        connection["page_info"] = page_info

    if walker == "posts":
        return {"data": {"feedData": connection}}
    return {"data": {"xdt_api__v1__users__search_connection": connection}}


def _feed_edge(thread_items: object) -> dict:
    return {
        "node": {
            "text_post_app_thread": {
                "thread_items": thread_items,
            }
        }
    }


CONNECTION_WALKERS = (
    ("posts", "feed", "data.feedData"),
    (
        "users",
        "people_search",
        "data.xdt_api__v1__users__search_connection",
    ),
)


def test_declared_operations_are_all_covered_by_a_logical_walker():
    assert set(parse.ENVELOPE_ROOTS) == {
        "feed",
        "profile",
        "profile_threads",
        "profile_threads_page",
        "profile_replies",
        "profile_replies_page",
        "post",
        "post_replies",
        "post_search",
        "people_search",
        "followers",
        "following",
    }
    assert {case[1] for case in POST_CASES} == set(parse.ENVELOPE_ROOTS) - {
        "profile",
        "people_search",
        "followers",
        "following",
    }
    assert {case[1] for case in USER_CASES} == {
        "people_search",
        "followers",
        "following",
    }


@pytest.mark.parametrize(
    ("fixture_name", "operation", "expected_ids", "expected_cursor", "has_next"),
    POST_CASES,
)
def test_every_post_operation_uses_its_anchored_path(
    load_fixture,
    fixture_name: str,
    operation: str,
    expected_ids: list[str],
    expected_cursor: str | None,
    has_next: bool,
):
    posts, cursor, actual_has_next = parse.walk_posts(load_fixture(fixture_name), operation)

    assert [post["pk"] for post in posts] == expected_ids
    assert cursor == expected_cursor
    assert actual_has_next is has_next


@pytest.mark.parametrize(
    ("fixture_name", "operation", "expected_ids", "expected_cursor", "has_next"),
    USER_CASES,
)
def test_every_user_connection_uses_its_anchored_path(
    load_fixture,
    fixture_name: str,
    operation: str,
    expected_ids: list[str],
    expected_cursor: str | None,
    has_next: bool,
):
    users, cursor, actual_has_next = parse.walk_users(load_fixture(fixture_name), operation)

    assert [user["pk"] for user in users] == expected_ids
    assert cursor == expected_cursor
    assert actual_has_next is has_next


def test_profile_extraction_handles_available_and_explicitly_unavailable_nodes(
    load_fixture,
):
    available = parse.extract_profile_user(load_fixture("followers.json"))

    assert available is not None
    assert available["pk"] == "101"
    assert available["username"] == "synthetic_owner"
    assert parse.extract_profile_user(load_fixture("unavailable.json")) is None


def test_explicit_empty_connection_and_thread_item_lists_are_valid():
    assert parse.walk_posts(
        _connection_response("posts", edges=[]),
        "feed",
    ) == ([], None, False)
    assert parse.walk_posts(
        _connection_response("posts", edges=[_feed_edge([])]),
        "feed",
    ) == ([], None, False)
    assert parse.walk_users(
        _connection_response("users", edges=[]),
        "people_search",
    ) == ([], None, False)


def test_null_direct_post_is_the_valid_empty_short_code_candidate():
    response = {"data": {"media": None}}

    assert parse.walk_posts(response, "post") == ([], None, False)


def test_empty_direct_post_object_is_still_a_post_node():
    response = {"data": {"media": {}}}

    assert parse.walk_posts(response, "post") == ([{}], None, False)


@pytest.mark.parametrize(
    ("page_info", "expected_cursor", "expected_has_next"),
    [
        pytest.param(_ABSENT, None, False, id="absent-is-eof"),
        pytest.param(None, None, False, id="null-is-eof"),
        pytest.param(
            {"has_next_page": False},
            None,
            False,
            id="false-with-absent-cursor",
        ),
        pytest.param(
            {"has_next_page": False, "end_cursor": None},
            None,
            False,
            id="false-with-null-cursor",
        ),
        pytest.param(
            {"has_next_page": False, "end_cursor": "terminal"},
            "terminal",
            False,
            id="false-with-terminal-cursor",
        ),
        pytest.param(
            {"has_next_page": True, "end_cursor": "next"},
            "next",
            True,
            id="true-with-cursor",
        ),
    ],
)
@pytest.mark.parametrize(
    ("walker", "operation"),
    [("posts", "feed"), ("users", "people_search")],
)
def test_valid_page_state_branches(
    walker: str,
    operation: str,
    page_info: object,
    expected_cursor: str | None,
    expected_has_next: bool,
):
    response = _connection_response(walker, edges=[], page_info=page_info)
    function = parse.walk_posts if walker == "posts" else parse.walk_users

    assert function(response, operation) == ([], expected_cursor, expected_has_next)


@pytest.mark.parametrize(
    ("page_info", "path_suffix", "expected"),
    [
        pytest.param([], "page_info", "an object or null", id="page-info-list"),
        pytest.param({}, "page_info.has_next_page", "a boolean", id="missing-has-next"),
        pytest.param(
            {"has_next_page": None},
            "page_info.has_next_page",
            "a boolean",
            id="null-has-next",
        ),
        pytest.param(
            {"has_next_page": 0},
            "page_info.has_next_page",
            "a boolean",
            id="integer-has-next",
        ),
        pytest.param(
            {"has_next_page": "false"},
            "page_info.has_next_page",
            "a boolean",
            id="string-has-next",
        ),
        pytest.param(
            {"has_next_page": False, "end_cursor": 7},
            "page_info.end_cursor",
            "a string or null",
            id="integer-cursor",
        ),
        pytest.param(
            {"has_next_page": True},
            "page_info",
            "a non-null end_cursor when has_next_page is true",
            id="true-with-absent-cursor",
        ),
        pytest.param(
            {"has_next_page": True, "end_cursor": None},
            "page_info",
            "a non-null end_cursor when has_next_page is true",
            id="true-with-null-cursor",
        ),
    ],
)
@pytest.mark.parametrize(("walker", "operation", "root_path"), CONNECTION_WALKERS)
def test_malformed_page_state_reports_the_exact_anchored_path(
    walker: str,
    operation: str,
    root_path: str,
    page_info: object,
    path_suffix: str,
    expected: str,
):
    response = _connection_response(walker, edges=[], page_info=page_info)
    function = parse.walk_posts if walker == "posts" else parse.walk_users
    path = f"{root_path}.{path_suffix}"

    with pytest.raises(EnvelopeParseError) as exc_info:
        function(response, operation)

    assert str(exc_info.value) == (
        f"response envelope drift for {operation!r} at {path!r}: expected {expected}"
    )


@pytest.mark.parametrize(
    ("response", "operation", "path", "expected"),
    [
        pytest.param(
            {},
            "feed",
            "data.feedData",
            "an object",
            id="missing-post-connection",
        ),
        pytest.param(
            {"data": {"mediaData": []}},
            "profile_threads",
            "data.mediaData",
            "an object",
            id="non-object-post-connection",
        ),
        pytest.param(
            _connection_response("posts"),
            "feed",
            "data.feedData.edges",
            "a list",
            id="missing-post-edges",
        ),
        pytest.param(
            _connection_response("posts", edges=None),
            "feed",
            "data.feedData.edges",
            "a list",
            id="null-post-edges",
        ),
        pytest.param(
            {"data": {"xdt_api__v1__users__search_connection": None}},
            "people_search",
            "data.xdt_api__v1__users__search_connection",
            "an object",
            id="null-user-connection",
        ),
        pytest.param(
            _connection_response("users"),
            "people_search",
            "data.xdt_api__v1__users__search_connection.edges",
            "a list",
            id="missing-user-edges",
        ),
        pytest.param(
            _connection_response("users", edges={}),
            "people_search",
            "data.xdt_api__v1__users__search_connection.edges",
            "a list",
            id="non-list-user-edges",
        ),
    ],
)
def test_malformed_connection_envelopes_report_exact_paths(
    response: dict,
    operation: str,
    path: str,
    expected: str,
):
    function = (
        parse.walk_users
        if operation in {"people_search", "followers", "following"}
        else parse.walk_posts
    )

    with pytest.raises(EnvelopeParseError) as exc_info:
        function(response, operation)

    assert str(exc_info.value) == (
        f"response envelope drift for {operation!r} at {path!r}: expected {expected}"
    )


@pytest.mark.parametrize(
    ("edges", "path", "expected"),
    [
        pytest.param(
            [_feed_edge([]), None],
            "data.feedData.edges[1]",
            "an object",
            id="null-edge",
        ),
        pytest.param(
            ["not-an-edge"],
            "data.feedData.edges[0]",
            "an object",
            id="scalar-edge",
        ),
        pytest.param(
            [{}],
            "data.feedData.edges[0].node",
            "an object",
            id="missing-node",
        ),
        pytest.param(
            [{"node": None}],
            "data.feedData.edges[0].node",
            "an object",
            id="null-node",
        ),
        pytest.param(
            [{"node": []}],
            "data.feedData.edges[0].node",
            "an object",
            id="non-object-node",
        ),
        pytest.param(
            [{"node": {"thread_items": [{"post": {"pk": "decoy"}}]}}],
            "data.feedData.edges[0].node.text_post_app_thread",
            "an object",
            id="missing-thread-container",
        ),
        pytest.param(
            [{"node": {"text_post_app_thread": None}}],
            "data.feedData.edges[0].node.text_post_app_thread",
            "an object",
            id="null-thread-container",
        ),
        pytest.param(
            [{"node": {"text_post_app_thread": []}}],
            "data.feedData.edges[0].node.text_post_app_thread",
            "an object",
            id="non-object-thread-container",
        ),
        pytest.param(
            [{"node": {"text_post_app_thread": {"nested": {"thread_items": []}}}}],
            "data.feedData.edges[0].node.text_post_app_thread.thread_items",
            "a list",
            id="missing-thread-items",
        ),
        pytest.param(
            [{"node": {"text_post_app_thread": {"thread_items": None}}}],
            "data.feedData.edges[0].node.text_post_app_thread.thread_items",
            "a list",
            id="null-thread-items",
        ),
        pytest.param(
            [{"node": {"text_post_app_thread": {"thread_items": {}}}}],
            "data.feedData.edges[0].node.text_post_app_thread.thread_items",
            "a list",
            id="non-list-thread-items",
        ),
        pytest.param(
            [_feed_edge([{"post": {"pk": "valid"}}, None])],
            "data.feedData.edges[0].node.text_post_app_thread.thread_items[1]",
            "an object",
            id="null-item-row",
        ),
        pytest.param(
            [_feed_edge(["not-an-item"])],
            "data.feedData.edges[0].node.text_post_app_thread.thread_items[0]",
            "an object",
            id="scalar-item-row",
        ),
        pytest.param(
            [
                _feed_edge(
                    [
                        {"post": {"pk": "valid"}},
                        {"wrapper": {"post": {"pk": "decoy"}}},
                    ]
                )
            ],
            "data.feedData.edges[0].node.text_post_app_thread.thread_items[1].post",
            "an object",
            id="missing-post-leaf",
        ),
        pytest.param(
            [_feed_edge([{"post": None}])],
            "data.feedData.edges[0].node.text_post_app_thread.thread_items[0].post",
            "an object",
            id="null-post-leaf",
        ),
        pytest.param(
            [_feed_edge([{"post": "not-a-post"}])],
            "data.feedData.edges[0].node.text_post_app_thread.thread_items[0].post",
            "an object",
            id="scalar-post-leaf",
        ),
    ],
)
def test_malformed_post_connection_children_report_indexed_paths(
    edges: list[object],
    path: str,
    expected: str,
):
    with pytest.raises(EnvelopeParseError) as exc_info:
        parse.walk_posts(_connection_response("posts", edges=edges), "feed")

    assert str(exc_info.value) == (
        f"response envelope drift for 'feed' at {path!r}: expected {expected}"
    )


@pytest.mark.parametrize(
    ("edges", "path"),
    [
        pytest.param(
            [{"node": {}}, None],
            "data.xdt_api__v1__users__search_connection.edges[1]",
            id="null-edge",
        ),
        pytest.param(
            [[]],
            "data.xdt_api__v1__users__search_connection.edges[0]",
            id="non-object-edge",
        ),
        pytest.param(
            [{}],
            "data.xdt_api__v1__users__search_connection.edges[0].node",
            id="missing-node",
        ),
        pytest.param(
            [{"node": None}],
            "data.xdt_api__v1__users__search_connection.edges[0].node",
            id="null-node",
        ),
        pytest.param(
            [{"node": "not-a-user"}],
            "data.xdt_api__v1__users__search_connection.edges[0].node",
            id="scalar-node",
        ),
    ],
)
def test_malformed_user_connection_children_report_indexed_paths(
    edges: list[object],
    path: str,
):
    with pytest.raises(EnvelopeParseError) as exc_info:
        parse.walk_users(_connection_response("users", edges=edges), "people_search")

    assert str(exc_info.value) == (
        f"response envelope drift for 'people_search' at {path!r}: expected an object"
    )


@pytest.mark.parametrize("value", [[], "not-a-user", 7])
def test_non_object_profile_user_is_drift(value: object):
    with pytest.raises(EnvelopeParseError) as exc_info:
        parse.extract_profile_user({"data": {"user": value}})

    assert str(exc_info.value) == (
        "response envelope drift for 'profile' at 'data.user': expected an object or null"
    )


def test_post_connection_ignores_recursive_and_wrong_level_decoys():
    response = {
        "data": {
            "feedData": {
                "edges": [
                    {
                        "node": {
                            "post": {"pk": "direct-decoy"},
                            "nested": {"post": {"pk": "recursive-decoy"}},
                            "text_post_app_thread": {
                                "thread_items": [],
                            },
                        }
                    }
                ]
            }
        }
    }

    assert parse.walk_posts(response, "feed") == ([], None, False)


@pytest.mark.parametrize(
    ("walker", "operation", "response", "path"),
    [
        pytest.param(
            "posts",
            "feed",
            {
                "data": {
                    "elsewhere": {
                        "edges": [_feed_edge([{"post": {"pk": "decoy"}}])],
                    }
                },
                "feedData": {"edges": [_feed_edge([{"post": {"pk": "decoy"}}])]},
            },
            "data.feedData",
            id="post-connection",
        ),
        pytest.param(
            "users",
            "people_search",
            {
                "data": {
                    "elsewhere": {
                        "edges": [{"node": {"pk": "decoy"}}],
                    }
                },
                "xdt_api__v1__users__search_connection": {
                    "edges": [{"node": {"pk": "decoy"}}],
                },
            },
            "data.xdt_api__v1__users__search_connection",
            id="user-connection",
        ),
    ],
)
def test_missing_connection_anchor_is_drift_even_when_valid_decoys_exist(
    walker: str,
    operation: str,
    response: dict,
    path: str,
):
    function = parse.walk_posts if walker == "posts" else parse.walk_users

    with pytest.raises(EnvelopeParseError) as exc_info:
        function(response, operation)

    assert str(exc_info.value) == (
        f"response envelope drift for {operation!r} at {path!r}: expected an object"
    )


def test_missing_post_anchor_is_drift_even_when_a_decoy_exists():
    response = {
        "data": {"elsewhere": {"media": {"pk": "decoy"}}},
        "media": {"pk": "top-level-decoy"},
    }

    with pytest.raises(EnvelopeParseError) as exc_info:
        parse.walk_posts(response, "post")

    assert str(exc_info.value) == (
        "response envelope drift for 'post' at 'data.media': expected an object or null"
    )


def test_missing_profile_anchor_is_drift_even_when_a_decoy_exists():
    response = {"data": {"elsewhere": {"user": {"pk": "decoy"}}}}

    with pytest.raises(EnvelopeParseError) as exc_info:
        parse.extract_profile_user(response)

    assert str(exc_info.value) == (
        "response envelope drift for 'profile' at 'data.user': expected an object or null"
    )


@pytest.mark.parametrize("value", [[], "not-an-object", 7])
def test_non_object_direct_post_is_drift(value: object):
    response = {"data": {"media": value}}

    with pytest.raises(EnvelopeParseError) as exc_info:
        parse.walk_posts(response, "post")

    assert str(exc_info.value) == (
        "response envelope drift for 'post' at 'data.media': expected an object or null"
    )


def test_unsupported_operation_is_not_treated_as_an_empty_result():
    with pytest.raises(EnvelopeParseError, match="unsupported post"):
        parse.walk_posts({}, "people_search")
    with pytest.raises(EnvelopeParseError, match="unsupported user"):
        parse.walk_users({}, "feed")
