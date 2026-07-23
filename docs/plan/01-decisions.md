# Decision Log

Decisions agreed with the user during the 2026-07-23 planning interview (Korean), most
via explicit AskUserQuestion. Each records the choice **and the reasoning**, so the
implementer can re-derive intent for cases the plan didn't enumerate. Load-bearing
overrides are marked.

---

### D1 — Threads follows the Facebook mechanism, hybrid-templated from X's shape
**Choice:** Copy `Agentic X`'s *shape* (httpx-primary, browser behind `[browser]` extra,
packaging/CLI/catalog/schema/skill scaffold) and `Agentic Facebook`'s *Meta-GraphQL
specifics* (doc_id registry, `fb_dtsg`/`lsd`/`jazoest` body, relay provider flags,
token refresh-over-http).
**Why:** Recon (2026-07-23) proved Threads = Meta GraphQL with `doc_id` + `fb_dtsg`/`lsd`
+ `sessionid`, and crucially **no `x-client-transaction-id` wall**. So the wire format is
Facebook's, but the "reads are clean httpx GraphQL, no per-request signing" property is
X's. Take the best of each. **Do NOT port `transaction.py`, `GATED_OPS`, the
browser-observe fallback, or exit-code-4-via-TransactionIdError from X.**

### D2 — Transport & dependency model: X-style (httpx primary + `[browser]` extra)
**Choice (user):** Base install = `httpx` + `platformdirs` only (tiny, no browser).
`scrapling[fetchers]` is an **optional `[browser]` extra** used **only for login/harvest**
(and `setup`). All reads go over pure `httpx`. `fb_dtsg`/`lsd` refreshed over HTTP from
cookies when stale.
**Rejected:** FB-style (scrapling as a base dependency + a browser passive-scroll
fallback for `fetch`/`feed`). Heavier (browser always bundled) and unnecessary because
Threads reads don't need a browser once the session is harvested.
**Implication:** Enforce the **lazy-scrapling-import** discipline (scrapling imported only
inside login/setup functions, never at module top level) and guard it with
`tests/test_no_scrapling_import.py` + the CI base-wheel smoke job — exactly as
`Agentic X` does.

### D3 — Command surface: full sibling parity
**Choice (user):** v1 ships the full set, not an MVP subset:
`login`, `status`, `setup`, `doctor`, `catalog`, `schema`, `feed`, `fetch <user>`
(+`--replies`), `post <url|id>`, `search <query>` (`--type posts|people`),
`followers <user>`, `following <user>`.
**Why:** The whole point is letting Claude navigate Threads like a person; missing
primitives (search, social graph) break the chaining that justifies the tool.

### D4 — Single-post command is named `post`, profile-posts is `fetch`
**Choice (user):** `post <url|id>` for one post + its reply thread; `fetch <user>` for a
profile's posts.
**Why:** `post` matches `agentic-facebook` and avoids the confusion of typing
`agentic-threads thread` (the word "thread" is overloaded — it is both the app name and
the reply-chain). `fetch` matches both siblings.
**Sub-decision (implementer to confirm in Phase 0):** `post <url>` **includes the reply
thread by default**, because `BarcelonaPostColumnPageQuery` returns the post and its
thread in one response (see `02-recon-findings.md`) — replies come essentially free, and
a Threads "post" is inherently its thread. Provide `--no-replies` to suppress if a
post-only view is wanted. (This differs from X, where `--replies` is opt-in.)

### D5 — Login required in v1; anonymous reads deferred (not foreclosed)
**Choice (user):** Read only from a logged-in session, like both siblings. Do not build
anonymous/logged-out reading in v1.
**Why:** Recon showed `POST /graphql/query` returns 200 and the public feed renders while
logged out — so anonymous reads are *feasible* and a clean future extension. But
Instagram/Meta rate-limits and challenges unauthenticated automated access more
aggressively, the accessible surface is uncertain, and supporting a no-session mode adds
branching to the read client. Keep the `ReadClient` factored so a future anonymous mode
is a small addition, but don't build it now.

### D6 — Login: headed stealth browser with **poll-based** wait + `--cookies` import
**Choice (user):** Two entry paths — (a) `login` opens a **headed** stealth browser at
threads.com, the user completes "Continue with Instagram", and the tool **polls the page
state** to detect a completed login (no `input()`/Enter keypress); (b) `login --cookies
FILE` imports a Netscape/JSON/cURL cookie export with no browser.
**Why (poll vs Enter):** `Agentic Facebook` moved from `input()` to polling because
`input()` **deadlocks non-interactive agent drivers** (it holds the Chromium profile lock
while blocking on stdin). Poll-based detection is agent-safe. `Agentic X` still uses
`input()`; do NOT copy that part. Detect login by inspecting the page/cookies for a
logged-in marker (a present `sessionid`/`ds_user_id` + a `fb_dtsg` in the document),
absence of the login form — mirror FB's `looks_logged_in` + `detect_wall` body-inspection
(Threads, like FB, serves the login state in-place at 200, so check the body, not the URL).
**Account:** throwaway Instagram account only (D9).

