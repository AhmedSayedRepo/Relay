# Relay Sprint 5 delivery report

## 1. Implementation summary

Sprint 5 is implemented as a backend-side, provider-neutral receipt pipeline. Finalized Sprint 4 images now create durable jobs without running OCR in the upload request. A bounded worker creates an immutable processing derivative, executes local Tesseract, stores generic OCR provenance, classifies receipts deterministically, parses InstaPay v1 fields, creates a separate receipt candidate, links duplicates, and exposes administrator review with an audit trail.

No external AI service, payment execution, reconciliation, Android OCR, new Android permission, production deployment, or Sprint 6 feature was added.

## 2. Full changed repository tree

The final manifest compares every included file with the exact pre-edit Sprint 4 manifest. It records 34 added files, 22 modified files, no deletions, and 218 included files after this report was added.

```text
A SPRINT_5_DELIVERY.md
A docs/sprint-5-android-evidence.json
A docs/sprint-5-architecture.md
A docs/sprint-5-ocr-evidence.json
A docs/sprint-5-postgres-evidence.json
A docs/sprint-5-security.md
A docs/sprint-5-verification-evidence.json
A docs/sprint-6-proposal.md
A migrations/versions/0005_receipt_processing.py
A src/relay/application/processing.py
A src/relay/application/processing_errors.py
A src/relay/application/processing_ports.py
A src/relay/application/receipts.py
A src/relay/domain/processing.py
A src/relay/infrastructure/image_processing.py
A src/relay/infrastructure/processing_models.py
A src/relay/infrastructure/processing_repository.py
A src/relay/infrastructure/tesseract_ocr.py
A src/relay/presentation/processing_dependencies.py
A src/relay/presentation/processing_schemas.py
A src/relay/presentation/routes/processing.py
A tests/fixtures/synthetic_receipts/README.md
A tests/fixtures/synthetic_receipts/ambiguous_amount.png
A tests/fixtures/synthetic_receipts/clean_instapay.png
A tests/fixtures/synthetic_receipts/corrupt_image.bin
A tests/fixtures/synthetic_receipts/duplicate_reference_a.png
A tests/fixtures/synthetic_receipts/duplicate_reference_b.png
A tests/fixtures/synthetic_receipts/glare_noise_instapay.png
A tests/fixtures/synthetic_receipts/low_contrast_instapay.png
A tests/fixtures/synthetic_receipts/malformed_reference.png
A tests/fixtures/synthetic_receipts/missing_reference.png
A tests/fixtures/synthetic_receipts/non_receipt.png
A tests/fixtures/synthetic_receipts/rotated_instapay.png
A tests/test_processing.py
M .env.example
M Dockerfile
M README.md
M artifacts/README.md
M artifacts/relay-agent-0.4.0-debug.apk
M compose.yaml
M migrations/env.py
M pyproject.toml
M requirements-dev.lock
M requirements.lock
M src/relay/application/binaries.py
M src/relay/application/binary_ports.py
M src/relay/application/event_ports.py
M src/relay/infrastructure/cli.py
M src/relay/infrastructure/config.py
M src/relay/infrastructure/event_repository.py
M src/relay/infrastructure/repositories.py
M src/relay/presentation/app.py
M tests/test_cli.py
M tests/test_config.py
M tests/test_migrations.py
M tests/test_postgres.py
```

Generated build directories, caches, logs, local settings, object data, and the final ZIP are excluded from the source manifest.

## 3. Sprint 1–4 baseline verification

Before edits, the Sprint 4 manifest matched exactly: 184 expected and current files, with zero missing, extra, or different files. Ruff passed, Black checked 75 files, strict MyPy checked 55 source files, SQLite had 153 passes with seven PostgreSQL tests deselected, and the full disposable PostgreSQL run had 160 passes. The PostgreSQL migration cycle and live API smoke passed. Android completed 117 build tasks, 42 JVM tests, and seven API 35 instrumentation cases with one explicitly backend-dependent skip.

The Sprint 4 binary upload, finalize, object API, quota, retention, and permission behavior was covered by those passing suites. No pre-existing migration was edited.

## 4. Processing architecture

The processing domain is independent of HTTP, SQLAlchemy, Pillow, and Tesseract. Domain records model jobs, attempts, OCR documents, fields, parsed receipts, candidates, confidence, review, and failures. Application ports isolate preprocessing, object reading, OCR, parsing, and persistence. Infrastructure supplies the implementations; presentation supplies authenticated APIs.

The concrete flow and dependency direction are documented in `docs/sprint-5-architecture.md`.

## 5. Worker/job model

Image finalization only ensures a `PENDING` job. The manual worker command is:

```text
python -m relay.infrastructure.cli process-pending-images --limit 10
```

Jobs are idempotent for object, processor version, parser type, and parser version. PostgreSQL workers claim through `FOR UPDATE SKIP LOCKED`. Attempts are bounded to three with one- and two-minute transient backoff. Permanent failures stop. A stale job may be reclaimed, while attempt ownership prevents the old worker from writing over the new lease.

## 6. Image preprocessing

