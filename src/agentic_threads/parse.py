"""Pure, operation-specific walkers for one Threads GraphQL response.

The live contract anchors every operation to a fixed Relay envelope. Walkers
never recursively search for a convenient ``post`` or ``user`` key: doing so
would accept decoy branches and hide a rotated response shape.
"""

from __future__ import annotations

from typing import Any

from .errors import EnvelopeParseError

# Sanitized Phase 0 live contract: recon-contract.json, schemaVersion 1.
ENVELOPE_ROOTS: dict[str, tuple[str, ...]] = {
    "feed": ("data", "feedData"),
    "profile": ("data", "user"),
    "profile_threads": ("data", "mediaData"),
    "profile_threads_page": ("data", "mediaData"),
    "profile_replies": ("data", "mediaData"),
    "profile_replies_page": ("data", "mediaData"),
    "post": ("data", "media"),
    "post_replies": ("data", "data"),
    "post_search": ("data", "searchResults"),
    "people_search": ("data", "xdt_api__v1__users__search_connection"),
    "followers": ("data", "user", "followers"),
    "following": ("data", "user", "following"),
}

# Relative to each connection edge's ``node``, these paths lead to the
# ``thread_items`` list. Each item must carry its post at the direct ``post`` key.
_POST_THREAD_ITEM_PATHS: dict[str, tuple[str, ...]] = {
    "feed": ("text_post_app_thread", "thread_items"),
    "profile_threads": ("thread_items",),
    "profile_threads_page": ("thread_items",),
    "profile_replies": ("thread_items",),
    "profile_replies_page": ("thread_items",),
    "post_replies": ("thread_items",),
    "post_search": ("thread", "thread_items"),
}

_USER_CONNECTION_OPERATIONS = frozenset({"people_search", "followers", "following"})
_MISSING = object()


def _path_text(path: tuple[str | int, ...]) -> str:
    text = ""
    for part in path:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += f".{part}" if text else part
    return text


def _get_path(value: object, path: tuple[str, ...]) -> object:
    current = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _drift(operation: str, path: tuple[str | int, ...], expected: str) -> EnvelopeParseError:
    return EnvelopeParseError(
        f"response envelope drift for {operation!r} at {_path_text(path)!r}: expected {expected}"
    )


def _connection(response: dict[str, Any], operation: str) -> dict[str, Any]:
    root_path = ENVELOPE_ROOTS[operation]
    value = _get_path(response, root_path)
    if not isinstance(value, dict):
        raise _drift(operation, root_path, "an object")
    edges = value.get("edges", _MISSING)
    if not isinstance(edges, list):
        raise _drift(operation, (*root_path, "edges"), "a list")
    return value


def _page_state(connection: dict[str, Any], operation: str) -> tuple[str | None, bool]:
    """Read optional Relay page_info without inventing an EOF cursor."""
    root_path = ENVELOPE_ROOTS[operation]
    page_info = connection.get("page_info", _MISSING)
    if page_info is _MISSING or page_info is None:
        return None, False
    if not isinstance(page_info, dict):
        raise _drift(operation, (*root_path, "page_info"), "an object or null")

    has_next_page = page_info.get("has_next_page", _MISSING)
    if not isinstance(has_next_page, bool):
        raise _drift(
            operation,
            (*root_path, "page_info", "has_next_page"),
            "a boolean",
        )

    end_cursor = page_info.get("end_cursor")
    if end_cursor is not None and not isinstance(end_cursor, str):
        raise _drift(
            operation,
            (*root_path, "page_info", "end_cursor"),
            "a string or null",
        )
    if has_next_page and end_cursor is None:
        raise _drift(
            operation,
            (*root_path, "page_info"),
            "a non-null end_cursor when has_next_page is true",
        )
    return end_cursor, has_next_page


