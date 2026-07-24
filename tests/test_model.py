from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentic_threads import model, parse

CAPTURED_AT = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
POST_FIXTURES = (
    ("feed.json", "feed"),
    ("profile_threads.json", "profile_threads"),
    ("profile_replies.json", "profile_replies"),
    ("post_root.json", "post"),
    ("post_replies.json", "post_replies"),
    ("post_search.json", "post_search"),
)
USER_FIXTURES = (
    ("account_search.json", "people_search"),
    ("followers.json", "followers"),
    ("following.json", "following"),
)
POST_FIELD_NAMES = (
    "id",
    "code",
    "url",
    "created_at",
    "text",
    "author",
    "like_count",
    "reply_count",
    "repost_count",
    "quote_count",
    "media",
    "is_reply",
    "reply_to_id",
    "root_post_id",
    "quoted_post",
    "reposted_post",
    "link_preview",
    "is_pinned",
    "captured_at",
    "raw",
)
POST_REQUIRED_FIELDS = (
    "id",
    "code",
    "url",
    "created_at",
    "text",
    "author",
    "like_count",
    "reply_count",
    "repost_count",
    "quote_count",
    "media",
    "is_reply",
    "reply_to_id",
    "root_post_id",
    "quoted_post",
    "reposted_post",
    "link_preview",
    "is_pinned",
    "captured_at",
)
USER_FIELD_NAMES = (
    "id",
    "username",
    "full_name",
    "is_verified",
    "follower_count",
    "following_count",
    "post_count",
    "bio",
    "profile_pic_url",
    "url",
    "raw",
)
USER_REQUIRED_FIELDS = (
    "id",
    "username",
    "full_name",
    "is_verified",
    "follower_count",
    "following_count",
    "post_count",
    "bio",
    "profile_pic_url",
    "url",
)
MEDIA_FIELD_NAMES = (
    "kind",
    "url",
    "width",
    "height",
    "alt_text",
)
MEDIA_REQUIRED_FIELDS = (
    "kind",
    "url",
    "width",
    "height",
    "alt_text",
)


def _format_checker(jsonschema):
    checker = jsonschema.FormatChecker()

    @checker.checks("date-time")
    def is_date_time(value):
        if not isinstance(value, str):
            return True
        if "T" not in value and "t" not in value:
            return False
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.tzinfo is not None

    return checker


def _raw_posts(load_fixture, name: str, operation: str) -> list[dict]:
    posts, _, _ = parse.walk_posts(load_fixture(name), operation)
    return posts


def _post(load_fixture, name: str, operation: str) -> model.Post:
    raw_posts = _raw_posts(load_fixture, name, operation)
    assert len(raw_posts) == 1
    post = model.build_post(raw_posts[0], captured_at=CAPTURED_AT)
    assert post is not None
    return post


def test_build_post_normalizes_the_actual_feed_shape(load_fixture):
    post = _post(load_fixture, "feed.json", "feed")

    assert post.id == "1001"
    assert post.code == "feed1"
    assert post.url == "https://www.threads.com/@synthetic_alice/post/feed1"
    assert post.created_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert post.text == "A synthetic feed post quoting another synthetic post."
    assert post.like_count == 3
    assert post.reply_count == 2
    assert post.repost_count == 1
    assert post.quote_count == 1
    assert post.media == []
    assert post.author is not None
    assert post.author.to_dict() == {
        "id": "101",
        "username": "synthetic_alice",
        "full_name": "Synthetic Alice",
        "is_verified": False,
        "follower_count": 12,
        "following_count": 4,
        "post_count": 6,
        "bio": "An entirely synthetic profile.",
        "profile_pic_url": "https://example.invalid/profiles/synthetic_alice.png",
        "url": "https://www.threads.com/@synthetic_alice",
    }


def test_quote_and_highest_resolution_media_are_normalized(load_fixture):
    post = _post(load_fixture, "feed.json", "feed")

    assert post.quoted_post is not None
    assert post.quoted_post.id == "1002"
    assert post.quoted_post.text == "Synthetic quoted text with one photo."
    assert post.quoted_post.captured_at is post.captured_at
    assert post.reposted_post is None
    assert [item.to_dict() for item in post.quoted_post.media] == [
        {
            "kind": "photo",
            "url": "https://example.invalid/media/quote-large.jpg",
            "width": 1280,
            "height": 960,
            "alt_text": "A synthetic blue square.",
        }
    ]


