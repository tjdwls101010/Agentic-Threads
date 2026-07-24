# Security and Privacy

Read [DISCLAIMER.md](../../DISCLAIMER.md) before using `agentic-threads`. This page explains the storage and redaction mechanics; it is not legal advice and does not make automated access safe or compliant.

`agentic-threads` is a local, read-only tool, not a hosted service. It still carries three material risks:

1. the saved profile is a live, password-less Meta session;
2. automated access can trigger a Threads/Instagram checkpoint, suspension, or ban; and
3. output contains identifiable third-party data that you are responsible for securing and deleting.

Use a dedicated throwaway Instagram account, never an account you care about.

## Scope and transport boundaries

The v0.1 package reads only. It does not publish, like, repost, follow, unfollow, message, or mutate Threads state. It accepts one target per invocation and does not provide batch, crawl, daemon, or scheduler modes.

All retrieval commands require a saved login and use paced HTTP requests to Threads' live GraphQL endpoint. Logged-out/anonymous retrieval is not supported. The optional browser is restricted to `setup` and interactive `login`; retrieval does not fall back to browser automation.

These boundaries reduce the available abuse surface. They do not make the activity invisible to Meta or remove Terms of Service, account, or legal risk.

## The saved profile is a live credential

Interactive login or `login --cookies FILE` produces a profile credential containing session material such as:

- `sessionid`, `ds_user_id`, and `csrftoken` cookies;
- the harvested user agent;
- harvested `doc_id` and Relay feature maps; and
- an extraction timestamp.

The default location is:

```text
platformdirs.user_data_dir("agentic-threads")/profiles/<name>/session.json
```

A browser login also keeps its persistent browser context under `profiles/<name>/browser/`. The profile directory is created with owner-only `0700` permissions, and `session.json` is written atomically with `0600` permissions where the operating system supports those modes.

Anyone who obtains usable session material may be able to access Threads as that account without its password or a fresh 2FA prompt. Filesystem permissions protect against ordinary local users; they do not protect against root access, malware, a compromised backup account, or physical disk access.

### Profile handling rules

- Do not put a profile under a synced folder, repository, shared volume, support bundle, or cloud backup.
- Keep `session.json`, cookie exports, browser profile files, and raw captures out of version control.
- `AGENTIC_THREADS_PROFILE_DIR` and `--profile-dir` change the storage location, not the sensitivity or permissions requirement.
- If a device or credential may be compromised, revoke the session from Meta/Instagram's account-session controls and then remove the local profile. Deleting only the local file does not invalidate server-side copies of that session.
- Each `--profile NAME` is an independent live credential and must be protected separately.

See [Configuration](Configuration.md) for path resolution.

## Cookie import is sensitive

`agentic-threads login --cookies FILE` accepts JSON cookie arrays, flat or wrapped JSON cookie objects, Netscape cookie files, and raw `Cookie:`/cURL cookie strings. Import avoids opening a browser, but it does not make the credential safer.

- The source export remains a live credential after import. Securely remove or protect it.
- Do not paste a cookie line into an issue, chat, shell history, screen recording, or CI secret log.
- Import failures must describe the structural problem without echoing raw cookie values.
- Use a throwaway account and import from the same trusted device/network context where practical; abrupt client or network changes can increase checkpoint risk.

## Redaction boundaries

The package uses one recursive redaction path for diagnostics. Redaction covers:

- verbose errors and other diagnostic output;
- login, status, doctor, setup, and retrieval error messages;
- cookie-import parse/validation diagnostics; and
- the GraphQL node attached by `--raw`, unless `--no-redact` is explicitly present.

The scrubber targets known sensitive keys such as `sessionid`, `csrftoken`, `ds_user_id`, `cookie`, token-shaped keys, and `authorization`. It truncates known free-text diagnostic fields and removes signing query strings from recognized `cdninstagram.com` and `fbcdn.net` URLs.

### What redaction does not cover

The normal parsed result file is deliberately **not anonymized or redacted**. It contains the posts, usernames, names, biography text, counts, URLs, and media metadata requested by the command. Redacting those fields would defeat the retrieval contract.

Redaction is pattern-based. It can miss a new or unexpected sensitive field, a token under an unfamiliar key, or personal data embedded in an unrecognized structure. Treat redaction as accidental-leak reduction, not a certification that content is safe to publish.

## `--raw` and `--no-redact`