The original object is never changed. Pillow produces a temporary grayscale, autocontrasted PNG with EXIF orientation applied in place and maximum edge 2,400 pixels. Limits are 8 MiB source bytes, 12,000 pixels per edge, 20 million pixels, and an 80 MiB estimated decoded surface. Corrupt, unsupported, bomb-like, and excessive inputs have explicit failure states.

## 7. OCR provider decision

The application depends on `OcrProvider`, not Tesseract. Tesseract 5 is the default because it is locally executable, open source under Apache-2.0, testable without Python-native bindings, and has English and Arabic trained data. Relay requires fixed absolute executable and tessdata paths. There is no external AI adapter and no network transmission of receipt data.

## 8. Actual OCR runtime verification

Tesseract `5.5.3.20260724` and both `eng` and `ara` packs were detected. A real smoke test passed. Four synthetic English variants were measured:

| Fixture | Expected anchors | Time | Result |
| --- | ---: | ---: | --- |
| Clean | 12/12, 1.000 | 281 ms | InstaPay, review required, reference valid |
| Low contrast | 12/12, 1.000 | 312 ms | InstaPay, review required, reference valid |
| Moderate glare/noise | 11/12, 0.917 | 312 ms | InstaPay, review required, damaged reference invalid |
| Raw 90° pixel rotation | 0/12, 0.000 | 375 ms | Unknown, rejected |

This small synthetic measurement does not establish general accuracy. Arabic language availability was verified; Arabic OCR accuracy was not tested or claimed.

## 9. OCR schema/storage

`ocr_results` stores job/object provenance, provider and provider version, processor version, exact requested and available languages, normalized text, bounded block JSON, mean confidence, duration, timestamps, and expiry. It is distinct from the object, raw event, and parsed candidate. A new processing version creates a new OCR record instead of overwriting history.

## 10. Receipt classification

Classification uses fixed recognizable markers. An InstaPay brand marker and a receipt-field marker are both required for `INSTAPAY_TRANSFER_RECEIPT`; otherwise the result is `UNKNOWN_RECEIPT`. Stored evidence names explain the decision. No LLM participates.

## 11. InstaPay parser v1

`InstaPayReceiptParser v1` consumes only OCR lines. It deterministically parses explicit amount/currency, date/time, original and normalized reference, optional sender, recipient, recipient identifier, and transfer status. It does not invent missing values or timezone data.

## 12. Extracted field schema

Each field records its name, normalized value, original text, numeric confidence, status, and evidence. Money uses `Decimal` and `NUMERIC(18,2)`. Currency must be explicit and three letters. Receipt datetimes are local timezone-free values with separate `KNOWN` or `UNKNOWN` state. Reference normalization preserves the source span and accepts 8–40 uppercase alphanumeric characters after separator removal.

## 13. Confidence/review model

Field states are `CONFIDENT`, `UNCERTAIN`, `MISSING`, or `INVALID`. Provider confidence is combined with deterministic validation. OCR confidence does not prove authenticity, so every recognized uploaded receipt begins in `NEEDS_REVIEW`. Unknown receipts are `REJECTED`. A human accept/correct action can produce `AUTO_ACCEPTED` only when amount, explicit currency, and normalized reference exist.

## 14. Duplicate-detection design

Duplicate identity is the normalized reference within the same device and receipt type. It does not rely on image hash, filename, amount/date, or OCR text equality, and it does not cross devices. A partial unique index permits one canonical candidate. Other images remain separate candidates linked with `duplicate_of_candidate_id` and reason `REFERENCE_NUMBER`. Device locking and PostgreSQL uniqueness close concurrent races.

## 15. Manual review/audit design

Administrators can inspect OCR, field evidence, the existing protected original-image endpoint, duplicate provenance, and audit history. `ACCEPT`, `CORRECT`, and `REJECT` requests are allowlisted and validated. The immutable machine result is separate from the reviewed result. Each audit row stores reviewer identity, action, timestamp, and bounded before/after snapshots without credentials.

## 16. Database migration/schema

`0005_receipt_processing` creates `processing_jobs`, `processing_attempts`, `ocr_results`, `receipt_candidates`, and `receipt_review_audit`, with foreign keys, checks, bounded indexes, expiry indexes, version uniqueness, and canonical-reference uniqueness.

The exact SHA-256 hashes for unchanged migrations are:

```text
0001  1c831591086c6f1ee960f638f5e7bebf49e7c7218ef6ba5465f64c9cd93f7e83
0002  343e1739be7418d87173b75d16c4b9fb51cc31b407ddea31968d34dca2a416a3
0003  84fd14c378ab89ec5fcacc57f208354d3093e0b823bfa45010b1845024efaa07
0004  31132b84837c2409812a3dd8c6519623853da57418d26c1e22c175a1cea49881
```

## 17. APIs

All new endpoints require an active administrator and receive `Cache-Control: no-store`:

- `POST /api/v1/objects/{object_id}/process`
- `GET /api/v1/processing/jobs`
- `GET /api/v1/processing/jobs/{job_id}`
- `GET /api/v1/receipts`
- `GET /api/v1/receipts/{candidate_id}`
- `POST /api/v1/receipts/{candidate_id}/review`

