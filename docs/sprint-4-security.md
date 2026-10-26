# Sprint 4 security review

## Scope and method

This review covers the Sprint 4 Android source and consent flow, encrypted binary outbox, upload protocol, backend authorization and validation, database constraints, filesystem storage adapter, administrator APIs, retention, deletion, logging, and race-sensitive behavior. The review combined direct source inspection, negative-path tests, exact APK manifest inspection, SQLite tests, and a final real PostgreSQL run.

No penetration test was performed. No external dynamic application scanner or cloud object store was used. Attempts to obtain three additional delegated reviews were blocked by the environment's usage limit, so their output is not counted as review coverage; the primary engineer completed the review directly.

## Result

No unresolved release-blocking security finding was identified in the reviewed Sprint 4 scope. One transaction-boundary defect was found during review and fixed before delivery: a conflicting event identifier could remove the finalized blob but leave its new `binary_objects` row under SQLite savepoint behavior. Object insertion now remains in the outer transaction, finalization explicitly rolls back on failure, and a regression test confirms that a conflict leaves exactly one durable object and one blob. The final SQLite and PostgreSQL suites include that test.

## Threat review

| Risk | Control and verification |
|---|---|
| Android permission/source model | Uses user-initiated `ACTION_OPEN_DOCUMENT_TREE` and persisted read access. No broad storage/media permission was added. Enumeration is limited to direct children of the selected tree. |
| Consent override | Local image consent is stored locally and is a mandatory gate. Remote configuration cannot grant consent or Android access. Revocation stops new collection; local consent revocation also clears queued Relay-owned encrypted copies. |
| Backfill/privacy boundary | A source approval watermark excludes files observed before approval by default. No historical import exists. No WhatsApp/private API, accessibility, UI, screen, notification-content, root, or app-private-storage access exists. |
| Malicious metadata and malformed images | Android bounds-only decoding and backend structural JPEG/PNG/WEBP parsing validate actual bytes, dimensions, pixel count, MIME, and size. Names, identifiers, timestamps, dimensions, and configuration fields are length/range bounded. |
| MIME spoofing | Declared MIME is restricted at initialization and checked again against the finalized file signature. Unsupported or mismatched content fails before object commit. |
| Decompression bombs | Maximum object size is 8 MiB, each dimension is at most 12,000 pixels, total pixels are at most 40 million, and the backend does not fully decode image pixels. Future processors must preserve independent decode limits. |
| Path traversal | Clients never submit storage paths. The adapter generates staging/object paths, rejects absolute and parent-traversal keys, and bounds reads and appends. Object storage keys are absent from API responses. |
| Upload-session hijacking | Every initialize, chunk, and finalize operation requires an authenticated active device and exact device plus installation ownership. Revoked/disabled credentials fail. |
| Cross-device object access | Device endpoints expose only owned upload sessions. Binary metadata/content/delete endpoints require administrator authentication. Dedupe is installation scoped, so responses do not disclose another device's content. |
| Hash mismatch | Android hashes actual bytes with SHA-256. The backend recomputes the complete finalized hash and rejects mismatch. Event identity, upload identity, and content identity remain separate. |
| Chunk overlap/order | Chunks are at most 256 KiB and accepted only at the exact current offset. An exact replay of already stored bytes is duplicate-safe; gaps, conflicting overlap, negative/bad offsets, oversized chunks, and declared-size overflow are rejected. |
| Lost response/retry | Android persists the server session and acknowledged offset inside encrypted state. It reconciles the server offset, retries exact chunks, renews credentials once, and treats accepted/duplicate completion as durable success. |
| Disk exhaustion | Core limits include 8 MiB per object, 256 KiB per request, active-session limits, initialization-rate limits, per-device stored/reserved-byte quota, bounded reads, and expired-session cleanup. The Android queue is also count/byte/age bounded. Edge rate limiting is still required for production defense in depth. |
| Abandoned uploads | Sessions have configurable expiry (24 hours by default); purge removes expired staging files and rows. Staging paths are generated and isolated. |
| Quota bypass/races | PostgreSQL finalization/initialization locks the device row before quota and session decisions. Database uniqueness protects upload and content identities. Real PostgreSQL concurrency tests cover active-session and concurrent-finalize behavior. |
| Local queue disclosure | Relay-owned image copies and their private metadata are AES-GCM encrypted with an Android Keystore key in no-backup storage. Queue key, ciphertext size, creation time, and SHA-256 used for uniqueness remain database columns. Rooted-device compromise is outside the Android Keystore guarantee. |
| Source disappearance/access withdrawal | New enumeration stops when persisted access is missing. Missing source bytes produce a bounded error and queued state is handled without plaintext fallback. Already encrypted, consented queue items may continue only while local consent and authenticated upload gates remain true. |
| Logs | No bytes, base64, thumbnails, source URI, filename, token, credential, or full content hash is logged. Server logs use identifiers, counts/status, byte count, and request ID. |
| Retention/deletion | Object and linked image-event expiry are aligned. Purge removes expired objects and staging sessions; administrator deletion and per-device erasure remove blobs and rows; local completion/consent revocation removes queued encrypted copies. |
| Object-store authorization | The storage adapter is reachable only through authenticated application services. Content download is administrator-only and returns `no-store`, `nosniff`, verified MIME, and a generated safe attachment filename. |
| Race conditions | Per-device row locks and database uniqueness serialize quota-sensitive work. Concurrent finalize is idempotent. The failed-finalization orphan condition found during review is covered by a regression test in both database modes. |

## Residual risks and operational requirements

- The filesystem adapter is suitable for a single-node/local deployment. A production multi-node deployment needs a separately reviewed shared object-store adapter with encryption, service identity, bucket/container policy, lifecycle, backup, and audit controls.
- The backend performs bounded structural image validation rather than a complete standards-compliant decode. Any future OCR or image processor must run in a resource-constrained parser sandbox and treat stored bytes as hostile.
- Purge is an operator/CLI action and must be scheduled and monitored by the deployment platform. The application does not contain an in-process scheduler.
- Core repository quotas prevent unbounded storage per device, but production must also apply edge request/concurrency limits, global capacity monitoring, alerting, and disk/volume quotas.
- A database outage during compensating cleanup can leave an upload session requiring reinitialization. Blob creation fails closed and cleanup is retried by retention operations, but cross-system atomicity cannot be guaranteed by a filesystem plus database transaction.
- The encrypted outbox keeps the SHA-256 column in plaintext for local uniqueness. This exposes equality/presence information to an attacker who already has offline access to the app-private database; image bytes and descriptive metadata remain encrypted.
- Android Keystore protection cannot fully defend a currently unlocked rooted or instrumented device. Device disable/revocation and server-side retention remain required lost-device controls.
- Role granularity is the existing administrator model; Sprint 4 does not add tenant roles or per-object grants.

## Verified evidence

- Backend: Ruff passed, Black check passed, strict MyPy passed, and 153 SQLite tests passed with 7 PostgreSQL-only tests excluded.
- PostgreSQL 16.15: migration upgrade, drift check, downgrade, re-upgrade, second drift check, live API smoke, and all 160 tests passed.
- Android: ktlint, 42 unit tests, debug/release lint, debug/release assembly, and API 35 instrumentation passed; the external backend round-trip test was skipped because no E2E endpoint/credential arguments were supplied.
- APK inspection confirmed no storage/media, camera, contacts, call-log, location, microphone, accessibility-service, or notification-listener permission.
