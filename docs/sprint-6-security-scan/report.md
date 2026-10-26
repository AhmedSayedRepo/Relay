# Security Review: Relay Sprint 6 operator product

## Scope

Sprint 6 operator APIs, read models, audit persistence, Excel export, web frontend, migration integration, and focused tests.

- Scan mode: scoped_path
- Target kind: directory_snapshot
- Target ID: sha256:0df2005ccd1082b3ca340d1f88371e5202ac2e8ed1796467a051601efcf80efc
- Snapshot digest: codex-security-snapshot/v1:sha256:0df2005ccd1082b3ca340d1f88371e5202ac2e8ed1796467a051601efcf80efc
- Inventory strategy: custom
- Included paths: src, frontend/src, migrations, tests/test_operations.py, Dockerfile, compose.yaml, .env.example, pyproject.toml, requirements.lock, requirements-dev.lock
- Excluded paths: frontend/node_modules, frontend/dist, android/app/build, generated caches and packaged artifacts
- Runtime or test status: Backend, PostgreSQL, frontend, Android build, and API 35 instrumentation passed; Docker unavailable.
- Artifacts reviewed: Sprint 6 source and migration, frontend behavior tests, backend operation tests, PostgreSQL migration and integration evidence, Android regression and APK permission evidence
- Scan context: Standard single-pass security review. Delegated worker capacity was unavailable after preflight, so the coordinator completed the documented fallback review.

Limitations and exclusions:
- No penetration test was performed.
- No dependency or container vulnerability scanner was available.
- Docker runtime was not installed, so the image was not built.
- Excluded android source: No Sprint 6 Android source change; regression build, instrumentation, and final APK permission inspection were performed separately.

### Scan Summary

| Field | Value |
| --- | --- |
| Scan outcome | completed |
| Reportable findings | 0 |
| Severity mix | none |
| Confidence mix | none |
| Coverage | complete |
| Validation mode | Manual source review plus automated unit, integration, migration, browser, build, and Android checks. |

Canonical artifacts: `scan-manifest.json`, `findings.json`, and `coverage.json`. This report is a deterministic projection of those files.

## Threat Model

An unauthenticated network client or malicious receipt content may try to read operator data, cross device boundaries, exhaust bounded operations, inject active workbook or browser content, or erase/forge audit evidence.

### Assets

- administrator credentials and bearer tokens
- receipt images and OCR text
- transaction and duplicate provenance
- device configuration
- operator audit history
- generated workbooks

### Trust Boundaries

- browser to FastAPI
- FastAPI to PostgreSQL
- FastAPI to object storage
- untrusted receipt and OCR text to UI and Excel
- Android agent to device API

### Attacker Capabilities

- send unauthenticated or malformed HTTP requests
- control receipt text rendered or exported
- repeat searches, retries, and exports
- present duplicate references across devices

### Security Objectives

- administrator-only visibility
- device-scoped deterministic duplicate identity
- bounded resource use
- inert untrusted content
- durable attributable operator actions
- no browser persistence of bearer tokens

### Assumptions

- TLS and reverse-proxy security are deployment responsibilities
- administrators are trusted to view all enrolled devices

## Findings

### No findings

No reportable findings survived the canonical discovery, validation, and reportability gates.

## Reviewed Surfaces

| Surface | Risk Area | Outcome | Notes |
| --- | --- | --- | --- |
| Operator API authorization and data exposure | authorization-privacy | No issue found | All Sprint 6 operator reads and writes require an active administrator; receipt images use authenticated content access and no-store responses. |
| Duplicate grouping and cross-device isolation | tenant-data-isolation | No issue found | Deterministic grouping remains scoped by device and receipt type; operator views preserve device provenance. |
| Excel generation and formula injection | file-generation | No issue found | Export is macro-free, in-memory, capped at 5000 rows, typed where appropriate, and escapes formula prefixes after leading whitespace. |
| Frontend XSS, Help rendering, token handling, and caching | browser-security | No issue found | React text rendering is used without raw HTML injection; bearer tokens stay in memory; operator responses are no-store; contextual Help uses registered static topics. |
| Pagination, search, retry, and export abuse bounds | resource-abuse | No issue found | List limits, offsets, date spans, search length, retry eligibility/attempts, and export rows are server-bounded. |
| Operator audit integrity and retention | audit-integrity | No issue found | Append-only actions identify actors and durable target UUIDs. The scan found and fixed cascading source foreign keys so audit rows outlive shorter source retention. |
| Configuration visibility and error leakage | secrets-errors | No issue found | The operational device projection excludes credentials and API errors use stable safe messages without OCR or receipt content. |
| Android permission regression | mobile-permissions | No issue found | Sprint 6 changes no Android source; both built APK manifests were inspected and add no permission. |
