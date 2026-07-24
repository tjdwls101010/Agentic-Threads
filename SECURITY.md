# Security Policy

## Supported versions

`agentic-threads` is pre-1.0 alpha software. Security fixes are made only on the latest published release line; there are no long-term-support branches or backports.

| Version | Supported |
|---|---|
| Latest 0.1.x release | Yes |
| Older releases | No — upgrade to the latest release |

Check [CHANGELOG.md](CHANGELOG.md) before upgrading. Threads' internal operation identifiers and response shapes can change independently of package security; staying current is also the normal compatibility path.

## Report vulnerabilities privately

**Do not open a public issue, discussion, or pull request for a suspected vulnerability. Do not publish details before a fix is available.**

Use GitHub's private vulnerability reporting for the [Agentic Threads repository](https://github.com/tjdwls101010/Agentic-Threads): open **Security → Report a vulnerability**. The report is visible privately to the maintainer.

If private vulnerability reporting is unavailable, contact [@tjdwls101010](https://github.com/tjdwls101010) through GitHub and ask for a private channel **without including vulnerability details** in the public message.

Include:

- the affected `agentic-threads` version, Python version, operating system, and install mode;
- the security impact and who or what could be harmed;
- the smallest reproducible steps, preferably against a synthetic fixture or mocked response;
- any preconditions and a proposed mitigation, if known.

### Never include live data or secrets

A security report must not contain:

- `session.json`, cookies, cookie exports, browser profiles, CSRF values, tokens, authorization headers, or account credentials;
- real GraphQL captures, read-command output, `--raw --no-redact` output, signed media URLs, or other people's Threads content;
- real usernames, numeric IDs, names, biographies, messages, email addresses, phone numbers, or other personal data.

Reconstruct the relevant shape with obviously synthetic values. Diagnostic redaction is not a guarantee, so inspect every attachment manually. If a problem appears to require a live secret to reproduce, describe the condition without sending the secret and coordinate a safer reproduction with the maintainer. The maintainer does not need and should not request access to your account.

## In scope

This package persists a live, password-less logged-in session, parses untrusted network responses and cookie files, writes sensitive output, and publishes through a package supply chain. Examples of in-scope vulnerabilities include:

- leaking session credentials or sensitive values through diagnostics, exceptions, subprocesses, paths, permissions, or unintended files;
- creating profile directories or credential files with weaker permissions than the documented `0700` / `0600` modes;
- bypassing diagnostic or raw-field redaction where the CLI claims redaction applies;
- path traversal, command or content injection, unsafe deserialization, or code execution from a crafted identifier, cookie import, GraphQL response, or output path;
- exposing data across named profiles or writing results somewhere other than the selected/default destination;
- a reachable vulnerability in a runtime dependency;
- compromise of the build, release, version, or PyPI Trusted Publishing path.

## Out of scope

The following are documented risks or ordinary compatibility bugs rather than package vulnerabilities:

- Meta/Instagram/Threads terms enforcement, account rate limits, checkpoints, suspensions, or bans;
- upstream operation-identifier rotation, changed response envelopes, deleted/private targets, or incomplete upstream data;
- the fact that a read output file contains unredacted result data, or that explicit `--raw --no-redact` output is unredacted;
- findings that require an attacker who already controls your operating-system user account, unless the package materially worsens that access;
- scanner output with no demonstrated, reachable impact.

This classification does not make those risks harmless or authorized. Follow [DISCLAIMER.md](DISCLAIMER.md). Report non-security breakage through the public issue tracker only after removing all secrets, captures, and personal data.

## Responsible security research

Research must be authorized, minimal, and non-destructive:

- use only a disposable account you control and synthetic/offline fixtures whenever possible;
- keep network work to the minimum needed to confirm a finding and honor the non-bypassable 1.0-second request floor;
- do not access another person's account or data, test credential theft, evade a checkpoint or rate limit, rotate identities, or degrade Meta's service;
- stop immediately if you encounter data outside the intended scope, an account challenge, or platform instability;
- retain no unnecessary personal data and disclose through the private channel before publishing details.

This policy authorizes research on this package only. It does not authorize testing Meta, Instagram, Threads, their users, or infrastructure.

## Disclosure and response

This is a single-maintainer project with no response-time service-level agreement. Reports are handled on a best-effort basis. The maintainer will assess impact, work toward a fix, and coordinate a disclosure date with the reporter. Please keep details private until a patched release or agreed disclosure date is available. Reporter credit is offered unless anonymity is requested.
