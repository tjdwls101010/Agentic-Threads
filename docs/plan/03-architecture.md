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
| `auth.py` | Cookie-backed `SessionCredential`; `load_session`/`save_session` (0700 dir, 0600 file, `os.open` atomic); `--cookies` import (Netscape/JSON/cURL, 3-format autodetect); identifier normalization (`@handle`, username, numeric id, profile/post URL); **shortcode↔postID** decode (Q-C). No credential token fields. | agentic-x `auth.py` |
| `session.py` | Headed stealth-browser `run_login` with poll-based completion (no `input()`); `run_setup`; one-request `run_status`; `run_doctor` (+`--refresh`). Login harvests the required cookies, current `doc_id`s, and feature settings. Browser imports stay lazy. | agentic-x `session.py` (structure) + agentic-facebook `session.py` (poll-based login detection) |
| `client.py` | Authenticated `ReadClient` over `httpx`: ordinary reads send only form fields `doc_id` and `variables`, with the current cookie/CSRF header contract; `_throttle` enforces the 1.0s floor; typed error mapping handles rate limits, session walls, drift, and non-2xx responses. | agentic-facebook `graphql.py` (transport shape) + agentic-x `client.py` (error map) |
| `gql.py` | Endpoint constant `https://www.threads.com/graphql/query`; `DEFAULT_FEATURES`/relay-provider-flag defaults; per-operation `variables` builders (`feed_variables`, `profile_variables`, `post_variables`, search, followers, following, and pagination variants). | agentic-x `gql.py` + agentic-facebook `queries.py` |
| `docids.py` | `DEFAULT_DOC_IDS` (recon table, D12 fallback); strict login harvest; bounded, browser-free re-anchoring from trusted Threads HTML/JavaScript and authenticated route definitions. | agentic-x `queryids.py` |
| `parse.py` | Pure envelope walk: per-operation `ENVELOPE_ROOTS` and operation-specific Post/User leaves; `EnvelopeParseError` on structural failure (→ exit 4). | agentic-x `parse.py` |
| `model.py` | `Post`/`User`/`Media` dataclasses + `to_dict()`; `FIELD_DESCRIPTIONS`; `schema_fields()` anchored on `to_dict()`; `json_schema()` (draft 2020-12). `build_post`/`build_user` normalizers. | agentic-x `model.py` |
| `retrieve.py` | Pagination orchestrator: cursor loop with EOF rule, `--limit`/`--since`/`--until` composition, stop-reason vocabulary, and bounded eager `RetrieveResult`/`UserResult` values. `fetch_profile`, `fetch_home`, `fetch_post`, `search`, `fetch_social_graph`. | agentic-x `retrieve.py` |
| `redact.py` | Scrub sensitive keys (`sessionid`, `fb_dtsg`, `lsd`, `csrftoken`, `ds_user_id`, cookie/token/authorization), truncate free-text keys, strip signing query-strings off signed CDN URLs (`cdninstagram.com`/`fbcdn.net`). Applies to diagnostics only, never the output file. | agentic-x `redact.py` |
| `errors.py` | Typed hierarchy under `AgenticThreadsError` (see `04-cli-spec.md`). | agentic-x `errors.py` |
| `cli.py` | argparse parser, subcommand handlers, `_HANDLERS` dispatch, exit-code contract, `catalog` (from parser) + `schema` (from model). | agentic-x `cli.py` |
| `_stealth_init.js` | `Object.defineProperty(navigator,"webdriver",{get:()=>undefined})`. Absolute path for scrapling `init_script`. | agentic-x (verbatim) |

### Phase-0 contract resolution

The re-verification in `02-recon-findings.md` §8 supersedes the provisional Q-A/Q-B assumptions that originally called for Meta token fields. Ordinary GraphQL reads use only `doc_id` and `variables` in the form body. Their current headers are `content-type`, the harvested `user-agent`, `x-csrftoken`, `x-fb-friendly-name`, `x-ig-app-id`, `origin`, and `referer`, alongside the three authenticated cookies. There is no `tokens.py`, no read-time token refresh, and no `fb_dtsg`, `lsd`, or `jazoest` field in `SessionCredential`. Login harvests cookies and live operation `doc_id`s and records the feature settings needed by the variable builders; subsequent reads and re-anchoring remain browser-free.

