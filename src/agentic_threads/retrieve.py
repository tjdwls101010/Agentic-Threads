"""Read orchestration: target resolution, pagination, filtering, and stop reasons."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import config, errors, gql, model, parse
from .auth import shortcode_to_post_id_candidates
from .docids import DEFAULT_DOC_IDS

STOP_REASONS = frozenset(
    {
        "limit_reached",
        "since_crossed",
        "feed_exhausted",
        "no_next_page",
        "no_matches",
        "empty_pages",
        "rate_limited",
        "max_requests",
    }
)

_EMPTY_USER_PAGE_LIMIT = 3
_HARD_STOP_REASONS = frozenset({"limit_reached", "rate_limited", "max_requests"})


@dataclass
class RetrieveResult:
    posts: list[model.Post]
    stop_reason: str
    requests_made: int
    since_target_crossed: bool = False
    raw_post_count: int = 0


@dataclass
class UserResult:
    users: list[model.User]
    stop_reason: str
    requests_made: int


@dataclass
class _PostState:
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    posts: list[model.Post] = field(default_factory=list)
    seen_ids: set[str] = field(default_factory=set)
    limit_count: int = 0
    since_target_crossed: bool = False
    raw_post_count: int = 0


@dataclass
class _RunContext:
    read_client: object
    doc_ids: Mapping[str, str]
    start_requests: int
    max_requests: int
    wait_on_limit: bool
    max_wait: float | None

    @classmethod
    def create(
        cls,
        read_client: object,
        doc_ids: Mapping[str, str],
        *,
        max_requests: int | None,
        wait_on_limit: bool,
        max_wait: float | None,
    ) -> _RunContext:
        budget = config.DEFAULT_MAX_REQUESTS if max_requests is None else max_requests
        if budget < 0:
            raise ValueError("max_requests must be non-negative")
        if max_wait is not None and max_wait < 0:
            raise ValueError("max_wait must be non-negative")
        return cls(
            read_client=read_client,
            doc_ids=doc_ids,
            start_requests=_request_count(read_client),
            max_requests=budget,
            wait_on_limit=wait_on_limit,
            max_wait=max_wait,
        )

    @property
    def requests_made(self) -> int:
        return max(0, _request_count(self.read_client) - self.start_requests)

    def budget_exhausted(self) -> bool:
        if self.requests_made >= self.max_requests:
            return True
        client_budget = getattr(self.read_client, "max_requests", None)
        return isinstance(client_budget, int) and _request_count(self.read_client) >= client_budget

    def post(
        self,
        operation_key: str,
        operation: str,
        variables: Mapping[str, object],
    ) -> tuple[dict | None, str | None]:
        """Return ``(body, None)`` or ``(None, terminal_stop_reason)``."""
        while True:
            if self.budget_exhausted():
                return None, "max_requests"
            try:
                body = self.read_client.post(
                    operation,
                    variables,
                    doc_id=_doc_id(self.doc_ids, operation_key),
                )
            except errors.RateLimitedError as exc:
                if not self.wait_on_limit or exc.reset_at is None:
                    return None, "rate_limited"
                wait_seconds = max(0.0, exc.reset_at - time.time())
                if self.max_wait is not None and wait_seconds > self.max_wait:
                    return None, "rate_limited"
                if self.budget_exhausted():
                    return None, "max_requests"
                time.sleep(wait_seconds)
                continue
            if not isinstance(body, dict):
                raise errors.EnvelopeParseError(f"{operation} returned a non-object response")
            return body, None


def _request_count(read_client: object) -> int:
    value = getattr(read_client, "requests_made", 0)
    return int(value) if isinstance(value, int) else 0


def _doc_id(doc_ids: Mapping[str, str], operation_key: str) -> str:
    value = doc_ids.get(operation_key) or DEFAULT_DOC_IDS.get(operation_key)
    if not value:
        raise errors.EnvelopeParseError(f"no doc_id is available for {operation_key}")
    return str(value)


def _normalise_bound(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _bounds(
    since: datetime | None,
    until: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    normal_since = _normalise_bound(since)
    normal_until = _normalise_bound(until)
    if normal_since is not None and normal_until is not None and normal_since > normal_until:
        raise ValueError("since must not be later than until")
    return normal_since, normal_until


def _consume_posts(
    raw_posts: Sequence[dict],
    state: _PostState,
    *,
    profile_connection: bool,
    limit: int | None,
    since: datetime | None,
    until: datetime | None,
    raw: bool,
) -> str | None:
    state.raw_post_count += len(raw_posts)
    terminal_reason: str | None = None
    for raw_post in raw_posts:
        post = model.build_post(
            raw_post,
            captured_at=state.captured_at,
            include_raw=raw,
        )
        if post is None:
            raise errors.EnvelopeParseError(
                "response envelope drift: a non-null post node could not be normalized"
            )

        if terminal_reason is not None and (not profile_connection or not post.is_pinned):
            continue
        if (
            profile_connection
            and not post.is_pinned
            and since is not None
            and post.created_at is not None
            and post.created_at < since
        ):
            state.since_target_crossed = True
            terminal_reason = "since_crossed"
            continue
        if post.id in state.seen_ids:
            continue
        state.seen_ids.add(post.id)

        if since is not None and post.created_at is not None and post.created_at < since:
            continue
        if until is not None and post.created_at is not None and post.created_at > until:
            continue

        state.posts.append(post)
        if profile_connection and post.is_pinned:
            continue
        state.limit_count += 1
        if limit is not None and state.limit_count >= limit:
            terminal_reason = "limit_reached"
    return terminal_reason


def _post_result(state: _PostState, context: _RunContext, stop_reason: str) -> RetrieveResult:
    if stop_reason not in STOP_REASONS:
        raise AssertionError(f"undeclared stop reason: {stop_reason}")
    return RetrieveResult(
        posts=state.posts,
        stop_reason=stop_reason,
        requests_made=context.requests_made,
        since_target_crossed=state.since_target_crossed,
        raw_post_count=state.raw_post_count,
    )


def _paginate_posts(
    context: _RunContext,
    state: _PostState,
    *,
    profile_connection: bool,
    initial_key: str,
    initial_operation: str,
    initial_variables: Callable[[], Mapping[str, object]],
    page_key: str,
    page_operation: str,
    page_variables: Callable[[str], Mapping[str, object]],
    eof_reason: str,
    limit: int | None,
    since: datetime | None,
    until: datetime | None,
    raw: bool,
) -> str:
    cursor: str | None = None
    seen_cursors: set[str] = set()
    first_page = True

    while True:
        if first_page:
            operation_key = initial_key
            operation = initial_operation
            variables = initial_variables()
        else:
            operation_key = page_key
            operation = page_operation
            assert cursor is not None
            variables = page_variables(cursor)

        body, stop_reason = context.post(operation_key, operation, variables)
        if stop_reason is not None:
            return stop_reason
        assert body is not None
        raw_posts, next_cursor, has_next_page = parse.walk_posts(body, operation_key)
        if has_next_page and next_cursor is not None and next_cursor in seen_cursors:
            raise errors.EnvelopeParseError(
                f"response envelope drift for {operation_key!r}: "
                "pagination cursor was already scheduled"
            )
        stop_reason = _consume_posts(
            raw_posts,
            state,
            profile_connection=profile_connection,
            limit=limit,
            since=since,
            until=until,
            raw=raw,
        )
        if stop_reason is not None:
            return stop_reason

        if not has_next_page or next_cursor is None:
            return eof_reason
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        first_page = False


def _exact_user_id(body: dict, username: str) -> str:
    raw_users, _, _ = parse.walk_users(body, "people_search")
    target = username.lstrip("@").casefold()
    for raw_user in raw_users:
        user = model.build_user(raw_user)
        if user is None:
            raise errors.EnvelopeParseError(
                "response envelope drift for 'people_search': "
                "a non-null user node could not be normalized"
            )
        if user.username and user.username.lstrip("@").casefold() == target:
            return user.id
    raise errors.ProfileUnavailableError(
        f"@{username.lstrip('@')} is unavailable or was not an exact search match"
    )


def resolve_user_id(
    read_client: object,
    doc_ids: Mapping[str, str],
    features: Mapping[str, object] | None,
    identifier_kind: str,
    identifier_value: str,
) -> str:
    """Resolve a username via an exact account-search match; ids pass through."""
    if identifier_kind == "user_id":
        return identifier_value
    if identifier_kind != "username":
        raise errors.InvalidIdentifierError(f"unsupported user identifier kind: {identifier_kind}")

    body = read_client.post(
        gql.PEOPLE_SEARCH_OPERATION,
        gql.people_search_variables(identifier_value, features=features),
        doc_id=_doc_id(doc_ids, "people_search"),
    )
    if not isinstance(body, dict):
        raise errors.EnvelopeParseError("people_search returned a non-object response")
    return _exact_user_id(body, identifier_value)


def _resolve_user_id_in_run(
    context: _RunContext,
    features: Mapping[str, object] | None,
    identifier_kind: str,
    identifier_value: str,
) -> tuple[str | None, str | None]:
    if identifier_kind == "user_id":
        return identifier_value, None
    if identifier_kind != "username":
        raise errors.InvalidIdentifierError(f"unsupported user identifier kind: {identifier_kind}")

    body, stop_reason = context.post(
        "people_search",
        gql.PEOPLE_SEARCH_OPERATION,
        gql.people_search_variables(identifier_value, features=features),
    )
    if stop_reason is not None:
        return None, stop_reason
    assert body is not None
    return _exact_user_id(body, identifier_value), None


def fetch_profile(
    read_client: object,
    doc_ids: Mapping[str, str],
    features: Mapping[str, object] | None,
    identifier_kind: str,
    identifier_value: str,
    *,
    replies: bool = False,
    limit: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    max_requests: int | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
    raw: bool = False,
) -> RetrieveResult:
    """Fetch a profile's Threads tab and, optionally, its separate Replies tab."""
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    since, until = _bounds(since, until)
    context = _RunContext.create(
        read_client,
        doc_ids,
        max_requests=max_requests,
        wait_on_limit=wait_on_limit,
        max_wait=max_wait,
    )
    state = _PostState()
    if limit == 0:
        return _post_result(state, context, "limit_reached")

    user_id, stop_reason = _resolve_user_id_in_run(
        context,
        features,
        identifier_kind,
        identifier_value,
    )
    if stop_reason is not None:
        return _post_result(state, context, stop_reason)
    assert user_id is not None

    threads_reason = _paginate_posts(
        context,
        state,
        initial_key="profile_threads",
        initial_operation=gql.PROFILE_THREADS_OPERATION,
        initial_variables=lambda: gql.profile_threads_variables(user_id, features=features),
        page_key="profile_threads_page",
        page_operation=gql.PROFILE_THREADS_PAGE_OPERATION,
        page_variables=lambda cursor: gql.profile_threads_page_variables(
            user_id,
            cursor=cursor,
            features=features,
        ),
        eof_reason="no_next_page",
        profile_connection=True,
        limit=limit,
        since=since,
        until=until,
        raw=raw,
    )
    if threads_reason in _HARD_STOP_REASONS or not replies:
        return _post_result(state, context, threads_reason)

    replies_reason = _paginate_posts(
        context,
        state,
        initial_key="profile_replies",
        initial_operation=gql.PROFILE_REPLIES_OPERATION,
        initial_variables=lambda: gql.profile_replies_variables(user_id, features=features),
        page_key="profile_replies_page",
        page_operation=gql.PROFILE_REPLIES_PAGE_OPERATION,
        page_variables=lambda cursor: gql.profile_replies_page_variables(
            user_id,
            cursor=cursor,
            features=features,
        ),
        eof_reason="no_next_page",
        profile_connection=True,
        limit=limit,
        since=since,
        until=until,
        raw=raw,
    )
    if replies_reason in _HARD_STOP_REASONS:
        if since is not None:
            # Crossing the Threads tab alone does not prove the independent
            # Replies tab reached the same boundary.
            state.since_target_crossed = False
        return _post_result(state, context, replies_reason)
    if threads_reason == "since_crossed" or replies_reason == "since_crossed":
        replies_reason = "since_crossed"
    return _post_result(state, context, replies_reason)


