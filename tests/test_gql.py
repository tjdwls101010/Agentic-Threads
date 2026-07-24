from __future__ import annotations

from types import MappingProxyType

import pytest

from agentic_threads import gql

BASE_FEATURES = {
    "__relay_internal__pv__BarcelonaIsLoggedInrelayprovider": True,
    "__relay_internal__pv__BarcelonaIsInternalUserrelayprovider": False,
}
CORE_POST_FEATURES = {
    "__relay_internal__pv__BarcelonaHasDearAlgoConsumptionrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasEventBadgerelayprovider": False,
    "__relay_internal__pv__BarcelonaGenAIRepliesEnabledrelayprovider": False,
    "__relay_internal__pv__BarcelonaIsSearchDiscoveryEnabledrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasCommunitiesrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasGameScoreSharerelayprovider": True,
    "__relay_internal__pv__BarcelonaHasPublicViewCountCardrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasCommunityEntityCardrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasScorecardCommunityrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasSportTeamAllegianceCardrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasMusicrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasNewspaperLinkStylerelayprovider": False,
    "__relay_internal__pv__BarcelonaHasMessagingrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasPodcastGuestCardrelayprovider": False,
    "__relay_internal__pv__BarcelonaShouldFulfillLightboxQueryrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasViewerRepliedrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasPrivateRepliesDeprecationrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasGhostPostEmojiActivationrelayprovider": False,
    "__relay_internal__pv__BarcelonaOptionalCookiesEnabledrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasDearAlgoWebProductionrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasWebFaviconsrelayprovider": False,
    "__relay_internal__pv__BarcelonaIsCrawlerrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasCommunityTopContributorsrelayprovider": False,
    "__relay_internal__pv__BarcelonaCanSeeSponsoredContentrelayprovider": False,
    "__relay_internal__pv__BarcelonaShouldShowFediverseM075Featuresrelayprovider": True,
}
PROFILE_FEATURES = {
    "__relay_internal__pv__BarcelonaHasMessagingrelayprovider": True,
    "__relay_internal__pv__BarcelonaIsLoggedOutrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasEventBadgerelayprovider": False,
    "__relay_internal__pv__BarcelonaHasCommunitiesrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasWebFaviconsrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasCommunityTopContributorsrelayprovider": False,
    "__relay_internal__pv__BarcelonaShouldShowFediverseM1Featuresrelayprovider": True,
}
PROFILE_TAB_FEATURES = {
    **CORE_POST_FEATURES,
    "__relay_internal__pv__BarcelonaHasProfileSelfReplyContextrelayprovider": True,
}
POST_REPLIES_FEATURES = {
    **CORE_POST_FEATURES,
    "__relay_internal__pv__BarcelonaHasPermalinkIndentationrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasPostAuthorNotifControlsrelayprovider": True,
    "__relay_internal__pv__BarcelonaShouldShowFediverseM1Featuresrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasPermalinkPodcastCardrelayprovider": False,
}
POST_SEARCH_FEATURES = {
    **CORE_POST_FEATURES,
    "__relay_internal__pv__BarcelonaHasSERPHeaderrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasCommunityGreenDotrelayprovider": False,
    "__relay_internal__pv__BarcelonaMessagesHasLiveChatMessagingrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasCommunityBobbleheadsrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasCommunityTrendingBadgingrelayprovider": False,
}
PEOPLE_SEARCH_FEATURES = {
    "__relay_internal__pv__BarcelonaIsCrawlerrelayprovider": False,
}
SOCIAL_FEATURES = {
    "__relay_internal__pv__BarcelonaIsCrawlerrelayprovider": False,
    "__relay_internal__pv__BarcelonaShouldShowFediverseListsrelayprovider": True,
}

EXPECTED_CONSTANTS = {
    "GRAPHQL_URL": "https://www.threads.com/graphql/query",
    "THREADS_ORIGIN": "https://www.threads.com",
    "THREADS_WEB_APP_ID": "238260118697367",
    "FEED_OPERATION": "BarcelonaFeedPaginationDirectQuery",
    "PROFILE_OPERATION": "BarcelonaProfilePageDirectQuery",
    "PROFILE_THREADS_OPERATION": "BarcelonaProfileThreadsTabDirectQuery",
    "PROFILE_THREADS_PAGE_OPERATION": "BarcelonaProfileThreadsTabRefetchableDirectQuery",
    "PROFILE_REPLIES_OPERATION": "BarcelonaProfileRepliesTabDirectQuery",
    "PROFILE_REPLIES_PAGE_OPERATION": "BarcelonaProfileRepliesTabRefetchableDirectQuery",
    "POST_OPERATION": "BarcelonaPostColumnPageQuery",
    "POST_REPLIES_OPERATION": "BarcelonaPostPageDirectQuery",
    "PEOPLE_SEARCH_OPERATION": "useBarcelonaAccountSearchGraphQLDataSourceQuery",
    "FOLLOWERS_OPERATION": "BarcelonaFriendshipsFollowersTabQuery",
    "FOLLOWING_OPERATION": "BarcelonaFriendshipsFollowingTabQuery",
    "POST_SEARCH_OPERATION": "BarcelonaSearchResultsQuery",
}

