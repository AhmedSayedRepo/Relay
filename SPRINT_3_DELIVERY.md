# Relay Sprint 3 delivery report

Date: 2026-09-03  
Delivered version: backend `0.3.0`, Android `versionName 0.3.0` / `versionCode 3`  
Baseline: exact contents of `relay-sprint-2.zip`  
Scope boundary: SMS-to-raw-event pipeline only; Sprint 4 is proposal-only

## 1. Implementation summary

Sprint 3 implements the first complete generic collector path: Android receives new SMS broadcasts, applies explicit local consent and runtime permission plus validated remote rules, persists only matching events in an encrypted bounded outbox, uploads bounded batches using Sprint 2 device authentication, and admits events independently into durable PostgreSQL raw storage. The server provides database-backed idempotency, per-event acknowledgements, retention/purge, administrator visibility, and per-device deletion.

The implementation preserves the Sprint 1 Clean Architecture backend and Sprint 2 enrollment, device credential, renewal/revocation, configuration, acknowledgement, and last-seen foundations. Migration files `0001` and `0002` were not edited. No WhatsApp, notification listener, images, media scanning, screenshots, OCR, AI, banking/payment logic, contacts, call logs, microphone, location, arbitrary remote commands, scripts, regex, or shell execution was added.

## 2. Full changed repository tree

Status is relative to the exact 119-file Sprint 2 archive manifest. Generated caches and Android build intermediates are excluded; final APK artifacts and evidence documents are included.

```text
[M] .env.example
[M] android/app/build.gradle.kts
[M] android/app/src/androidTest/java/com/relay/agent/BackendRoundTripInstrumentedTest.kt
[A] android/app/src/androidTest/java/com/relay/agent/EncryptedOutboxInstrumentedTest.kt
[M] android/app/src/androidTest/java/com/relay/agent/SecureStorageInstrumentedTest.kt
[M] android/app/src/main/AndroidManifest.xml
[M] android/app/src/main/java/com/relay/agent/application/AgentController.kt
[A] android/app/src/main/java/com/relay/agent/application/SmsCollector.kt
[M] android/app/src/main/java/com/relay/agent/data/ConfigurationCodec.kt
[M] android/app/src/main/java/com/relay/agent/data/DataStoreAgentStore.kt
[A] android/app/src/main/java/com/relay/agent/data/EncryptedSqliteOutbox.kt
[A] android/app/src/main/java/com/relay/agent/data/EventCodec.kt
[M] android/app/src/main/java/com/relay/agent/data/HttpRelayApi.kt
[A] android/app/src/main/java/com/relay/agent/data/SmsReceiver.kt
[M] android/app/src/main/java/com/relay/agent/data/SyncScheduler.kt
[A] android/app/src/main/java/com/relay/agent/domain/Events.kt
[M] android/app/src/main/java/com/relay/agent/domain/Models.kt
[M] android/app/src/main/java/com/relay/agent/presentation/AgentViewModel.kt
[M] android/app/src/main/java/com/relay/agent/presentation/MainActivity.kt
[M] android/app/src/main/java/com/relay/agent/RelayApplication.kt
[M] android/app/src/main/res/values/strings.xml
[M] android/app/src/test/java/com/relay/agent/application/AgentControllerTest.kt
[A] android/app/src/test/java/com/relay/agent/application/EventUploaderTest.kt
[A] android/app/src/test/java/com/relay/agent/application/SmsCollectorTest.kt
[M] android/app/src/test/java/com/relay/agent/data/ConfigurationCodecTest.kt
[A] android/app/src/test/java/com/relay/agent/data/EventCodecTest.kt
[M] android/app/src/test/java/com/relay/agent/data/HttpRelayApiTest.kt
[M] artifacts/README.md
[A] artifacts/relay-agent-0.3.0-debug.apk
[A] artifacts/relay-agent-0.3.0-release-unsigned.apk
[A] docs/sprint-3-architecture.md
[A] docs/sprint-3-emulator-evidence.json
[A] docs/sprint-3-postgres-evidence.json
[A] docs/sprint-3-runtime-smoke-evidence.json
[A] docs/sprint-3-security-scan/artifacts/01_context/threat_model.md
[A] docs/sprint-3-security-scan/artifacts/02_discovery/review_receipt.md
[A] docs/sprint-3-security-scan/coverage.json
[A] docs/sprint-3-security-scan/exports/results.sarif
[A] docs/sprint-3-security-scan/findings.json
[A] docs/sprint-3-security-scan/report.md
[A] docs/sprint-3-security-scan/scan-manifest.json
[A] docs/sprint-3-security.md
[A] docs/sprint-3-verification-evidence.json
[A] docs/sprint-4-proposal.md
[M] migrations/env.py
[A] migrations/versions/0003_raw_events.py
[M] pyproject.toml
[M] README.md
[A] SPRINT_3_DELIVERY.md
[A] src/relay/application/event_ports.py
[A] src/relay/application/events.py
[M] src/relay/domain/agent.py
[A] src/relay/domain/events.py
[M] src/relay/infrastructure/cli.py
[M] src/relay/infrastructure/config.py
[A] src/relay/infrastructure/event_models.py
[A] src/relay/infrastructure/event_repository.py
[M] src/relay/infrastructure/logging.py
[M] src/relay/infrastructure/repositories.py
[M] src/relay/presentation/app.py
[A] src/relay/presentation/event_dependencies.py
[A] src/relay/presentation/event_schemas.py
[A] src/relay/presentation/routes/events.py
[M] tests/test_agent.py
[M] tests/test_cli.py
[A] tests/test_events.py
[M] tests/test_migrations.py
[M] tests/test_postgres.py
[M] tests/test_security.py
```