def fetch_home(
    read_client: object,
    doc_ids: Mapping[str, str],
    features: Mapping[str, object] | None,
    *,
    limit: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    max_requests: int | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
    raw: bool = False,
) -> RetrieveResult:
    """Fetch the authenticated account's For You feed."""
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    since, until = _bounds(since, until)
    context = _RunContext.create(
        read_client,
        doc_ids,
        max_requests=max_requests,
        wait_on_limit=wait_on_limit,
        max_wait=max_wait,
    )
    state = _PostState()
    if limit == 0:
        return _post_result(state, context, "limit_reached")

    stop_reason = _paginate_posts(
        context,
        state,
        initial_key="feed",
        initial_operation=gql.FEED_OPERATION,
        initial_variables=lambda: gql.feed_variables(features=features),
        page_key="feed",
        page_operation=gql.FEED_OPERATION,
        page_variables=lambda cursor: gql.feed_variables(cursor=cursor, features=features),
        eof_reason="feed_exhausted",
        profile_connection=False,
        limit=limit,
        since=since,
        until=until,
        raw=raw,
    )
    return _post_result(state, context, stop_reason)


def fetch_post(
    read_client: object,
    doc_ids: Mapping[str, str],
    features: Mapping[str, object] | None,
    identifier_kind: str,
    identifier_value: str,
    *,
    replies: bool = True,
    limit: int | None = None,
    max_requests: int | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
    raw: bool = False,
) -> RetrieveResult:
    """Fetch a root post, probing shortcode candidates, then its reply connection."""
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    context = _RunContext.create(
        read_client,
        doc_ids,
        max_requests=max_requests,
        wait_on_limit=wait_on_limit,
        max_wait=max_wait,
    )
    state = _PostState()
    if limit == 0:
        return _post_result(state, context, "limit_reached")

    if identifier_kind == "post_id":
        candidates = (identifier_value,)
    elif identifier_kind == "shortcode":
        candidates = tuple(
            str(value) for value in shortcode_to_post_id_candidates(identifier_value)
        )
    else:
        raise errors.InvalidIdentifierError(f"unsupported post identifier kind: {identifier_kind}")

    resolved_id: str | None = None
    for candidate in candidates:
        body, stop_reason = context.post(
            "post",
            gql.POST_OPERATION,
            gql.post_variables(candidate, features=features),
        )
        if stop_reason is not None:
            return _post_result(state, context, stop_reason)
        assert body is not None
        raw_posts, _, _ = parse.walk_posts(body, "post")
        if not raw_posts:
            continue
        stop_reason = _consume_posts(
            raw_posts,
            state,
            profile_connection=False,
            limit=limit,
            since=None,
            until=None,
            raw=raw,
        )
        if not state.posts:
            raise errors.EnvelopeParseError("root post media could not be normalized")
        root = state.posts[-1]
        if root.id != candidate:
            raise errors.EnvelopeParseError(
                f"response envelope drift for 'post': normalized root id {root.id!r} "
                f"does not match probed candidate {candidate!r}"
            )
        if identifier_kind == "shortcode" and root.code != identifier_value:
            raise errors.EnvelopeParseError(
                f"response envelope drift for 'post': normalized root code {root.code!r} "
                f"does not match requested shortcode {identifier_value!r}"
            )
        resolved_id = candidate
        if stop_reason == "limit_reached":
            return _post_result(state, context, stop_reason)
        break

    if resolved_id is None:
        raise errors.NotFoundError(f"post {identifier_value} was not found or is unavailable")
    if not replies:
        return _post_result(state, context, "no_next_page")

    stop_reason = _paginate_posts(
        context,
        state,
        initial_key="post_replies",
        initial_operation=gql.POST_REPLIES_OPERATION,
        initial_variables=lambda: gql.post_replies_variables(resolved_id, features=features),
        page_key="post_replies",
        page_operation=gql.POST_REPLIES_OPERATION,
        page_variables=lambda cursor: gql.post_replies_variables(
            resolved_id,
            cursor=cursor,
            features=features,
        ),
        eof_reason="no_next_page",
        profile_connection=False,
        limit=limit,
        since=None,
        until=None,
        raw=raw,
    )
    return _post_result(state, context, stop_reason)


