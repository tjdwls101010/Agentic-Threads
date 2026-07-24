# Configuration

`agentic-threads` has one environment override, named profiles, command-line output controls, and a non-bypassable pacing floor. There is no setting that enables anonymous reads, write actions, browser-based retrieval, batching, or crawling.

For the full per-command flag list, see [CLI Reference](CLI-Reference.md).

## Platform data directory

The default root is resolved by:

```python
platformdirs.user_data_dir("agentic-threads")
```

Typical roots are:

| Platform | Default data root |
|---|---|
| macOS | `~/Library/Application Support/agentic-threads/` |
| Linux | `$XDG_DATA_HOME/agentic-threads/`, or `~/.local/share/agentic-threads/` when `XDG_DATA_HOME` is unset |
| Windows | the `platformdirs` user-data location under `%LOCALAPPDATA%` (normally `%LOCALAPPDATA%\agentic-threads\agentic-threads\`) |

`platformdirs` and operating-system configuration are authoritative; for example, setting `XDG_DATA_HOME` changes the Linux root.

The data tree is:

```text
<platform data dir>/
├── profiles/
│   └── <name>/
│       ├── session.json
│       └── browser/
├── browsers/
└── output/
```

| Path | Purpose |
|---|---|
| `profiles/<name>/session.json` | Saved session cookies, user agent, `doc_id`/feature metadata, and extraction time for one login profile. The profile directory is owner-only `0700`; the credential file is atomically written `0600` where supported. |
| `profiles/<name>/browser/` | Persistent browser context for the headed login flow, nested inside the protected profile tree. Retrieval does not use it. |
| `browsers/` | Isolated Chromium/Playwright installation provisioned by `agentic-threads setup`. It is not shared with other tools. |
| `output/` | Default destination for read-command JSON/NDJSON files. |

The profile and its browser directory contain a live authenticated session. Do not sync, share, back up, or commit them. The output directory contains third-party data and requires the same care; see [Security and Privacy](Security-and-Privacy.md).

## Named profiles

A profile is an independent saved Threads/Instagram login. The default name is `default`.

```bash
agentic-threads login --profile research
agentic-threads status --profile research
agentic-threads feed --profile research --limit 10
```

`login`, `status`, `doctor`, and every read command accept:

| Flag | Default | Effect |
|---|---|---|
| `--profile NAME` | `default` | Select the named profile. |
| `--profile-dir PATH` | unset | Override the root under which named profiles are stored for this invocation. |

`setup` is not profile-specific. Its browser install stays under the platform data root.

## `AGENTIC_THREADS_PROFILE_DIR`

`AGENTIC_THREADS_PROFILE_DIR` changes the profile-store root for commands that do not pass `--profile-dir`.

Resolution order:

1. `--profile-dir PATH` for the current invocation;
2. `AGENTIC_THREADS_PROFILE_DIR`, when set and non-empty;
3. `<platform data dir>/profiles/`.

The selected profile name is then appended to that root:

```bash
export AGENTIC_THREADS_PROFILE_DIR=/Volumes/secure/threads-profiles
agentic-threads login --profile research
# profile: /Volumes/secure/threads-profiles/research/session.json
```

A one-off command-line override wins over the environment:

```bash
agentic-threads status \
  --profile research \
  --profile-dir /Volumes/other/threads-profiles
```

This override moves **profiles only**. The isolated `browsers/` install and default `output/` directory remain under `platformdirs.user_data_dir("agentic-threads")`.

## Browser installation

The base package performs reads over HTTP. Browser support is optional and has only two purposes:

1. `agentic-threads setup` provisions an isolated browser under `<platform data dir>/browsers/`.
2. `agentic-threads login` uses that browser for a visible, poll-detected interactive login and operation-metadata harvest.

Install the `[browser]` extra before using those browser paths. Cookie import through `login --cookies FILE` does not open a browser. Once a profile is saved, `status`, `doctor`, `doctor --refresh`, and all read commands use HTTP rather than browser automation.

`setup --force` reinstalls a missing or corrupted isolated browser. There is no flag or environment variable that moves the browser cache or enables it as a retrieval fallback.

## Request pacing

Every GraphQL read enforces:

```text
MIN_REQUEST_PAUSE_SECONDS = 1.0
```

The delay applies before every request, including the first, regardless of command or entry point. A lower requested/internal value is raised to `1.0` seconds; it cannot be disabled by a flag or environment variable.

This is a per-process minimum, not a global rate limiter and not an account-safety guarantee. Concurrent invocations can still exceed safe volume. Do not use multiple processes to work around the floor.

A separate per-run request budget can end pagination with `stop_reason: max_requests`. It is a safety boundary, not evidence that the source was exhausted.

## Rate-limit waiting

Every read command accepts:

| Flag | Default | Effect |
|---|---|---|
| `--wait-on-limit` | off | When a 429 offers a usable wait opportunity, wait and retry instead of stopping immediately. |
| `--max-wait SECONDS` | unset | Hard-cap the reset wait when `--wait-on-limit` is active. If the required delay exceeds the cap, stop as `rate_limited` instead of sleeping partially. It does nothing by itself. |

Without `--wait-on-limit`, a rate-limited read stops with exit `3` and stop reason `rate_limited`. Waiting never bypasses the 1.0-second request floor. Repeated rate limits are a signal to reduce depth and frequency.

## Output location and format

Every read command writes a file and prints its path in a one-line stderr summary. No useful result is sent to stdout. Result files are atomically written with owner-only `0600` permissions where POSIX modes are supported, but normalized content remains sensitive third-party personal data and must still be protected.

| Flag | Default | Effect |
|---|---|---|
| `--format {json,ndjson}` | `json` | Write one JSON array or one JSON object per line. |
| `--output PATH` | generated path under `<platform data dir>/output/` | Override the result path. |
| `--limit N` | unbounded | Strictly cap `User` and non-pinned `Post` objects at `N`; pinned profile posts are preserved and may make total `Post` rows exceed `N`. |

The generated name is:

```text
<safe_identifier>-<UTC timestampZ>.<json|ndjson>
```

For `feed`, `<safe_identifier>` is `home`. Other commands derive a filesystem-safe identifier from their target or query. The default location is never the current working directory or repository.

Example:

```bash
agentic-threads fetch synthetic_alice --limit 20 --format ndjson
# stderr reports a path such as:
# .../agentic-threads/output/synthetic_alice-20260723T093000Z.ndjson
```

`--output` moves only that command's result. It does not move profiles or the browser install. A custom path can point into a repository, but doing so is unsafe because normalized output is not redacted.

## Date bounds

`feed`, `fetch`, and `search --type posts` accept `--since YYYY-MM-DD` and `--until YYYY-MM-DD` and apply the bounds client-side to `Post.created_at`. `search --type people` rejects either date flag because `User` results have no event-date field.

Post search always performs live Threads GraphQL requests. Its date bounds are applied client-side to posts returned by that search rather than sent as server-side search constraints. Deep or sparse windows can therefore require pagination and can end before `--since` is confirmed. Exit `7` means the saved result is partial with respect to that lower bound.

## Raw-output controls

| Flag | Default | Effect |
|---|---|---|
| `--raw` | off | Attach the underlying GraphQL node to each result. The node is scrubbed by default. |
| `--no-redact` | off | With `--raw`, disable that scrubbing and print a warning. |
| `-v`, `--verbose` | off | Show more error detail; diagnostic text remains redacted. |

These flags do not anonymize normalized output. `--no-redact` is a debugging escape hatch for the attached raw node, not a normal configuration. See [Security and Privacy](Security-and-Privacy.md) before using it.