Session status performs exactly one authenticated profile request, closes the client, and interprets the anchored body through `parse.extract_profile_user`. An explicit null profile or account-id mismatch is expired; a missing or malformed envelope propagates `EnvelopeParseError` (exit 4). Login follows a harvested post link only when it is HTTPS on a trusted Threads host with the default port and a `/post/` permalink path. Challenge state has precedence, only recognized transient states are polled again, and failures while reading the page URL or content, evaluating, or waiting terminate with sanitized evidence and no credential save.

Retrieval intentionally has no streaming-iterator public API. Each read returns one bounded, eager `RetrieveResult` or `UserResult`; the CLI serializes that complete result to JSON or NDJSON through a temporary file and atomic replace. NDJSON is an output encoding, not a placeholder streaming wrapper.

**`transaction.py`, `observe.py`, `queryids.reanchor` bearer trick, `GATED_OPS` — NOT ported** (no txid wall on Threads).

## Storage (`platformdirs.user_data_dir("agentic-threads")`)

- `profiles/<name>/session.json` — the credential, dir `0700`, file `0600`, atomic write.
- `profiles/<name>/browser/` — the stealth browser's persistent context (nested in the 0700 tree).
- `browsers/` — isolated Playwright/Chromium install (`PLAYWRIGHT_BROWSERS_PATH`), never shared.
- `output/` — default output dir (never cwd/repo).
- Env override: `AGENTIC_THREADS_PROFILE_DIR` (or `--profile-dir`).

`SessionCredential` fields: `sessionid`, `ds_user_id`, `csrftoken`, `user_agent`, `doc_ids: dict|None`, `features: dict|None` (relay-provider flags), `extracted_at`. Serialized to `session.json`; token credentials are intentionally absent under the Phase-0 read contract.

## Data model (output schema)

The Phase-0 fixtures confirm three output object types (mirror `agentic-x`'s `Tweet`/`User`/`Media`, renamed and retargeted):

**`Post`** (one element of the output array): `id` (ASCII-decimal pk serialized as a string — dedup key), `code` (shortcode), `url`, `created_at` (ISO-8601 UTC `Z` or null), `text` (caption), `author` (nested `User`), `like_count`, `reply_count`, `repost_count`, `quote_count`, `media[]` (`Media`), `is_reply`, `reply_to_id` and `root_post_id` (nullable ASCII-decimal strings), `quoted_post` (nested `Post`, recursive), `reposted_post` (nested `Post`, recursive), `link_preview` (`{url,title}` or null), `is_pinned`, `captured_at` (when *you* scraped — not an event time), `raw` (only with `--raw`).

**`User`** (`Post.author`, and the object emitted by `followers`/`following`/`search --type people`): `id` (ASCII-decimal pk serialized as a string), `username`, `full_name`, `is_verified`, `follower_count`, `following_count`, `post_count`, `bio`, `profile_pic_url`, `url`.

**`Media`** (element of `Post.media`): `kind` (`photo`/`video`/`carousel`/`unknown`), `url`, `width`, `height`, `alt_text`. (Threads posts can be carousels of multiple media.)

Schema is **generated from the code** (`schema_fields()` anchored on `to_dict()` output, `json_schema()` draft 2020-12), never hand-written — so `agentic-threads schema --json` cannot drift. Semantic refinements add numeric-ID patterns, nullable relationship-ID patterns, the `Media.kind` enum, and `date-time` formats without hand-copying serializer keys. `test_model.py` checks the structure and validates real fixtures with a format-aware draft 2020-12 validator.

## The two invariants to preserve (from the siblings)

1. **Everything derived, never transcribed**: `catalog` from the live argparse parser, schema from `to_dict()`-anchored descriptions, exit codes from one `errors`/`exits` table. Tests assert non-drift (`test_cli.py`: every handler appears in the catalog and declares its output object).
2. **Non-bypassable rate floor + single redaction path**: 1.0s clamp applies on every request regardless of entry point; every diagnostic surface routes through `redact`, the output file never does.
