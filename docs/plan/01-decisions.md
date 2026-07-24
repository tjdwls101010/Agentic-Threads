# Decision Log

Decisions agreed with the user during the 2026-07-23 planning interview (Korean), most via explicit AskUserQuestion. Each records the choice **and the reasoning**, so the implementer can re-derive intent for cases the plan didn't enumerate. Load-bearing overrides are marked.

---

### D1 — Threads uses Meta persisted queries with X-style package boundaries
**Choice:** Copy `Agentic X`'s *shape* (httpx-primary, browser behind the `[browser]` extra, packaging/CLI/catalog/schema scaffold) and only the verified Threads subset of `Agentic Facebook`'s Meta-GraphQL mechanics: a rotating `doc_id` registry, operation feature flags, authenticated cookies, and per-operation variables. Completed Phase 0 proved ordinary reads send only `doc_id` and `variables`, not `fb_dtsg`, `lsd`, `jazoest`, or a static bearer token. **Why:** Threads uses Meta persisted queries without an `x-client-transaction-id` wall, but copying unverified Facebook token fields would increase secret exposure and drift risk. **Do NOT port** `transaction.py`, `GATED_OPS`, the browser-observe fallback, Facebook's token-refresh body, or exit-code-4-via-TransactionIdError.

### D2 — Transport and dependency model: X-style (`httpx` primary + `[browser]` extra)
**Choice (user):** Base install = `httpx` + `platformdirs` only (tiny, no browser). `scrapling[fetchers]` is an optional `[browser]` extra used only for `login` and `setup`; all reads and `doctor` re-anchoring use pure `httpx`. Ordinary reads never persist or refresh `fb_dtsg`/`lsd`; authenticated route-definition discovery may extract them ephemerally without placing them in `SessionCredential`. **Rejected:** FB-style Scrapling as a base dependency plus a browser passive-scroll fallback for reads. **Implication:** Enforce lazy Scrapling imports inside explicit setup/login functions and guard the boundary with `tests/test_no_scrapling_import.py` plus clean base-wheel CI smoke tests.

### D3 — Command surface: full sibling parity
**Choice (user):** v1 ships the full set, not an MVP subset: `login`, `status`, `setup`, `doctor`, `catalog`, `schema`, `feed`, `fetch <user>` (+`--replies`), `post <url|id>`, `search <query>` (`--type posts|people`), `followers <user>`, `following <user>`. **Why:** The whole point is letting Claude navigate Threads like a person; missing primitives (search, social graph) break the chaining that justifies the tool.

### D4 — Single-post command is named `post`, profile-posts is `fetch`
**Choice (user):** `post <url|id>` for one post + its reply thread; `fetch <user>` for a profile's posts. **Why:** `post` matches `agentic-facebook` and avoids the confusion of typing `agentic-threads thread` (the word "thread" is overloaded — it is both the app name and the reply-chain). `fetch` matches both siblings. **Binding completed Phase 0 mechanism:** `post <url|id>` includes the reply thread by default, but the implementation performs separate persisted operations: `post` (`BarcelonaPostColumnPageQuery`) reads the root detail, then `post_replies` (`BarcelonaPostPageDirectQuery`) reads the dedicated replies connection (see `02-recon-findings.md` §8 Q-D). Replies are an explicit additional request, not a free side effect of the root response; `--no-replies` skips `post_replies` and returns only the root detail. (This differs from X, where `--replies` is opt-in.)

### D5 — Login required in v1; anonymous reads deferred (not foreclosed)
**Choice (user):** Read only from a logged-in session, like both siblings. Do not build anonymous/logged-out reading in v1. **Why:** Recon showed `POST /graphql/query` returns 200 and the public feed renders while logged out — so anonymous reads are *feasible* and a clean future extension. But Instagram/Meta rate-limits and challenges unauthenticated automated access more aggressively, the accessible surface is uncertain, and supporting a no-session mode adds branching to the read client. Keep the `ReadClient` factored so a future anonymous mode is a small addition, but don't build it now.

### D6 — Login: headed stealth browser with poll-based wait + `--cookies` import
**Choice (user):** Two entry paths: (a) `login` opens a headed stealth browser at a trusted Threads origin, the user completes “Continue with Instagram,” and the tool polls trusted page state to detect completion without `input()`; (b) `login --cookies FILE` imports a Netscape, JSON, or cURL cookie export with no browser. Browser login requires the three authenticated cookies with trusted Threads-domain provenance, rejects conflicting records, validates origin before every page read or action, classifies challenge walls before success, and retains only bounded operation-name/numeric-`doc_id` projections from matching same-origin GraphQL requests. **Why:** Polling avoids the profile-lock deadlock that stdin prompts cause in non-interactive agent drivers, while the trust and minimization boundaries prevent foreign-page and raw-request data from entering credentials. **Account:** disposable Instagram account only (D9).

