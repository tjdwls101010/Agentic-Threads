"""Pure request-shape builders for Threads' persisted GraphQL reads."""

from __future__ import annotations

from collections.abc import Mapping

GRAPHQL_URL = "https://www.threads.com/graphql/query"
THREADS_ORIGIN = "https://www.threads.com"
THREADS_WEB_APP_ID = "238260118697367"

FEED_OPERATION = "BarcelonaFeedPaginationDirectQuery"
PROFILE_OPERATION = "BarcelonaProfilePageDirectQuery"
PROFILE_THREADS_OPERATION = "BarcelonaProfileThreadsTabDirectQuery"
PROFILE_THREADS_PAGE_OPERATION = "BarcelonaProfileThreadsTabRefetchableDirectQuery"
PROFILE_REPLIES_OPERATION = "BarcelonaProfileRepliesTabDirectQuery"
PROFILE_REPLIES_PAGE_OPERATION = "BarcelonaProfileRepliesTabRefetchableDirectQuery"
POST_OPERATION = "BarcelonaPostColumnPageQuery"
POST_REPLIES_OPERATION = "BarcelonaPostPageDirectQuery"
PEOPLE_SEARCH_OPERATION = "useBarcelonaAccountSearchGraphQLDataSourceQuery"
FOLLOWERS_OPERATION = "BarcelonaFriendshipsFollowersTabQuery"
FOLLOWING_OPERATION = "BarcelonaFriendshipsFollowingTabQuery"
POST_SEARCH_OPERATION = "BarcelonaSearchResultsQuery"

# These are the two flags common to every live-captured v0.1 operation. Builders
# add only the operation-specific flags from the capture, then apply harvested
# overrides last so a saved session can follow a newer web bundle.
DEFAULT_FEATURES: dict[str, object] = {
    "__relay_internal__pv__BarcelonaIsLoggedInrelayprovider": True,
    "__relay_internal__pv__BarcelonaIsInternalUserrelayprovider": False,
}