BUILDER_CASES = (
    pytest.param(
        "feed_variables",
        (),
        {},
        {
            "after": None,
            "before": None,
            "data": {
                "feed_view_info": "[]",
                "pagination_source": "text_post_feed_threads",
                "reason": "cold_start_fetch",
            },
            "first": 10,
            "last": None,
            "sort_by": None,
            "variant": "for_you",
        },
        CORE_POST_FEATURES,
        id="feed",
    ),
    pytest.param(
        "profile_variables",
        ("123",),
        {},
        {"canSeeFeedsTab": True, "userID": "123"},
        PROFILE_FEATURES,
        id="profile",
    ),
    pytest.param(
        "profile_threads_variables",
        ("123",),
        {},
        {"allow_page_info_for_lox_user": False, "first": 4, "userID": "123"},
        PROFILE_TAB_FEATURES,
        id="profile-threads",
    ),
    pytest.param(
        "profile_threads_page_variables",
        ("123",),
        {"cursor": None},
        {
            "after": None,
            "allow_page_info_for_lox_user": False,
            "before": None,
            "first": 10,
            "last": None,
            "userID": "123",
        },
        PROFILE_TAB_FEATURES,
        id="profile-threads-page",
    ),
    pytest.param(
        "profile_replies_variables",
        ("123",),
        {},
        {"first": 4, "userID": "123"},
        PROFILE_TAB_FEATURES,
        id="profile-replies",
    ),
    pytest.param(
        "profile_replies_page_variables",
        ("123",),
        {"cursor": None},
        {
            "after": None,
            "allow_page_info_for_lox_user": False,
            "before": None,
            "first": 10,
            "last": None,
            "userID": "123",
        },
        PROFILE_TAB_FEATURES,
        id="profile-replies-page",
    ),
    pytest.param(
        "post_variables",
        ("456",),
        {},
        {"postID": "456"},
        CORE_POST_FEATURES,
        id="post",
    ),
    pytest.param(
        "post_replies_variables",
        ("456",),
        {},
        {"postID": "456", "sort_order": "TOP"},
        POST_REPLIES_FEATURES,
        id="post-replies",
    ),
    pytest.param(
        "post_search_variables",
        ("synthetic query",),
        {},
        {
            "meta_place_id": None,
            "power_search_info": None,
            "query": "synthetic query",
            "recent": 0,
            "search_surface": "default",
            "tagID": None,
            "trend_fbid": None,
        },
        POST_SEARCH_FEATURES,
        id="post-search",
    ),
    pytest.param(
        "people_search_variables",
        ("synthetic query",),
        {},
        {
            "query": "synthetic query",
            "first": 10,
            "should_fetch_ig_inactive_on_text_app": None,
            "should_fetch_friendship_status": False,
            "should_fetch_fediverse_profiles": True,
            "hide_unconnected_private": False,
            "is_internal_user": False,
        },
        PEOPLE_SEARCH_FEATURES,
        id="people-search",
    ),
    pytest.param(
        "followers_variables",
        ("123",),
        {},
        {"first": 20, "userID": "123"},
        SOCIAL_FEATURES,
        id="followers",
    ),
    pytest.param(
        "following_variables",
        ("123",),
        {},
        {"first": 20, "userID": "123"},
        SOCIAL_FEATURES,
        id="following",
    ),
)

OPERATION_OVERRIDES = {
    "feed_variables": (
        "__relay_internal__pv__BarcelonaHasCommunitiesrelayprovider",
        False,
    ),
    "profile_variables": (
        "__relay_internal__pv__BarcelonaHasMessagingrelayprovider",
        False,
    ),
    "profile_threads_variables": (
        "__relay_internal__pv__BarcelonaHasProfileSelfReplyContextrelayprovider",
        False,
    ),
    "profile_threads_page_variables": (
        "__relay_internal__pv__BarcelonaHasProfileSelfReplyContextrelayprovider",
        False,
    ),
    "profile_replies_variables": (
        "__relay_internal__pv__BarcelonaHasProfileSelfReplyContextrelayprovider",
        False,
    ),
    "profile_replies_page_variables": (
        "__relay_internal__pv__BarcelonaHasProfileSelfReplyContextrelayprovider",
        False,
    ),
    "post_variables": (
        "__relay_internal__pv__BarcelonaHasCommunitiesrelayprovider",
        False,
    ),
    "post_replies_variables": (
        "__relay_internal__pv__BarcelonaHasPostAuthorNotifControlsrelayprovider",
        False,
    ),
    "post_search_variables": (
        "__relay_internal__pv__BarcelonaHasCommunityBobbleheadsrelayprovider",
        False,
    ),
    "people_search_variables": (
        "__relay_internal__pv__BarcelonaIsCrawlerrelayprovider",
        True,
    ),
    "followers_variables": (
        "__relay_internal__pv__BarcelonaShouldShowFediverseListsrelayprovider",
        False,
    ),
    "following_variables": (
        "__relay_internal__pv__BarcelonaShouldShowFediverseListsrelayprovider",
        False,
    ),
}


