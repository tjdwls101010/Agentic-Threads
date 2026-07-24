# CLI Reference

This is the complete v0.1 command surface for `agentic-threads`. The installed CLI is the final authority: `agentic-threads catalog` describes the live parser, and `agentic-threads schema --json` describes the objects it writes.

`agentic-threads` is read-only with respect to Threads. Every network read uses an authenticated session and live Threads GraphQL over HTTP. The optional browser is used only by `setup` and interactive `login`; retrieval commands do not drive a browser and have no browser fallback. Anonymous reads, write actions, multi-target batching, and crawling are outside the v0.1 surface.

## Global option

```bash
agentic-threads --version
```

`--version` prints the installed version and exits `0`. A missing or unknown subcommand, a missing required argument, or an invalid flag is a usage error and exits `1`, not `argparse`'s usual `2`. Code `2` is reserved for login/session failures.

## Output contract for read commands

The read commands are `feed`, `fetch`, `post`, `search`, `followers`, and `following`.

- Results are written to a file. Nothing useful is written to stdout.
- A one-line result summary is written to stderr, including the count, stop reason, and saved path. Post results also report their observed date range when available; graph results count accounts.
  ```text
  {N} posts, range {oldest}..{newest}, stop reason: {reason}. Saved to {path}
  ```
  User-returning graph/search summaries use `accounts` instead of `posts` and omit a date range when no event dates exist.
- `--format json` writes one JSON array. `--format ndjson` writes one object per line.
- Result files are atomically written with owner-only `0600` permissions where POSIX modes are supported, but normalized content remains sensitive third-party personal data and must still be protected.
- Without `--output`, the destination is `<platform data dir>/output/<safe_identifier>-<UTC timestampZ>.<json|ndjson>`. The `feed` identifier is literally `home`. The default is never the current directory or repository.
- A result can be valid but partial. Always read the stop reason as well as the exit code.

See [Output Schema](Output-Schema.md) for the `Post`, `User`, and `Media` fields and [Configuration](Configuration.md) for platform-specific paths.

## Common read flags

| Flag | Default | Applies to | Meaning |
|---|---|---|---|
| `--format {json,ndjson}` | `json` | all read commands | Select a JSON array or newline-delimited JSON objects. |
| `--output PATH` | generated platform-data path | all read commands | Write the result to this path instead of the default `output/` path. |
| `--limit N` | unbounded | all read commands | Strictly cap `User` and non-pinned `Post` objects at `N`; pinned profile posts are preserved and may make total `Post` rows exceed `N`. |
| `--since YYYY-MM-DD` | unset | `feed`, `fetch`, `search --type posts` | Apply a lower date bound to `Post.created_at`. A run that cannot confirm this bound was reached can exit `7`. |
| `--until YYYY-MM-DD` | unset | `feed`, `fetch`, `search --type posts` | Apply an upper date bound to `Post.created_at`. |
| `--wait-on-limit` | off | all read commands | Wait and continue when a rate limit provides a usable wait opportunity instead of stopping immediately. |
| `--max-wait SECONDS` | unset | all read commands | Hard-cap a rate-limit reset wait when `--wait-on-limit` is active. If the required wait exceeds the cap, stop as `rate_limited` instead of sleeping partially. It does not enable waiting by itself. |
| `--profile NAME` | `default` | all read commands | Use this named saved session. |
| `--profile-dir PATH` | unset | all read commands | Override the profile-store root for this invocation. |
| `--raw` | off | all read commands | Attach the source GraphQL node to each output object for debugging. It is redacted by default. |
| `--no-redact` | off | all read commands | With `--raw`, leave the attached raw node unscrubbed. A warning is printed. |
| `-v`, `--verbose` | off | all read commands | Include more diagnostic detail. Diagnostics remain redacted. |

`--since` and `--until` describe `Post` dates and are applied client-side for `feed`, `fetch`, and `search --type posts`. `search --type people` rejects both flags; `followers` and `following` do not accept date bounds because `User` results have no event-date field.

The request path delays every request, including the first, by at least **1.0 seconds**. There is no CLI flag that disables or lowers this floor.

# Session and maintenance commands

## `login`

Create or refresh a logged-in profile.