_CORE_POST_FEATURES: dict[str, object] = {
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

_PROFILE_FEATURES: dict[str, object] = {
    "__relay_internal__pv__BarcelonaHasMessagingrelayprovider": True,
    "__relay_internal__pv__BarcelonaIsLoggedOutrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasEventBadgerelayprovider": False,
    "__relay_internal__pv__BarcelonaHasCommunitiesrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasWebFaviconsrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasCommunityTopContributorsrelayprovider": False,
    "__relay_internal__pv__BarcelonaShouldShowFediverseM1Featuresrelayprovider": True,
}

_PROFILE_TAB_FEATURES: dict[str, object] = {
    **_CORE_POST_FEATURES,
    "__relay_internal__pv__BarcelonaHasProfileSelfReplyContextrelayprovider": True,
}

_POST_REPLIES_FEATURES: dict[str, object] = {
    **_CORE_POST_FEATURES,
    "__relay_internal__pv__BarcelonaHasPermalinkIndentationrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasPostAuthorNotifControlsrelayprovider": True,
    "__relay_internal__pv__BarcelonaShouldShowFediverseM1Featuresrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasPermalinkPodcastCardrelayprovider": False,
}

_POST_SEARCH_FEATURES: dict[str, object] = {
    **_CORE_POST_FEATURES,
    "__relay_internal__pv__BarcelonaHasSERPHeaderrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasCommunityGreenDotrelayprovider": False,
    "__relay_internal__pv__BarcelonaMessagesHasLiveChatMessagingrelayprovider": False,
    "__relay_internal__pv__BarcelonaHasCommunityBobbleheadsrelayprovider": True,
    "__relay_internal__pv__BarcelonaHasCommunityTrendingBadgingrelayprovider": False,
}

_PEOPLE_SEARCH_FEATURES: dict[str, object] = {
    "__relay_internal__pv__BarcelonaIsCrawlerrelayprovider": False,
}

_SOCIAL_FEATURES: dict[str, object] = {
    "__relay_internal__pv__BarcelonaIsCrawlerrelayprovider": False,
    "__relay_internal__pv__BarcelonaShouldShowFediverseListsrelayprovider": True,
}


def _with_features(
    variables: dict[str, object],
    operation_features: Mapping[str, object],
    features: Mapping[str, object] | None,
) -> dict[str, object]:
    variables.update(DEFAULT_FEATURES)
    variables.update(operation_features)
    if features:
        variables.update(features)
    return variables


def feed_variables(
    *,
    cursor: str | None = None,
    count: int = 10,
    features: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the home-feed variables observed for initial and cursor pages."""
    return _with_features(
        {
            "after": cursor,
            "before": None,
            "data": {
                "feed_view_info": "[]",
                "pagination_source": "text_post_feed_threads",
                "reason": "cold_start_fetch",
            },
            "first": count,
            "last": None,
            "sort_by": None,
            "variant": "for_you",
        },
        _CORE_POST_FEATURES,
        features,
    )


def profile_variables(
    user_id: str,
    *,
    features: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the profile-header request used for a known numeric user id."""
    return _with_features(
        {"canSeeFeedsTab": True, "userID": str(user_id)},
        _PROFILE_FEATURES,
        features,
    )


def profile_threads_variables(
    user_id: str,
    *,
    count: int = 4,
    features: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the initial profile Threads-tab request."""
    return _with_features(
        {
            "allow_page_info_for_lox_user": False,
            "first": count,
            "userID": str(user_id),
        },
        _PROFILE_TAB_FEATURES,
        features,
    )


def profile_threads_page_variables(
    user_id: str,
    *,
    cursor: str | None,
    count: int = 10,
    features: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a refetchable profile Threads-tab cursor request."""
    return _with_features(
        {
            "after": cursor,
            "allow_page_info_for_lox_user": False,
            "before": None,
            "first": count,
            "last": None,
            "userID": str(user_id),
        },
        _PROFILE_TAB_FEATURES,
        features,
    )


def profile_replies_variables(
    user_id: str,
    *,
    count: int = 4,
    features: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the initial profile Replies-tab request."""
    return _with_features(
        {"first": count, "userID": str(user_id)},
        _PROFILE_TAB_FEATURES,
        features,
    )


def profile_replies_page_variables(
    user_id: str,
    *,
    cursor: str | None,
    count: int = 10,
    features: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a refetchable profile Replies-tab cursor request."""
    return _with_features(
        {
            "after": cursor,
            "allow_page_info_for_lox_user": False,
            "before": None,
            "first": count,
            "last": None,
            "userID": str(user_id),
        },
        _PROFILE_TAB_FEATURES,
        features,
    )


def post_variables(
    post_id: str,
    *,
    features: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the root-post probe for a numeric candidate id."""
    return _with_features(
        {"postID": str(post_id)},
        _CORE_POST_FEATURES,
        features,
    )


def post_replies_variables(
    post_id: str,
    *,
    cursor: str | None = None,
    sort_order: str = "TOP",
    features: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the permalink reply-connection request."""
    variables: dict[str, object] = {"postID": str(post_id), "sort_order": sort_order}
    if cursor is not None:
        variables["after"] = cursor
    return _with_features(variables, _POST_REPLIES_FEATURES, features)


def post_search_variables(
    query: str,
    *,
    cursor: str | None = None,
    features: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a post-search request; keyword-suggestion queries are intentionally absent."""
    variables: dict[str, object] = {
        "meta_place_id": None,
        "power_search_info": None,
        "query": query,
        "recent": 0,
        "search_surface": "default",
        "tagID": None,
        "trend_fbid": None,
    }
    if cursor is not None:
        variables["after"] = cursor
    return _with_features(variables, _POST_SEARCH_FEATURES, features)


def people_search_variables(
    query: str,
    *,
    cursor: str | None = None,
    count: int = 10,
    features: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build an account-search request."""
    variables: dict[str, object] = {
        "query": query,
        "first": count,
        "should_fetch_ig_inactive_on_text_app": None,
        "should_fetch_friendship_status": False,
        "should_fetch_fediverse_profiles": True,
        "hide_unconnected_private": False,
        "is_internal_user": False,
    }
    if cursor is not None:
        variables["after"] = cursor
    return _with_features(variables, _PEOPLE_SEARCH_FEATURES, features)


def _social_variables(
    user_id: str,
    *,
    cursor: str | None,
    count: int,
    features: Mapping[str, object] | None,
) -> dict[str, object]:
    variables: dict[str, object] = {"first": count, "userID": str(user_id)}
    if cursor is not None:
        variables["after"] = cursor
    return _with_features(variables, _SOCIAL_FEATURES, features)


def followers_variables(
    user_id: str,
    *,
    cursor: str | None = None,
    count: int = 20,
    features: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a followers-connection request."""
    return _social_variables(
        user_id,
        cursor=cursor,
        count=count,
        features=features,
    )


def following_variables(
    user_id: str,
    *,
    cursor: str | None = None,
    count: int = 20,
    features: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a following-connection request."""
    return _social_variables(
        user_id,
        cursor=cursor,
        count=count,
        features=features,
    )