def test_carousel_repost_and_pinned_shapes_are_normalized(load_fixture):
    post = _post(load_fixture, "profile_threads.json", "profile_threads")

    assert post.id == "2001"
    assert post.is_pinned is True
    assert [(item.kind, item.url, item.width, item.height) for item in post.media] == [
        (
            "photo",
            "https://example.invalid/media/carousel-photo.jpg",
            800,
            600,
        ),
        (
            "video",
            "https://example.invalid/media/carousel-video-high.mp4",
            1080,
            1080,
        ),
    ]
    assert post.reposted_post is not None
    assert post.reposted_post.id == "2002"
    assert post.reposted_post.text == ("Original synthetic post selected for reposting.")
    assert post.reposted_post.author is not None
    assert post.reposted_post.author.username == "synthetic_carol"
    assert post.quoted_post is None


def test_reply_link_preview_and_missing_date_are_normalized(load_fixture):
    reply = _post(load_fixture, "profile_replies.json", "profile_replies")

    assert reply.id == "3002"
    assert reply.created_at is None
    assert reply.to_dict()["created_at"] is None
    assert reply.is_reply is True
    assert reply.reply_to_id == "3001"
    assert reply.root_post_id == "3001"
    assert reply.link_preview == {
        "url": "https://example.invalid/articles/synthetic-reference",
        "title": "Synthetic reference",
    }


@pytest.mark.parametrize(
    ("text_info", "expected_is_reply", "expected_reply_to_id"),
    [
        pytest.param(
            {"reply_to_id": "3001"},
            True,
            "3001",
            id="absent-flag-with-parent-id",
        ),
        pytest.param(
            {"reply_to_author": {"pk": "102", "username": "synthetic_parent"}},
            True,
            None,
            id="absent-flag-with-parent-author",
        ),
        pytest.param(
            {"reply_to_id": None, "reply_to_author": None},
            False,
            None,
            id="absent-flag-with-null-linkage",
        ),
        pytest.param(
            {"is_reply": True},
            True,
            None,
            id="explicit-true-without-linkage",
        ),
        pytest.param(
            {"is_reply": False},
            False,
            None,
            id="explicit-false-without-linkage",
        ),
    ],
)
def test_reply_flag_normalization(
    text_info,
    expected_is_reply,
    expected_reply_to_id,
):
    post = model.build_post(
        {"pk": "3002", "text_post_app_info": text_info},
        captured_at=CAPTURED_AT,
    )

    assert post is not None
    assert post.is_reply is expected_is_reply
    assert post.reply_to_id == expected_reply_to_id


@pytest.mark.parametrize(
    "raw_is_reply",
    [
        pytest.param(None, id="null"),
        pytest.param("true", id="string"),
        pytest.param(1, id="integer"),
        pytest.param(0.0, id="float"),
        pytest.param([], id="list"),
        pytest.param({}, id="mapping"),
    ],
)
def test_explicit_malformed_reply_flags_fail_closed(raw_is_reply):
    post = model.build_post(
        {
            "pk": "3002",
            "text_post_app_info": {"is_reply": raw_is_reply},
        },
        captured_at=CAPTURED_AT,
    )

    assert post is None


@pytest.mark.parametrize(
    "reply_linkage",
    [
        pytest.param({"reply_to_id": "3001"}, id="parent-id"),
        pytest.param(
            {"reply_to_author": {"pk": "102", "username": "synthetic_parent"}},
            id="parent-author",
        ),
    ],
)
def test_explicit_false_reply_flag_with_linkage_fails_closed(reply_linkage):
    post = model.build_post(
        {
            "pk": "3002",
            "text_post_app_info": {"is_reply": False, **reply_linkage},
        },
        captured_at=CAPTURED_AT,
    )

    assert post is None


