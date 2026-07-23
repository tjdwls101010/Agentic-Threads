# Recon Findings — Live Capture 2026-07-23

Empirical grounding for the whole plan. Captured live from a **logged-in throwaway
account** (`@tjdwls101010`, ds_user_id `63485801431`) on `www.threads.com` via a
`fetch`/XHR interceptor injected into the page, plus `performance.getResourceTiming`.
Everything below is **observed**, not assumed. **`doc_id`s rotate — re-verify in Phase 0**
(they are shipped as fallback defaults, see D12).

> Methodology note for Phase 0: the package's own `scrapling` `StealthySession(capture_xhr=...)`
> will re-capture all of this (full request headers + bodies + responses) during a real
> `login`, exactly as the FB/X recon scripts did. The interceptor approach used here could
> read request/response bodies but not request headers (they pass via a `Request` object),
> which is why the header set is listed as "known/confirm in Phase 0".

## 1. Endpoints

Two GraphQL endpoints exist on `www.threads.com`:

| Path | Style | Role |
|---|---|---|
| **`POST /graphql/query`** | Relay **persisted query** (`doc_id` + form body) | **Primary** — nearly all read ops observed here |
| `POST /api/graphql` | classic Instagram/FB form-encoded GraphQL | Secondary/legacy — fired on initial page bootstrap; not needed for reads |

Build the read client against `POST https://www.threads.com/graphql/query`.

## 2. Auth model (≈ identical to Facebook)

**Cookies** (from `document.cookie` + known HttpOnly):
- `sessionid` — **HttpOnly** (invisible to JS). The critical long-lived session cookie.
  **Must be harvested from the browser cookie jar** at login (Playwright/scrapling
  `response.cookies` includes HttpOnly cookies).
- `ds_user_id` — the logged-in numeric user id (e.g. `63485801431`).
- `csrftoken` — sent as the `x-csrftoken` header.
- `mid` — Meta device/machine id.

**Tokens** (extracted from the page HTML, same shapes as FB):
- `fb_dtsg` — form `NAfw...:17864970403026470:1784806979` (token:userid:timestamp).
  Rotates within a session (~30 min, like FB). Extract via regex over the document, same
  as `agentic-facebook/tokens.py` (`"DTSGInitialData",[],{"token":"([^"]+)"`).
  **Open Q-A: may not be required for reads — probe.**
- `lsd` — short token (e.g. `F23mQ_nX-TXCXmSKzVsW7p`). Regex `"LSD",[],{"token":"([^"]+)"`.
- `jazoest` — **computed**, not scraped: `"2" + str(sum(ord(c) for c in fb_dtsg))` (FB rule).
- `__comet_req` — observed `29` (a per-build constant in the body).

**Headers** (known Threads-web set — confirm minimal set in Phase 0, Q-B):
`x-ig-app-id` (Threads web app id), `x-fb-lsd`, `x-csrftoken`, `x-fb-friendly-name`
(= the op friendly name), `x-asbd-id`, `content-type: application/x-www-form-urlencoded`,
plus the harvested `user-agent` and the cookie header.

## 3. Request body shape (form-urlencoded, FB-style)

Observed fields on `/graphql/query` POST bodies:
`fb_api_req_friendly_name=<Op>`, `doc_id=<id>`, `variables=<json>`, `lsd=<lsd>`,
`__comet_req=29`, `server_timestamps=true`, plus the standard Meta envelope fields
(`av`, `__user`, `fb_dtsg`, `jazoest`, `__a`, `__req`, `__hs`, `dpr`, `__ccg`, `__rev`,
`__spin_r/b/t`). Mirror `agentic-facebook/graphql.py::ActiveFetcher._body`.

**Relay provider flags are required in `variables`** — booleans named
`__relay_internal__pv__Barcelona*relayprovider` (e.g. `BarcelonaIsLoggedInrelayprovider`,
`BarcelonaIsInternalUserrelayprovider`, `BarcelonaIsCrawlerrelayprovider`,
`BarcelonaHasCommunitiesrelayprovider`, …). This is the exact analogue of FB's
`RELAY_PROVIDER_FLAGS` (omitting them degrades the response). Harvest the full per-op set
from a live capture; ship a shared default set.

## 4. Operation codename & catalog (the core read ops)

Threads' internal codename at Meta is **"Barcelona"** — every op is `Barcelona*` or
`useBarcelona*`. Captured op → `doc_id` → response envelope root:

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
| `following <user>` | *(symmetric — confirm Phase 0, Q-D)* likely `BarcelonaFriendshipsFollowingTabQuery` | — | `user`, `counts` | |

Other ops observed but out of v1 scope: `BarcelonaSearchRecentSearchesQuery`,
`BarcelonaSearchRecommendedUsers[Refetchable]Query`, `BarcelonaProfileCompletionQuery`,
`BarcelonaCommunityEntityCardsPanelQuery`, `Barcelona*ScreenTimeTracking*`,
`useBarcelonaBatchedDynamicPostCountsSubscriptionQuery` (like/comment count polling),
`BarcelonaNotificationBadgeContextQueryDirectQuery`, `BarcelonaPostViewCountQuery`.

## 5. Envelope & pagination shape

- **Envelope**: fields are `xdt_*`-prefixed (Instagram/Threads convention) and/or Relay
  connection shapes. Different ops use different root keys (see table). The parser must be
  a per-op `ENVELOPE_ROOTS` map (mirror `agentic-x/parse.py::ENVELOPE_ROOTS` and FB's
  connection-key approach), not a single hardcoded path.
- **Pagination**: **Relay cursor** style — variables `after` / `before` / `first` /
  `last`; responses carry `page_info { has_next_page, end_cursor }`. Cursors are opaque
  base64 (the feed's `after` was `GgYYCQECABAA__8AABYIGBBjb2xkX3N0YXJ0X2ZldGNo...`).
  EOF rule: stop when `has_next_page` is false or the cursor stops advancing (X's rule).
- **Post identity**: the URL shortcode (`DbIgBALjw2D`) ≠ the query `postID`
  (`3947545879791996291`). Need a shortcode→id decode (Instagram base64 pk transform) or
  an HTML permalink→id bridge (FB style). See Q-C.

## 6. Logged-out behaviour

`POST /graphql/query` returned **200 while logged out**, and the public "추천" feed
rendered without a session. So anonymous reads are *feasible* — but **out of scope for v1**
(D5). Do not build them; just don't foreclose them in the `ReadClient` factoring.

## 7. What maps directly from which sibling

- **From `agentic-facebook`** (wire format): `tokens.py` (`fb_dtsg`/`lsd` extraction +
  `jazoest` computation + refresh-over-http), the form-urlencoded GraphQL body, the
  relay-provider-flags requirement, the URL→id HTML bridge, body-inspecting login/expiry
  detection.
- **From `agentic-x`** (shape): `config.py` (paths, rate floor, non-bypassable clamp),
  `auth.py` (0700 dir / 0600 file credential store, cookie-import across 3 formats,
  identifier normalization), `retrieve.py` (cursor-EOF pagination orchestrator,
  limit/since/until, stop-reason vocabulary), `model.py`/`parse.py` split (dataclass +
  `to_dict` + generated JSON Schema; pure envelope-walk), `redact.py`, `errors.py` +
  exit-code contract, `catalog`/`schema` self-describing commands, `_stealth_init.js`,
  the lazy-scrapling-import discipline + its test/CI guards, packaging + trusted publishing.
- **Do NOT port from `agentic-x`**: `transaction.py`, `GATED_OPS`, `GatedOpRejectedError`,
  `observe.py` browser fallback, the static public bearer trick, exit-code-4-via-txid.
