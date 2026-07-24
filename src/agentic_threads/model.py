"""Typed output objects and pure Threads response normalization.

``parse.py`` locates operation-specific raw nodes. This module only turns those
nodes into stable ``Media``, ``User``, and ``Post`` objects; it performs no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    """Serialize a datetime deterministically as ISO-8601 UTC with a ``Z`` suffix."""
    if value is None:
        return None
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_taken_at(value: object) -> datetime | None:
    """Parse Threads' Unix-second ``taken_at`` value without guessing other formats."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or not stripped.lstrip("-").isdigit():
            return None
        value = int(stripped)
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _identifier(value: object) -> str | None:
    """Return an ASCII-decimal identifier string, or ``None`` for any other value."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    if isinstance(value, str) and value and value.isascii() and value.isdecimal():
        return value
    return None


def _node_identifier(node: dict[str, Any]) -> str | None:
    primary = node.get("pk")
    return _identifier(node.get("id") if primary is None else primary)


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped.lstrip("-").isdigit():
            return int(stripped)
    return None


@dataclass
class Media:
    kind: str  # "photo" | "video" | "carousel" | "unknown"
    url: str
    width: int | None = None
    height: int | None = None
    alt_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "url": self.url,
            "width": self.width,
            "height": self.height,
            "alt_text": self.alt_text,
        }


@dataclass
class User:
    id: str
    username: str
    full_name: str | None
    is_verified: bool | None
    follower_count: int | None
    following_count: int | None
    post_count: int | None
    bio: str | None
    profile_pic_url: str | None
    url: str | None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name,
            "is_verified": self.is_verified,
            "follower_count": self.follower_count,
            "following_count": self.following_count,
            "post_count": self.post_count,
            "bio": self.bio,
            "profile_pic_url": self.profile_pic_url,
            "url": self.url,
            **({"raw": self.raw} if self.raw is not None else {}),
        }


@dataclass
class Post:
    id: str
    code: str | None
    url: str | None
    created_at: datetime | None
    text: str
    author: User | None
    like_count: int | None
    reply_count: int | None
    repost_count: int | None
    quote_count: int | None
    media: list[Media]
    is_reply: bool
    reply_to_id: str | None
    root_post_id: str | None
    quoted_post: Post | None
    reposted_post: Post | None
    link_preview: dict[str, str | None] | None
    is_pinned: bool
    captured_at: datetime
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "code": self.code,
            "url": self.url,
            "created_at": _iso(self.created_at),
            "text": self.text,
            "author": self.author.to_dict() if self.author is not None else None,
            "like_count": self.like_count,
            "reply_count": self.reply_count,
            "repost_count": self.repost_count,
            "quote_count": self.quote_count,
            "media": [item.to_dict() for item in self.media],
            "is_reply": self.is_reply,
            "reply_to_id": self.reply_to_id,
            "root_post_id": self.root_post_id,
            "quoted_post": self.quoted_post.to_dict() if self.quoted_post is not None else None,
            "reposted_post": (
                self.reposted_post.to_dict() if self.reposted_post is not None else None
            ),
            "link_preview": dict(self.link_preview) if self.link_preview is not None else None,
            "is_pinned": self.is_pinned,
            "captured_at": _iso(self.captured_at),
            **({"raw": self.raw} if self.raw is not None else {}),
        }


# JSON-facing types and descriptions are deliberately co-located with the
# serializers. Python annotations are not the output contract: datetimes become
# strings and nullable keys remain present with JSON null values.
MEDIA_FIELD_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "kind": ("string", "One of photo | video | carousel | unknown."),
    "url": (
        "string",
        "The selected highest-resolution Threads media URL; signed CDN query data is sensitive.",
    ),
    "width": ("integer | null", "Media width in pixels, or null when unavailable."),
    "height": ("integer | null", "Media height in pixels, or null when unavailable."),
    "alt_text": ("string | null", "Author-provided accessibility text, or null."),
}

USER_FIELD_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "id": ("string", "Stable numeric Threads user identifier."),
    "username": ("string", "Threads handle without the leading at-sign."),
    "full_name": ("string | null", "Display name, or null when unavailable."),
    "is_verified": ("boolean | null", "Whether Threads marks the account verified, or null."),
    "follower_count": ("integer | null", "Follower count, or null when unavailable."),
    "following_count": ("integer | null", "Following count, or null when unavailable."),
    "post_count": ("integer | null", "Post count, or null when unavailable."),
    "bio": ("string | null", "Profile biography, or null when unavailable."),
    "profile_pic_url": (
        "string | null",
        "Profile-picture URL, often a signed CDN URL, or null when unavailable.",
    ),
    "url": ("string | null", "Canonical Threads profile URL, or null without a username."),
    "raw": (
        "object",
        "Diagnostics-only raw GraphQL user node, present only when raw output was requested.",
    ),
}

FIELD_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "id": (
        "string",
        "Stable numeric post identifier and deduplication key; never dedupe on captured_at.",
    ),
    "code": ("string | null", "Threads permalink shortcode, or null when unavailable."),
    "url": ("string | null", "Canonical Threads post URL, or null when it cannot be built."),
    "created_at": (
        "string | null",
        "ISO-8601 UTC timestamp with a Z suffix derived from taken_at, or null.",
    ),
    "text": ("string", "Post caption text; an empty string means the post has no caption."),
    "author": ("object | null", "The post author as a nested User, or null."),
    "like_count": ("integer | null", "Like count, or null when unavailable."),
    "reply_count": ("integer | null", "Direct reply count, or null when unavailable."),
    "repost_count": ("integer | null", "Repost count, or null when unavailable."),
    "quote_count": ("integer | null", "Quote-post count, or null when unavailable."),
    "media": (
        "array<object>",
        "Ordered Media attachments; carousel children are emitted as separate entries.",
    ),
    "is_reply": ("boolean", "Whether Threads identifies this post as a reply."),
    "reply_to_id": (
        "string | null",
        "Parent post identifier when Threads supplies one; never inferred from an author id.",
    ),
    "root_post_id": (
        "string | null",
        "Root post identifier when Threads supplies one; null when the payload omits it.",
    ),
    "quoted_post": ("object | null", "Recursively normalized quoted Post, or null."),
    "reposted_post": ("object | null", "Recursively normalized reposted Post, or null."),
    "link_preview": (
        "object | null",
        "Link preview with always-present url and title keys, or null.",
    ),
    "is_pinned": ("boolean", "Whether the post is pinned to its author's profile."),
    "captured_at": (
        "string",
        "ISO-8601 UTC timestamp when this tool captured the response; never a dedup key.",
    ),
    "raw": (
        "object",
        "Diagnostics-only raw GraphQL post node, present only when raw output was requested.",
    ),
}

POST_FIELD_DESCRIPTIONS = FIELD_DESCRIPTIONS


def _schema_representative_media() -> Media:
    return Media(kind="photo", url="", width=None, height=None, alt_text=None)


def _schema_representative_user() -> User:
    return User(
        id="1",
        username="synthetic",
        full_name=None,
        is_verified=None,
        follower_count=None,
        following_count=None,
        post_count=None,
        bio=None,
        profile_pic_url=None,
        url=None,
        raw={},
    )


def _schema_representative_post() -> Post:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Post(
        id="1",
        code=None,
        url=None,
        created_at=None,
        text="",
        author=None,
        like_count=None,
        reply_count=None,
        repost_count=None,
        quote_count=None,
        media=[],
        is_reply=False,
        reply_to_id=None,
        root_post_id=None,
        quoted_post=None,
        reposted_post=None,
        link_preview=None,
        is_pinned=False,
        captured_at=now,
        raw={},
    )


def _schema_fields(
    sample: dict[str, Any],
    descriptions: dict[str, tuple[str, str]],
    *,
    optional: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Describe emitted keys in ``to_dict()`` order, not dataclass field order."""
    return [
        {
            "name": key,
            "type": descriptions[key][0],
            "description": descriptions[key][1],
            "always_present": key not in optional,
        }
        for key in sample
    ]