## 3. Existing Sprint 1/2 verification baseline

Before Sprint 3 changes, the 119 source files exactly matched the 119 files in `relay-sprint-2.zip`; the archive SHA-256 was `631d5164b2b87de89b9e8265c5f19019bde304ab87849cc2078d6a03fdd809ea`.

Baseline execution passed 131 backend tests with five PostgreSQL tests skipped when no opt-in URL was supplied, then 136 tests against PostgreSQL 16.15. Android passed 19 JVM tests, the standard instrumentation run passed three tests with one explicitly optional live-backend test skipped, and the configured emulator passed its one Sprint 2 live round-trip test. Ktlint, Android lint, debug build, and unsigned release build passed. The baseline manifest had no personal-content permission.

## 4. SMS permission/consent design

The effective gate is:

```text
effective_collection =
    local_consent
    AND runtime_permission
    AND remote_collector_enabled
    AND device_active
```

The Android UI exposes a plain local-consent switch and a separate user-initiated permission button. Remote configuration cannot change either state. Lifecycle refresh detects a withdrawn permission; collection then stops without repeated permission prompts. Valid installation credentials represent active state locally, while the backend authoritatively rechecks device, installation, credential, expiry, and revocation state. Queue data remains encrypted after authorization loss and can be explicitly discarded. Only new broadcasts are handled; there is no historical inbox read or backfill.

## 5. SMS collector architecture

`SmsReceiver` is the Android broadcast adapter. It accepts the protected `SMS_RECEIVED` broadcast, assembles multipart message parts, and completes quickly through `goAsync`. `SmsCollector` is an application service that evaluates consent, permission, credentials, last-known-good configuration, remote enablement, and bounded rules. Nonmatching messages stop there. The first matching rule creates a collector-independent `GenericEvent`, persists it, and schedules unique upload work. Network delivery never blocks the receiver.

The boundary is `SmsReceiver -> SmsCollector -> GenericEvent -> EventOutbox`. Android framework types do not enter the backend domain, and the generic envelope/outbox/uploader can serve future collectors.

## 6. Rule configuration schema

The Sprint 2 outer configuration remains schema version 1. Its optional `collectors.sms` contains `enabled` and at most 20 uniquely identified rules. Each rule has an ID, enabled flag, optional sender matcher, and optional body matcher. Sender modes are exact, contains, prefix, or suffix. Body supports contains, contains-any, and contains-all. Comparisons are deterministic and case-insensitive.

