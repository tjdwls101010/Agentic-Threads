# Testing, Packaging & CI

Mirror `agentic-x` almost verbatim; it is the cleaner scaffold and the httpx-primary model matches ours.

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

Base deps deliberately **exclude scrapling** (it's the `[browser]` extra). `jsonschema` is a declared dev dep so the schema-validation test runs in CI (not `importorskip`'d away).

## Tests (`tests/`) — offline, fixture-driven; no network/browser in CI

- `conftest.py`: `load_fixture` returns a fixture file's bytes/JSON.
- `tests/fixtures/*.json`: **hand-authored synthetic, PII-free** GraphQL-response skeletons (fake usernames like `synthetic_alice`, fake numeric ids) for `feed`, `profile_threads`, `profile_replies`, `post_root`, `post_replies`, `post_search`, `account_search`, `followers`, `following`, and `unavailable`, including carousel, quote/repost, reply, missing-date, and unavailable edge shapes. They are un-ignored in `.gitignore` by **exact name**, never a wildcard. Real captures live in gitignored `scratch/` and are never committed.
- Unit test files (all offline, with mock transports/fixtures rather than network calls):
  - `test_parse.py` — every anchored post/user envelope path, direct and Relay connection shapes, cursor/page-info invariants, exact drift diagnostics, empty/null branches, and recursive or wrong-level decoy rejection.
  - `test_model.py` — post/user/media normalization, ISO-Z serialization, `raw` opt-in, identifier boundaries, schema/`to_dict` parity, complete field descriptions, generated JSON Schema validity, and fixture-derived model validation with `jsonschema`.
  - `test_client.py` — `ReadClient` sends exactly `doc_id` and `variables` as form fields with authenticated cookies and the current content-type/user-agent/CSRF/friendly-name/app/origin/referer headers; mandatory first-and-every-request pacing, lifetime budgets, cross-client concurrency serialization and budget races, lifecycle, sanitized transport failures, and HTTP/GraphQL error precedence are covered.
  - `test_gql.py` — public endpoint/app/operation constants, shared and per-operation feature settings, every variable builder, cursor/non-cursor branches, and caller feature overrides without input/default mutation.
  - `test_docids.py` — exact operation/`doc_id` route-preloader pairing, ephemeral `fb_dtsg`/`LSD` route forms and computed `jazoest`, anti-JSON streams, authenticated route and bounded trusted-JavaScript fallback scans, mandatory pacing, trusted URL/cookie scope, and wall/auth/rate/transport failure precedence.
  - `test_retrieve.py` — exact username resolution, verified shortcode candidates, deduplication, cursor EOF/cycles, limits and client-side date bounds, request budgets, bounded rate-limit waits, partial results, and empty-page guards.
  - `test_auth.py` — private atomic session storage and symlink defenses, required-cookie import across JSON/Netscape/raw/curl formats with domain filtering and redacted errors, trusted identifier normalization, and exact shortcode candidate decoding.
  - `test_session.py` — saved `doc_id`/feature merges, one-request health/status classification, bounded browser-free doctor refresh and persistence, trusted wall classification, fixed harvest navigation, narrowly filtered ephemeral browser request artifacts, minimal login credential persistence, lazy browser setup, and browser/client closure on every failure path.
  - `test_cli.py` — parser/handler/catalog synchronization, every help and flag surface, offline catalog and plain/JSON Schema output, documented exit-code mapping, stdout/stderr contracts, redacted unexpected failures, and login/status/doctor/read dispatch.
  - `test_redact.py` and `test_fixture_pii.py` — recursive and unstructured secret redaction, signed-media URL handling, bounded/cyclic input, committed-fixture scanning, scanner diagnostics, and private/path-confined fixture recording.
  - `test_no_scrapling_import.py` — fresh subprocess imports of the base package, CLI, auth, and session surfaces never load Scrapling.
- `tests/live/` (opt-in, gated behind an env flag e.g. `AGENTIC_THREADS_LIVE=1`, **never in CI**): a real throwaway session; asserts **shapes and invariants, never content** (no PII). Env: `AGENTIC_THREADS_LIVE_PROFILE`, `AGENTIC_THREADS_LIVE_TARGET`.

## CI (`.github/workflows/ci.yml`) — Python 3.11 and 3.12

- `lint-and-test`: use a cost-controlled explicit include matrix with `ubuntu-latest`/Python 3.11, `ubuntu-latest`/Python 3.12, and `macos-latest`/Python 3.12; install pinned `requirements-dev.lock` + `pip install -e . --no-deps`; run `ruff check .`, `ruff format --check .`, `python scripts/check_fixtures_pii.py`, and `pytest`.
- `build-and-smoke`: run on `macos-latest` and `ubuntu-latest` with Python 3.12; build an sdist from the checkout, derive a dependency-free base wheel from that sdist with `pip wheel --no-deps`, install the derived wheel into a clean venv, assert Scrapling is absent, then smoke-test `agentic-threads --version`, `--help`, `catalog`, `schema`, and `schema --json` offline. This sdist-first path verifies both source-distribution completeness and the lazy browser dependency boundary.

## Publishing (`.github/workflows/publish.yml`) — PyPI Trusted Publishing (OIDC)

The pending publisher is already configured: repo `tjdwls101010/Agentic-Threads`, workflow **`publish.yml`**, environment **(all)**. So the workflow filename **must** be `publish.yml`.

- Trigger: `on: release: published` (a GitHub Release, not a bare tag push).
- `build` job: `scripts/check_tag_version.py` verifies the tag == `pyproject`/`__init__` version; `python -m build` (sdist+wheel); upload artifact.
- `publish` job: `pypa/gh-action-pypi-publish` **pinned to a commit SHA** (not a floating tag), `permissions: id-token: write`. No stored API token anywhere.

## pre-commit + scripts

- `.pre-commit-config.yaml`: `ruff --fix` + `ruff-format`, plus a local hook running `scripts/check_fixtures_pii.py` on `tests/fixtures/*.json`.
- `scripts/check_tag_version.py` (tag/version gate, `tomllib`), `scripts/check_fixtures_pii.py` (coarse allowlist scan: CDN hosts, token-shaped keys, emails/phones, high-entropy strings — structural only; human review is the real control), `scripts/record_fixture.py` (dev tool to capture real bodies into gitignored `scratch/`).
- `.gitignore`: `scratch/`, `*.raw.json`, `output/`, `profiles/`, `.venv/`, and the general Python set. Fixtures un-ignored by exact name.