def media_schema_fields() -> list[dict[str, Any]]:
    return _schema_fields(_schema_representative_media().to_dict(), MEDIA_FIELD_DESCRIPTIONS)


def user_schema_fields() -> list[dict[str, Any]]:
    return _schema_fields(
        _schema_representative_user().to_dict(),
        USER_FIELD_DESCRIPTIONS,
        optional=frozenset({"raw"}),
    )


def schema_fields() -> list[dict[str, Any]]:
    return _schema_fields(
        _schema_representative_post().to_dict(),
        FIELD_DESCRIPTIONS,
        optional=frozenset({"raw"}),
    )


def post_schema_fields() -> list[dict[str, Any]]:
    """Named sibling of ``user_schema_fields`` and ``media_schema_fields``."""
    return schema_fields()


_JSON_SCHEMA_TYPES: dict[str, dict[str, Any]] = {
    "string": {"type": "string"},
    "string | null": {"type": ["string", "null"]},
    "boolean": {"type": "boolean"},
    "boolean | null": {"type": ["boolean", "null"]},
    "integer | null": {"type": ["integer", "null"]},
    "array<object>": {"type": "array", "items": {"type": "object"}},
    "object | null": {"type": ["object", "null"]},
    "object": {"type": "object"},
}

_NUMERIC_ID_SCHEMA: dict[str, Any] = {
    "type": "string",
    "pattern": "^[0-9]+$",
}
_NULLABLE_NUMERIC_ID_SCHEMA: dict[str, Any] = {
    "type": ["string", "null"],
    "pattern": "^[0-9]+$",
}
_MEDIA_SCHEMA_OVERRIDES: dict[str, dict[str, Any]] = {
    "kind": {
        "type": "string",
        "enum": ["photo", "video", "carousel", "unknown"],
    },
}
_USER_SCHEMA_OVERRIDES: dict[str, dict[str, Any]] = {
    "id": _NUMERIC_ID_SCHEMA,
}