def _paginate_users(
    context: _RunContext,
    *,
    operation_key: str,
    operation: str,
    build_variables: Callable[[str | None], Mapping[str, object]],
    limit: int | None,
    empty_page_guard: bool,
    raw: bool,
) -> tuple[list[model.User], str]:
    users: list[model.User] = []
    seen_ids: set[str] = set()
    cursor: str | None = None
    seen_cursors: set[str] = set()
    empty_pages = 0

    while True:
        body, stop_reason = context.post(
            operation_key,
            operation,
            build_variables(cursor),
        )
        if stop_reason is not None:
            return users, stop_reason
        assert body is not None
        raw_users, next_cursor, has_next_page = parse.walk_users(body, operation_key)
        if has_next_page and next_cursor is not None and next_cursor in seen_cursors:
            raise errors.EnvelopeParseError(
                f"response envelope drift for {operation_key!r}: "
                "pagination cursor was already scheduled"
            )

        added = 0
        for raw_user in raw_users:
            user = model.build_user(raw_user, include_raw=raw)
            if user is None:
                raise errors.EnvelopeParseError(
                    f"response envelope drift for {operation_key!r}: "
                    "a non-null user node could not be normalized"
                )
            if user.id in seen_ids:
                continue
            seen_ids.add(user.id)
            users.append(user)
            added += 1
            if limit is not None and len(users) >= limit:
                return users, "limit_reached"

        if not has_next_page or next_cursor is None:
            return users, "no_next_page"
        empty_pages = 0 if added else empty_pages + 1
        if empty_page_guard and empty_pages >= _EMPTY_USER_PAGE_LIMIT:
            return users, "empty_pages"
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def search(
    read_client: object,
    doc_ids: Mapping[str, str],
    features: Mapping[str, object] | None,
    query: str,
    *,
    search_type: str = "posts",
    limit: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    max_requests: int | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
    raw: bool = False,
) -> RetrieveResult | UserResult:
    """Search posts or people, returning the result type that matches the surface."""
    if not query.strip():
        raise errors.InvalidIdentifierError("search query must not be empty")
    if search_type not in {"posts", "people"}:
        raise errors.InvalidIdentifierError(f"unsupported search type: {search_type}")
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")

    context = _RunContext.create(
        read_client,
        doc_ids,
        max_requests=max_requests,
        wait_on_limit=wait_on_limit,
        max_wait=max_wait,
    )
    if search_type == "people":
        if limit == 0:
            return UserResult([], "limit_reached", context.requests_made)
        users, stop_reason = _paginate_users(
            context,
            operation_key="people_search",
            operation=gql.PEOPLE_SEARCH_OPERATION,
            build_variables=lambda cursor: gql.people_search_variables(
                query,
                cursor=cursor,
                features=features,
            ),
            limit=limit,
            empty_page_guard=False,
            raw=raw,
        )
        if not users and stop_reason == "no_next_page":
            stop_reason = "no_matches"
        return UserResult(users, stop_reason, context.requests_made)

    since, until = _bounds(since, until)
    state = _PostState()
    if limit == 0:
        return _post_result(state, context, "limit_reached")
    stop_reason = _paginate_posts(
        context,
        state,
        initial_key="post_search",
        initial_operation=gql.POST_SEARCH_OPERATION,
        initial_variables=lambda: gql.post_search_variables(query, features=features),
        page_key="post_search",
        page_operation=gql.POST_SEARCH_OPERATION,
        page_variables=lambda cursor: gql.post_search_variables(
            query,
            cursor=cursor,
            features=features,
        ),
        eof_reason="no_next_page",
        profile_connection=False,
        limit=limit,
        since=since,
        until=until,
        raw=raw,
    )
    if not state.posts and stop_reason == "no_next_page":
        stop_reason = "no_matches"
    return _post_result(state, context, stop_reason)


