# Testing, Packaging & CI

Mirror `agentic-x` almost verbatim; it is the cleaner scaffold and the httpx-primary
model matches ours.

## pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "agentic-threads"
version = "0.1.0"                      # bump per release; gated vs tag
requires-python = ">=3.11"
license = "MIT"
dependencies = [
    "httpx>=0.27",
    "platformdirs>=4.0",
]

[project.optional-dependencies]
browser = ["scrapling[fetchers]>=0.4.10,<0.5"]
dev     = ["pytest>=8", "ruff>=0.8", "pre-commit>=3.8", "build>=1.2", "jsonschema>=4.0"]

[project.scripts]
agentic-threads = "agentic_threads.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/agentic_threads"]

[tool.ruff]
line-length = 100
target-version = "py311"
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[project.urls]
Homepage  = "https://github.com/tjdwls101010/Agentic-Threads"
Issues    = "https://github.com/tjdwls101010/Agentic-Threads/issues"
Changelog = "https://github.com/tjdwls101010/Agentic-Threads/blob/main/CHANGELOG.md"
```

Base deps deliberately **exclude scrapling** (it's the `[browser]` extra). `jsonschema` is
a declared dev dep so the schema-validation test runs in CI (not `importorskip`'d away).

## Tests (`tests/`) — offline, fixture-driven; no network/browser in CI

- `conftest.py`: `load_fixture` returns a fixture file's bytes/JSON.
- `tests/fixtures/*.json`: **hand-authored synthetic, PII-free** GraphQL-response
  skeletons (fake usernames like `synthetic_alice`, fake numeric ids), one per shape:
  `feed`, `profile_threads`, `post_detail_with_replies`, `keyword_search`,
  `account_search`, `followers`, plus edge cases (carousel media, quoted/reposted post,
  reply, missing date, private/unavailable). Un-ignored in `.gitignore` by **exact name**,
  never a wildcard. Real captures live in a gitignored `scratch/` and are never committed.
- Unit test files (all offline, mock bytes not the network):
  - `test_parse.py` — per-op envelope roots, Relay connection walk, `page_info`/cursor
    EOF, `EnvelopeParseError` on structural failure, decoy protection.
  - `test_model.py` — `build_post`/`build_user`, ISO-Z serialization, `raw` only-when-set,
    schema/`to_dict` parity, every key has a description, `jsonschema` validation of
    fixtures against `json_schema()`.
  - `test_client.py` — rate floor **cannot** be bypassed (even on a single request);
    body carries `doc_id` + friendly name (+ `jazoest`/`fb_dtsg` per Q-A); header set;
    error mapping (429→RateLimited, 401/soft-lock→SessionExpired, non-200→parse/other).
    Uses a `_FakeClient` that no-ops `_throttle` for the pagination tests.
  - `test_tokens.py` *(if Q-A)* — `fb_dtsg`/`lsd` extraction, `jazoest` compute,
    staleness, refresh-over-http.
  - `test_retrieve.py` — a `FakeReadClient` returning canned pages drives dedup, cursor
    EOF, limit/since/until composition, stop-reason vocabulary, `empty_pages`,
    soft-lock probe.
  - `test_auth.py` — cookie-file parsing (3 formats), identifier normalization
    (`@`/username/numeric/URL, `--by`), shortcode↔postID decode.
  - `test_redact.py`, `test_cli.py` (exit-code map + catalog coverage + schema output),
    `test_no_scrapling_import.py` (subprocess: `import agentic_threads[.cli/.auth/.session]`
    never pulls in scrapling), `test_catalog.py`.
- `tests/live/` (opt-in, gated behind an env flag e.g. `AGENTIC_THREADS_LIVE=1`, **never in
  CI**): a real throwaway session; asserts **shapes and invariants, never content**
  (no PII). Env: `AGENTIC_THREADS_LIVE_PROFILE`, `AGENTIC_THREADS_LIVE_TARGET`.

## CI (`.github/workflows/ci.yml`) — matrix `[macos-latest, ubuntu-latest]`, Python 3.12

- `lint-and-test`: install pinned `requirements-dev.lock` + `pip install -e . --no-deps`;
  `ruff check .`, `ruff format --check .`, `python scripts/check_fixtures_pii.py`, `pytest`.
- `build-and-smoke`: build the **base wheel only** (no `[browser]`), install into a clean
  venv, smoke-test `agentic-threads --version/--help/schema/schema --json/catalog`. The
  load-bearing regression this catches is an accidental eager `import scrapling` in the
  base path (would crash `--version` when scrapling isn't installed).

## Publishing (`.github/workflows/publish.yml`) — PyPI Trusted Publishing (OIDC)

The pending publisher is already configured: repo `tjdwls101010/Agentic-Threads`, workflow
**`publish.yml`**, environment **(all)**. So the workflow filename **must** be `publish.yml`.

- Trigger: `on: release: published` (a GitHub Release, not a bare tag push).
- `build` job: `scripts/check_tag_version.py` verifies the tag == `pyproject`/`__init__`
  version; `python -m build` (sdist+wheel); upload artifact.
- `publish` job: `pypa/gh-action-pypi-publish` **pinned to a commit SHA** (not a floating
  tag), `permissions: id-token: write`. No stored API token anywhere.

## pre-commit + scripts

- `.pre-commit-config.yaml`: `ruff --fix` + `ruff-format`, plus a local hook running
  `scripts/check_fixtures_pii.py` on `tests/fixtures/*.json`.
- `scripts/check_tag_version.py` (tag/version gate, `tomllib`),
  `scripts/check_fixtures_pii.py` (coarse allowlist scan: CDN hosts, token-shaped keys,
  emails/phones, high-entropy strings — structural only; human review is the real control),
  `scripts/record_fixture.py` (dev tool to capture real bodies into gitignored `scratch/`).
- `.gitignore`: `scratch/`, `*.raw.json`, `output/`, `profiles/`, `.venv/`, and the
  general Python set. Fixtures un-ignored by exact name.
