# agentic-threads

Read-only access to logged-in **Threads** data through single-target CLI primitives. A headed browser, or a cookie import, establishes an Instagram session once; profile posts, the home feed, post threads, search, followers, and following are then read over plain HTTP and written as structured JSON files.

This is an unofficial project. It is not affiliated with, endorsed by, or authorized by Meta, Instagram, or Threads. Automated access can violate their terms and can cause account bans or checkpoints. **Use only a disposable Instagram account, never a primary account, and read [DISCLAIMER.md](https://github.com/tjdwls101010/Agentic-Threads/blob/main/DISCLAIMER.md) first.** Read-only behavior is not permission to access or retain data.

## Requirements and installation

Python 3.11 or newer is required.

The base install contains the HTTP reader and cookie-import path, with no browser dependency:

```bash
pip install agentic-threads
```

Install the optional browser support only when you need `setup` or interactive `login`:

```bash
pip install "agentic-threads[browser]"
agentic-threads setup
```

All read commands use HTTP. The browser is used only to provision and establish a session; it is not a fallback transport for reads.

## Establish a session

### Interactive login

```bash
agentic-threads setup
agentic-threads login
agentic-threads status
```

`login` opens a headed browser at Threads. Complete **Continue with Instagram** manually. The CLI polls for successful login, saves the session, and closes the browser; it does not wait for an Enter keypress. `setup --force` reinstalls the isolated browser when needed.

### Cookie import

The base install can import cookies without starting a browser:

```bash
agentic-threads login --cookies /secure/path/threads-cookies.json
agentic-threads status
```

Netscape cookie files, JSON cookie exports, and cURL cookie strings are accepted. Export only from a disposable account that you control. The source export remains a live credential after import; secure or delete it yourself.

Named profiles use `--profile NAME` (default `default`). Use `--profile-dir PATH` or `AGENTIC_THREADS_PROFILE_DIR` to override profile storage. Session directories are created with mode `0700` and `session.json` with mode `0600`, but credentials are not encrypted at rest.

## CLI primitives

### Session and self-description

| Command | Purpose |
|---|---|
| `agentic-threads login` | Establish a session in a headed browser, or import one with `--cookies FILE` |
| `agentic-threads status` | Classify the saved session as logged in, expired, or rate-limited; `--json` emits machine-readable status |
| `agentic-threads setup` | Provision the isolated login browser; requires the `[browser]` extra |
| `agentic-threads doctor` | Perform an authenticated round trip; `--refresh` refreshes operation identifiers over HTTP |
| `agentic-threads catalog` | Emit the parser-derived command, argument, output-object, and exit-code catalog as JSON; offline and login-free |
| `agentic-threads schema` | Describe `Post`, `User`, and `Media`; `--json` emits JSON Schema draft 2020-12; offline and login-free |

`agentic-threads --version` prints the installed version. `catalog --json` is accepted for symmetry and still emits the same JSON catalog.

### Read commands

| Command | Output | Purpose |
|---|---|---|
| `agentic-threads feed` | `Post` | Read the logged-in session's home feed; it takes no target |
| `agentic-threads fetch <user>` | `Post` | Read a profile's posts; `--replies` also reads its replies tab |
| `agentic-threads post <url-or-id>` | `Post` | Read one post and its reply thread; `--no-replies` returns the root only |
| `agentic-threads search <query>` | `Post` | Search posts; this is the default `--type posts` mode |
| `agentic-threads search <query> --type people` | `User` | Search accounts |
| `agentic-threads followers <user>` | `User` | Read accounts following a user |
| `agentic-threads following <user>` | `User` | Read accounts a user follows |

A user target may be an `@username`, username, numeric user ID, or profile URL. On `fetch`, use `--by username` or `--by id` to disambiguate an all-digit target. A post target may be a Threads post URL or numeric post ID. For `post`, the root object is written first and replies follow with reply relationship fields.

There is no aggregate `crawl` command. Callers compose these primitives themselves.

## Examples

The names and queries below are synthetic placeholders. Use only targets you are permitted to access.

```bash
# A bounded profile read to an explicit file outside the repository.
agentic-threads fetch synthetic_alice --limit 20 --output /tmp/threads-posts.json

# Include the profile's replies and keep newline-delimited output.
agentic-threads fetch synthetic_alice --replies --limit 20 \
  --format ndjson --output /tmp/threads-posts.ndjson

# Home feed and date-bounded post search.
agentic-threads feed --limit 20
agentic-threads search "synthetic topic" --since 2026-07-01 --limit 20

# A post without its reply thread, and account search.
agentic-threads post 1111111111111111111 --no-replies --limit 1
agentic-threads search "synthetic researcher" --type people --limit 10

# Social graph reads.
agentic-threads followers synthetic_alice --limit 20
agentic-threads following synthetic_alice --limit 20
```

## File-output model

Every read command:

- writes an array with `--format json` (default) or one object per line with `--format ndjson`;
- writes to `--output PATH`, or to a timestamped file under the platform user-data directory's `agentic-threads/output/` directory;
- writes no useful result data to stdout;
- prints one summary line to stderr with the object count, date range when applicable, stop reason, and saved path.
- commits each result through a same-directory temporary file and atomic replacement; on POSIX, newly created output directories use mode `0700` and result files use mode `0600`.

The default never writes into the current directory or repository. `Post` commands include nested `User` and `Media` objects; people search and social-graph commands emit `User` objects. Pinned profile posts do not consume the non-pinned `--limit`, so `fetch --limit N` can emit more than N rows. Run `agentic-threads schema --json` for the installed output contract and `agentic-threads catalog` for the installed flag surface.

Common read controls include `--format`, `--output`, `--limit`, `--profile`, `--profile-dir`, `--wait-on-limit`, `--max-wait`, `--raw`, `--no-redact`, and `-v`/`--verbose`. `feed`, `fetch`, and post search also accept `--since YYYY-MM-DD` and `--until YYYY-MM-DD`; people search rejects those post-only date flags.

`--raw` attaches the source GraphQL node for debugging. That raw field is redacted unless `--no-redact` is also given, which prints a warning. Do not share raw output.

## Pacing, rate limits, and exit status

A **non-bypassable 1.0-second minimum** is enforced between HTTP requests, regardless of caller or configuration. A lower requested delay is raised to 1.0 seconds. This floor reduces request volume; it does not make automation safe, authorized, or immune to rate limits and account challenges. Prefer explicit, shallow `--limit` values.

The CLI uses these stable exit meanings:

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Usage, identifier, cookie, browser, or unexpected error |
| 2 | Login required, session expired, or account challenged/soft-locked |
| 3 | Rate-limited |
| 4 | Operation identifier drift or an unexpected response shape; run `doctor --refresh` or upgrade |
| 5 | Target unavailable, private, suspended, or deleted |
| 7 | A requested `--since` boundary could not be confirmed before retrieval stopped |

`--wait-on-limit` may wait up to `--max-wait`; it does not bypass pacing or platform controls. Challenges are surfaced to the user and are not automatically retried.

## Privacy and credential safety

Read output can contain third-party names, text, profile metadata, and media URLs. It is intentionally not redacted: only diagnostics and the optional `raw` field use the diagnostic redaction path. Treat every output file as personal data.

- Never commit output, raw captures, cookie exports, or profile directories.
- Review even redacted logs before attaching them to an issue; redaction is risk reduction, not certification.
- Keep captures outside repositories, restrict access, retain only what is necessary, and delete them promptly.
- Revoke the Instagram session if a credential file, cookie export, or machine may be compromised.
- Determine and honor the legal basis, consent, access, deletion, and retention duties that apply to your use.

See [SECURITY.md](https://github.com/tjdwls101010/Agentic-Threads/blob/main/SECURITY.md) for private vulnerability reporting and [DISCLAIMER.md](https://github.com/tjdwls101010/Agentic-Threads/blob/main/DISCLAIMER.md) for the full risk statement.

## v0.1 scope and non-goals

Version 0.1 is alpha software for low-volume, logged-in, read-only retrieval through the commands above. Threads' private web API can change without notice: operation identifiers rotate, response shapes drift, sessions expire, and results may become incomplete or unavailable. `doctor --refresh` can repair identifier drift; it cannot guarantee compatibility.

Deliberate non-goals for v0.1:

- no posting, replying, liking, reposting, following, unfollowing, direct messages, or any other write;
- no anonymous or logged-out mode;
- no batch crawler, daemon, scheduler, or mass-collection interface;
- no browser-driven read fallback;
- no profile media/reposts tabs, communities, notifications/activity, or insights;
- no bundled Claude Code skill. Skill work is a separate, later project after the package release.

## Contributing and license

Focused issues and pull requests are welcome; read [CONTRIBUTING.md](https://github.com/tjdwls101010/Agentic-Threads/blob/main/CONTRIBUTING.md). Report vulnerabilities privately as described in [SECURITY.md](https://github.com/tjdwls101010/Agentic-Threads/blob/main/SECURITY.md), never in a public issue.

MIT — see [LICENSE](https://github.com/tjdwls101010/Agentic-Threads/blob/main/LICENSE). The license covers the software, not permission to access Threads or any right to collect, retain, or share data.