```bash
agentic-threads login [--profile NAME] [--profile-dir PATH] \
  [--cookies FILE] [--timeout-seconds SECONDS]
```

Without `--cookies`, `login` opens a visible stealth browser at Threads. Complete the Instagram/Threads login in that window; completion is detected by polling, so there is no terminal Enter prompt. The browser path also harvests current operation metadata for later HTTP reads.

With `--cookies FILE`, the command imports an existing cookie export without opening a browser. JSON cookie arrays, flat or wrapped JSON cookie objects, Netscape cookie files, and raw `Cookie:`/cURL cookie strings are auto-detected. Treat the source export as a live credential even after a successful import.

| Flag | Default | Meaning |
|---|---|---|
| `--profile NAME` | `default` | Name of the profile to save. |
| `--profile-dir PATH` | unset | Override the profile-store root. |
| `--cookies FILE` | unset | Import a cookie export instead of opening the browser. |
| `--timeout-seconds SECONDS` | `300` | Maximum time to wait for browser login completion. Parsed as a floating-point value. |

Exit codes: `0` for a verified login/import, `2` when login could not be verified, and `1` for malformed cookies, browser/setup failures, or another failure.

## `status`

Perform one cheap authenticated read and classify the saved session.

```bash
agentic-threads status [--profile NAME] [--profile-dir PATH] [--json]
```

| Flag | Default | Meaning |
|---|---|---|
| `--profile NAME` | `default` | Profile to check. |
| `--profile-dir PATH` | unset | Override the profile-store root. |
| `--json` | off | Emit a machine-readable status object instead of the human status line. |

Exit codes: `0` when logged in, `2` when no usable login exists or the session is expired/soft-locked, `3` when the probe is rate-limited, and `1` for an unexpected status-check failure.

## `setup`

Provision the isolated Chromium browser used by interactive login.

```bash
agentic-threads setup [--force]
```

`setup` requires the optional `[browser]` installation extra. Retrieval commands themselves do not require or use this browser.

| Flag | Default | Meaning |
|---|---|---|
| `--force` | off | Reinstall even when the isolated browser is already present. |

Exit codes: `0` on success and `1` on failure.

## `doctor`

Verify an authenticated HTTP round trip and optionally refresh rotated Threads `doc_id` values.

```bash
agentic-threads doctor [--profile NAME] [--profile-dir PATH] [--refresh]
```

| Flag | Default | Meaning |
|---|---|---|
| `--profile NAME` | `default` | Profile to diagnose. |
| `--profile-dir PATH` | unset | Override the profile-store root. |
| `--refresh` | off | Re-anchor current operation `doc_id` values from Threads' JavaScript bundle over HTTP and persist them. No browser is used. |

Run `doctor --refresh` after exit `4`. It distinguishes a stale operation identifier from a broader response-shape change. Exit codes: `0` when the requested checks succeed and `1` otherwise.

# Introspection commands

Both commands are offline, need no login, and write their result to stdout.

## `catalog`

```bash
agentic-threads catalog [--json]
```

`catalog` emits a machine-readable JSON description generated from the installed argument parser. It includes `catalog_version`, package/command/version information, every command and argument, the exit-code map, and each read command's output object. `--json` is accepted but is a no-op because catalog output is always JSON. Exit code: `0`.

## `schema`

```bash
agentic-threads schema [--json]
```

Without `--json`, `schema` prints an annotated `Post`/`User`/`Media` field listing. With `--json`, it prints JSON Schema draft 2020-12. The schema is generated from the same serialization contract used for output. Exit code: `0`.

# Read commands