def test_raw_nodes_are_strictly_opt_in_and_propagate_to_nested_posts(load_fixture):
    raw = _raw_posts(load_fixture, "feed.json", "feed")[0]

    without_raw = model.build_post(raw, captured_at=CAPTURED_AT)
    with_raw = model.build_post(raw, captured_at=CAPTURED_AT, include_raw=True)

    assert without_raw is not None
    assert without_raw.raw is None
    assert "raw" not in without_raw.to_dict()
    assert without_raw.quoted_post is not None
    assert without_raw.quoted_post.raw is None

    assert with_raw is not None
    assert with_raw.raw is raw
    assert with_raw.to_dict()["raw"] is raw
    assert with_raw.quoted_post is not None
    assert with_raw.quoted_post.raw is raw["text_post_app_info"]["share_info"]["quoted_post"]
    assert with_raw.author is not None
    assert with_raw.author.raw is None
    assert "raw" not in with_raw.author.to_dict()


def test_build_user_normalizes_people_search_nodes(load_fixture):
    raw_users, cursor, has_next_page = parse.walk_users(
        load_fixture("account_search.json"), "people_search"
    )
    users = [model.build_user(raw) for raw in raw_users]

    assert cursor is None
    assert has_next_page is False
    assert all(user is not None for user in users)
    assert [user.id for user in users if user is not None] == ["301", "302"]
    first = users[0]
    assert first is not None
    assert first.username == "search_fixture_one"
    assert first.post_count == 2
    assert first.follower_count == 4
    assert first.following_count == 1
    assert first.url == "https://www.threads.com/@search_fixture_one"
    second = users[1]
    assert second is not None
    assert second.is_verified is True


def test_user_raw_nodes_are_strictly_opt_in(load_fixture):
    raw = parse.walk_users(load_fixture("account_search.json"), "people_search")[0][0]

    without_raw = model.build_user(raw)
    with_raw = model.build_user(raw, include_raw=True)

    assert without_raw is not None
    assert without_raw.raw is None
    assert "raw" not in without_raw.to_dict()
    assert with_raw is not None
    assert with_raw.raw is raw
    assert with_raw.to_dict()["raw"] is raw


def test_to_dict_serializes_created_and_capture_times_as_utc_z(load_fixture):
    raw = _raw_posts(load_fixture, "post_root.json", "post")[0]
    captured_in_japan = datetime(2026, 7, 23, 9, 30, tzinfo=timezone(timedelta(hours=9)))
    post = model.build_post(raw, captured_at=captured_in_japan)

    assert post is not None
    assert post.to_dict()["created_at"] == "2026-01-03T00:00:00Z"
    assert post.to_dict()["captured_at"] == "2026-07-23T00:30:00Z"


def test_missing_post_or_user_identity_returns_none():
    assert model.build_post({"caption": {"text": "synthetic tombstone"}}) is None
    assert model.build_user({"username": "synthetic_without_an_id"}) is None
    assert model.build_user(None) is None


@pytest.mark.parametrize(
    ("raw_id", "expected"),
    [
        pytest.param("0", "0", id="zero-string"),
        pytest.param("00123", "00123", id="leading-zero-string"),
        pytest.param(0, "0", id="zero-integer"),
        pytest.param(123, "123", id="positive-integer"),
    ],
)
def test_ascii_decimal_identifiers_normalize_across_model_boundaries(raw_id, expected):
    post = model.build_post(
        {
            "pk": raw_id,
            "user": {"pk": raw_id, "username": "synthetic"},
            "text_post_app_info": {
                "reply_to_id": raw_id,
                "root_post_id": raw_id,
            },
        },
        captured_at=CAPTURED_AT,
    )
    user = model.build_user({"pk": raw_id, "username": "synthetic"})
    fallback_post = model.build_post({"id": raw_id}, captured_at=CAPTURED_AT)
    fallback_user = model.build_user({"id": raw_id, "username": "synthetic"})

    assert post is not None
    assert post.id == expected
    assert post.author is not None
    assert post.author.id == expected
    assert post.reply_to_id == expected
    assert post.root_post_id == expected
    assert user is not None
    assert user.id == expected
    assert fallback_post is not None
    assert fallback_post.id == expected
    assert fallback_user is not None
    assert fallback_user.id == expected