Lists have limit `1..100`, bounded offset, and typed filters. Errors never include OCR or receipt text.

## 18. Retention/deletion

Processing expiry follows the source image. Object, raw-event, and device deletion cascade through jobs, attempts, OCR, candidates, and audits. The processing purge runs before existing binary/event cleanup. If an expired canonical has surviving duplicates, one is deterministically promoted and all others are relinked before deletion, so the uniqueness rule cannot block retention.

## 19. Security review

The review found three material themes: stale workers could overwrite newer results, canonical deletion could violate the partial unique index with multiple duplicates, and native OCR required stronger authenticity and resource boundaries. The implementation now validates attempt ownership, explicitly promotes surviving duplicates, requires review of all recognized uploads, reduces the decoded limit, transposes in place, strictly bounds Tesseract, and adds a separate constrained Compose worker.

Residual production work includes a narrow worker database role, stronger per-job OS sandboxing, production object storage, outbound-network enforcement evidence, and dependency/container vulnerability scanning. Docker configuration was reviewed statically because Docker is not installed. The supplied directory is not a Git repository, so no hosted immutable diff scan or Git status exists; the SHA-256 manifest is the change-control evidence.

## 20. Backend tests

Final quality results:

- Black: 89 files unchanged.
- Ruff: all checks passed.
- strict MyPy: no issues in 67 source files.
- focused processing/migration suite: 14 passed.
- SQLite mode with real OCR enabled: 171 passed, eight PostgreSQL tests deselected, 134.81 seconds.
- Full suite with disposable PostgreSQL and real OCR: 179 passed, 120.41 seconds.

Tests cover provider behavior and bounds, parsing, confidence, review, authorization, logs, idempotency, versions, duplicates, stale leases, retention, and inherited Sprint 1–4 behavior.

## 21. PostgreSQL results

A fresh loopback-only PostgreSQL 16.15 cluster completed:

```text
upgrade head -> drift clean -> downgrade base -> re-upgrade head -> drift clean
```

The live Uvicorn/PostgreSQL smoke had 17 successful checks. All 179 tests passed against the disposable database, including concurrent same-reference processing, cross-device isolation, stale-worker rejection, database constraints, and canonical promotion with two surviving duplicates. Random test credentials were discarded and are not in the evidence.

## 22. Android regression/build/permission results

No Android source changed. A clean JDK 21/API 36 build ran `ktlintCheck`, debug and release unit tests, debug and release lint, and both APK assemblies: 123 tasks executed in 3m 32s. The 42 JVM tests passed in both variants, for 84 executions.

On the API 35 `RelayTestApi35` emulator, seven instrumentation tests produced six passes, one explicit backend-dependent skip, and zero failures. The APK manifest still has only Internet, network state, incoming SMS, wake lock, boot, foreground-service, and the app-specific receiver permission. No storage/media or new Sprint 5 permission appears.

Fresh artifacts:

```text
75A02C0E1A6A2A670B5EB16EE0B82E4CE4DD3E56CE445E60619E15795DF5A399  relay-agent-0.4.0-debug.apk
A851CA0298B4F19CAD564F64D3E9591F714B2F599AAA446AE3D6909226045249  relay-agent-0.4.0-release-unsigned.apk
```

The release artifact remains unsigned and is not deployable without operator-controlled signing.

## 23. Synthetic test corpus

All committed fixtures are generated and contain no private receipts. The corpus contains clean, rotated, low-contrast, glare/noise, missing-reference, malformed-reference, ambiguous-amount, two visually different same-reference images, non-receipt, and corrupt inputs. Parser unit tests also include deterministic mixed Arabic/English text. Only English image accuracy was measured.

## 24. Known limitations

- Raw pixel rotation without EXIF orientation is not detected.
- Arabic packs are available, but Arabic image accuracy is unverified.
- Tesseract/Pillow native processing has container limits but no hardened disposable per-job sandbox.
- The worker shares the application database role and read-only access to the complete object volume.
- The filesystem object adapter remains for local/single-node use.
- Human review does not prove bank authenticity; there is no bank API, reconciliation, four-eyes approval, RBAC, tenant model, or tamper-evident external audit sink.
- The worker is an operator/scheduler-invoked bounded batch, not a production queue service.
- Docker runtime validation and hosted diff/dependency scans were unavailable in this environment.

## 25. Exact Sprint 6 proposal

Sprint 6 should add operational views over reviewed/final data: a transaction dashboard; processing and review queues; bounded search, filtering, sorting, and date ranges; currency-separated summary totals; duplicate-reference alerts; device/source reporting; safe retention administration; and audited Excel export with formula-injection protection. Machine extraction, OCR provenance, source objects, duplicate groups, and audit history must remain traceable from each final transaction.

Optional AI fallback, additional bank/wallet parsers, multi-user tenant authorization, production object storage, and production deployment/monitoring stay separately scoped later work. No part of this proposal is implemented in Sprint 5.
