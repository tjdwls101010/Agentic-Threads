# Installation

This page installs the `agentic-threads` v0.1 command-line tool. The package requires **Python 3.11 or newer**.

> Use a dedicated, throwaway Instagram account, never an account you care about. Threads automation can violate platform terms and put the account at risk. Read [DISCLAIMER.md](../../DISCLAIMER.md) before creating a session.

## Install it in an isolated environment

Use a tool installer such as [`uv`](https://docs.astral.sh/uv/) or [`pipx`](https://pipx.pypa.io/). Each gives `agentic-threads` its own Python environment while putting the executable on your `PATH`:

```bash
uv tool install agentic-threads
```

or:

```bash
pipx install agentic-threads
```

Isolation is especially important for the browser extra because its browser engine dependencies are version-matched. Installing those dependencies in an environment shared with another Playwright-based tool can create resolver or browser-binary conflicts.

## Choose an install profile

### Base: HTTP reads and cookie import

The commands above install the base package. It depends on `httpx` and `platformdirs` only. The base install can:

- import an existing session with `agentic-threads login --cookies FILE`;
- run every read command over HTTP;
- run `status` and `doctor` after a session has been imported;
- run browser-free `agentic-threads doctor --refresh`; and
- run the offline `catalog` and `schema` commands.

It cannot open an interactive login browser or run `setup`.

### Browser extra: headed interactive login

To log in through a visible browser, install the `[browser]` extra instead:

```bash
uv tool install "agentic-threads[browser]"
```

or:

```bash
pipx install "agentic-threads[browser]"
```

The extra adds `scrapling[fetchers]` and its browser engine. It is used only by `setup` and interactive `login`. Once a session is saved, **all retrievals use plain HTTP**; there is no browser read mode or browser fallback.

If cookie import is your only login path, the base package is sufficient.

## Provision the isolated browser

After installing `[browser]`, provision Chromium once:

```bash
agentic-threads setup
```

`setup` installs the browser into an `agentic-threads`-owned `browsers/` cache under the platform data directory. It does not use or modify another tool's Playwright browser cache. To replace a damaged or mismatched browser install:

```bash
agentic-threads setup --force
```

Browser provisioning is separate from login. Continue with the [Quick Start](Quick-Start.md) after `setup` completes.

## Verify the command

These checks are offline and need no session:

```bash
agentic-threads --version
agentic-threads catalog
```

After login or cookie import, check the saved session with:

```bash
agentic-threads status
agentic-threads doctor
```

`status` makes one cheap authenticated HTTP read and classifies the session. `doctor` performs an authenticated HTTP round trip. Neither command launches a browser. If Threads has rotated the persisted GraphQL document IDs, the deeper repair check is also browser-free:

```bash
agentic-threads doctor --refresh
```

## Upgrade

With `uv`:

```bash
uv tool upgrade agentic-threads
```

With `pipx`:

```bash
pipx upgrade agentic-threads
```

If an upgrade changes browser dependencies, run `agentic-threads setup` again; it is safe to leave an already-matching isolated browser in place.

## Stored data

Uninstalling the Python package does not remove its platform data directory. That directory can contain a live session, the isolated browser, and retrieved output. Session files grant access without a password, and output can contain third-party personal data. Do not put either in a repository, cloud-synced folder, fixture, or bug report. Secure them locally and remove them when no longer needed; see [DISCLAIMER.md](../../DISCLAIMER.md) for the full risk and revocation guidance.

---

Next: [Quick Start](Quick-Start.md). Project source: <https://github.com/tjdwls101010/Agentic-Threads>.
