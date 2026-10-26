# Relay Sprint 6 architecture

## Decision

Sprint 6 adds an operator web application as a separate React 19, TypeScript 6, and Vite 8 project under `frontend/`. The browser consumes versioned FastAPI endpoints and has no database access. Existing Clean Architecture boundaries remain: domain values and policy are framework independent, application services depend on ports, SQLAlchemy and OpenPyXL are infrastructure adapters, and FastAPI is the presentation layer.

The application uses React Router for page composition and Radix Dialog for the accessible Help surface. A small typed fetch client supplies bearer authentication. The token is held only in React memory and is lost on refresh or sign-out. Vite emits static files to `frontend/dist`; the Python application serves that directory at `/operator/` only when it exists. The Dockerfile compiles the frontend in a Node stage and copies only the generated files into the non-root Python runtime image.

## Operational flow

```text
SMS / approved receipt images
  -> existing ingestion and object storage
  -> existing bounded processing, local OCR, parsing, and duplicate detection
  -> Sprint 6 read models and operator actions
  -> dashboard, transaction evidence, queues, reporting, and Excel export
```

All dashboard and reporting date windows use the UTC timestamp at which Relay created the receipt candidate. Start and end dates are inclusive to the operator; the application converts the end into an exclusive UTC boundary. The maximum span is 366 days. This basis is displayed in the product and Help.

## Read and action models

`OperationService` coordinates read-only operational queries and three actions: duplicate-resolution annotation, safe processing retry, and export. `OperationRepository` is the application port. `SqlOperationRepository` joins existing job, OCR, candidate, device, and audit records into API-safe projections. Queries remain server-side and use typed filters, allowlisted sort columns, bounded list sizes, and bounded offsets.

The transaction list returns candidate, source, device, processing, review, canonical, and duplicate fields. Details add field-level machine extraction, OCR provenance, protected image URL, review history, and duplicate-resolution history. Duplicate groups are determined by the existing Sprint 5 device-scoped reference identity; Sprint 6 does not recalculate identity, merge candidates, or delete evidence.

## Operator audit

Migration `0006` adds `operator_audit` for `DUPLICATE_RESOLUTION`, `PROCESSING_RETRY`, and `EXPORT`. Each row has a stable UUID, administrator actor, one valid target shape, bounded JSON context, creation timestamp, and expiry. Candidate and job target IDs are values rather than cascading foreign keys so the configured 365-day audit can outlive shorter receipt and processing retention. The actor foreign key uses `RESTRICT`.

Duplicate decisions are supplemental operator judgments. They never change the deterministic canonical/member link. Retry is permitted only for failed `OCR_TIMEOUT`, `OCR_FAILED`, or `INTERNAL_ERROR` jobs and never after five attempts. A repository transaction writes the state change and audit together. Export audit is written only after the in-memory workbook builds successfully.

## Excel adapter

OpenPyXL creates one macro-free `.xlsx` in memory and returns bytes directly. The request is capped at 5,000 transaction rows. The workbook contains `Transactions`, `Duplicates`, and `Summary`. Dates, times, amounts, and confidence values receive spreadsheet types and formats. All untrusted text strips disallowed control characters and gains an apostrophe when its first non-whitespace character is `=`, `+`, `-`, or `@`.

## Help and localization

Help is a typed registry of stable topic IDs, English titles, summaries, steps, and related-topic IDs. Every page declares a contextual topic. Tests fail when related links or contextual IDs do not resolve. React renders content as text; there is no HTML interpreter. Page/navigation strings start in `i18n/en.ts`, and stable IDs allow additional locale catalogs later without changing routes or behavior. Sprint 6 remains English-only.

## Operations and retention

`OPERATOR_AUDIT_RETENTION_DAYS` defaults to 365 and is bounded to 90–2,555 days. The existing purge command now removes expired operator-audit records before the shorter-lived processing, object, and raw-event records. `OPERATOR_FRONTEND_DIR` identifies the compiled static directory; production must contain a valid build. Database migrations remain an explicit release step.

No Android code or permission changed. No AI, external OCR service, image manipulation detector, authenticity score, payment execution, or Sprint 7 visual redesign is present.
