# Sprint 3 focused security review receipt

## Scope and method

The review compared the exact Sprint 2 archive baseline at Git mirror commit `a7ae986888d842e9e45b224480edf8f9413ee8b1` with the current Sprint 3 working tree. The refreshed binary patch contains 50 changed files, 3,285 insertions, and 45 deletions and has SHA-256 `dbcea58cd747ce0883918d2913b1c168aac8303e07564594454778df0e44984f`. The patch passed `git diff --check`.

The Codex Security workbench could not bind the prepared mirror even though its local `HEAD` resolved, so the documented terminal fallback was used. Review consisted of threat modeling, full changed-file inspection, focused source tracing across Android capture through PostgreSQL persistence, configuration and payload boundary checks, and the executed backend, Android, PostgreSQL, and emulator verification. This was a source review, not penetration testing.

## Reviewed surfaces

| Surface | Disposition | Evidence and conclusion |
|---|---|---|
| SMS permission and broadcast boundary | No issue found | Only `RECEIVE_SMS` was added for personal content; the receiver requires the system `BROADCAST_SMS` sender permission and processes new broadcasts without inbox access. |
| Consent and permission withdrawal | No issue found | Capture requires local consent, current runtime permission, remote enablement, and active device credentials; the UI exposes and refreshes all states. |
| Configuration abuse | No issue found | Both parsers enforce bounded declarative rules, nonempty match terms, supported match modes, unique IDs, and no regex, script, or executable content. |
| Encrypted durable outbox | No issue found | SMS event payloads are AES-GCM ciphertext in no-backup SQLite under an Android Keystore key; instrumentation verified no plaintext and restart persistence. Rooted, instrumented, or unlocked compromised devices remain outside the confidentiality guarantee. |
| Queue capacity and retention | No issue found | Count, byte, single-record, batch, and seven-day offline limits are explicit; oldest records are dropped with state visibility and duplicate insertion cannot evict another event. |
| Upload authentication and revocation | No issue found | Delivery uses the Sprint 2 device credential boundary, one renewal attempt, stable event IDs, bounded retry, and preserves but cannot transmit queued events after terminal authorization loss. |
| Backend authorization and ownership | No issue found | Device ingestion and administrator inspection use separate dependencies; installation/device state is database checked and each envelope is bound to the authenticated installation. |
| Payload and batch abuse | No issue found | HTTP, batch, event, string, timestamp, schema, configuration, and rule bounds are independently enforced; malformed items receive bounded per-item rejection. |
| Idempotency and concurrency | No issue found | PostgreSQL uniqueness on installation plus event ID and savepoint conflict handling produced one durable row in the real concurrent test. Changed-content reuse is rejected. |
| SQL and JSON injection | No issue found | SQLAlchemy bound expressions and typed/validated JSON are used; raw query construction from event content was not introduced. |
| Logging leakage | No issue found | Backend and Android event logs emit bounded identifiers, counts, and categories; payloads and credentials are excluded, with regression coverage. |
| Retention and deletion | No issue found | Expiry is stored and indexed, purge is bounded by a configurable policy, and the authenticated device deletion service does not preserve payload content in logs. Operators must schedule the maintenance command. |
| Request-rate and volume abuse | Needs follow-up | A valid compromised device can repeatedly submit individually bounded requests until disabled. A shared per-device rate or volume limiter is expected at the deployment edge but is not implemented or verified in this repository. |

## Outcome

No confirmed reportable Sprint 3 security vulnerabilities remained after review. During implementation, the review caught and fixed empty match lists that could act as match-all rules, loss of the last-success timestamp on an upload error, duplicate enqueue eviction, whole-batch rejection for a scalar item, missing safe metric allowlisting, and stale runtime permission display. The remaining deployment-edge rate-limit dependency is recorded as deferred coverage and a known limitation, not as a proven code vulnerability.