All read commands require a usable saved login and accept the applicable [common read flags](#common-read-flags).

## `feed`

Read the logged-in account's home/For You feed. It takes no target.

```bash
agentic-threads feed [common read flags]
```

Output: `Post`. The generated filename uses `home` as its safe identifier. `--since` and `--until` are available.

## `fetch`

Read a profile's Threads-tab posts.

```bash
agentic-threads fetch <user> [--replies] [--by {username,id}] [common read flags]
```

`<user>` accepts an `@handle`, bare username, numeric user id, or Threads profile URL. An all-digit value can be disambiguated explicitly with `--by`.

| Flag | Default | Meaning |
|---|---|---|
| `--replies` | off | Include the profile's replies-tab results in addition to its Threads-tab posts. |
| `--by {username,id}` | automatic | Force an all-digit identifier to be interpreted as a username or numeric id. |

Output: `Post`. **Replies are opt-in for `fetch`.** `--since` and `--until` are available.

## `post`

Read one post and, by default, its reply thread.

```bash
agentic-threads post <url|id> [--no-replies] [common read flags]
```

`<url|id>` accepts a Threads post URL containing a shortcode or a numeric post id.

| Flag | Default | Meaning |
|---|---|---|
| `--no-replies` | off | Return the requested post without its reply thread. |

Output: `Post`. **Replies are on by default for `post`.** The root post is first; following objects are replies with `is_reply: true`. This is the opposite of `fetch`, whose replies tab requires `--replies`.

## `search`

Search Threads posts or accounts.

```bash
agentic-threads search <query> [--type {posts,people}] [common read flags]
```

| Flag | Default | Meaning |
|---|---|---|
| `--type {posts,people}` | `posts` | Return matching posts or matching accounts. |

- `--type posts` emits `Post` objects.
- `--type people` emits `User` objects.

Post search calls Threads' **live GraphQL keyword-search operation**. It does not search previously saved files or a static index, and it does not use a browser fallback. `--since` and `--until` are filtered **client-side** against returned `Post.created_at` values; they are not server-side search constraints. A structurally valid search with no hits uses stop reason `no_matches` and exits `0`.

## `followers` and `following`

Read a profile's social graph.

```bash
agentic-threads followers <user> [common read flags]
agentic-threads following <user> [common read flags]
```

`<user>` accepts the same profile identifier forms as `fetch`: `@handle`, bare username, numeric id, or profile URL.

Both commands emit top-level `User` objects and count accounts in their summaries. They do not accept date bounds. A graph run that stops after repeated cursor pages with no accounts reports `empty_pages`; that means the result is incomplete, not that the graph was exhausted.

## Stop reasons

| Stop reason | Meaning | Complete? |
|---|---|---|
| `limit_reached` | `--limit` stopped the run while more data may exist. | No. |
| `feed_exhausted` | The source reported that no next page exists. | Yes. |
| `no_next_page` | The connection reported `has_next_page=false`. | Yes. |
| `no_matches` | A valid search returned no hits. | Yes. |
| `since_crossed` | The requested lower date boundary was reached. | Yes for that requested window. |
| `empty_pages` | A graph run gave up after repeated cursor pages with no accounts. | No. |
| `rate_limited` | A 429 stopped the run. | No. |
| `max_requests` | The per-run request budget stopped pagination. | No. |

## Exit codes

The numeric contract is shared by the CLI and its generated catalog. There is no exit code `6`.

| Code | Meaning | Correct response |
|---|---|---|
| `0` | Success: limit met, requested date boundary reached, source exhausted, or a search genuinely had no matches. | Read the output file and stop reason. |
| `1` | Usage error, invalid identifier, setup/import failure, or unexpected failure. | Correct the invocation; use `-v` for redacted diagnostic detail when appropriate. |
| `2` | Login required, session expired, session soft-locked, or a Meta checkpoint/challenge made the session unusable. | Login again for ordinary expiry; a checkpoint requires human action and is never auto-retried. Do not confuse this code with a usage error. |
| `3` | Rate-limited before completion. | Slow down, wait, or use `--wait-on-limit` with an appropriate `--max-wait`. |
| `4` | Threads' response no longer matches the expected envelope, commonly because a `doc_id` rotated. | Run `agentic-threads doctor --refresh`; upgrade/report persistent response-shape drift. |
| `5` | Target user or post does not exist or is unavailable, including private, suspended, or deleted targets. | Treat the target as unavailable; do not retry aggressively. |
| `7` | `--since` was requested, but the run stopped before confirming that boundary was reached. | Treat the saved result as partial and inspect its stop reason. |

Meta checkpoints/challenges are surfaced and are never automatically retried. Stop automated requests and resolve the checkpoint manually before logging in again; see [FAQ and Troubleshooting](FAQ-and-Troubleshooting.md).
