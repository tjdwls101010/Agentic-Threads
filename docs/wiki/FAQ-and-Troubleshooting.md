# FAQ and Troubleshooting

Use the exact exit code and stderr stop reason to diagnose a run. Read commands save their data to a file; stdout is intentionally empty.

## FAQ

### Is `agentic-threads` read-only?

Yes. The v0.1 command surface reads feeds, profile posts, a post/reply thread, live search results, followers, and following. It does not publish, edit, delete, like, repost, follow, unfollow, or message. It also has no multi-target batch, crawler, daemon, or scheduler command.

The tool does write local session state and result files. “Read-only” means it does not intentionally mutate Threads state.

### Can it read Threads without logging in?

No. Every v0.1 read requires a saved authenticated profile. Anonymous operation is unsupported. Run `login`, then confirm with `status`.

### Do read commands open a browser?

No. `setup` provisions the optional browser, and interactive `login` uses it. Once a profile exists, `status`, `doctor`, `doctor --refresh`, and all retrieval commands use HTTP. There is no browser fallback for a failed read.

`login --cookies FILE` imports a supported cookie export without opening a browser.

### Where did the results go?

Read commands do not stream results to stdout. They write JSON or NDJSON to `--output`, or by default to:

```text
platformdirs.user_data_dir("agentic-threads")/output/
```

The stderr summary prints the exact saved path. Do not pipe a read command directly into `jq`; run it, then pass the reported file to your JSON tool. See [Configuration](Configuration.md).

### Which commands return Posts and which return Users?

- `feed`, `fetch`, `post`, and `search --type posts` return `Post`.
- `followers`, `following`, and `search --type people` return `User`.
- `Media` occurs only inside `Post.media`.

See [Output Schema](Output-Schema.md).

### Why do `post` and `fetch` have opposite reply defaults?

Threads' single-post operation returns the root and reply thread together, so:

```bash
agentic-threads post <url-or-id>                # root plus replies
agentic-threads post <url-or-id> --no-replies   # root only
```

A profile's Threads and replies tabs are separate operations, so:

```bash
agentic-threads fetch <user>             # Threads tab only
agentic-threads fetch <user> --replies   # also include replies-tab posts
```

For `post`, the root object is first and later reply objects have `is_reply: true`.

### Does `search --type posts` search a local index?

No. It calls Threads' live GraphQL keyword-search operation for each run. `--since` and `--until` are applied client-side to the returned Posts; they are not passed as server-side search constraints. A sparse or deep date window can therefore require several pages and can end with exit `7` if `--since` was not confirmed.

People search uses a separate live account-search operation and returns Users, which have no event-date field.

### What does a successful empty search look like?

A structurally valid search with no hits is not an error. It writes an empty JSON array (or no NDJSON records), reports stop reason `no_matches`, and exits `0`.

### Will this protect my account from a checkpoint or ban?

No. The non-bypassable 1.0-second delay before every request (including the first), single-target commands, and request budget reduce obvious high-volume behavior; they do not make automation safe or compliant. Use a dedicated throwaway Instagram account, keep volume low, and read [DISCLAIMER.md](../../DISCLAIMER.md).

### Can I run it in a loop or on many targets at once?

Do not. There is no built-in batch, crawl, daemon, or scheduler mode. Wrapping commands in concurrent loops works around the personal-scale guardrails and raises rate-limit/checkpoint risk. Run one deliberate target at a time.

### How do I interpret graph stop reason `empty_pages`?

`followers` and `following` can encounter repeated cursor pages that contain no accounts. The run stops rather than consuming its entire request budget. `empty_pages` means **the tool gave up and the result is incomplete**; it does not mean the account's graph ended.

### How do I revoke the saved login?

End the corresponding Threads/Instagram session through Meta's account-session controls, then delete the local named profile. Removing only `session.json` does not revoke copies of a session that Meta still accepts. Protect cookie exports and the browser profile too. See [Security and Privacy](Security-and-Privacy.md).

## Troubleshooting

### `agentic-threads setup` fails or the browser is missing

Browser login requires the optional `[browser]` extra and an isolated browser provisioned by `setup`.

1. Confirm the package was installed with its `[browser]` extra.
2. Check network access and available disk space for the Chromium download.
3. Retry a partial/corrupted installation with:

   ```bash
   agentic-threads setup --force
   ```

4. If browser installation is unavailable, `login --cookies FILE` can import a session without a browser. Retrieval itself does not need the browser extra.

`setup` exits `0` on success and `1` on failure.

### The browser opens, but `login` does not complete

Interactive login is visible and poll-based. Complete the official Instagram/Threads flow in the browser; do not wait for or press Enter in the terminal. The default timeout is 300 seconds and can be changed with `--timeout-seconds`.

Check these cases:

- The login form or “Continue with Instagram” flow is not fully complete.
- A 2FA, consent, or checkpoint page still needs manual action.
- Required session cookies (`sessionid`, `ds_user_id`, and `csrftoken`) were never established.
- The timeout elapsed before the logged-in state became detectable.

A browser login that cannot be verified exits `2`. Other browser/setup failures exit `1`. Do not share screenshots containing cookies, tokens, account identifiers, or challenge details.

### `login --cookies` rejects my file

The import path accepts JSON arrays of cookie objects, flat or wrapped JSON cookie objects, Netscape-format cookie files, and raw `Cookie:`/cURL cookie strings. It validates that the required Threads/Instagram session cookies are usable before saving anything.

Export again in one of those formats. Do not edit, paste, or normalize live token values in a public tool. The source export remains a live password-less credential after a successful import and should be secured or removed.

### `status` exits `2` immediately after login