@pytest.mark.parametrize(
    "raw_id",
    [
        pytest.param("alphabetic", id="alphabetic"),
        pytest.param("-1", id="negative-string"),
        pytest.param("+1", id="positive-signed-string"),
        pytest.param(-1, id="negative-integer"),
        pytest.param(" \t\n", id="whitespace-only"),
        pytest.param("١٢٣", id="unicode-digits"),
    ],
)
def test_non_ascii_decimal_identifiers_are_rejected_at_model_boundaries(raw_id):
    assert model.build_post({"pk": raw_id}) is None
    assert model.build_user({"pk": raw_id, "username": "synthetic"}) is None
    assert model.build_post({"id": raw_id}) is None
    assert model.build_user({"id": raw_id, "username": "synthetic"}) is None
    assert model.build_post({"pk": raw_id, "id": "123"}) is None
    assert model.build_user({"pk": raw_id, "id": "123", "username": "synthetic"}) is None

    relationship_post = model.build_post(
        {
            "pk": "1",
            "text_post_app_info": {
                "reply_to_id": raw_id,
                "root_post_id": raw_id,
            },
        },
        captured_at=CAPTURED_AT,
    )
    assert relationship_post is None


def test_invalid_non_null_nested_nodes_fail_post_normalization_but_nulls_remain_nullable():
    invalid_author = model.build_post(
        {"pk": "1", "user": {"pk": "alphabetic", "username": "synthetic"}}
    )
    invalid_quote = model.build_post(
        {
            "pk": "1",
            "text_post_app_info": {
                "share_info": {"quoted_post": {"pk": "alphabetic"}},
            },
        }
    )
    nullable_relationships = model.build_post(
        {
            "pk": "1",
            "user": None,
            "text_post_app_info": {
                "reply_to_id": None,
                "root_post_id": None,
                "share_info": {"quoted_post": None, "reposted_post": None},
            },
        }
    )

    assert invalid_author is None
    assert invalid_quote is None
    assert nullable_relationships is not None
    assert nullable_relationships.author is None
    assert nullable_relationships.quoted_post is None
    assert nullable_relationships.reposted_post is None
    assert nullable_relationships.reply_to_id is None
    assert nullable_relationships.root_post_id is None


def test_schema_fields_match_independent_output_contract_and_descriptions():
    contracts = (
        (
            model._schema_representative_post().to_dict(),
            model.post_schema_fields(),
            model.POST_FIELD_DESCRIPTIONS,
            POST_FIELD_NAMES,
            POST_REQUIRED_FIELDS,
        ),
        (
            model._schema_representative_user().to_dict(),
            model.user_schema_fields(),
            model.USER_FIELD_DESCRIPTIONS,
            USER_FIELD_NAMES,
            USER_REQUIRED_FIELDS,
        ),
        (
            model._schema_representative_media().to_dict(),
            model.media_schema_fields(),
            model.MEDIA_FIELD_DESCRIPTIONS,
            MEDIA_FIELD_NAMES,
            MEDIA_REQUIRED_FIELDS,
        ),
    )

    for serialized, fields, descriptions, expected_names, expected_required in contracts:
        names = tuple(field["name"] for field in fields)
        required = tuple(field["name"] for field in fields if field["always_present"])
        assert tuple(serialized) == expected_names
        assert names == expected_names
        assert tuple(descriptions) == expected_names
        assert required == expected_required
        assert all(field["type"] for field in fields)
        assert all(field["description"] for field in fields)
        assert all(field["description"] == descriptions[field["name"]][1] for field in fields)