def _thread_items(
    node: dict[str, Any],
    operation: str,
    node_path: tuple[str | int, ...],
    path: tuple[str, ...],
) -> tuple[list[Any], tuple[str | int, ...]]:
    current = node
    current_path = node_path
    for key in path[:-1]:
        current_path = (*current_path, key)
        value = current.get(key, _MISSING)
        if not isinstance(value, dict):
            raise _drift(operation, current_path, "an object")
        current = value

    items_path = (*current_path, path[-1])
    items = current.get(path[-1], _MISSING)
    if not isinstance(items, list):
        raise _drift(operation, items_path, "a list")
    return items, items_path


def _posts_from_connection(connection: dict[str, Any], operation: str) -> list[dict[str, Any]]:
    root_path = ENVELOPE_ROOTS[operation]
    thread_item_path = _POST_THREAD_ITEM_PATHS[operation]
    posts: list[dict[str, Any]] = []
    for edge_index, edge in enumerate(connection["edges"]):
        edge_path = (*root_path, "edges", edge_index)
        if not isinstance(edge, dict):
            raise _drift(operation, edge_path, "an object")

        node_path = (*edge_path, "node")
        node = edge.get("node", _MISSING)
        if not isinstance(node, dict):
            raise _drift(operation, node_path, "an object")

        items, items_path = _thread_items(node, operation, node_path, thread_item_path)
        for item_index, item in enumerate(items):
            item_path = (*items_path, item_index)
            if not isinstance(item, dict):
                raise _drift(operation, item_path, "an object")

            post_path = (*item_path, "post")
            post = item.get("post", _MISSING)
            if not isinstance(post, dict):
                raise _drift(operation, post_path, "an object")
            posts.append(post)
    return posts


def walk_posts(
    response: dict[str, Any], operation: str
) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Return ``(raw_posts, end_cursor, has_next_page)`` for one post operation.

    ``data.media: null`` is a valid, empty ``post`` response (used while probing
    shortcode low bits). Missing anchored keys and wrong connection shapes are
    drift and raise :class:`EnvelopeParseError`.
    """
    if operation == "post":
        media_path = ENVELOPE_ROOTS[operation]
        media = _get_path(response, media_path)
        if media is _MISSING:
            raise _drift(operation, media_path, "an object or null")
        if media is None:
            return [], None, False
        if not isinstance(media, dict):
            raise _drift(operation, media_path, "an object or null")
        return [media], None, False

    if operation not in _POST_THREAD_ITEM_PATHS:
        raise EnvelopeParseError(f"unsupported post envelope operation {operation!r}")

    connection = _connection(response, operation)
    posts = _posts_from_connection(connection, operation)
    end_cursor, has_next_page = _page_state(connection, operation)
    return posts, end_cursor, has_next_page


def walk_users(
    response: dict[str, Any], operation: str
) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Return raw user nodes and Relay page state for search/social-graph ops."""
    if operation not in _USER_CONNECTION_OPERATIONS:
        raise EnvelopeParseError(f"unsupported user envelope operation {operation!r}")

    connection = _connection(response, operation)
    root_path = ENVELOPE_ROOTS[operation]
    users: list[dict[str, Any]] = []
    for edge_index, edge in enumerate(connection["edges"]):
        edge_path = (*root_path, "edges", edge_index)
        if not isinstance(edge, dict):
            raise _drift(operation, edge_path, "an object")

        node_path = (*edge_path, "node")
        node = edge.get("node", _MISSING)
        if not isinstance(node, dict):
            raise _drift(operation, node_path, "an object")
        users.append(node)

    end_cursor, has_next_page = _page_state(connection, operation)
    return users, end_cursor, has_next_page


def extract_profile_user(response: dict[str, Any]) -> dict[str, Any] | None:
    """Return only the profile operation's anchored ``data.user`` node.

    Explicit null means the profile is unavailable. A missing key or any other
    leaf type signals structural drift; decoy ``user`` keys elsewhere are never
    considered.
    """
    operation = "profile"
    path = ENVELOPE_ROOTS[operation]
    user = _get_path(response, path)
    if user is _MISSING:
        raise _drift(operation, path, "an object or null")
    if user is None:
        return None
    if not isinstance(user, dict):
        raise _drift(operation, path, "an object or null")
    return user