def fetch_social_graph(
    read_client: object,
    doc_ids: Mapping[str, str],
    features: Mapping[str, object] | None,
    operation: str,
    identifier_kind: str,
    identifier_value: str,
    *,
    limit: int | None = None,
    max_requests: int | None = None,
    wait_on_limit: bool = False,
    max_wait: float | None = None,
    raw: bool = False,
) -> UserResult:
    """Fetch a user's followers or following connection."""
    if operation not in {"followers", "following"}:
        raise errors.InvalidIdentifierError(f"unsupported social graph operation: {operation}")
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")

    context = _RunContext.create(
        read_client,
        doc_ids,
        max_requests=max_requests,
        wait_on_limit=wait_on_limit,
        max_wait=max_wait,
    )
    if limit == 0:
        return UserResult([], "limit_reached", context.requests_made)

    user_id, stop_reason = _resolve_user_id_in_run(
        context,
        features,
        identifier_kind,
        identifier_value,
    )
    if stop_reason is not None:
        return UserResult([], stop_reason, context.requests_made)
    assert user_id is not None

    friendly_name = gql.FOLLOWERS_OPERATION if operation == "followers" else gql.FOLLOWING_OPERATION

    def build_variables(cursor: str | None) -> Mapping[str, object]:
        if operation == "followers":
            return gql.followers_variables(user_id, cursor=cursor, features=features)
        return gql.following_variables(user_id, cursor=cursor, features=features)

    users, stop_reason = _paginate_users(
        context,
        operation_key=operation,
        operation=friendly_name,
        build_variables=build_variables,
        limit=limit,
        empty_page_guard=True,
        raw=raw,
    )
    return UserResult(users, stop_reason, context.requests_made)