def test_json_schema_describes_every_emitted_key_and_nested_contract():
    schema = model.json_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "Post"
    assert set(schema["$defs"]) == {"Media", "User", "Post"}
    schema_contracts = (
        (schema, POST_FIELD_NAMES, POST_REQUIRED_FIELDS),
        (schema["$defs"]["Post"], POST_FIELD_NAMES, POST_REQUIRED_FIELDS),
        (schema["$defs"]["User"], USER_FIELD_NAMES, USER_REQUIRED_FIELDS),
        (schema["$defs"]["Media"], MEDIA_FIELD_NAMES, MEDIA_REQUIRED_FIELDS),
    )
    for object_schema, expected_names, expected_required in schema_contracts:
        assert tuple(object_schema["properties"]) == expected_names
        assert tuple(object_schema["required"]) == expected_required
    assert schema["properties"]["author"]["anyOf"][0]["$ref"] == "#/$defs/User"
    assert schema["properties"]["media"]["items"]["$ref"] == "#/$defs/Media"
    assert schema["properties"]["quoted_post"]["anyOf"][0]["$ref"] == ("#/$defs/Post")
    assert "captured_at" in schema["required"]
    assert "raw" not in schema["required"]
    user_schema = schema["$defs"]["User"]
    assert user_schema["properties"]["raw"]["type"] == "object"
    assert "raw" not in user_schema["required"]
    numeric_id_pattern = "^[0-9]+$"
    for post_schema in (schema, schema["$defs"]["Post"]):
        properties = post_schema["properties"]
        assert properties["id"]["type"] == "string"
        assert properties["id"]["pattern"] == numeric_id_pattern
        assert properties["reply_to_id"]["type"] == ["string", "null"]
        assert properties["reply_to_id"]["pattern"] == numeric_id_pattern
        assert properties["root_post_id"]["type"] == ["string", "null"]
        assert properties["root_post_id"]["pattern"] == numeric_id_pattern
        assert properties["created_at"]["format"] == "date-time"
        assert properties["captured_at"]["format"] == "date-time"

    assert user_schema["properties"]["id"]["type"] == "string"
    assert user_schema["properties"]["id"]["pattern"] == numeric_id_pattern
    assert schema["$defs"]["Media"]["properties"]["kind"]["enum"] == [
        "photo",
        "video",
        "carousel",
        "unknown",
    ]

    object_schemas = (schema, *schema["$defs"].values())
    for object_schema in object_schemas:
        assert object_schema["description"]
        assert object_schema["additionalProperties"] is False
        assert all(
            property_schema["description"]
            for property_schema in object_schema["properties"].values()
        )


def test_json_schema_enforces_identifier_kind_and_datetime_constraints():
    import jsonschema

    validator = jsonschema.Draft202012Validator(
        model.json_schema(),
        format_checker=_format_checker(jsonschema),
    )
    nullable = model._schema_representative_post().to_dict()
    validator.validate(nullable)

    valid = deepcopy(nullable)
    valid["created_at"] = "2026-01-01T00:00:00Z"
    valid["reply_to_id"] = "2"
    valid["root_post_id"] = "1"
    valid["author"] = model._schema_representative_user().to_dict()
    valid["media"] = [model._schema_representative_media().to_dict()]
    validator.validate(valid)

    invalid_payloads = []
    for field, value in (
        ("id", "alphabetic"),
        ("reply_to_id", "-1"),
        ("root_post_id", "١٢٣"),
        ("created_at", "not-a-date"),
        ("captured_at", "2026-01-01"),
    ):
        payload = deepcopy(valid)
        payload[field] = value
        invalid_payloads.append(payload)

    invalid_author = deepcopy(valid)
    invalid_author["author"]["id"] = " "
    invalid_payloads.append(invalid_author)

    invalid_media = deepcopy(valid)
    invalid_media["media"][0]["kind"] = "animated"
    invalid_payloads.append(invalid_media)

    for payload in invalid_payloads:
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(payload)


def test_json_schema_is_valid_and_fixture_models_conform(load_fixture):
    import jsonschema

    schema = model.json_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    format_checker = _format_checker(jsonschema)
    post_validator = jsonschema.Draft202012Validator(
        schema,
        format_checker=format_checker,
    )
    validated_posts = 0

    for name, operation in POST_FIXTURES:
        for raw in _raw_posts(load_fixture, name, operation):
            for include_raw in (False, True):
                post = model.build_post(
                    raw,
                    captured_at=CAPTURED_AT,
                    include_raw=include_raw,
                )
                assert post is not None
                post_validator.validate(post.to_dict())
                validated_posts += 1

    user_schema = schema["$defs"]["User"]
    jsonschema.Draft202012Validator.check_schema(user_schema)
    user_validator = jsonschema.Draft202012Validator(
        user_schema,
        format_checker=format_checker,
    )
    validated_users = 0
    for name, operation in USER_FIXTURES:
        raw_users, _, _ = parse.walk_users(load_fixture(name), operation)
        for raw in raw_users:
            for include_raw in (False, True):
                user = model.build_user(raw, include_raw=include_raw)
                assert user is not None
                user_validator.validate(user.to_dict())
                validated_users += 1

    assert validated_posts == 14
    assert validated_users == 10
