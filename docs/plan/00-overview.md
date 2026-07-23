# Agentic Threads — Plan Overview

> Planning session: 2026-07-23 (Korean interview, English artifacts). This directory
> is the full, self-contained plan for a **separate implementation session**. Read it
> in order: `00` (this) → `01-decisions` → `02-recon-findings` → `03-architecture` →
> `04-cli-spec` → `05-testing-and-ci` → `06-implementation-phases` → `07-skill-plan` →
> `IMPLEMENTATION-KICKOFF`.

## What this is

A read-only Threads reader: **log in once with a throwaway Instagram account, then read
Threads over plain HTTP** — a profile's posts, your home feed, a post and its reply
thread, search (posts + people), and the social graph (followers / following). Output
is clean, schema'd JSON written to a file. It is the third sibling of an existing
family:

- `Agentic Facebook` (`agentic-facebook`, PyPI `agentic-facebook`)
- `Agentic X` (`agentic-x`, PyPI `agentic-twitter`)
- **`Agentic Threads` (`agentic-threads`, PyPI `agentic-threads`)** ← this project

Each is a **CLI of single-target primitives** plus a **Claude Code skill** that chains
those primitives to answer multi-hop questions ("what is X's circle discussing?").
The CLI does fast structured retrieval; the *skill* (i.e. Claude) does the navigation
reasoning. There is deliberately no `crawl` command.

## Why a CLI and not browser-use / WebFetch

- **WebFetch / WebSearch**: only surfaces the sliver of Threads that portals index, with
  no schema.
- **browser-use (visual)**: slow (screenshot-observe loops) and can't return clean,
  structured post/author/date/comment fields.
- **This CLI**: hits Threads' own GraphQL backend and returns a defined schema in
  milliseconds per request. LLMs are language machines; hand them language-shaped data.

## The central finding (recon-proven, see `02-recon-findings.md`)

**Threads is a Meta property and its web backend is structurally the same as Facebook's,
NOT X's.** Live capture on 2026-07-23 confirmed:

- GraphQL over `POST https://www.threads.com/graphql/query` with `doc_id` persisted
  queries + `fb_dtsg` / `lsd` tokens + a `sessionid` (HttpOnly) session cookie — i.e.
  the **Facebook mechanism**.
- **No `x-client-transaction-id` wall.** X's single most fragile, rot-prone subsystem
  (`transaction.py`, per-request reverse-engineered signed header) has **no analogue
  here** and must not be ported.

**Consequence:** `Agentic Facebook` and `Agentic X` are both templates, but the split is:
- Copy **`Agentic X`'s shape** (httpx-primary "harvest-then-replay", browser only at
  login behind a `[browser]` extra, the whole packaging/CLI/catalog/schema/skill
  scaffold) — because Threads reads are clean GraphQL with no txid wall.
- Copy **`Agentic Facebook`'s Meta-GraphQL specifics** (doc_id registry, `fb_dtsg`/`lsd`/
  `jazoest` request body, relay-provider-flags-are-required, token refresh-over-http) —
  because the wire format is Facebook's.

Threads is therefore the **lowest-risk of the three** to build.

## Goals (v1)

1. `login` (headed stealth browser, poll-based wait) + `--cookies` import; `status`;
   `setup`; `doctor` (+ `--refresh`).
2. Read primitives, all writing schema'd JSON to a file: `feed`, `fetch <user>`
   (+`--replies`), `post <url|id>`, `search <query>` (`--type posts|people`),
   `followers <user>`, `following <user>`.
3. `catalog` (self-describing CLI, generated from the parser) + `schema` (output
   object schema, generated from the model).
4. Non-bypassable **1.0s** inter-request rate floor. PII discipline. Typed errors +
   an exit-code contract.
5. Ship to PyPI via GitHub Actions Trusted Publishing (the pending publisher is already
   configured: repo `tjdwls101010/Agentic-Threads`, workflow `publish.yml`).

## Non-goals (v1)

- **No writes** — no posting, liking, following, reposting, DMs. Read-only.
- **No anonymous/logged-out reads** — login required (logged-out 200s were observed but
  are out of scope; architecture may leave the door open, see D5).
- **No `crawl`/batch/daemon** — single-target primitives only; chaining is the skill's job.
- **No `media`/`reposts` profile tabs, no communities, no notifications/activity, no
  insights** in v1 (candidate v1.1+).
- **No `transaction.py`-style header generator** — Threads has no txid wall.
- **The Claude skill is built in a later session**, after the package is on PyPI
  (see `07-skill-plan.md`), using the `harness-creator` skill.

## Success criteria

Every implementation phase in `06-implementation-phases.md` carries an explicit verify
gate. The package is "done" for v1 when: all read primitives return schema-valid JSON
against a live throwaway session; unit tests are green offline (no network/browser in
CI); the base wheel imports with no `scrapling`; and `agentic-threads` publishes to PyPI
on a GitHub Release.