def test_all_public_constants_are_explicitly_contract_tested():
    observed = {name for name in vars(gql) if name.isupper() and not name.startswith("_")}
    assert observed == set(EXPECTED_CONSTANTS) | {"DEFAULT_FEATURES"}


@pytest.mark.parametrize(("name", "expected"), EXPECTED_CONSTANTS.items())
def test_public_constant_value(name, expected):
    assert getattr(gql, name) == expected


def test_default_features_constant():
    assert gql.DEFAULT_FEATURES == BASE_FEATURES


def test_all_public_variable_builders_are_explicitly_contract_tested():
    observed = {
        name
        for name, value in vars(gql).items()
        if not name.startswith("_") and name.endswith("_variables") and callable(value)
    }
    tested = {case.values[0] for case in BUILDER_CASES}
    assert observed == tested


@pytest.mark.parametrize(
    ("name", "args", "kwargs", "payload", "operation_features"),
    BUILDER_CASES,
)
def test_variable_builder_contract(name, args, kwargs, payload, operation_features):
    expected = {**payload, **BASE_FEATURES, **operation_features}

    assert getattr(gql, name)(*args, **kwargs) == expected


@pytest.mark.parametrize(
    ("name", "args", "kwargs", "keeps_none", "custom_fields"),
    (
        pytest.param(
            "feed_variables",
            (),
            {"count": 3},
            True,
            {"first": 3},
            id="feed",
        ),
        pytest.param(
            "profile_threads_page_variables",
            ("123",),
            {"count": 3},
            True,
            {"first": 3},
            id="profile-threads-page",
        ),
        pytest.param(
            "profile_replies_page_variables",
            ("123",),
            {"count": 3},
            True,
            {"first": 3},
            id="profile-replies-page",
        ),
        pytest.param(
            "post_replies_variables",
            ("456",),
            {"sort_order": "RECENT"},
            False,
            {"sort_order": "RECENT"},
            id="post-replies",
        ),
        pytest.param(
            "post_search_variables",
            ("query",),
            {},
            False,
            {},
            id="post-search",
        ),
        pytest.param(
            "people_search_variables",
            ("query",),
            {"count": 3},
            False,
            {"first": 3},
            id="people-search",
        ),
        pytest.param(
            "followers_variables",
            ("123",),
            {"count": 3},
            False,
            {"first": 3},
            id="followers",
        ),
        pytest.param(
            "following_variables",
            ("123",),
            {"count": 3},
            False,
            {"first": 3},
            id="following",
        ),
    ),
)
def test_cursor_and_noncursor_branches(
    name,
    args,
    kwargs,
    keeps_none,
    custom_fields,
):
    builder = getattr(gql, name)

    without_cursor = builder(*args, cursor=None, **kwargs)
    with_cursor = builder(*args, cursor="synthetic_cursor_page_2", **kwargs)

    if keeps_none:
        assert "after" in without_cursor
        assert without_cursor["after"] is None
    else:
        assert "after" not in without_cursor
    assert with_cursor["after"] == "synthetic_cursor_page_2"
    for key, expected in custom_fields.items():
        assert without_cursor[key] == expected
        assert with_cursor[key] == expected


@pytest.mark.parametrize(
    ("name", "args", "kwargs", "payload", "operation_features"),
    BUILDER_CASES,
)
def test_caller_features_win_without_mutating_input_or_defaults(
    name,
    args,
    kwargs,
    payload,
    operation_features,
):
    operation_key, operation_value = OPERATION_OVERRIDES[name]
    operation_key, operation_value = OPERATION_OVERRIDES[name]
    login_key = "__relay_internal__pv__BarcelonaIsLoggedInrelayprovider"
    supplied = {
        login_key: False,
        operation_key: operation_value,
        "synthetic_caller_feature": "kept",
    }
    supplied_before = dict(supplied)
    defaults_before = dict(gql.DEFAULT_FEATURES)
    builder = getattr(gql, name)

    variables = builder(*args, features=MappingProxyType(supplied), **kwargs)

    assert variables[login_key] is False
    assert variables[operation_key] is operation_value
    assert variables["synthetic_caller_feature"] == "kept"
    assert supplied == supplied_before
    assert gql.DEFAULT_FEATURES == defaults_before

    variables["synthetic_caller_feature"] = "changed"
    fresh = builder(*args, **kwargs)
    assert supplied == supplied_before
    assert fresh[login_key] is BASE_FEATURES[login_key]
    assert fresh[operation_key] is operation_features[operation_key]
    assert "synthetic_caller_feature" not in fresh
