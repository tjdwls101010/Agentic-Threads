# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-23

### Added

- Initial alpha release of the `agentic-threads` distribution and `agentic-threads` CLI for logged-in, read-only Threads retrieval.
- Base installation with `httpx` and `platformdirs`; optional `[browser]` support is isolated to browser setup and interactive login.
- Poll-based headed-browser login and browser-free Netscape, JSON, or cURL cookie import, with named profile storage.
- Session commands: `login`, `status`, `setup`, and `doctor`, including browser-free operation-identifier refresh and stale profile-operation recovery with `doctor --refresh`.
- Read primitives for the home `feed`, profile `fetch` with optional replies, a `post` and its reply thread, post or people `search`, `followers`, and `following`.
- Timestamped JSON-array and NDJSON file output, explicit `--output`, bounded and date-filtered retrieval controls, stop-reason summaries, typed exit codes, and atomic owner-only result-file replacement.
- Parser-derived `catalog` output and model-derived `schema` / JSON Schema output, both available offline without a login.
- Structured `Post`, `User`, and `Media` output objects, including optional redacted raw-node diagnostics.
- Offline, fixture-driven tests and release automation for the base wheel and optional browser dependency boundary.

### Security

- Enforced a non-bypassable 1.0-second minimum between HTTP requests.
- Stored profile directories with mode `0700` and session credentials with mode `0600`.
- Routed diagnostics through one redaction path while keeping full result files explicit and outside the repository by default.
- Added synthetic-fixture and private-vulnerability-reporting policies that prohibit live credentials, captures, and third-party personal data in the repository.

[Unreleased]: https://github.com/tjdwls101010/Agentic-Threads/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tjdwls101010/Agentic-Threads/releases/tag/v0.1.0
