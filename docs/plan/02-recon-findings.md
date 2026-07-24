# Recon Findings — Live Capture 2026-07-23

Empirical grounding for the whole plan. Captured live from a **logged-in throwaway account** (`@<synthetic-handle>`, `ds_user_id` `<synthetic-account-id>`) on `www.threads.com` via a `fetch`/XHR interceptor injected into the page, plus `performance.getResourceTiming`. Everything in §§1–7 is the **historical observed recon record**, not assumed; the completed Phase 0 resolution in §8 supersedes its open questions and implementation proposals where they differ. **`doc_id`s rotate** (current values are shipped as fallback defaults, see D12).

> Historical Phase 0 methodology note (superseded by the completed resolution in §8): the original plan expected the package's own `scrapling` `StealthySession(capture_xhr=...)` to re-capture full request headers, bodies, and responses during `login`, following the FB/X recon scripts. The interceptor used for the original recon could read request/response bodies but not request headers because they passed via a `Request` object. The shipped login path instead uses a browser request listener that retains only narrowly filtered Threads GraphQL POST artifacts in memory long enough to harvest operation/`doc_id` pairs, then drops their raw bodies; it neither captures nor persists a general browser-request log.

## 1. Endpoints

Two GraphQL endpoints exist on `www.threads.com`:

| Path | Style | Role |
|---|---|---|
| **`POST /graphql/query`** | Relay **persisted query** (`doc_id` + form body) | **Primary** — nearly all read ops observed here |
| `POST /api/graphql` | classic Instagram/FB form-encoded GraphQL | Secondary/legacy — fired on initial page bootstrap; not needed for reads |

Build the read client against `POST https://www.threads.com/graphql/query`.

## 2. Historical auth observations (current ordinary-read contract in §8 Q-A/Q-B)

**Cookies** (from `document.cookie` + known HttpOnly):
- `sessionid` — **HttpOnly** (invisible to JS). The critical long-lived session cookie. **Must be harvested from the browser cookie jar** at login (Playwright/scrapling `response.cookies` includes HttpOnly cookies).
- `ds_user_id` — the logged-in numeric user id (schematic value: `<synthetic-account-id>`).
- `csrftoken` — sent as the `x-csrftoken` header.
- `mid` — Meta device/machine id.

**Historically observed page tokens (superseded as ordinary-read inputs by completed Q-A in §8):**
- `fb_dtsg` — schematic form `<synthetic-token>:<synthetic-user-id>:<synthetic-timestamp>` (token:user-id:timestamp), observed rotating within a session (~30 min, like FB). The original recon proposed the `agentic-facebook/tokens.py` regex (`"DTSGInitialData",[],{"token":"([^"]+)"`); Q-A established that it is neither persisted nor sent by ordinary reads. Only browser-free doctor re-anchoring may extract it ephemerally for authenticated route-definition calls.
- `lsd` — short token (schematic value: `<synthetic-lsd-token>`), historically matched with `"LSD",[],{"token":"([^"]+)"`. It is neither persisted nor sent by ordinary reads and is extracted ephemerally only alongside `fb_dtsg` for those route-definition calls.
- `jazoest` — **computed**, not scraped: the historical FB rule was `"2" + str(sum(ord(c) for c in fb_dtsg))`. The shipped code computes it in memory only for authenticated route-definition calls during browser-free doctor re-anchoring.
- `__comet_req` — observed as `29`, a per-build browser-envelope constant; ordinary reads do not send it.

**Historically observed browser headers** (the original Q-B input): `x-ig-app-id` (Threads web app id), `x-fb-lsd`, `x-csrftoken`, `x-fb-friendly-name` (= the op friendly name), `x-asbd-id`, `content-type: application/x-www-form-urlencoded`, plus the harvested `user-agent` and cookie header. Completed Q-B in §8 supersedes that candidate set for ordinary reads with `content-type`, `user-agent`, `x-csrftoken`, `x-fb-friendly-name`, `x-ig-app-id`, `origin`, `referer`, and authenticated cookies; `x-fb-lsd` and `x-asbd-id` are not sent.

## 3. Historical captured request body shape (form-urlencoded, FB-style; superseded for ordinary reads by §8 Q-A)

