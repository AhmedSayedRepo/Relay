# Relay Sprint 3 security and privacy review

This review covers the Sprint 3 diff from the exact Sprint 2 archive baseline. It is a source, architecture, test, PostgreSQL, and emulator review. No penetration testing was performed or claimed. The sealed scan contract is under `docs/sprint-3-security-scan/`; it reports zero confirmed vulnerabilities and partial coverage because production edge rate/volume enforcement was unavailable.

## Permission and consent boundary

The only new personal-content permission is `android.permission.RECEIVE_SMS`. Relay does not request `READ_SMS`, so it cannot scan the historical inbox through the implemented flow. The exported receiver is guarded by Android's signature-level `BROADCAST_SMS` permission and checks the expected action. The app also declares telephony hardware optional.

Remote configuration is never consent. The local user must enable the SMS consent switch and separately initiate Android's permission prompt. Capture additionally requires a valid enabled remote collector and current installation credentials. Revoking permission or consent immediately makes the effective state false; the app does not loop on permission requests. Tests cover every gate and withdrawal.

## Data minimization and local protection

SMS v1 admits only sender, message body, and received timestamp plus event/config/rule metadata. No contacts, subscriber information, location, device fingerprint, media, call log, microphone, or camera data is collected. Nonmatching messages are discarded before a generic event is created.

Matching payloads are encrypted with AES-GCM before SQLite persistence. The key is Android Keystore-backed, ciphertext lives under the no-backup directory, and instrumentation verified plaintext absence and persistence after close/reopen. This protects ordinary file extraction and backup paths. It cannot promise confidentiality on a rooted, instrumented, unlocked, or otherwise compromised running device; an attacker with the app process or OS control may observe plaintext before encryption or after decryption.

## Configuration safety

Rules are declarative, bounded, and parsed independently on backend and Android. Regex, scripts, shell execution, dynamic code, and arbitrary remote commands are absent. Rule count, unique IDs, string lengths, and nonempty term arrays are enforced. Case-insensitive matching uses deterministic primitives. Local consent remains mandatory even if an administrator broadens a rule.

An administrator compromise can still publish a broad rule to already-consenting devices. Protecting administrator accounts, reviewing configuration changes, and adding richer audit/approval workflows remain operational and future hardening concerns.

## Delivery, authorization, and revocation

The existing installation-bound device credential is required for ingestion. Backend authentication rechecks active device, installation, credential family, expiry, and revocation state. Event installation IDs must match the authenticated installation. Administrator event reads/deletes use the separate administrator token dependency. Cross-domain and cross-installation tests passed.

The uploader attempts credential renewal once for an expired access token. A revoked or disabled device cannot ingest queued events. Terminal authorization loss clears credentials but preserves encrypted queue data for explicit local discard. It is not silently rebound to a later installation.

## Idempotency and input abuse

The request body, batch count, event envelope, SMS body, metadata, timestamps, configuration references, and rule references are bounded and validated. Each malformed item gets a safe independent rejection, preventing both whole-batch denial and accidental acceptance. PostgreSQL enforces `(installation_id, event_id)` uniqueness; canonical hashing distinguishes identical retry from changed-content ID reuse. The real concurrent submission test produced exactly one row.

Persistence uses SQLAlchemy-bound expressions and validated JSONB. No SQL strings are assembled from sender, body, metadata, filters, or event IDs. Admin listing uses bounded pagination and validated ordered timezone-aware ranges.

## Logging, retention, and deletion

Application logs reject arbitrary context keys and permit only safe identifiers, categories, and counts. Event payloads and SMS text do not enter backend or Android logs. Tests use marker values to verify absence. HTTP errors and deletion/purge output likewise do not echo content.

Server retention defaults to 30 days and is bounded to 1..365 days. Expiry is computed at admission and indexed. The provided purge command must be scheduled by the operator; automatic production scheduling is intentionally outside Sprint 3. An administrator can delete all raw events for a device. Database administrators and authorized Relay administrators can read plaintext raw events and must be treated as privileged.

## Availability and volume limitation

Individual request and storage units are bounded, and device disabling stops further authenticated admission. The repository does not implement a shared distributed per-device request-rate or accepted-event quota. A compromised valid device can send many individually valid bounded requests until an operator revokes it. Before production rollout, the trusted deployment edge must enforce and verify per-device request and volume quotas and alert on unusual accepted-event counts. This is the one deferred security coverage item in the sealed scan.

## Review results

No confirmed reportable vulnerability remained. Review-driven fixes included rejecting empty match lists, preserving the last successful upload timestamp on later errors, preventing duplicate enqueue from evicting the oldest record, providing independent scalar-item rejection, allowlisting safe event metrics, refreshing runtime permission UI state, and declaring telephony hardware optional for correct packaging.

The canonical report is `docs/sprint-3-security-scan/report.md`; `findings.json` is empty, `coverage.json` records the reviewed surfaces and deferred rate-limit proof, and `exports/results.sarif` is the machine-readable projection.