_POST_SCHEMA_OVERRIDES: dict[str, dict[str, Any]] = {
    "id": _NUMERIC_ID_SCHEMA,
    "created_at": {"type": ["string", "null"], "format": "date-time"},
    "reply_to_id": _NULLABLE_NUMERIC_ID_SCHEMA,
    "root_post_id": _NULLABLE_NUMERIC_ID_SCHEMA,
    "captured_at": {"type": "string", "format": "date-time"},
    "author": {"anyOf": [{"$ref": "#/$defs/User"}, {"type": "null"}]},
    "media": {"type": "array", "items": {"$ref": "#/$defs/Media"}},
    "quoted_post": {"anyOf": [{"$ref": "#/$defs/Post"}, {"type": "null"}]},
    "reposted_post": {"anyOf": [{"$ref": "#/$defs/Post"}, {"type": "null"}]},
    "link_preview": {
        "anyOf": [
            {
                "type": "object",
                "properties": {
                    "url": {"type": ["string", "null"]},
                    "title": {"type": ["string", "null"]},
                },
                "required": ["url", "title"],
                "additionalProperties": False,
            },
            {"type": "null"},
        ]
    },
}


def _json_properties(
    fields: list[dict[str, Any]],
    *,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    overrides = overrides or {}
    properties: dict[str, dict[str, Any]] = {}
    for field in fields:
        override = overrides.get(field["name"])
        prop = dict(override) if override is not None else dict(_JSON_SCHEMA_TYPES[field["type"]])
        prop["description"] = field["description"]
        properties[field["name"]] = prop
    return properties


def _json_required(fields: list[dict[str, Any]]) -> list[str]:
    return [field["name"] for field in fields if field["always_present"]]


def _object_schema(
    fields: list[dict[str, Any]],
    description: str,
    *,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "properties": _json_properties(fields, overrides=overrides),
        "required": _json_required(fields),
        "additionalProperties": False,
    }


def json_schema() -> dict[str, Any]:
    """Return the generated Post schema using JSON Schema draft 2020-12."""
    media_definition = _object_schema(
        media_schema_fields(),
        "One attachment in a Post.media array.",
        overrides=_MEDIA_SCHEMA_OVERRIDES,
    )
    user_definition = _object_schema(
        user_schema_fields(),
        "A Threads account emitted directly or nested as Post.author.",
        overrides=_USER_SCHEMA_OVERRIDES,
    )
    post_definition = _object_schema(
        schema_fields(),
        "One element of a Post output array or one NDJSON line.",
        overrides=_POST_SCHEMA_OVERRIDES,
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Post",
        **post_definition,
        "$defs": {
            "Media": media_definition,
            "User": user_definition,
            "Post": post_definition,
        },
    }


def _best_candidate(value: object) -> dict[str, Any] | None:
    """Select the usable candidate with the largest pixel area, preserving tie order."""
    if not isinstance(value, list):
        return None
    usable = [
        candidate
        for candidate in value
        if isinstance(candidate, dict)
        and isinstance(candidate.get("url"), str)
        and candidate["url"]
    ]
    if not usable:
        return None

    def area(candidate: dict[str, Any]) -> int:
        width = _integer(candidate.get("width"))
        height = _integer(candidate.get("height"))
        if width is None or height is None:
            return -1
        return width * height

    return max(usable, key=area)


def build_media(raw_media_item: dict[str, Any]) -> Media:
    """Normalize one Instagram-style Threads media object."""
    media_type = _integer(raw_media_item.get("media_type"))
    image_versions = raw_media_item.get("image_versions2")
    image_candidates = (
        image_versions.get("candidates") if isinstance(image_versions, dict) else None
    )
    image = _best_candidate(image_candidates)
    video = _best_candidate(raw_media_item.get("video_versions"))

    if media_type == 2:
        selected = video or image
    else:
        selected = image or video

    if media_type == 1:
        kind = "photo"
    elif media_type == 2:
        kind = "video"
    elif media_type == 8:
        kind = "carousel"
    elif media_type == 19:
        kind = "unknown"
    elif video is not None:
        kind = "video"
    elif image is not None:
        kind = "photo"
    else:
        kind = "unknown"

    selected = selected or {}
    url = selected.get("url") if isinstance(selected.get("url"), str) else ""
    width = _integer(selected.get("width"))
    height = _integer(selected.get("height"))
    if width is None:
        width = _integer(raw_media_item.get("original_width"))
    if height is None:
        height = _integer(raw_media_item.get("original_height"))
    alt_text = raw_media_item.get("accessibility_caption")

    return Media(
        kind=kind,
        url=url,
        width=width,
        height=height,
        alt_text=alt_text if isinstance(alt_text, str) else None,
    )


def build_user(
    raw_user: dict[str, Any] | None,
    *,
    include_raw: bool = False,
) -> User | None:
    """Normalize a user node whose primary identity is ASCII decimal."""
    if not isinstance(raw_user, dict):
        return None
    user_id = _node_identifier(raw_user)
    if user_id is None:
        return None

    username_value = raw_user.get("username")
    username = username_value.lstrip("@") if isinstance(username_value, str) else ""
    full_name = raw_user.get("full_name")
    verified = raw_user.get("is_verified")
    if not isinstance(verified, bool):
        verified = raw_user.get("text_post_app_is_verified")
    bio = raw_user.get("biography")
    profile_pic_url = raw_user.get("profile_pic_url")
    post_count = _integer(raw_user.get("post_count"))
    if post_count is None:
        post_count = _integer(raw_user.get("media_count"))

    return User(
        id=user_id,
        username=username,
        full_name=full_name if isinstance(full_name, str) else None,
        is_verified=verified if isinstance(verified, bool) else None,
        follower_count=_integer(raw_user.get("follower_count")),
        following_count=_integer(raw_user.get("following_count")),
        post_count=post_count,
        bio=bio if isinstance(bio, str) else None,
        profile_pic_url=profile_pic_url if isinstance(profile_pic_url, str) else None,
        url=f"https://www.threads.com/@{username}" if username else None,
        raw=raw_user if include_raw else None,
    )


def _link_preview(text_post_app_info: dict[str, Any]) -> dict[str, str | None] | None:
    attachment = text_post_app_info.get("link_preview_attachment")
    if not isinstance(attachment, dict):
        return None
    url = attachment.get("url")
    title = attachment.get("title")
    normalized = {
        "url": url if isinstance(url, str) else None,
        "title": title if isinstance(title, str) else None,
    }
    return normalized if any(value is not None for value in normalized.values()) else None


def _post_media(raw_post: dict[str, Any]) -> list[Media]:
    carousel = raw_post.get("carousel_media")
    if isinstance(carousel, list) and carousel:
        raw_items = [item for item in carousel if isinstance(item, dict)]
    elif isinstance(raw_post.get("image_versions2"), dict) or isinstance(
        raw_post.get("video_versions"), list
    ):
        raw_items = [raw_post]
    else:
        raw_items = []
    return [media for media in (build_media(item) for item in raw_items) if media.url]


_MAX_NESTED_POST_DEPTH = 8


def build_post(
    raw_post: dict[str, Any],
    *,
    captured_at: datetime | None = None,
    include_raw: bool = False,
) -> Post | None:
    """Normalize one live Threads post node.

    Missing post identity yields ``None`` for direct tombstone handling. Invalid non-null
    author, quote, repost, or relationship identities invalidate the containing post, while
    explicit null relationships remain supported. Nested posts share the capture time and
    are guarded against identifier cycles and unexpectedly deep payloads.
    """
    captured = _utc(captured_at) if captured_at is not None else datetime.now(UTC)
    return _build_post(
        raw_post,
        captured_at=captured,
        include_raw=include_raw,
        depth=0,
        ancestor_ids=set(),
    )


def _build_post(
    raw_post: object,
    *,
    captured_at: datetime,
    include_raw: bool,
    depth: int,
    ancestor_ids: set[str],
) -> Post | None:
    if not isinstance(raw_post, dict) or depth > _MAX_NESTED_POST_DEPTH:
        return None
    post_id = _node_identifier(raw_post)
    if post_id is None or post_id in ancestor_ids:
        return None

    ancestor_ids.add(post_id)
    try:
        code_value = raw_post.get("code")
        code = code_value if isinstance(code_value, str) and code_value else None
        raw_author = raw_post.get("user")
        author = build_user(raw_author)
        if raw_author is not None and author is None:
            return None
        if code is None:
            url = None
        elif author is not None and author.username:
            url = f"https://www.threads.com/@{author.username}/post/{code}"
        else:
            url = f"https://www.threads.com/t/{code}"

        caption = raw_post.get("caption")
        text_value = caption.get("text") if isinstance(caption, dict) else None
        text = text_value if isinstance(text_value, str) else ""

        text_info_value = raw_post.get("text_post_app_info")
        text_info: dict[str, Any] = text_info_value if isinstance(text_info_value, dict) else {}
        raw_reply_to_id = text_info.get("reply_to_id")
        reply_to_id = _identifier(raw_reply_to_id)
        if raw_reply_to_id is not None and reply_to_id is None:
            return None
        raw_root_post_id = text_info.get("root_post_id")
        root_post_id = _identifier(raw_root_post_id)
        if raw_root_post_id is not None and root_post_id is None:
            return None
        pinned_info = text_info.get("pinned_post_info")
        pinned_value = (
            pinned_info.get("is_pinned_to_profile") if isinstance(pinned_info, dict) else False
        )
        reply_to_author = text_info.get("reply_to_author")
        has_reply_linkage = reply_to_id is not None or reply_to_author is not None
        if "is_reply" in text_info:
            is_reply_value = text_info["is_reply"]
            if not isinstance(is_reply_value, bool):
                return None
            if not is_reply_value and has_reply_linkage:
                return None
            is_reply = is_reply_value
        else:
            is_reply = has_reply_linkage

        share_info_value = text_info.get("share_info")
        share_info: dict[str, Any] = share_info_value if isinstance(share_info_value, dict) else {}
        raw_quoted_post = share_info.get("quoted_post")
        quoted_post = _build_post(
            raw_quoted_post,
            captured_at=captured_at,
            include_raw=include_raw,
            depth=depth + 1,
            ancestor_ids=ancestor_ids,
        )
        if raw_quoted_post is not None and quoted_post is None:
            return None
        raw_reposted_post = share_info.get("reposted_post")
        reposted_post = _build_post(
            raw_reposted_post,
            captured_at=captured_at,
            include_raw=include_raw,
            depth=depth + 1,
            ancestor_ids=ancestor_ids,
        )
        if raw_reposted_post is not None and reposted_post is None:
            return None

        return Post(
            id=post_id,
            code=code,
            url=url,
            created_at=_parse_taken_at(raw_post.get("taken_at")),
            text=text,
            author=author,
            like_count=_integer(raw_post.get("like_count")),
            reply_count=_integer(text_info.get("direct_reply_count")),
            repost_count=_integer(text_info.get("repost_count")),
            quote_count=_integer(text_info.get("quote_count")),
            media=_post_media(raw_post),
            is_reply=is_reply,
            reply_to_id=reply_to_id,
            root_post_id=root_post_id,
            quoted_post=quoted_post,
            reposted_post=reposted_post,
            link_preview=_link_preview(text_info),
            is_pinned=pinned_value if isinstance(pinned_value, bool) else False,
            captured_at=captured_at,
            raw=raw_post if include_raw else None,
        )
    finally:
        ancestor_ids.remove(post_id)