`status` performs a real authenticated probe; it does not merely check that `session.json` exists. Meta can return HTTP 200 with an empty or malformed body for an expired or soft-locked session, so a recently saved file is not proof that the session is usable.

1. Stop repeated reads.
2. Check the account in the official Threads/Instagram UI for a checkpoint or forced re-authentication.
3. Resolve it manually.
4. Run `agentic-threads login --profile <name>` again.
5. Recheck the same profile with `status`.

Also confirm `--profile`, `--profile-dir`, and `AGENTIC_THREADS_PROFILE_DIR` resolve to the profile you intended.

### Threads or Instagram shows a checkpoint/challenge

A checkpoint is not an ordinary transient error. It is surfaced as exit `2` because the authenticated session is unusable, and the package never automatically retries `ChallengeError`.

- Stop the command and any wrappers around it.
- Do not hammer `status`, `login`, or retrieval in a retry loop.
- Complete the checkpoint manually in the official Meta flow.
- Allow the account to settle, then run `login` and one `status` probe.
- If challenges recur, reduce frequency/depth and discontinue use of that account rather than attempting to bypass the checkpoint.

### A command exits `3` or reports `rate_limited`

Threads returned a rate limit before the run completed. The saved file may contain valid partial results; inspect its stop reason and count.

Default behavior is to stop. For a deliberate long run, `--wait-on-limit` allows the command to wait when the response provides a usable reset time. `--max-wait SECONDS` is a hard cap: if the required wait exceeds it, the command stops as `rate_limited` instead of sleeping partially.

Example:

```bash
agentic-threads fetch synthetic_alice \
  --limit 50 \
  --wait-on-limit \
  --max-wait 300
```

Waiting does not bypass the 1.0-second request floor and does not guarantee success. Repeated 429s mean lower the limit, narrow the date window, wait longer between invocations, and avoid concurrent processes.

### A command exits `4` or reports envelope/`doc_id` drift

Threads uses persisted GraphQL operation identifiers (`doc_id`) that rotate. Exit `4` means the response no longer matches the expected operation envelope; it is not the same as a valid empty result.

Run:

```bash
agentic-threads doctor --profile <name> --refresh
```

`doctor` first checks an authenticated round trip. `--refresh` then re-anchors operation identifiers from Threads' JavaScript bundle over HTTP and saves them to that profile; it does not open a browser.

Retry the failed read once. If exit `4` persists after a successful refresh, the response structure itself may have changed or the installed version may be stale. Upgrade to a compatible release or file a synthetic, redacted report. Do not keep blind-retrying and do not attach a live capture.

### `doctor --refresh` fails

Check in this order:

1. `status` for the same profile. Exit `2` means log in again before diagnosing operation drift.
2. Network access to `www.threads.com` and its JavaScript bundle.
3. A checkpoint or rate limit in the official UI.
4. The exact profile root selected by `--profile-dir` or `AGENTIC_THREADS_PROFILE_DIR`.
5. Whether the installed package is current.

`doctor` exits only `0` for success or `1` for failure; its redacted message carries the reason.

### A command exits `5`

The target user/post is unavailable: nonexistent, private, suspended, deleted, or otherwise not visible to the logged-in profile. This is a target result, not a request to retry identifiers rapidly. Confirm the input form once, then treat the target as unavailable.

Accepted profile forms are `@username`, bare username, numeric id, and profile URL. `fetch --by {username,id}` resolves ambiguity for all-digit profile identifiers. `post` accepts a post URL/shortcode or numeric post id.

### A command exits `7`

You requested `--since`, but pagination stopped before the run confirmed that the lower boundary was reached. The saved Posts are real but may not cover the requested window completely.

Read the stop reason:

- `max_requests`: the run's request budget ended first;
- `rate_limited`: the rate-limit outcome takes precedence and is normally exit `3`;
- another incomplete reason means the source did not prove the date boundary.

Narrow the window, use a smaller deliberate target, or accept the result as partial. Do not report it as a complete historical set.

For post search specifically, remember that date bounds are client-side over live GraphQL pages, so reaching an old lower boundary may require more pages than the result count suggests.

### `followers` or `following` returns fewer accounts than expected

Check the stop reason before assuming the list is complete:

- `limit_reached`: your requested limit stopped the run;
- `empty_pages`: repeated empty pages caused a safety stop;
- `rate_limited`: the run stopped on a 429;
- `max_requests`: the per-run budget ended pagination;
- `no_next_page`/`feed_exhausted`: the connection reported an actual end.

Only the last category establishes exhaustion.

### `post` returns replies when I wanted one object

Replies are the default for `post`. Use:

```bash
agentic-threads post <url-or-id> --no-replies
```

Do not use `--replies`; that flag belongs to `fetch` and changes the profile tabs it reads.

### My bug report needs a raw response

Start with `--raw`, not `--no-redact`. The attached raw GraphQL node is scrubbed by default. Review it manually and reduce it to a hand-authored synthetic example before sharing.

Never report using `--raw --no-redact`. It disables raw-node redaction and can expose session-adjacent values, full third-party text, identifiers, and signed media URLs. Diagnostic redaction reduces risk but is not a guarantee; inspect all output yourself.

### Exit-code quick reference

| Code | Meaning |
|---|---|
| `0` | Success, including a genuine empty search. |
| `1` | Usage/identifier/setup/import/unexpected error. |
| `2` | Login required, expired/soft-locked session, or checkpoint/challenge requiring human action. |
| `3` | Rate-limited. |
| `4` | `doc_id` or response-envelope drift. |
| `5` | Target unavailable. |
| `7` | `--since` boundary not confirmed; partial result. |

There is no code `6`. See [CLI Reference](CLI-Reference.md#exit-codes) for the normative contract.