The original browser capture observed these `/graphql/query` POST fields: `fb_api_req_friendly_name=<Op>`, `doc_id=<id>`, `variables=<json>`, `lsd=<lsd>`, `__comet_req=29`, `server_timestamps=true`, plus the standard Meta envelope fields (`av`, `__user`, `fb_dtsg`, `jazoest`, `__a`, `__req`, `__hs`, `dpr`, `__ccg`, `__rev`, `__spin_r/b/t`). The original porting proposal was to mirror `agentic-facebook/graphql.py::ActiveFetcher._body`; completed Q-A in §8 supersedes that proposal, and ordinary `ReadClient` forms contain exactly `doc_id` and `variables` with no token or write-oriented Meta envelope fields.

**Relay provider flags were observed as required in `variables`** — booleans named `__relay_internal__pv__Barcelona*relayprovider` (e.g. `BarcelonaIsLoggedInrelayprovider`, `BarcelonaIsInternalUserrelayprovider`, `BarcelonaIsCrawlerrelayprovider`, `BarcelonaHasCommunitiesrelayprovider`, …). This is the exact analogue of FB's `RELAY_PROVIDER_FLAGS` (omitting them degrades the response). The historical Phase 0 action was to harvest the full per-operation set; shipped shared defaults and operation-specific feature settings now carry that contract.

## 4. Operation codename & catalog (the core read ops)

Threads' internal codename at Meta is **"Barcelona"** — every op is `Barcelona*` or `useBarcelona*`. Captured op → `doc_id` → response envelope root:

| CLI command | Barcelona op (`fb_api_req_friendly_name`) | `doc_id` (2026-07-23, rotates) | envelope root key(s) | notes |
|---|---|---|---|---|
| `feed` (home / 추천) | `BarcelonaFeedPaginationDirectQuery` | `27850575591298078` | `feedData` | vars: opaque base64 `after` cursor + `data.pagination_source="text_post_feed_threads"` |
| `fetch <user>` (profile 스레드) | `BarcelonaProfileThreadsTabDirectQuery` | `28037669779182544` | `mediaData`, `xdt_text_app_user` | `mediaData` = posts; `xdt_text_app_user` = the profile User |
| (profile header/user) | `BarcelonaProfilePageDirectQuery` | `27315641728135541` | `user` | resolves a profile's User object (+ numeric id for graph ops) |
| `post <url\|id>` (post + thread) | `BarcelonaPostColumnPageQuery` | `27618162774505383` | `media`, `viewer` | vars: **`postID`** (numeric, not shortcode); returns post + reply thread together |
| (alt post detail) | `BarcelonaPostPageDirectQuery` | `27810629915236396` | `data` | secondary post-detail op observed on the same page |
| `search` (keyword/posts) | `useBarcelonaKeywordSearchGraphQLDataSourceQuery` | `27714610818198822` | `xdt_api__v1__text_feed__keyword_search` | vars: `{query, has_communities, has_favicons}` |
| `search --type people` (accounts) | `useBarcelonaAccountSearchGraphQLDataSourceQuery` | `27962697876655098` | `xdt_api__v1__users__search_connection` | vars: `{query, first, ...}` |
| `followers <user>` | `BarcelonaFriendshipsFollowersTabQuery` | `27390125367306731` | `user`, `counts` | vars: `{first:20, userID:"<numeric>"}` |
| `following <user>` | *(historical Q-D candidate, superseded by the completed operation in §8)* likely `BarcelonaFriendshipsFollowingTabQuery` | — | `user`, `counts` | |

Other ops observed but out of v1 scope: `BarcelonaSearchRecentSearchesQuery`, `BarcelonaSearchRecommendedUsers[Refetchable]Query`, `BarcelonaProfileCompletionQuery`, `BarcelonaCommunityEntityCardsPanelQuery`, `Barcelona*ScreenTimeTracking*`, `useBarcelonaBatchedDynamicPostCountsSubscriptionQuery` (like/comment count polling), `BarcelonaNotificationBadgeContextQueryDirectQuery`, `BarcelonaPostViewCountQuery`.

## 5. Envelope & pagination shape

