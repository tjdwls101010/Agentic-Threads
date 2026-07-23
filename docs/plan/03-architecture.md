# Architecture

## Naming triple + layout

- PyPI distribution: **`agentic-threads`**
- Import package: **`agentic_threads`** (`src/`-layout)
- Console script: **`agentic-threads`** → `agentic_threads.cli:main`
- Build backend: `hatchling`. Python `>=3.11`. License MIT. `Development Status :: 3 - Alpha`.
- `__version__` in `src/agentic_threads/__init__.py`, gated against the git tag at release.

## Module structure (`src/agentic_threads/`)

Ported from `agentic-x` (shape) with `agentic-facebook`'s Meta-GraphQL specifics folded in.

| Module | Responsibility | Primary source to adapt |
|---|---|---|
| `__init__.py` | `__version__`, package docstring. **No eager `import scrapling`.** | agentic-x |
| `config.py` | Paths (`platformdirs.user_data_dir("agentic-threads")`), `MIN_REQUEST_PAUSE_SECONDS = 1.0` + `clamp_request_pause`, `DEFAULT_MAX_REQUESTS`, env prefix `AGENTIC_THREADS_PROFILE_DIR`. | agentic-x `config.py` |
| `auth.py` | `SessionCredential` dataclass; `load_session`/`save_session` (0700 dir, 0600 file, `os.open` atomic); `--cookies` import (Netscape/JSON/cURL, 3-format autodetect); identifier normalization (`@handle`, username, numeric id, profile/post URL); **shortcode↔postID** decode (Q-C). | agentic-x `auth.py` |
| `session.py` | Headed stealth-browser `run_login` with **poll-based** wait (no `input()`); `run_setup`; `run_status`; `run_doctor` (+`--refresh`); harvest cookies + `doc_id`s + tokens during login. Lazy `import scrapling` inside functions only. | agentic-x `session.py` (structure) + agentic-facebook `session.py` (poll-based login detection) |
| `client.py` | `ReadClient` over `httpx`: builds the form-urlencoded POST body (`doc_id`, `variables`, `lsd`, `fb_dtsg`?, `jazoest`, comet fields) + headers (`x-ig-app-id`, `x-fb-lsd`, `x-csrftoken`, `x-fb-friendly-name`, …); `_throttle` (1.0s floor); error mapping (429/401/soft-lock/non-200). | agentic-facebook `graphql.py` (body/headers) + agentic-x `client.py` (error map) |
| `gql.py` | Endpoint constant `https://www.threads.com/graphql/query`; `DEFAULT_FEATURES`/relay-provider-flag defaults; per-op `variables` builders (`feed_variables`, `profile_variables`, `post_variables`, `keyword_search_variables`, `account_search_variables`, `followers_variables`, …). | agentic-x `gql.py` + agentic-facebook `queries.py` |
| `docids.py` | `DEFAULT_DOC_IDS` (recon table, D12 fallback); `harvest_from_browser(captured_xhr)`; `reanchor_via_main_js(...)` (browser-free re-anchor from the JS bundle). | agentic-x `queryids.py` |
| `tokens.py` | `fb_dtsg`/`lsd` extraction regexes; `jazoest` compute; `refresh_over_http` (re-derive tokens from cookies with no browser); staleness (`TOKEN_MAX_AGE_SECONDS`). **Only if Q-A says reads need `fb_dtsg`.** | agentic-facebook `tokens.py` |
| `parse.py` | Pure envelope walk: per-op `ENVELOPE_ROOTS` → `(list[raw_post], page_info/cursor)`; a `walk_user_connection` sibling for `User`-returning ops; `EnvelopeParseError` on structural failure (→ exit 4). | agentic-x `parse.py` |
| `model.py` | `Post`/`User`/`Media` dataclasses + `to_dict()`; `FIELD_DESCRIPTIONS`; `schema_fields()` anchored on `to_dict()`; `json_schema()` (draft 2020-12). `build_post`/`build_user` normalizers. | agentic-x `model.py` |
| `retrieve.py` | Pagination orchestrator: cursor loop with EOF rule, `--limit`/`--since`/`--until` composition, `stop_reason` vocabulary, streaming iterators, `RetrieveResult`. `fetch_profile`, `fetch_home`, `fetch_post`, `search`, `fetch_social_graph`. | agentic-x `retrieve.py` |
| `redact.py` | Scrub sensitive keys (`sessionid`, `fb_dtsg`, `lsd`, `csrftoken`, `ds_user_id`, cookie/token/authorization), truncate free-text keys, strip signing query-strings off signed CDN URLs (`cdninstagram.com`/`fbcdn.net`). Applies to diagnostics only, never the output file. | agentic-x `redact.py` |
| `errors.py` | Typed hierarchy under `AgenticThreadsError` (see `04-cli-spec.md`). | agentic-x `errors.py` |
| `cli.py` | argparse parser, subcommand handlers, `_HANDLERS` dispatch, exit-code contract, `catalog` (from parser) + `schema` (from model). | agentic-x `cli.py` |
| `_stealth_init.js` | `Object.defineProperty(navigator,"webdriver",{get:()=>undefined})`. Absolute path for scrapling `init_script`. | agentic-x (verbatim) |

