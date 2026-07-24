# Skill Plan — `.claude/skills/threads/` (later session)

Built **after** `agentic-threads` is on PyPI, in a **fresh session**, using the `harness-creator` skill. It wraps the *installed* CLI, not a repo checkout. Mirror `Agentic X`'s `.claude/skills/x/SKILL.md` and `.claude/harness-spec.md` — they are the closest template (httpx-primary, `[browser]` extra, doc_id rotation).

## Shape

- **Single file**: `.claude/skills/threads/SKILL.md`. No `references/`, no `scripts/` — a retrieval task needs all the rules together (the ban/PII stop-rules must not hide behind a conditionally-loaded file). Plus a `.claude/harness-spec.md` design record.
- **Frontmatter**:
  ```yaml
  name: Threads retrieval
  description: Read Threads via the agentic-threads CLI — a profile's posts, your home feed, a post and its reply thread, search (posts/people), or the social graph (followers/following) — and chain those to answer multi-hop questions. Use whenever the user wants something off Threads, however they phrase it … Also use when the user hands over a threads.com / threads.net URL. NOT for developing the agentic-threads package itself, and not for any other social network.
  allowed-tools: Bash(agentic-threads:*), Bash(uv:*), Bash(pipx:*), Bash(curl:*), Read
  ```
  Engineer the `description` to trigger on intent ("what has X posted on Threads", "check my Threads feed", "who follows X", a threads.com URL) and to name near-misses out of scope (developing the package = ordinary repo work; other networks). It must correctly **not** trigger on Instagram/Facebook/X requests (those have their own tools).

## Body (workflow, not a flag reference)

1. **Get the tool, and the *current* one.** Check `agentic-threads --version` against the PyPI **simple index** (`curl -s https://pypi.org/simple/agentic-threads/`), not the JSON API (it lags). If behind, say so in one line and upgrade first — Meta rotates `doc_id`s and the fix ships as a release, so a stale install presents as "Threads is broken", not "you're out of date". Install via `uv tool`/`pipx`, **never** shared `pip` (the `[browser]` extra pins Playwright). `agentic-threads setup` only if the browser is needed (login).
2. **`agentic-threads catalog`** — learn every command/flag/exit-code/output-type in one call. Work from what it says; never restate the flag list here (it would drift).
3. **`agentic-threads status`** — exit 0 ready; exit 2 needs a human at a browser (`agentic-threads login`, throwaway account). Claude cannot complete login; ask the user.
4. **The file trap** — every read command writes a JSON *file* and prints only a stderr summary. Always pass `--output`, then `Read` the file.
5. **Two object types** — most commands emit `Post`; `followers`/`following`/`search --type people` emit `User`. `captured_at` is scrape-time, not an event time. Run `agentic-threads schema` for the field list.
6. **What each primitive is for + chaining** — the judgment the catalog can't carry: `fetch`=one profile's posts (date-filterable), `feed`=your home feed (no target), `post`=a post + its thread, `search`=discovery (posts or people), `followers`/`following`=the graph. Chain via `author.username`→`fetch`/`followers`, `post id`→`post`. **Bound the fan-out before starting; report the shape of what you did.**
7. **`stop_reason`** semantics (`limit_reached`/`no_next_page`/`no_matches`/`empty_pages`="gave up, not finished"/`rate_limited`/`max_requests`) and the exit-7 `--since` caveat.
8. **Ban risk** — Instagram/Meta is aggressive about automation bans/checkpoints; throwaway account only, 1.0s floor is un-bypassable, don't fabricate concurrency by launching parallel processes; exit 3 = stop, don't retry-loop; exit 2 with a checkpoint is not retryable.
9. **Third-party data** — scraped output is other people's PII (esp. `followers`/`following`). Write to temp, never `git add`, delete when done, quote named individuals only when the question needs it. `--raw`/`--no-redact` are debug-only.
10. **Failure playbook** — exit 4 → `agentic-threads doctor --refresh` (or upgrade if a `doc_id` rotation shipped as a release); exit 2 → login; exit 5 → not retryable; "invalid choice" → out-of-date install (step 1). Most failures here are informative, not transient.

## harness-spec.md

Record: one skill (not per-command), single-file rationale, a behavior inventory, the `allowed-tools` reasoning, the **version-check-at-task-start** policy (like the X sibling — this package rots by design), and a live e2e validation log (target the sibling standard: scenarios pass, incl. correctly NOT triggering on repo-work or on Instagram/X/Facebook requests).
