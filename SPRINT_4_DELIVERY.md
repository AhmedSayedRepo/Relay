# Relay Sprint 4 delivery report

## 1. Implementation summary

Sprint 4 adds a generic, consent-gated image pipeline without changing the Sprint 1–3 foundations. Android uses a user-selected Storage Access Framework tree, detects only post-approval direct-child JPEG/PNG/WEBP candidates, applies deterministic configuration rules, hashes actual bytes, and stores matching items in a Keystore-backed encrypted bounded binary outbox. A dedicated authenticated protocol initializes, resumes, validates, deduplicates, and finalizes bounded uploads. The backend stores bytes through an object-storage port, links metadata-only `images` schema v1 raw events, exposes bounded administrator APIs, and supports expiry, abandoned-session cleanup, object deletion, and per-device erasure.

No OCR, AI, payment extraction, InstaPay parser, WhatsApp-private integration, accessibility automation, UI/screen scraping, notification image scraping, root access, app-private-storage access, gallery UI, or deployment was added.

## 2. Changed repository tree

The following files differ from the captured Sprint 3 baseline. No baseline file was deleted.

```text
relay/
|-- .dockerignore
|-- .env.example
|-- .github/workflows/ci.yml
|-- Dockerfile
|-- README.md
|-- SPRINT_4_DELIVERY.md
|-- compose.yaml
|-- pyproject.toml
|-- migrations/
|   |-- env.py
|   `-- versions/0004_binary_objects.py
|-- src/relay/
|   |-- application/
|   |   |-- binaries.py
|   |   `-- binary_ports.py
|   |-- domain/
|   |   |-- agent.py
|   |   |-- binary_objects.py
|   |   `-- events.py
|   |-- infrastructure/
|   |   |-- binary_models.py
|   |   |-- binary_repository.py
|   |   |-- cli.py
|   |   |-- config.py
|   |   |-- event_models.py
|   |   |-- event_repository.py
|   |   |-- object_storage.py
|   |   `-- repositories.py
|   `-- presentation/
|       |-- app.py
|       |-- binary_dependencies.py
|       |-- binary_schemas.py
|       |-- event_schemas.py
|       |-- middleware.py
|       `-- routes/
|           |-- binary_objects.py
|           `-- events.py
|-- tests/
|   |-- conftest.py
|   |-- test_binary_objects.py
|   |-- test_migrations.py
|   `-- test_postgres.py
|-- android/app/
|   |-- build.gradle.kts
|   `-- src/
|       |-- androidTest/java/com/relay/agent/
|       |   |-- BackendRoundTripInstrumentedTest.kt
|       |   `-- EncryptedBinaryOutboxInstrumentedTest.kt
|       |-- main/
|       |   |-- java/com/relay/agent/
|       |   |   |-- RelayApplication.kt
|       |   |   |-- application/
|       |   |   |   |-- AgentController.kt
|       |   |   |   |-- ImageCollector.kt
|       |   |   |   `-- ImageUploader.kt
|       |   |   |-- data/
|       |   |   |   |-- BinaryImageCodec.kt
|       |   |   |   |-- ConfigurationCodec.kt
|       |   |   |   |-- DataStoreAgentStore.kt
|       |   |   |   |-- EncryptedBinaryOutbox.kt
|       |   |   |   |-- HttpRelayApi.kt
|       |   |   |   |-- SafTreeImageSource.kt
|       |   |   |   `-- SyncScheduler.kt
|       |   |   |-- domain/
|       |   |   |   |-- Images.kt
|       |   |   |   `-- Models.kt
|       |   |   `-- presentation/
|       |   |       |-- AgentViewModel.kt
|       |   |       `-- MainActivity.kt
|       |   `-- res/values/strings.xml
|       `-- test/java/com/relay/agent/
|           |-- application/ImageCollectorTest.kt
|           |-- application/ImageUploaderTest.kt
|           `-- data/ConfigurationCodecTest.kt
|-- docs/
|   |-- sprint-4-android-evidence.json
|   |-- sprint-4-architecture.md
|   |-- sprint-4-postgres-evidence.json
|   |-- sprint-4-security.md
|   |-- sprint-4-verification-evidence.json
|   `-- sprint-5-proposal.md
`-- artifacts/
    |-- README.md
    |-- relay-agent-0.4.0-debug.apk
    `-- relay-agent-0.4.0-release-unsigned.apk