Rule IDs, values, term counts, and term lengths are bounded. Term arrays require 1..10 values, preventing an empty contains-all rule from becoming an accidental match-all. Backend and Android both reject malformed primitives, unknown modes, duplicates, regex, code, and executable instructions. Captured events retain configuration version and rule ID, and the server validates them against immutable configuration history.

## 7. Generic event envelope

The reusable envelope contains `event_id`, `installation_id`, `collector`, `schema_version`, `occurred_at`, `captured_at`, collector payload, and metadata. SMS schema v1 contains sender, body, and received timestamp only. Metadata contains configuration version and rule ID. Server receipt and expiry time are server-owned.

Event IDs are UUIDs created before enqueue and remain stable across restarts, lost responses, and WorkManager re-execution. The encoded event is capped at 8,192 bytes and the SMS body at 4,096 characters. Timestamp order and bounded age/skew are validated. Canonical SHA-256 content hashing distinguishes a retry from changed-content reuse of an event ID.

## 8. Local encrypted outbox design

`EncryptedSqliteOutbox` uses SQLite in the app no-backup directory for crash-safe ordering and retry metadata. Event content is encrypted before persistence with AES-GCM and a separate Android Keystore alias; each record uses an authenticated fresh IV. Android DataStore holds small agent/status values, not event JSON. Instrumentation inspected the actual database for plaintext marker absence, closed and reopened the database, and verified data and retry-count persistence.

The queue records event ID, creation sequence/time, encrypted byte count, ciphertext, and retry count. FIFO delivery is stable. Expired and corrupt records are safely discarded. Keystore loss or invalidation makes affected payloads unrecoverable by design rather than allowing plaintext fallback.

## 9. Capacity/backpressure policy

The maximums are 500 queued events, 2 MiB total encrypted bytes, 12 KiB per encrypted record, 20 events per batch, and seven days of offline retention. At capacity the outbox drops the oldest records until a new record fits and increments a visible cumulative dropped count. This favors recent matching messages and prevents unbounded storage growth. Duplicate enqueue is checked before eviction so a local replay cannot displace another event. Queue count, dropped count, last success, and last error category are visible in the app.

## 10. Backend event architecture

`relay.domain.events` defines the generic envelope, SMS v1 schema, bounds, timestamp rules, canonical hash, statuses, and safe reasons. `relay.application.events` implements independent item admission, installation binding, historical configuration/rule defense, list/read/delete, and purge. `relay.application.event_ports` defines repository and unit-of-work boundaries. Infrastructure provides SQLAlchemy persistence; presentation provides FastAPI schemas, dependencies, and routes.

The service verifies authenticated installation ownership, active device and credential state, supported collector/schema, payload/event size, UUID, timestamps, configuration version, enabled rule, and server-side rule match. A malformed JSON scalar is still an item and receives its own rejected result instead of invalidating valid neighbors.

## 11. Database migration/schema

New migration `0003_raw_events` creates `raw_events`; `0001` and `0002` remain byte-for-byte unchanged from Sprint 2. The table stores internal/event/device/installation IDs, collector, schema and configuration version, rule ID, occurred/captured/received timestamps, payload, metadata, canonical content hash, and expiry.

PostgreSQL uses JSONB for payload and metadata. Foreign keys bind device and installation, check constraints bound supported values and temporal relationships, indexes support device/time and expiry queries, and unique `(installation_id, event_id)` enforces idempotency. Sprint 1-to-3 and Sprint 2-to-3 upgrade tests passed. Upgrade, drift check, downgrade to base, re-upgrade, and second drift check all passed on PostgreSQL 16.15.

## 12. Ingestion API

`POST /api/v1/device/events/batch` requires the Sprint 2 device bearer token and accepts 1..20 JSON items. It returns one result per input item in original order. Status is `accepted`, `duplicate`, or `rejected`; terminal rejection reasons are stable machine-readable values and never echo payload content. One malformed or unsupported item does not roll back admitted neighbors.

The global HTTP body cap remains 16 KiB. Android additionally uses a conservative 14 KiB serialized-request cap. Administrator tokens are rejected by this route. Disabled devices, revoked/expired/invalid credentials, wrong installation IDs, unsupported collectors/schemas, invalid timestamps/payloads, oversized records, disabled rules, and changed-content ID conflicts are rejected.

