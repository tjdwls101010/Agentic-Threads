# agentic-threads wiki

`agentic-threads` v0.1 is a read-only command-line client for Threads. It acquires a logged-in session once through a headed browser or a cookie import, then makes all reads as plain `httpx` requests to Threads' GraphQL endpoint. The optional browser is used only for setup and interactive login; reads never launch a browser and have no browser fallback.

The CLI provides single-target primitives for the home feed, profile posts and replies, one post and its reply thread, post or people search, followers, and following. It does not post, reply, like, follow, or crawl targets on its own. Requests are paced with a non-bypassable 1.0-second minimum interval.

> **Read [DISCLAIMER.md](../../DISCLAIMER.md) before logging in.** Use only a dedicated, throwaway Instagram account that you are willing to lose. Threads automation can violate platform terms and trigger restrictions. Retrieved posts, profiles, and media metadata can contain third-party personal data; keep output outside repositories, do not commit or publish captures, and delete it when the task is complete.

## Getting started

- [Installation](Installation.md) — Python 3.11+, isolated installation, the base package versus the `[browser]` extra, and isolated browser setup
- [Quick Start](Quick-Start.md) — browser or cookie login, `status`, `doctor`, a bounded first read, and where the result is saved

## Runtime reference

The installed CLI is its own authoritative reference:

```bash
agentic-threads catalog
agentic-threads schema
```

`catalog` describes the available commands, arguments, output types, and exit codes. `schema` prints the JSON Schema for the `Post`, `User`, and `Media` objects. Both commands are offline and do not require a login.

Read commands write JSON or NDJSON to a file and print only a one-line summary to stderr. With no `--output`, the file is timestamped under `agentic-threads`' platform data directory rather than the current directory. This keeps captures away from a source checkout by default, but the files still contain unredacted result data and must be handled as potentially sensitive.

## Project

- [Repository](https://github.com/tjdwls101010/Agentic-Threads)
- [Main README](../../README.md)
- [Disclaimer](../../DISCLAIMER.md)

This `docs/wiki/` directory is tracked with the repository; it is not GitHub's separate Wiki feature.