### D7 — Rate floor: non-bypassable **1.0s** between requests
**Choice (user):** `MIN_REQUEST_PAUSE_SECONDS = 1.0`, clamped in code regardless of any
flag/env/library entry, with a stderr note when a lower value is raised.
**Why:** Matches `agentic-facebook`'s active-mode floor. Instagram/Meta is aggressive
about automation bans/checkpoints — more conservative than X's 0.5s is the safer default
for a fresh throwaway account. Can be revisited downward later with evidence.

### D8 — Output model: file + one-line stderr summary; Claude reads the file
**Choice:** Every read command writes results to a `--output` path (default: a timestamped
file under the platform data dir, **never cwd/repo**) and prints only a one-line summary
to stderr. `--format json` (array) or `ndjson`.
**Why:** Proven sibling pattern; hands context control to Claude (it decides how much of
the file to `Read`) and keeps third-party PII out of the repo.

### D9 — Throwaway Instagram account; PII discipline
**Choice (user):** Use a disposable IG account. Treat all scraped output as third-party
PII: temp paths not the repo, never `git add` captures, fixtures are hand-authored
synthetic skeletons, a CI/pre-commit PII scanner, redaction on all diagnostic surfaces
(never the output file itself).
**Why:** Same ToS/GDPR/ban posture as the siblings; Threads/Instagram if anything more
ban-happy. `DISCLAIMER.md` tone must not be weakened.

### D10 — Naming triple + clean env prefix
**Choice:** PyPI dist `agentic-threads`; import package `agentic_threads`; console command
`agentic-threads`. Env override `AGENTIC_THREADS_PROFILE_DIR` (clean, no legacy `SF*`
prefix — the siblings' `SFB_`/`SFX_` are leftovers from their old "scraper-for-*" names;
this project is new).
**Why:** Matches the configured PyPI pending publisher (`agentic-threads` @
`tjdwls101010/Agentic-Threads`) and the sibling convention. `__version__` lives in
`__init__.py` and is gated against the git tag at release by `scripts/check_tag_version.py`.

### D11 — English artifacts
**Choice:** SKILL.md, code, comments, docstrings, README, wiki, CLI output — all English.
The planning interview is Korean.
**Why:** Matches both siblings and Ultra Fetch; most stable substrate for skill-triggering
and technical vocabulary.

### D12 — doc_id freshness: shipped defaults + harvest-at-login + `doctor --refresh`
**Choice:** Ship the recon-captured `doc_id`s as `DEFAULT_DOC_IDS` (fallback only), harvest
live ones during login (scrapling `capture_xhr` over the login navigations), and add
`doctor --refresh` to re-anchor `doc_id`s from Threads' JS bundle over HTTP (no browser),
mirroring `Agentic X`'s `queryids.reanchor_via_main_js`.
**Why:** Meta rotates `doc_id`s; a single hardcoded snapshot rots. Merge per-op (harvested
wins, defaults fill gaps). This is why the skill (later) checks the PyPI version at task
start and upgrades — rotations ship as releases.

### D13 — The skill is a later, separate session
**Choice:** Build the package first, publish to PyPI, THEN build
`.claude/skills/threads/SKILL.md` in a fresh session using the `harness-creator` skill.
**Why:** Mirrors the sibling workflow; keeps the package correct before wrapping it, and
lets the skill point at the *installed* CLI's `catalog`/`schema` rather than a repo checkout.

---

## Open technical questions for Phase 0 (not user decisions — resolve by live probing)

These are flagged in `02-recon-findings.md` and `06-implementation-phases.md`:

- **Q-A — Is `fb_dtsg` required for READ queries on `/graphql/query`?** For Facebook it is.
  For Instagram/Threads, reads may need only `lsd` + `x-csrftoken` + `sessionid` +
  `x-ig-app-id`, with `fb_dtsg` being write/CSRF-oriented. Probe the minimal working
  header/body set; drop `fb_dtsg` from reads if unneeded (fewer rotating tokens to refresh).
- **Q-B — Exact request header set.** Known Threads-web headers: `x-ig-app-id`
  (Threads web app id), `x-fb-lsd`, `x-csrftoken`, `x-fb-friendly-name`, `x-asbd-id`,
  `content-type: application/x-www-form-urlencoded`. Confirm the minimal set via
  scrapling `capture_xhr` (it captures full request incl. headers).
- **Q-C — Shortcode ↔ numeric postID.** URLs use a shortcode (`DbIgBALjw2D`); the query
  needs numeric `postID` (`3947545879791996291`). Confirm the Instagram base64 shortcode
  decode, or read the id from the permalink page (FB-style URL→id bridge) as a fallback.
- **Q-D — `following` op + exact envelope leaf paths.** Recon captured the `doc_id`s and
  envelope *roots*; map the nested item paths (`edges[].node...`) and the `following`
  op (symmetric to `BarcelonaFriendshipsFollowersTabQuery`) from real captures.
- **Q-E — `--since`/`--until` support.** Confirm whether the profile/search ops accept
  server-side date bounds (like FB's `afterTime`/`beforeTime`) or must be filtered
  client-side from `created_at`.