## 13. Idempotency design

The idempotency key is `(installation_id, event_id)`. The first valid insert is accepted. An identical replay returns duplicate without another row. The same event ID under another installation is independent, while reuse within one installation with changed canonical content is rejected. The repository performs the insert under a nested savepoint and interprets the PostgreSQL unique violation without poisoning the batch transaction.

Tests cover duplicate events and batches, lost-response resubmission, repeated worker runs, same ID across installations, changed content, and concurrent submission. The real PostgreSQL concurrency test produced exactly one durable row.

## 14. Retry/offline behavior

WorkManager schedules unique one-time upload work with a network constraint and bounded exponential backoff. Periodic agent sync also drains the outbox and returns retry only for retryable conditions. Batches are FIFO and bounded by both count and serialized HTTP size.

Accepted and duplicate results delete local records. Permanent rejected results are terminal and deleted. Transport errors, HTTP 429, and 5xx preserve records and increment retry state. One 401 performs a Sprint 2 credential renewal using a durable request ID; a second authorization failure clears credentials, preserves the encrypted queue, and stops transmission. Re-enrollment creates a new installation, so old installation-bound data is never silently uploaded.

## 15. Retention/deletion design

Server `RAW_EVENT_RETENTION_DAYS` defaults to 30 and is validated from 1..365. Expiry is assigned on admission and indexed. `python -m relay.infrastructure.cli purge-expired-events` deletes expired rows and reports a count without content. Production scheduling is deliberately left to the operator. `DELETE /api/v1/devices/{device_id}/events` lets an authenticated administrator delete all raw events for a device and returns only the deletion count. Android separately expires offline queue entries after seven days and exposes explicit queue discard.

## 16. Administrator APIs

`GET /api/v1/events` provides bounded pagination (`limit` 1..100, `offset` 0..1,000,000), optional device and collector filters, and validated timezone-aware ordered time bounds. `GET /api/v1/events/{event_id}` returns one event. `DELETE /api/v1/devices/{device_id}/events` performs per-device erasure. All require an active administrator token and return `Cache-Control: no-store`. Device tokens cannot use them. There is no dashboard frontend.

## 17. Test results

Final local backend execution: 147 passed and six PostgreSQL-marked tests deselected in 77.07 seconds. Ruff passed. Black reported 64 files unchanged. Strict MyPy passed 46 production source files. The full PostgreSQL run passed all 153 tests in 83.46 seconds. A live Uvicorn/PostgreSQL smoke ran 17 HTTP checks covering health, readiness, auth, device management, enrollment, SMS configuration, accepted and duplicate events, administrator visibility, and disabled-device rejection.

Coverage includes valid/mixed/duplicate/conflicting batches, scalar malformed items, unsupported and oversized input, timestamp bounds, installation ownership, token-domain separation, revocation, retention, deletion log safety, configuration abuse, filters, migration lineage, CLI purge, and safe metrics logging. No existing test was suppressed.

## 18. PostgreSQL results

Verification used a fresh isolated PostgreSQL 16.15 cluster and disposable test database. Alembic upgrade reached `0003`; `alembic check` reported no new operations. Downgrade to base, re-upgrade to head, and the second drift check passed. All 153 tests passed, including JSONB behavior and the concurrent uniqueness race. The cluster was stopped afterward. Evidence is preserved in `docs/sprint-3-postgres-evidence.json` and `docs/sprint-3-runtime-smoke-evidence.json`.

## 19. Android tests/build/lint

JVM tests: 36 passed across agent control, uploader outcomes, retries, lost response, renewal/revocation, SMS gates and rules, configuration/event codecs, credential codec, and HTTP contracts. The ordinary emulator instrumentation run passed six tests and skipped one explicitly optional live-backend test. It exercised real Android Keystore/SQLite encryption, plaintext absence, reopen and retry persistence, 500-record capacity, duplicate-safe eviction, seven-day expiry, secure state, and manifest boundary.