### D7 — Rate floor: non-bypassable **1.0s** between requests
**Choice (user):** `MIN_REQUEST_PAUSE_SECONDS = 1.0`, clamped in code regardless of any flag/env/library entry, with a stderr note when a lower value is raised. **Why:** Matches `agentic-facebook`'s active-mode floor. Instagram/Meta is aggressive about automation bans/checkpoints — more conservative than X's 0.5s is the safer default for a fresh throwaway account. Can be revisited downward later with evidence.

### D8 — Output model: file + one-line stderr summary; Claude reads the file
**Choice:** Every read command writes results to a `--output` path (default: a timestamped file under the platform data dir, **never cwd/repo**) and prints only a one-line summary to stderr. `--format json` (array) or `ndjson`. **Why:** Proven sibling pattern; hands context control to Claude (it decides how much of the file to `Read`) and keeps third-party PII out of the repo.

### D9 — Throwaway Instagram account; PII discipline
**Choice (user):** Use a disposable IG account. Treat all scraped output as third-party PII: temp paths not the repo, never `git add` captures, fixtures are hand-authored synthetic skeletons, a CI/pre-commit PII scanner, redaction on all diagnostic surfaces (never the output file itself). **Why:** Same ToS/GDPR/ban posture as the siblings; Threads/Instagram if anything more ban-happy. `DISCLAIMER.md` tone must not be weakened.

### D10 — Naming triple + clean env prefix
**Choice:** PyPI dist `agentic-threads`; import package `agentic_threads`; console command `agentic-threads`. Env override `AGENTIC_THREADS_PROFILE_DIR` (clean, no legacy `SF*` prefix — the siblings' `SFB_`/`SFX_` are leftovers from their old "scraper-for-*" names; this project is new). **Why:** Matches the configured PyPI pending publisher (`agentic-threads` @ `tjdwls101010/Agentic-Threads`) and the sibling convention. `__version__` lives in `__init__.py` and is gated against the git tag at release by `scripts/check_tag_version.py`.

### D11 — English artifacts
**Choice:** SKILL.md, code, comments, docstrings, README, wiki, CLI output — all English. The planning interview is Korean. **Why:** Matches both siblings and Ultra Fetch; most stable substrate for skill-triggering and technical vocabulary.

### D12 — `doc_id` freshness: shipped defaults + minimized login harvest + `doctor --refresh`
**Choice:** Ship the recon-captured public operation `doc_id`s as `DEFAULT_DOC_IDS` fallbacks, harvest live operation/name pairs during login through a same-origin request listener that validates and immediately projects matching form bodies, and add browser-free `doctor --refresh` re-anchoring from trusted Threads HTML and route assets. Raw request headers, bodies, responses, and general XHR logs are never retained or persisted; harvested ids win and defaults fill gaps per operation. **Why:** Meta rotates `doc_id`s, so a single hardcoded snapshot rots, but freshness does not justify collecting account-linked traffic.

### D13 — The skill is a later, separate session
**Choice:** Build the package first, publish to PyPI, THEN build `.claude/skills/threads/SKILL.md` in a fresh session using the `harness-creator` skill. **Why:** Mirrors the sibling workflow; keeps the package correct before wrapping it, and lets the skill point at the *installed* CLI's `catalog`/`schema` rather than a repo checkout.

---

## Historical Phase 0 technical questions (resolved)

The questions below preserve the original investigation record. The binding outcomes are recorded in `02-recon-findings.md` §8 and supersede each provisional assumption:

- **Q-A — Is `fb_dtsg` required for READ queries on `/graphql/query`?** For Facebook it is. For Instagram/Threads, reads may need only `lsd` + `x-csrftoken` + `sessionid` + `x-ig-app-id`, with `fb_dtsg` being write/CSRF-oriented. Probe the minimal working header/body set; drop `fb_dtsg` from reads if unneeded (fewer rotating tokens to refresh).
- **Q-B — Exact request header set.** Known Threads-web headers: `x-ig-app-id` (Threads web app id), `x-fb-lsd`, `x-csrftoken`, `x-fb-friendly-name`, `x-asbd-id`, `content-type: application/x-www-form-urlencoded`. Confirm the minimal set with a bounded browser request listener that retains only validated operation-name and numeric `doc_id` projections; never retain raw request headers or bodies.
- **Q-C — Shortcode ↔ numeric postID.** URLs use an opaque shortcode (for example, `EXAMPLE_SHORTCODE`); the query needs a numeric `postID` (for example, `0000000000000000000`). Confirm the Instagram base64 shortcode decode, or read the id from the permalink page (FB-style URL→id bridge) as a fallback.
- **Q-D — `following` op + exact envelope leaf paths.** Recon captured the `doc_id`s and envelope *roots*; map the nested item paths (`edges[].node...`) and the `following` op (symmetric to `BarcelonaFriendshipsFollowersTabQuery`) from real captures.
- **Q-E — `--since`/`--until` support.** Confirm whether the profile/search ops accept server-side date bounds (like FB's `afterTime`/`beforeTime`) or must be filtered client-side from `created_at`.
