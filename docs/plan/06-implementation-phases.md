# Implementation Phases (with verify gates)

Ordered; loop each phase until its verify gate passes. Prefer many small green steps over
one big one. Use a throwaway Instagram account for all live probing.

---

## Phase 0 — Live recon re-verification (do NOT skip)

`doc_id`s rotate; re-capture before writing code, using the package's *own* browser
(`scrapling.StealthySession(capture_xhr=r"/graphql/")`), reproducing the FB/X recon-script
workflow into a gitignored `scratch/`.

Resolve the open questions from `02-recon-findings.md` §Q and `01-decisions.md`:
- **Q-A** — minimal working header/body for a READ on `/graphql/query`. Specifically: is
  `fb_dtsg` required, or do `lsd` + `x-csrftoken` + `sessionid` + `x-ig-app-id` suffice?
  This decides whether `tokens.py` is needed at all.
- **Q-B** — exact request header set (capture full headers via `capture_xhr`).
- **Q-C** — shortcode→numeric `postID` (test the Instagram base64 decode; keep the HTML
  permalink→id bridge as fallback).
- **Q-D** — the `following` op name + the nested envelope **leaf** paths for every op
  (`edges[].node...`), and the full per-op relay-provider-flag set.
- **Q-E** — whether `fetch`/`search` accept server-side `--since`/`--until` date bounds.
- Re-capture fresh `doc_id`s for all 8 ops → seed `docids.DEFAULT_DOC_IDS`.

**Verify:** a scratch script logs in, harvests `sessionid`+tokens+`doc_id`s, and replays a
`BarcelonaProfileThreadsTabDirectQuery` over pure `httpx` returning a real post. Record
findings in `scratch/` and update `02-recon-findings.md` if reality differs.

## Phase 1 — Scaffold + packaging + offline commands

`src/agentic_threads/` skeleton, `pyproject.toml`, `config.py` (paths + 1.0s floor),
`errors.py`, `__init__.py`. Implement `catalog` + `schema` end-to-end against a stubbed
`model.py`. Set up CI (`ci.yml`), `publish.yml`, pre-commit, scripts, `.gitignore`, the
root docs stubs.

**Verify:** `agentic-threads --version/--help/catalog/schema/schema --json` work with no
network and no scrapling installed; `ruff` clean; base-wheel smoke job green;
`test_no_scrapling_import.py` green.

## Phase 2 — Auth + login + session

`auth.py` (`SessionCredential`, 0700/0600 store, `--cookies` 3-format import, identifier
normalization, shortcode↔id), `session.py` (`run_setup`; **poll-based** `run_login`;
`run_status`; `run_doctor`), `tokens.py` (if Q-A), `_stealth_init.js`. Harvest
cookies + `doc_id`s + tokens during login.

**Verify:** `agentic-threads setup` provisions the isolated browser; `login` opens a headed
browser, auto-detects completion by polling (no keypress), and saves `session.json`
(0600); `status` returns exit 0 on the live throwaway session; `login --cookies <export>`
works with no browser. `test_auth.py`/`test_tokens.py` green.

## Phase 3 — Read client + GraphQL + parser (one vertical slice: `fetch`)

`client.py` (`ReadClient`: body/headers/throttle/error-map), `gql.py` (endpoint, relay
flags, `profile_variables`), `docids.py`, `parse.py` (`ENVELOPE_ROOTS` for the profile op),
`model.py` (`Post`/`User`/`Media` + `to_dict` + schema), `retrieve.py` (`fetch_profile`
with cursor pagination), and the `fetch` subcommand.

**Verify:** `agentic-threads fetch <handle> --limit 20 --output /tmp/p.json` writes
schema-valid `Post` JSON from the live session; `--limit`/cursor EOF/`--since` behave;
rate floor observed; `jsonschema` validates the output. Unit tests for parse/model/client/
retrieve green.

## Phase 4 — Remaining read primitives

Add `feed` (`BarcelonaFeedPaginationDirectQuery` → `feedData`), `post` (`BarcelonaPostColumnPageQuery`,
shortcode→id, replies-by-default), `search --type posts|people` (keyword + account ops,
two output types), `followers`/`following` (`User` output, `empty_pages` guard). Extend
`fetch --replies` (답글 tab). Extend `parse.py` `ENVELOPE_ROOTS` and `retrieve.py`
orchestrators per op.

**Verify:** each command returns schema-valid output from the live session with correct
`stop_reason`; `search --type people`/`followers`/`following` emit `User`; `post`
returns root + reply thread; date filters and limits behave. Fixture-driven unit tests for
every op green; `test_cli.py` catalog/exit-code coverage green.

## Phase 5 — Hardening + docs + release

`redact.py` fully wired to all diagnostic surfaces; soft-lock detection (200-empty →
exit 2) verified; `doctor --refresh` re-anchors `doc_id`s from the JS bundle. Write
`README.md`, `CHANGELOG.md` (Keep-a-Changelog), `DISCLAIMER.md` (Instagram/Meta ban +
ToS + GDPR, tone **not** weakened), `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
`LICENSE`, and `docs/wiki/` (mirror the sibling set). Bump to the release version.

**Verify:** full offline suite green on both CI legs; base-wheel smoke green; a broad
**live e2e pass** exercising every command against the throwaway account (documented,
outputs deleted, no PII committed); `git tag` == version.

## Phase 6 — Publish

Open PR → merge to `main` → create a GitHub **Release** (triggers `publish.yml` → PyPI
Trusted Publishing). Confirm `pip install agentic-threads` installs the `agentic-threads`
command and `agentic-threads --version` matches.

**Verify:** the package is installable from PyPI; `uv tool install agentic-threads` +
`agentic-threads setup` + `login` + `fetch` works end-to-end from a clean machine state.

## Phase 7 — The Claude skill (SEPARATE later session)

Not part of the package build. See `07-skill-plan.md`.

---

## Hard constraints (do not violate — from CLAUDE.md + the siblings)

- **Minimum code, surgical changes, no speculative abstractions or unrequested features.**
  D2-scope-out (media/reposts tabs, communities, notifications, insights, writes,
  anonymous reads) — don't build them.
- **1.0s rate floor is non-bypassable**; single-target primitives, no batch/daemon/crawl.
- **PII**: `scratch/`, `*.raw.json`, `output/`, `profiles/` gitignored; fixtures synthetic
  + PII-scanned; never commit real captures.
- **`test_no_scrapling_import.py` stays green**: reads are pure httpx; scrapling only lazy-
  imported in login/setup.
- **DISCLAIMER tone not weakened**; throwaway account only.
- If a scope change beyond this plan seems needed, **ask first**.
