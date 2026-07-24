# Disclaimer — read before you use this

This is not legal advice. It is a plain-language warning about risks you accept by using this software. Obtain advice from a qualified professional for your jurisdiction and use case.

## 1. Unofficial and not authorized by Meta

`agentic-threads` is an independent, unofficial automation tool. It is not affiliated with, endorsed by, sponsored by, or authorized by Meta Platforms, Instagram, or Threads.

Meta, Instagram, and Threads terms and policies restrict automated access, scraping, and collection. **Assume this use is prohibited unless Meta has given you explicit written permission.** You are solely responsible for reading the terms that apply to you and for obtaining every permission required by the platform, an account owner, a data subject, and the law.

**Read-only scope is not authorization.** The fact that this tool cannot post, like, follow, or send messages does not grant permission to automate reads, bypass an access restriction, collect content, or retain and share the result. A successful HTTP response is not consent or a lawful basis.

## 2. Account bans, checkpoints, and loss of access

Automating a logged-in Instagram account can trigger rate limits, checkpoint or login-approval challenges, temporary restrictions, session revocation, suspension, or a permanent ban. Meta can change its detection and enforcement without notice. Neither a headed login nor a one-second request interval makes the activity human, permitted, or undetectable.

Use a **dedicated disposable Instagram account only — never a primary, business, creator, or otherwise valuable account**. Assume the account may be lost without warning. Keep every run shallow and low-volume, prefer an explicit `--limit`, and stop immediately when challenged or rate-limited. Do not automate challenge solving, rotate accounts or networks to evade controls, or use another person's session.

The CLI enforces at least 1.0 second between HTTP requests. That floor is harm reduction, not a safe-harbor threshold or a guarantee against enforcement.

## 3. Terms, law, GDPR, and third-party personal data

Threads posts, replies, profiles, social graphs, names, biographies, text, timestamps, and media URLs can identify or concern real people. Collection can make you a controller or processor of personal data under the GDPR or other privacy law, depending on your circumstances. Similar obligations may arise under the CCPA/CPRA and other national, state, contractual, employment, research, confidentiality, database, copyright, or computer-access rules.

You are responsible for, among other things:

- establishing a valid lawful basis and respecting purpose limitation;
- collecting only what is necessary and retaining it only as long as necessary;
- securing the data and honoring applicable access, correction, objection, and deletion rights;
- determining whether notice, consent, institutional approval, or a data-protection assessment is required;
- avoiding surveillance, profiling, harassment, discrimination, doxxing, re-identification, and publication that could harm a person.

"Personal research" is not automatically exempt, and public visibility does not make personal data free of legal or ethical duties. The MIT license covers this code only; it grants no rights in content or personal data.

## 4. Output files contain PII and are not scrubbed

Read-command output is the full structured result. It may contain third-party personal data and signed or identifying media URLs. It is **not redacted**, even though the default location is outside the current repository. `--raw` can add more source data, and `--raw --no-redact` deliberately disables raw-field redaction.

Diagnostic and verbose messages pass through a redaction path, but redaction is risk reduction, not certification. A missed secret, identifier, free-text detail, or URL may still appear.

Never commit captures, result files, cookie exports, session profiles, or raw responses to any repository. Do not paste them into issues, pull requests, chats, screenshots, or logs. Restrict access, encrypt storage where appropriate, avoid cloud synchronization and backups, and delete data promptly when the purpose ends.

## 5. A saved session is a live credential

Interactive login and cookie import persist a Threads/Instagram session in `session.json`. Anyone who obtains that file or the source cookie export may be able to act as the logged-in account without its password or a new two-factor challenge. Filesystem modes `0700` and `0600` limit ordinary local access; they are not encryption and do not protect a compromised user account or machine.

- Do not commit, email, upload, synchronize, or casually back up a profile directory or cookie export.
- Secure or delete the original cookie export after import; this tool does not delete it.
- Do not send credentials to maintainers, even in a private vulnerability report.
- If exposure is possible, revoke the session through Instagram/Meta account security controls and establish a new disposable session. Deleting the local file alone does not revoke it.

## 6. Responsible use does not include bypass

Use the tool only with an account you control, for targets you are authorized and legally permitted to read. Do not use it to defeat authentication, privacy settings, checkpoints, rate limits, blocks, deleted-content controls, or any other technical or policy restriction. Do not turn the single-target primitives into a crawler, daemon, scheduler, or mass-collection system.

Security research must be authorized, minimal, non-destructive, and coordinated. Stop before accessing data you do not need, and report package vulnerabilities privately under [SECURITY.md](SECURITY.md).

## 7. No warranty

This software is provided **"as is"**, without warranty of any kind, under the MIT License. There is no warranty that it is lawful or permitted for your use, that it will preserve an account, that its redaction catches every sensitive value, or that output is complete, accurate, current, or fit for any purpose.

Threads uses an undocumented internal web interface. Operation identifiers, session requirements, response shapes, and enforcement can change at any time. The tool may fail, return partial data, or stop working without notice. To the maximum extent permitted by law, maintainers and contributors are not liable for account loss, platform action, legal claims, privacy incidents, data loss, incomplete results, or other consequences of using or distributing this software.
