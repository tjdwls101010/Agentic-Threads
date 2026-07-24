from __future__ import annotations

import os

import pytest

from agentic_threads import auth, client, model, retrieve, session

_LIVE_ENV = "AGENTIC_THREADS_LIVE"
_PROFILE_ENV = "AGENTIC_THREADS_LIVE_PROFILE"
_TARGET_ENV = "AGENTIC_THREADS_LIVE_TARGET"
_MAX_REQUESTS = 2

pytestmark = pytest.mark.skipif(
    os.environ.get(_LIVE_ENV) != "1",
    reason=f"set {_LIVE_ENV}=1 to run authorized read-only live checks",
)


def _live_settings() -> tuple[str, str]:
    if os.environ.get(_LIVE_ENV) != "1":
        pytest.skip("live checks are disabled")
    target = os.environ.get(_TARGET_ENV)
    if not target:
        pytest.skip(f"set {_TARGET_ENV} to an authorized disposable-account target")
    return os.environ.get(_PROFILE_ENV, "default"), target


def _require(condition: bool, message: str) -> None:
    if not condition:
        pytest.fail(message, pytrace=False)


def test_bounded_read_only_profile_shape():
    profile, target = _live_settings()
    identifier_kind, identifier_value = auth.normalize_user_identifier(target)
    credential = auth.load_session(profile)
    doc_ids, features = session.query_data_for(credential)

    with client.ReadClient(credential, max_requests=_MAX_REQUESTS) as read_client:
        result = retrieve.fetch_profile(
            read_client,
            doc_ids,
            features,
            identifier_kind,
            identifier_value,
            replies=False,
            limit=1,
            max_requests=_MAX_REQUESTS,
            wait_on_limit=False,
            raw=False,
        )

    _require(
        isinstance(result, retrieve.RetrieveResult),
        "profile result has the wrong type",
    )
    _require(isinstance(result.posts, list), "profile posts field is not a list")
    _require(
        all(isinstance(post, model.Post) for post in result.posts),
        "profile posts contain an unexpected type",
    )
    _require(isinstance(result.stop_reason, str), "profile stop reason is not a string")
    _require(
        result.stop_reason in retrieve.STOP_REASONS,
        "profile stop reason is not recognized",
    )
    _require(type(result.requests_made) is int, "request count is not an integer")
    _require(
        1 <= result.requests_made <= _MAX_REQUESTS,
        "request count escaped the bounded live-test budget",
    )
    _require(type(result.raw_post_count) is int, "raw post count is not an integer")
    _require(
        result.raw_post_count >= len(result.posts),
        "raw post count violates its invariant",
    )
    _require(
        type(result.since_target_crossed) is bool,
        "boundary marker is not a boolean",
    )