The configured API 35 emulator run passed six instrumentation tests. Its live test enrolled against a running backend, synchronized configuration version 2, enabled local consent, passed a synthetic message through `SmsCollector`, persisted it in the encrypted outbox, uploaded it, cleared the queue, and verified one administrator-visible `sms` raw event with rule `e2e-generic`. Ktlint, 36 unit tests, `lintDebug`, debug assembly, and unsigned release assembly then passed in the final build. Evidence is in `docs/sprint-3-emulator-evidence.json`.

## 20. APK declared permissions

The final release APK declares:

```text
android.permission.RECEIVE_SMS
android.permission.INTERNET
android.permission.FOREGROUND_SERVICE
android.permission.ACCESS_NETWORK_STATE
android.permission.WAKE_LOCK
com.relay.agent.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION
android.permission.RECEIVE_BOOT_COMPLETED
```

`RECEIVE_SMS` is the only personal-content permission and is new in Sprint 3. The dynamic receiver permission is generated by AndroidX and scoped to this application. The APK does not declare `READ_SMS`, contacts, call-log, location, media/storage, microphone, or camera permission. Telephony hardware is optional. Debug declares the same platform permissions with its debug-application dynamic receiver permission.

APK hashes:

```text
F734D6A189C928E23520CA632863E593FAFC258E83A08869ED2C645D9BA563F8  relay-agent-0.3.0-debug.apk
2EBBC102384D246D49183FEEF47B3881D47FE4FAC91278C5E39C910D05156D4A  relay-agent-0.3.0-release-unsigned.apk
```

## 21. Security review

A focused diff review examined SMS permission and protected broadcasts, consent, Keystore/SQLite at-rest protection, lost/stolen/rooted device implications, authorization and ownership, revocation, idempotency, payload/batch abuse, rate limiting, SQL/JSON injection, logging leakage, retention, and configuration abuse. The sealed Codex Security contract reports zero confirmed reportable findings. Coverage is partial solely because the expected production edge per-device rate/volume limiter was outside this repository and could not be verified. No penetration testing was performed.

Review-driven fixes included rejecting empty term lists, retaining the last successful-upload timestamp after later errors, checking duplicate enqueue before eviction, handling scalar malformed items independently, allowlisting safe event metrics, refreshing permission state on lifecycle resume, and declaring telephony hardware optional. Canonical findings, coverage, SARIF, threat model, and review receipt are under `docs/sprint-3-security-scan/`.

## 22. Known limitations

- A valid compromised device can send many individually bounded requests until revocation; production must verify a shared per-device request and accepted-event quota plus alerts at the trusted edge.
- Android Keystore and sandboxing do not protect plaintext on a rooted, instrumented, unlocked, or otherwise compromised running device. There is no hardware attestation or remote wipe.
- SMS broadcast delivery depends on Android/OEM policy and the application's distribution eligibility. No historical backfill exists.
- Raw events are plaintext to authorized Relay administrators and database operators; operational access control, backups, monitoring, database least privilege, and secret management remain deployment responsibilities.
- Server purge is a provided maintenance command, not an in-process scheduler. Operators must schedule it.
- The release APK is unsigned and not deployable until an operator performs controlled signing. No production deployment was performed.
- Configuration publication has strict schema validation but no multi-party approval workflow. Protect administrator accounts and review broad rules.
- The repository is a delivered source directory without Git metadata; no commit, push, pull request, signing, release, or deployment action was performed.

## 23. Exact Sprint 4 proposal

Sprint 4 is proposal-only and was not implemented. The expected scope is:

- explicit user-approved image source strategy
- Android media/file ingestion
- configurable image identification/filtering
- generic binary object storage
- image event metadata
- hashing/deduplication
- secure upload
- retention
- preparation for OCR

OCR and payment-specific extraction must still be separately evaluated rather than automatically bundled. Before implementation, approve the Android source/distribution model, consent and permissions, payload/metadata fields, binary size and queue bounds, storage encryption, resumable transfer, deduplication identity, local/server retention, deletion behavior, and production abuse controls. The Sprint 3 envelope/authentication ideas may be reused for metadata, but binary content needs a separate bounded object-transfer and object-storage design. See `docs/sprint-4-proposal.md`.

