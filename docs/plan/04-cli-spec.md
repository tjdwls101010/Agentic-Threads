# CLI Specification

`prog = agentic-threads`. stdlib `argparse`. `main(argv)` → `build_parser().parse_args()` → dispatch via `_HANDLERS`. Subparsers `required=True`. Global `--version`. Custom `_ArgumentParser.error()` exits **1** (not argparse's 2 — exit 2 is reserved for "login required").

## Commands

### Session / meta
- **`login`** — headed stealth-browser login (poll-based wait) or `--cookies` import. Flags: `--profile` (default `default`), `--profile-dir`, `--cookies FILE`, `--timeout-seconds` (float, default 300). stderr messages; exit 0/1/2.
- **`status`** — one cheap authenticated read → classify. Flags: `--profile`, `--profile-dir`, `--json`. Exit: 0 logged-in, 2 expired, 3 rate-limited.
- **`setup`** — provision the isolated browser (`scrapling install` into `browsers/`). Flags: `--force`. (Needs the `[browser]` extra.)
- **`doctor`** — authenticated round-trip + (with `--refresh`) browser-free `doc_id` re-anchor from the JS bundle. Flags: `--profile`, `--profile-dir`, `--refresh`. Exit 0/1 for completed checks; applicable typed failures retain their canonical exit from the global table (for example, drift exits 4).
- **`catalog`** — machine-readable description of the whole CLI, generated from the parser. Flag: `--json` (no-op; always JSON). Offline.
- **`schema`** — the `Post`/`User`/`Media` output schema. Flag: `--json` (JSON Schema draft 2020-12). Offline, no login.

### Read primitives (write JSON to a file; one-line stderr summary)
- **`feed`** — the logged-in home feed (推薦/For You). **No target.** → `Post`.
- **`fetch <user>`** — a profile's posts (스레드 tab). `<user>` = `@handle`, username, numeric id, or profile URL. `--replies` also includes the 답글 tab. `--by {username,id}` disambiguates all-digit identifiers. → `Post`.
- **`post <url|id>`** — one post **plus its reply thread** (default on; `--no-replies` for post-only). `<url|id>` = post URL (shortcode) or numeric post id. → `Post` (root post first, then replies as `Post`s with `is_reply=true`).
- **`search <query>`** — `--type {posts,people}` (default `posts`). `posts` → keyword-search `Post`s; `people` → account-search `User`s. `--since`/`--until` are valid only with `--type posts`. → `Post` or `User`.
- **`followers <user>`** / **`following <user>`** — the social graph. → `User`.

### Common read flags (shared group)
`--format {json,ndjson}` (default json), `--output PATH` (default: timestamped file under the platform data dir), `--limit N` (default unbounded), `--since YYYY-MM-DD`, `--until YYYY-MM-DD` (available on fetch/feed; for search, valid only with `--type posts`; see Q-E — server-side vs client-side), `--wait-on-limit`, `--max-wait SECONDS`, `--profile`, `--profile-dir`, `--raw`, `--no-redact`, `-v/--verbose`.

`--output` default naming: `<safe_identifier>-<YYYYMMDDTHHMMSSffffffZ>.<json|ndjson>` under the platform user-data root's `agentic-threads/output/` directory. `feed`'s identifier is the literal `home`; non-alphanumeric identifier runs become `-`.

## Output contract

- Read commands write to a **file**; only a one-line summary hits **stderr**; nothing useful goes to stdout.
- Summary format: `"{N} posts, range {oldest}..{newest}, stop reason: {reason}. Saved to {path}"` (`{N} accounts, …` for graph commands).
- A `rate_limited` result is still saved and summarized before the command exits 3.
- Only in summaries, C0/DEL characters in the output path are rendered as `\xNN`; the real filesystem path is unchanged, and ordinary path text is preserved.
- `--raw` attaches the raw GraphQL node per object (redacted unless `--no-redact`, which prints a warning). Debug-only.

## `stop_reason` vocabulary (in the stderr summary)

- `limit_reached` — `--limit` stopped it; there is more.
- `feed_exhausted` / `no_next_page` — genuinely the end (`has_next_page=false`).
- `no_matches` — a search with no hits (real; report as such).
- `since_crossed` — `--since` date boundary reached.
- `empty_pages` *(graph)* — gave up after K cursor pages with no accounts (incomplete, not finished).
- `rate_limited` — stopped by a 429 (see `--wait-on-limit`).
- `max_requests` — stopped by the per-run request budget.

## Exit-code contract (single source in `errors.py`; asserted by `test_cli.py`)

| Code | Meaning |
|---|---|
| 0 | success (limit met / date window reached / feed exhausted) |
| 1 | usage error, invalid identifier, or unexpected failure |
| 2 | not logged in / session expired / soft-locked |
| 3 | rate-limited (see `--wait-on-limit`) |
| 4 | Threads' response no longer matches expectations — `doc_id` drift / envelope parse failure. Fix: `agentic-threads doctor --refresh`, or upgrade. |
| 5 | target user/post does not exist / is unavailable (private, suspended, deleted) |
| 7 | `--since` requested but the run stopped before confirming it was reached |

(No txid/gated-op codes — Threads has no transaction-id wall.)

## Typed errors (`errors.py`, base `AgenticThreadsError`)

`LoginRequiredError` (→2), `SessionExpiredError` (→2, incl. soft-lock: 200 with empty/malformed body — inspect the body like FB/X), `RateLimitedError(reset_at)` (→3), `EnvelopeParseError` (→4, structural/`doc_id` drift), `ProfileUnavailableError`/ `NotFoundError` (→5), `InvalidIdentifierError`/`InvalidCookieError` (→1), `BrowserSetupError` (setup/login-time browser problems), `ChallengeError` (Meta checkpoint — **never auto-retry**; surface to the user).

## `catalog` (mirror agentic-x verbatim)

`build_catalog()` reflects over `build_parser()._actions` → `{catalog_version, package, command, version, commands[], exit_codes{}, output_schema}`. Each command carries `name`, `help`, `output` (`Post`/`User`/None from a `_COMMAND_OUTPUT` map), `arguments[]` (flags/types/defaults/choices). `test_cli.py` asserts every `_HANDLERS` command is in the catalog and every read command declares its output object — the anti-drift gate.
