# Contributing to agentic-threads

Thanks for considering a contribution. Keep patches focused, explicit, and consistent with the package's personal-scale, read-only design. Read [DISCLAIMER.md](DISCLAIMER.md) before any live work and [SECURITY.md](SECURITY.md) before reporting a vulnerability.

## Project scope

Good contributions include parser fixes, operation-identifier refreshes, clearer errors, privacy hardening, offline tests, and documentation for the existing single-target primitives.

The following are out of scope for v0.1:

- writes such as posting, liking, reposting, following, or messaging;
- anonymous/logged-out retrieval;
- batch crawling, schedulers, daemons, or mass collection;
- weakening or bypassing the 1.0-second request floor;
- browser-driven fallback reads or moving browser dependencies into the base install;
- media/reposts profile tabs, communities, notifications/activity, or insights;
- a bundled agent/Claude Code skill, which is deliberately deferred to a separate later project.

Discuss a new runtime dependency or a material scope change before implementing it. The base installation must remain limited to the HTTP/runtime dependencies and must not import `scrapling`; browser support stays lazy and optional behind `[browser]`.

## Development setup

Python 3.11 or newer is required.

```bash
git clone https://github.com/tjdwls101010/Agentic-Threads.git
cd Agentic-Threads
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Browser support is unnecessary for normal development and the offline suite. For explicitly authorized manual login work only:

```bash
.venv/bin/python -m pip install -e ".[dev,browser]"
.venv/bin/agentic-threads setup
```

Do not use a primary Instagram account for development.

## Offline tests and checks

The default test suite must be deterministic, fixture-driven, and offline: no network, no browser, no saved profile, and no environmental dependency on a logged-in account. Mock HTTP responses and timing where needed.

Run the focused offline checks before opening a pull request:

```bash
.venv/bin/python -m pytest tests --ignore=tests/live
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python scripts/check_fixtures_pii.py
```

CI runs lint, formatting, the PII fixture scan, and offline tests on macOS and Linux. It also installs the built base wheel without browser dependencies and exercises offline CLI commands. Do not add a test that contacts Threads, starts a browser, depends on a local profile, or imports `scrapling` from a base code path.

Add tests for observable behavior: response-envelope branches, pagination and cursor termination, limits/date bounds, identifiers, error mapping, redaction, permissions, exit codes, and catalog/schema drift. Avoid tests that merely restate a default or implementation detail.

## Never commit live captures, secrets, or PII

**A live response is not a fixture.** It can contain other people's names, usernames, text, profile metadata, graph relationships, signed media URLs, and hidden identifiers. Removing a few obvious values does not make a copied capture synthetic, and deleting it in a later commit does not remove it from Git history.

Every committed fixture must be hand-authored synthetic data:

- use unmistakably fake usernames, IDs, text, URLs, timestamps, and media hosts;
- preserve only the minimum structural keys needed by the test;
- never copy a real response and mutate it into a fixture;
- review fixture diffs manually; the PII scanner is a backstop, not certification;
- un-ignore each approved fixture by exact filename, never with a broad wildcard.

Never commit or attach any of the following anywhere in the project:

- `session.json`, browser profiles, cookies, cookie exports, tokens, authorization headers, or environment secrets;
- result JSON/NDJSON, raw GraphQL bodies, screenshots of real content, or `--raw --no-redact` output;
- real usernames, user/post IDs, names, text, profile links, relationship lists, or media URLs.

The same rule applies to issue bodies, pull-request descriptions, commit messages, test logs, and chat. Diagnostic redaction can miss free text and novel secrets; manually inspect and reconstruct examples with synthetic values.

A maintainer may need a narrowly scoped live observation after an upstream response-shape change. Such work belongs only in the gitignored `scratch/` area, must use a disposable account, and must never enter Git. Record only shape and invariants in the patch, create a fresh synthetic fixture by hand, and delete the live material when the observation is complete.

## Live tests and responsible research

Live tests under `tests/live/` are opt-in and must never run in CI. They use a disposable account controlled by the researcher and assert shapes or invariants only — never specific people, posts, counts, or text. They must not save response bodies as test artifacts.

Any live work must be authorized, minimal, and read-only:

- use only an account you control and are prepared to lose;
- set shallow limits and preserve the non-bypassable 1.0-second request floor;
- stop on a rate limit, checkpoint, login challenge, unexpected private data, or platform instability;
- do not solve challenges automatically, rotate accounts or networks, replay another person's credentials, probe access controls, or stress Meta infrastructure;
- collect no more data than the specific parser or protocol question requires, and delete it promptly.

Contributing to this package does not authorize research against Meta, Instagram, Threads, or their users. Follow applicable terms, law, institutional rules, and coordinated-disclosure norms.

## Making a change

1. Open an issue for a non-trivial change so scope and evidence can be agreed before implementation. Security issues are the exception: report them privately under [SECURITY.md](SECURITY.md).
2. Branch from `main` and keep the diff traceable to one problem. Do not reformat or refactor unrelated code.
3. Add a failing offline test that demonstrates the behavior, then make the smallest change that passes it.
4. Preserve derived contracts: `catalog` comes from the argument parser and `schema` from model serialization. Fix their source rather than maintaining a parallel transcription.
5. Run the offline tests, lint, format check, and PII scanner. State exactly what ran; never imply that live verification occurred when it did not.
6. Update user-facing help, root/wiki documentation, and `CHANGELOG.md` under `Unreleased` when behavior changes. Do not bump release versions in an ordinary pull request.
7. Open a pull request explaining the problem, approach, security/privacy impact, and verification. Say explicitly whether any authorized live check was performed and confirm that no live data was retained or committed.

## Style

- Target Python 3.11 and follow the existing Ruff configuration (100-column line length).
- Match surrounding conventions and use type hints on public functions.
- Prefer boring, explicit code over speculative abstraction.
- Comments should explain constraints and reasons, especially around pacing, privacy, credential storage, and dependency boundaries.
- Do not suppress tests or warnings to make a check pass.

## Bugs, features, and security

Use the [public issue tracker](https://github.com/tjdwls101010/Agentic-Threads/issues) for sanitized bug reports and in-scope feature discussions. Include the package/Python/OS versions, command and exit code, expected behavior, and a fully synthetic reproduction. Never attach a result file or credential.

Report vulnerabilities only through the private process in [SECURITY.md](SECURITY.md). By participating, you agree to follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