- **Envelope**: fields are `xdt_*`-prefixed (Instagram/Threads convention) and/or Relay connection shapes. Different ops use different root keys (see table). The parser must be a per-op `ENVELOPE_ROOTS` map (mirror `agentic-x/parse.py::ENVELOPE_ROOTS` and FB's connection-key approach), not a single hardcoded path.
- **Pagination**: **Relay cursor** style — variables `after` / `before` / `first` / `last`; responses carry `page_info { has_next_page, end_cursor }`. Cursors are opaque base64 (schematic captured `after`: `<synthetic-opaque-cursor>`). EOF rule: stop when `has_next_page` is false or the cursor stops advancing (X's rule).
- **Post identity**: the URL shortcode (`<synthetic-shortcode>`) ≠ the query `postID` (`<synthetic-post-id>`). The original recon therefore called for a shortcode→id decode (Instagram base64 pk transform) or an HTML permalink→id bridge (FB style); completed Q-C in §8 supersedes that proposal with the exact four-candidate probe.

## 6. Logged-out behaviour

`POST /graphql/query` returned **200 while logged out**, and the public "추천" feed rendered without a session. So anonymous reads are *feasible* — but **out of scope for v1** (D5). Do not build them; just don't foreclose them in the `ReadClient` factoring.

## 7. Historical sibling porting map (superseded where noted by §8)

- **Historical candidates from `agentic-facebook`** (wire format): the original map listed `tokens.py` (`fb_dtsg`/`lsd` extraction + `jazoest` computation + refresh-over-http), the full form-urlencoded GraphQL body, the relay-provider-flags requirement, the URL→id HTML bridge, and body-inspecting login/expiry detection. Completed Phase 0 in §8 superseded the token/full-body port for ordinary reads: there is no credential token field or read-time refresh, and only browser-free doctor re-anchoring extracts `fb_dtsg`/`LSD` and computes `jazoest` ephemerally for authenticated route-definition calls.
- **From `agentic-x`** (shape): `config.py` (paths, rate floor, non-bypassable clamp), `auth.py` (0700 dir / 0600 file credential store, cookie-import across 3 formats, identifier normalization), `retrieve.py` (cursor-EOF pagination orchestrator, limit/since/until, stop-reason vocabulary), `model.py`/`parse.py` split (dataclass + `to_dict` + generated JSON Schema; pure envelope-walk), `redact.py`, `errors.py` + exit-code contract, `catalog`/`schema` self-describing commands, `_stealth_init.js`, the lazy-scrapling-import discipline + its test/CI guards, packaging + trusted publishing.
- **Do NOT port from `agentic-x`**: `transaction.py`, `GATED_OPS`, `GatedOpRejectedError`, `observe.py` browser fallback, the static public bearer trick, exit-code-4-via-txid.

## 8. Phase 0 re-verification — 2026-07-23

This section supersedes the open-question assumptions above while preserving the original recon history. It is based on a sanitized live replay contract; no account identifier, cookie value, or raw capture is reproduced here.

### Q-A — Minimal authentication for reads

A persisted read succeeded with only `doc_id` and `variables` in the form body. The authenticated session cookies `sessionid`, `ds_user_id`, and `csrftoken` were present, and the `csrftoken` value was repeated in the dynamic `x-csrftoken` header. `fb_dtsg`, `lsd`, and computed `jazoest` were all omitted and are **not required for reads**. The v0.1 read path therefore has no credential token fields and no read-time token-refresh module.

### Q-B — Request headers

The re-verified read header set is `content-type`, the harvested `user-agent`, `x-csrftoken`, `x-fb-friendly-name`, `x-ig-app-id`, `origin`, and `referer`. The Threads web app id is `238260118697367`. Of these, `x-csrftoken` is session-derived and required; the operation-specific `x-fb-friendly-name` accompanies each persisted query. No static bearer, `x-fb-lsd`, or write-oriented Meta form fields are part of the read contract.

### Q-C — Permalink shortcode to numeric post-id candidates

The shortcode decodes to `post_id >> 2`, not the full post id. Interpret the base64url decoded bytes as an unsigned big-endian integer named `decoded`, then probe exactly:

```text
(decoded << 2) | low_bits, for low_bits = 0, 1, 2, 3
```

Exactly one candidate returned non-null `data.media`; the other candidates returned `data.media = null`. Code must retain and probe all four candidates rather than claiming that the shortcode contains the omitted low two bits.

### Q-D — Operations, pagination ids, and leaf paths

Post search is `BarcelonaSearchResultsQuery`, not the keyword-suggestion operation recorded earlier. Post root and reply-thread reads are also separate persisted operations. The re-verified catalog is:

| Logical key | Operation | `doc_id` (2026-07-23; rotates) | Item/object leaf |
|---|---|---:|---|
| `feed` | `BarcelonaFeedPaginationDirectQuery` | `27850575591298078` | `data.feedData.edges[].node.text_post_app_thread.thread_items[].post` |
| `profile` | `BarcelonaProfilePageDirectQuery` | `27315641728135541` | `data.user` |
| `profile_threads` | `BarcelonaProfileThreadsTabDirectQuery` | `28037669779182544` | `data.mediaData.edges[].node.thread_items[].post` |
| `profile_threads_page` | `BarcelonaProfileThreadsTabRefetchableDirectQuery` | `28437090222560814` | `data.mediaData.edges[].node.thread_items[].post` |
| `profile_replies` | `BarcelonaProfileRepliesTabDirectQuery` | `27873042895625707` | `data.mediaData.edges[].node.thread_items[].post` |
| `profile_replies_page` | `BarcelonaProfileRepliesTabRefetchableDirectQuery` | `38287806247476832` | `data.mediaData.edges[].node.thread_items[].post` |
| `post` | `BarcelonaPostColumnPageQuery` | `27618162774505383` | `data.media` |
| `post_replies` | `BarcelonaPostPageDirectQuery` | `27810629915236396` | `data.data.edges[].node.thread_items[].post` |
| `post_search` | `BarcelonaSearchResultsQuery` | `27495177273458101` | `data.searchResults.edges[].node.thread.thread_items[].post` |
| `people_search` | `useBarcelonaAccountSearchGraphQLDataSourceQuery` | `27962697876655098` | `data.xdt_api__v1__users__search_connection.edges[].node` |
| `followers` | `BarcelonaFriendshipsFollowersTabQuery` | `27390125367306731` | `data.user.followers.edges[].node` |
| `following` | `BarcelonaFriendshipsFollowingTabQuery` | `26705592482449608` | `data.user.following.edges[].node` |

Profile replies switch from the initial operation to the refetchable `profile_replies_page` operation for cursor pages. Followers and following keep their respective operation ids while advancing the connection cursor; their `page_info` objects are siblings of `edges` under `data.user.followers` and `data.user.following`. Post replies use the dedicated `post_replies` connection rather than being inferred from the root-media response.

Browser-free re-anchoring is intentionally best-effort: it accepts only exact known operation-name/numeric-id pairs found together in Threads HTML or same-origin/trusted JavaScript route artifacts, with a bounded lazy-bundle walk. An operation that exists only in an unreferenced lazy chunk is left absent for harvested-session ids or shipped defaults to fill; re-anchoring does not start a browser or scrape unrelated numeric literals.

The authenticated `POST /ajax/route-definition/` response provides a stronger current anchor for direct route preloaders. Only this browser-free doctor re-anchoring path extracts ephemeral `fb_dtsg` and `LSD` values from the home document, computes `jazoest` in memory, and submits fixed public route URLs. It accepts a preloader `queryID` only when its `preloaderID` embeds one exact known operation. These route metadata fields are confined to `doctor --refresh`; ordinary GraphQL reads still use only the minimal Q-A/Q-B contract. If route metadata is unavailable, the bounded trusted-JavaScript scan remains a fallback.

Scrapling 0.4.11's `captured_xhr` conversion retains response objects but omits the Playwright request POST body that carries `doc_id`. Browser login therefore accepts only narrowly filtered same-origin `/graphql/query` POST request events during its fixed public harvest navigation and keeps normalized request artifacts in memory only long enough to pair a known friendly operation name with its form `doc_id`. It clears raw request bodies before constructing the persisted credential; only required cookies, the harvested user agent, live `doc_id`s, and feature settings are saved, and converted XHR responses alone are not proof of a fresh pair.

### Q-E — Date bounds

No server-side `since`/`until` variables were observed for profile, feed, or search operations. Date windows must be applied client-side to normalized `created_at` values while normal cursor pagination and EOF safeguards remain in force.
