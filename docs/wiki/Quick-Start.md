# Quick Start

This walkthrough takes `agentic-threads` v0.1 from login to one small, read-only result. It assumes Python 3.11+ and an isolated installation from the [Installation](Installation.md) page.

> **Use a dedicated, throwaway Instagram account that you are willing to lose.** Threads automation can violate platform terms or trigger a checkpoint, restriction, or ban. Read [DISCLAIMER.md](../../DISCLAIMER.md) first. Do not repeatedly retry a challenged account.

## Results go to a file

Every retrieval command writes JSON or NDJSON to a file. It prints a one-line summary to stderr, not result data to stdout, so piping a retrieval command directly into another program will not provide the records.

With `--output PATH`, you choose the file. Without it, `agentic-threads` creates a timestamped file under its platform data directory's `output/` folder, never in the current directory or repository. The summary tells you the saved path and why retrieval stopped.

## 1. Create a session

Choose one of the following login paths.

### Headed browser login

This path requires the `[browser]` extra and its isolated browser setup:

```bash
agentic-threads setup
agentic-threads login
```

A real, visible browser opens at Threads. Complete **Continue with Instagram**, including any 2FA or challenge, in that window. The CLI polls the page and cookies for a completed login and continues automatically; do not press Enter or wait for a terminal prompt. The default timeout is 300 seconds and can be changed when necessary:

```bash
agentic-threads login --timeout-seconds 600
```

The browser is only a login and session-harvesting tool. After login, retrieval, `status`, and `doctor` use HTTP and do not launch a browser.

### Cookie import

The base install can import a logged-in Threads session without installing a browser:

```bash
agentic-threads login --cookies /secure/path/threads-cookies.json
```

Netscape, JSON, and cURL-style cookie exports are accepted. The export and the saved profile both contain password-less access to the account. Keep them out of repositories and synced folders, restrict who can read them, and securely remove the export after confirming the import. Logging out or revoking the session at Instagram/Threads is the reliable response if a credential may have been exposed.

## 2. Check the session

Run the inexpensive session check first:

```bash
agentic-threads status
```

`status` performs one authenticated HTTP read. Exit code `0` means the session is ready, `2` means login is required or the session is no longer usable, and `3` means rate-limited. For scripts, use:

```bash
agentic-threads status --json
```

For a deeper authenticated round trip, run:

```bash
agentic-threads doctor
```

`doctor` is still browser-free. If a read reports that Threads' GraphQL document IDs or response shape have drifted, try the HTTP-only re-anchor check once:

```bash
agentic-threads doctor --refresh
```

If the account is challenged or rate-limited, stop rather than looping these commands. Clear the challenge through the normal Threads or Instagram interface before creating a new session.

## 3. Make a bounded first read

Start with a small public institutional profile, an explicit limit, and an output path in the operating system's temporary directory. This example uses NASA's public profile rather than a private individual:

```bash
OUT="${TMPDIR:-/tmp}/agentic-threads-first-read.json"
agentic-threads fetch nasa --limit 3 --output "$OUT"
python -m json.tool "$OUT"
```

`fetch` accepts a username, `@username`, numeric user ID, or Threads profile URL. The command writes a JSON array to `$OUT`; only its completion summary is printed by the CLI. Read that summary as well as the file: `limit_reached` means more data existed but the requested bound stopped the run, while an exhaustion reason means the available connection ended.

This is deliberately a single target and three records. The client enforces at least 1.0 second between HTTP requests. Do not work around that floor, add concurrent requests, or turn the example into an unattended crawl.

## 4. Handle the output as personal data

Normal result files are not anonymized. Posts and user objects can contain names, usernames, text, timestamps, profile metadata, and media URLs belonging to other people. For the first read and later work:

- keep captures outside source repositories and cloud-synced directories;
- do not commit, publish, paste into issues, or convert live captures into test fixtures;
- request only the records needed for the immediate task;
- avoid `--raw` and `--no-redact` unless debugging requires them; and
- delete `$OUT` when the inspection is complete.

Diagnostic redaction does not sanitize the result file itself. The full legal, account, credential, and privacy guidance remains in [DISCLAIMER.md](../../DISCLAIMER.md).

## Other read primitives

Once the bounded fetch works, `agentic-threads catalog` gives the authoritative command and flag list. The v0.1 read surface includes:

- `feed` for the logged-in home feed;
- `fetch USER --replies` for a profile's posts and replies;
- `post URL_OR_ID` for one post and its reply thread (`--no-replies` for only the root post);
- `search QUERY --type posts` or `--type people`; and
- `followers USER` and `following USER` for user records.

Each remains a single-target, file-writing HTTP operation. The tool performs no writes to Threads and has no browser-based retrieval path.

---

Back to the [wiki index](README.md) or the [project repository](https://github.com/tjdwls101010/Agentic-Threads).