**`transaction.py`, `observe.py`, `queryids.reanchor` bearer trick, `GATED_OPS` — NOT ported** (no txid wall on Threads).

## Storage (`platformdirs.user_data_dir("agentic-threads")`)

- `profiles/<name>/session.json` — the credential, dir `0700`, file `0600`, atomic write.
- `profiles/<name>/browser/` — the stealth browser's persistent context (nested in the 0700 tree).
- `browsers/` — isolated Playwright/Chromium install (`PLAYWRIGHT_BROWSERS_PATH`), never shared.
- `output/` — default output dir (never cwd/repo).
- Env override: `AGENTIC_THREADS_PROFILE_DIR` (or `--profile-dir`).

`SessionCredential` fields: `sessionid`, `ds_user_id`, `csrftoken`, `user_agent`,
`fb_dtsg` (nullable if Q-A), `lsd`, `doc_ids: dict|None`, `features: dict|None`
(relay provider flags), `extracted_at`. Serialized to `session.json`.

## Data model (output schema)

Three object types (mirror `agentic-x`'s `Tweet`/`User`/`Media`, renamed/retargeted).
Exact leaf fields to be confirmed against a real capture in Phase 0; this is the intended shape:

**`Post`** (one element of the output array):
`id` (numeric pk — dedup key), `code` (shortcode), `url`, `created_at` (ISO-8601 UTC `Z`
or null), `text` (caption), `author` (nested `User`), `like_count`, `reply_count`,
`repost_count`, `quote_count`, `media[]` (`Media`), `is_reply`, `reply_to_id`,
`root_post_id`, `quoted_post` (nested `Post`, recursive), `reposted_post` (nested `Post`,
recursive), `link_preview` (`{url,title}` or null), `is_pinned`, `captured_at` (when *you*
scraped — not an event time), `raw` (only with `--raw`).

**`User`** (`Post.author`, and the object emitted by `followers`/`following`/`search --type people`):
`id` (numeric pk), `username`, `full_name`, `is_verified`, `follower_count`, `bio`,
`profile_pic_url`, `url`. (Add `following_count`/`post_count` if present in the profile op.)

**`Media`** (element of `Post.media`):
`kind` (`photo`/`video`/`carousel`/`unknown`), `url`, `width`, `height`, `alt_text`.
(Threads posts can be carousels of multiple media.)

Schema is **generated from the code** (`schema_fields()` anchored on `to_dict()` output,
`json_schema()` draft 2020-12), never hand-written — so `agentic-threads schema --json`
can't drift. `test_model.py` validates real fixtures against it with `jsonschema`.

## The two invariants to preserve (from the siblings)

1. **Everything derived, never transcribed**: `catalog` from the live argparse parser,
   schema from `to_dict()`-anchored descriptions, exit codes from one `errors`/`exits`
   table. Tests assert non-drift (`test_cli.py`: every handler appears in the catalog and
   declares its output object).
2. **Non-bypassable rate floor + single redaction path**: 1.0s clamp applies on every
   request regardless of entry point; every diagnostic surface routes through `redact`,
   the output file never does.