```

## 3. Sprint 1–3 baseline verification

Before modification, the complete repository and Sprint 1–3 delivery/architecture/security documents were inspected. The captured Sprint 3 ZIP SHA-256 was `2eff281ee0869128afe09ac3919174f59ed7b84de590983cf3d655864fe5d6ca`. Backend verification passed 147 non-PostgreSQL tests plus 6 PostgreSQL tests. Android passed 36 JVM tests; API 35 instrumentation passed 6 tests and skipped the externally configured backend round-trip test. Backend quality gates, Android ktlint/lint/debug and release builds, migration checks, and the existing SMS emulator flow passed. The baseline APK permission manifest was recorded before changes.

The final suites in sections 17–20 re-ran the inherited behavior together with Sprint 4 coverage.

## 4. Android image-source decision and rationale

Sprint 4 uses `ACTION_OPEN_DOCUMENT_TREE` through Android's Storage Access Framework (SAF), with persisted read permission to one user-selected document-provider tree. This provides a clear picker, limits access to a user-approved location, is revocable, works on API 26–36, and requires no broad storage or media permission. Relay enumerates direct children only and does not crawl arbitrary storage.

The approval timestamp is a collection watermark: candidates with a provider modification time at or before approval are excluded. Historical import is neither enabled nor implemented. `ImageSource`, `ImageCandidate`, and `ImageCollector` keep source acquisition generic so a separately reviewed MediaStore source can be added later.

## 5. Consent/access design

New image collection requires all five gates:

```text
local image consent
AND active device/installation credential
AND remote collectors.images.enabled
AND configured source matches the approved source
AND persisted Android read access still exists
```

Source selection and consent are user-initiated. The server cannot set local consent or grant OS access. Local denial always wins. Revoking consent immediately prevents new collection and clears Relay-owned queued encrypted image copies. Clearing the source releases persisted URI access. OS access withdrawal stops enumeration. The UI does not repeatedly request access after denial.

## 6. Image collector architecture

`SafTreeImageSource` performs bounded direct-child discovery and returns generic candidates. `ImageCollector` validates gates and metadata, chooses a deterministic rule, reads bounded bytes only for a match, verifies bounds, computes SHA-256, and enqueues an encrypted record. WorkManager and manual sync use the same controller path. The UI shows consent, access, remote and effective states, approved source, queued count/bytes, dropped count, last success, and last error category.

Candidate fields include source ID/type, private content reference, observed/media time, MIME, optional display name, byte size, and dimensions. Private URIs and filenames are not logged.

## 7. Filtering schema

The declarative `collectors.images` configuration contains bounded `sources` and `rules`. Sprint 4 rules support source ID, MIME allowlist, filename contains/prefix/suffix, minimum/maximum byte size, and minimum/maximum width and height. Only exact deterministic matching is used. Configuration version, matched rule ID, and source identity travel with the queued record and event.

Nonmatching candidates are not read into the binary queue, uploaded, or represented as backend objects. Schema validation rejects unknown fields, arbitrary code, shell, executable rules, regex, OCR/text rules, and payment fields. An optional configurable time-window filter was considered but is not implemented; the approval watermark supplies the required no-backfill behavior.

## 8. Hash/deduplication design

Android computes SHA-256 from the actual complete image bytes. Local uniqueness uses content hash, so renaming a file does not evade duplicate detection. Event UUID, upload UUID, and content hash remain distinct.

Server dedupe is scoped to installation and protected by `(installation_id, content_hash)` uniqueness. Stable `(installation_id, upload_id)` initialization makes retries idempotent. Dedupe responses never reveal another device's objects. PostgreSQL device-row locking serializes quota-sensitive initialization/finalization; concurrent finalization returns accepted/duplicate semantics without duplicating storage.

## 9. Encrypted binary outbox

The image queue is separate from the Sprint 3 JSON/SMS outbox. The complete image record, bytes, filename, private reference, metadata, server session ID, and acknowledged offset are AES-GCM encrypted with a distinct Android Keystore key in the app's no-backup directory. It survives process death, restart, reboot, offline periods, and lost responses. Database columns retain only queue key, ciphertext size, creation time, and content hash for uniqueness.

Completion removes the encrypted record. Source disappearance is reported as a bounded error with no plaintext fallback. Consent revocation clears queued image records.

## 10. Capacity/quota policy

Android limits are 20 queued images, 32 MiB total encrypted queue size, 8 MiB per image plus bounded encryption/metadata overhead, and 7 days local retention. Capacity uses deterministic oldest-first eviction and increments the visible dropped count.

Backend limits are 8 MiB per object, 12,000 pixels per dimension, 40 million pixels, 256 KiB per chunk/request, configurable active sessions per device, configurable upload initializations per hour, configurable stored plus reserved bytes per device, and 24-hour upload-session expiry by default. Core bounds are repository enforced. Production edge request/concurrency limiting and global storage monitoring remain required defense in depth.

## 11. Upload protocol

1. Android initializes with authenticated device/installation identity, stable upload/event IDs, SHA-256, MIME, size, dimensions, source, config version, rule, and timestamps.
2. The backend validates the active installation, current config/rule/source, request fingerprint, limits, rate, quota, and installation-scoped duplicate state.
3. The server returns a stable session and acknowledged offset, or a finalized duplicate.
4. Android sends at most 256 KiB at the exact offset. Exact replay is duplicate-safe; gaps, bad/negative offsets, conflicting overlap, overflow, and oversized chunks fail.
5. Android persists acknowledged progress and reconciles server offsets after loss. It renews credentials once on authentication expiry.
6. Finalization verifies complete size, SHA-256, actual JPEG/PNG/WEBP structure, and dimensions before an atomic staging-to-object move.
7. Object metadata and `images` raw-event linkage commit together. Accepted/duplicate durable completion removes the local item.

Initialization, chunks, and finalization are installation-bound and safely retryable. A failed event conflict rolls back metadata and removes the newly moved blob; a regression test covers this orphan-prevention path.

## 12. Object storage abstraction

Application code depends on `ObjectStoragePort`; domain/application layers do not know filesystem paths or a cloud vendor. The filesystem adapter creates generated staging and `objects/<installation>/<uuid>.blob` paths, rejects absolute/parent traversal, bounds reads/appends, fsyncs chunks, and uses atomic replacement. Docker Compose mounts a named `relay_object_data` volume at `/var/lib/relay/objects`.

The port can be implemented by a future S3-compatible, Azure Blob, or other provider adapter without changing domain/use-case code. No paid provider is required or verified in Sprint 4.

## 13. Database migration/schema

Migration `0004_binary_objects` adds `binary_objects`, `binary_upload_sessions`, and nullable `raw_events.object_id`. It defines foreign keys, installation-scoped upload/content uniqueness, checked MIME/size/dimension/state fields, source/config/rule metadata, acknowledged byte progress, storage/finalization/expiry fields, and quota/purge indexes. Object deletion cascades to linked image events.

Migrations `0001`, `0002`, and `0003` were not modified. Their final SHA-256 values remain:

- `0001`: `1c831591086c6f1ee960f638f5e7bebf49e7c7218ef6ba5465f64c9cd93f7e83`
- `0002`: `343e1739be7418d87173b75d16c4b9fb51cc31b407ddea31968d34dca2a416a3`
- `0003`: `84fd14c378ab89ec5fcacc57f208354d3093e0b823bfa45010b1845024efaa07`

## 14. Raw-event linkage

Collector `images`, schema version 1, reuses the generic Sprint 3 raw-event model. JSON stores only object ID and bounded metadata: content hash/MIME/size/dimensions, source type/ID, config version, rule ID, and media timestamp. Image bytes are never embedded as base64 or JSONB. There is no extracted text, amount, reference, sender, transaction, OCR, or payment field.

## 15. Admin APIs

- `GET /api/v1/events?collector=images` lists bounded image events.
- `GET /api/v1/events/{id}` returns image event metadata.
- `GET /api/v1/objects/{id}` returns object metadata without a storage key.
- `GET /api/v1/objects/{id}/content` downloads administrator-authorized content with verified MIME, safe generated attachment filename, `Cache-Control: no-store`, and `X-Content-Type-Options: nosniff`.
- `DELETE /api/v1/objects/{id}` deletes the object and linked event.
- `DELETE /api/v1/devices/{id}/image-data` erases the device's objects, linked events, and upload sessions.

All object/event administrator routes use the existing explicit administrator bearer authentication. Device upload routes use active device credentials and installation binding.

## 16. Retention/deletion

Finalized objects and linked events share configurable retention, 14 days by default. Active upload sessions expire after 24 hours by default. `purge-expired-events` removes expired staging files/sessions and finalized object blobs/rows together with linked events. Administrator object deletion and per-device image-data erasure remove storage and database state. Android deletes its encrypted queue copy only after durable accepted/duplicate completion or local consent revocation.

The deployment must schedule and monitor purge execution; no in-process scheduler was added.

## 17. Backend tests

Final local verification passed:

- Ruff: passed.
- Black check: passed; 75 files unchanged.
- Strict MyPy: passed across 55 source files.
- Pytest with SQLite: 153 passed, 7 PostgreSQL-only tests deselected.

Coverage includes initialization, valid/duplicate upload, hash/MIME/size/dimension validation, wrong installation, revoked/disabled devices, source/config/rule validation, active/rate/byte quotas, exact/gap/overlap/duplicate/oversized chunks, final-size mismatch, abandoned cleanup, idempotent/concurrent finalize, raw-event linkage/conflict cleanup, cross-device isolation, administrator access, unauthorized access, download headers, deletion, per-device erasure, purge, and log redaction.

## 18. PostgreSQL results

PostgreSQL 16.15 was started as a fresh isolated server. The final run passed:

- upgrade through migration 0004;
- schema drift check: no new upgrade operations;
- downgrade;
- re-upgrade;
- second schema drift check: no new upgrade operations;
- live Uvicorn/PostgreSQL authentication, device, configuration, event, duplicate, disable, and authorization smoke checks;
- all 160 tests in 72.88 seconds, including 7 PostgreSQL-specific tests and real row-lock/concurrent-finalize coverage.

The database was stopped cleanly. Machine-readable details are in `docs/sprint-4-postgres-evidence.json`.

## 19. Android tests/build/lint/instrumentation

The final Android quality matrix passed `ktlintCheck`, 42 debug JVM unit tests, `lintDebug`, `lintRelease`, `assembleDebug`, and `assembleRelease` (117 Gradle tasks). Unit coverage includes consent/source/config gates, deterministic filters, MIME/size/dimension validation, hashing/dedupe, upload retry, lost response, offset resume, credential renewal, source disappearance, access withdrawal, queue cleanup, and inherited disabled/config/SMS behavior.

API 35 emulator instrumentation recorded 7 tests: 6 passed, 1 skipped, 0 failed/errors. Android media/SAF and Keystore-backed encrypted SMS/binary outbox behavior ran. The backend round-trip instrumentation test was skipped because no external E2E endpoint and credentials were supplied; this is not reported as executed coverage. CI now includes API 35 instrumentation through the Android emulator runner.

APK SHA-256:

- Debug: `b4ee1f78be41bac404420f34203be2fc21b845052e4511a328354a1c3d583ced`
- Unsigned release: `a851ca0298b4f19cad564f64d3e9591f714b2f599aaa446ae3d6909226045249`

## 20. Exact APK permissions

`aapt2 36.0.0 dump permissions` reported these debug permissions:

- `android.permission.INTERNET`
- `android.permission.ACCESS_NETWORK_STATE`
- `android.permission.RECEIVE_SMS`
- `android.permission.WAKE_LOCK`
- `android.permission.RECEIVE_BOOT_COMPLETED`
- `android.permission.FOREGROUND_SERVICE`
- declared and used `com.relay.agent.debug.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION`

Release permissions are identical except the app-scoped permission is declared and used as `com.relay.agent.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION`.

No storage/media, camera, contacts, call-log, location, microphone, accessibility-service, or notification-listener permission is present. Image access relies on the user-selected SAF grant.

## 21. Security review

The explicit review covered Android permission/source and consent, hostile metadata, MIME spoofing, decompression bounds, path traversal, session hijacking, installation/object isolation, dedupe leaks, hash mismatch, chunk semantics, disk exhaustion, abandoned sessions, quota races, lost/rooted devices, logging, retention/deletion, object-store authorization, malformed files, and concurrent finalization.

One orphan-metadata transaction defect was found and fixed before delivery; its regression passes under SQLite and PostgreSQL. No unresolved release-blocking issue was identified. Three attempted additional delegated reviews produced no usable output because the environment usage limit blocked them, so the result relies on direct primary review and the stated automated/runtime evidence. No penetration testing is claimed. Full controls and residual risks are documented in `docs/sprint-4-security.md`.

## 22. Known limitations

- SAF UI approves one tree and enumerates direct children; multiple-source UI, recursive traversal, MediaStore, and historical import are not implemented.
- The optional configurable time-window filter is not implemented.
- Filesystem storage is a single-node/local adapter. Production multi-node use needs a reviewed shared object-store adapter and storage operations controls.
- File validation is bounded structural/header validation, not a full image decode. Future processors must treat content as hostile and run with independent sandbox/resource limits.
- Purge requires external scheduling and monitoring.
- Edge rate limiting, global capacity monitoring, volume quotas, alerting, and backup/restore validation remain production responsibilities.
- Filesystem and PostgreSQL cannot form one atomic transaction. Failure cleanup is compensating and fail-closed; a database outage during compensation may require session reinitialization and later retention cleanup.
- SHA-256 remains a plaintext encrypted-outbox index for local equality detection; private bytes and descriptive metadata are encrypted.
- Android Keystore cannot fully protect an unlocked rooted/instrumented device.
- Authorization retains the existing administrator role; tenant-specific roles/object grants are outside Sprint 4.
- The release APK is unsigned. No deployment, production cloud store, penetration test, physical-device OEM matrix, or externally configured backend instrumentation round trip was performed.
- The supplied source directory is not a Git repository, so there is no branch, Git status, commit, push, or pull request to report.

## 23. Exact Sprint 5 proposal

Sprint 5 is proposal only. Nothing in this section is implemented in Sprint 4.

- OCR/extraction pipeline
- processor interface
- deterministic preprocessing
- confidence/error model
- structured extraction schema
- optional provider abstraction
- rules-first extraction where practical
- payment-receipt candidate processing
- explicit InstaPay/payment parser as a business module if approved
- duplicate transaction detection using extracted reference identifiers
- review/manual correction flow

AI must remain optional. OCR must not automatically mean paid AI APIs. Evaluate local/open-source OCR first and use external AI only as a bounded fallback if justified. Separate approval must precede implementation, with parser sandboxing/resource limits, privacy and provider review, no-live-personal-data test corpora, extracted-text retention, access control, provenance/versioning, and auditability defined first.