`--raw` attaches the underlying GraphQL node to each output object. It exists only for diagnosing parser or response-shape drift. By default that attached node is recursively redacted before it is written.

`--no-redact` matters only with `--raw`. It disables scrubbing for the attached raw node and always prints a warning. The resulting file can contain session-adjacent fields, complete third-party text, identifiers, and signed media URLs.

Use `--raw --no-redact` only locally for the shortest necessary time. Never:

- attach it to a public or private issue without manual review and anonymization;
- paste it into a chat or model prompt;
- commit it, even temporarily;
- keep it as a test fixture; or
- assume deleting a query string or username makes the rest anonymous.

For bug reports, prefer redacted `--raw`, inspect it manually, then reduce the issue to a hand-authored synthetic skeleton.

## Third-party data

Every result can contain data about people other than the logged-in user: authors, repliers, quoted/reposted authors, followers, and followed accounts. The default output directory reduces accidental repository commits, but it is not a security boundary. Result files are atomically written with owner-only `0600` permissions where POSIX modes are supported, but normalized content remains sensitive third-party personal data and must still be protected.

Practical handling rules:

- collect only what is necessary for a specific, personal-scale task;
- use shallow limits instead of archival pulls, remembering that `--limit N` strictly caps `User` and non-pinned `Post` objects while preserved pinned profile posts may make total `Post` rows exceed `N`;
- restrict access to output directories;
- do not publish, resell, redistribute, or upload captures;
- honor applicable deletion/retention obligations; and
- delete output and raw captures promptly when the task ends.

Depending on jurisdiction and use, collecting identifiable data can make you responsible for data-controller or similar privacy obligations. The MIT license covers the software, not the lawfulness of data collection or downstream use.

## Synthetic and offline development discipline

Repository fixtures and examples must be hand-authored, synthetic, and PII-free. Use reserved domains such as `example.test`, fake usernames such as `synthetic_alice`, and non-live token skeletons. Real responses belong only in temporary, ignored scratch locations and must never be committed.

Tests and CI are designed to be offline. Do not make routine verification depend on a real account, browser, network, or capture. A PII scanner is an additional guardrail, not a substitute for human review.

## Pacing, rate limits, and checkpoints

Every HTTP read enforces a non-bypassable **1.0-second minimum delay before every request, including the first**. No flag, environment variable, or alternate entry point may lower it. One target per invocation and the per-run request budget further limit volume.

This floor is a minimum pacing guardrail, not a guarantee against rate limits or checkpoints. Multiple concurrent processes can still create unsafe aggregate traffic. Do not wrap commands in a retry loop, cron job, or high-volume orchestration.

When Threads rate-limits a run:

- without `--wait-on-limit`, the command stops with exit `3` and a `rate_limited` stop reason;
- `--wait-on-limit` may wait and continue when the response provides enough information;
- `--max-wait SECONDS` is a hard cap: when the required reset wait exceeds it, the run stops as `rate_limited` rather than sleeping partially; and
- repeated 429s mean reduce frequency and depth, not launch more sessions.

A Meta checkpoint/challenge is different from an ordinary rate limit. The tool never automatically retries a challenge. Stop requests, resolve the checkpoint manually in the official Threads/Instagram flow, allow the account to settle, and log in again. Repeated automated retries can turn a temporary checkpoint into a longer restriction or permanent loss.

## `doc_id` refresh is not a bypass

Threads rotates persisted GraphQL `doc_id` values. Exit `4` indicates that the response no longer matches the expected envelope, commonly because those identifiers drifted. `agentic-threads doctor --refresh` re-anchors operation identifiers from Threads' public JavaScript bundle over HTTP; it does not bypass authentication, rate limits, checkpoints, target privacy, or response-shape changes.

If a refresh does not resolve exit `4`, stop retrying and treat it as a compatibility problem that may require a package update.

## Reporting a security or parser issue

Include the installed version, operating system, exact command with identifiers anonymized, exit code, stop reason, and redacted `-v` diagnostics. Never include:

- `session.json` or a browser profile;
- cookie-export contents;
- `sessionid`, `csrftoken`, `ds_user_id`, cookie, token, or authorization values;
- raw output produced with `--no-redact`;
- real profile/post captures; or
- signed CDN URL query strings.

Use a synthetic minimal reproduction and review even redacted diagnostics before sharing them.
