# Relay Sprint 6 proposal

This document is a proposal only. No Sprint 6 feature is implemented in the Sprint 5 source.

## Goal

Make reviewed receipt data operationally usable without changing the collection, processing, or review provenance contracts.

## Proposed scope

1. Add an administrator transaction workspace based only on reviewed/final receipt snapshots.
2. Provide bounded search, filter, sort, and date-range queries for type, device/source, review state, duplicate state, amount, currency, and reference.
3. Add processing and review queue views that show failures, retry state, age, and workload without exposing OCR text in list endpoints.
4. Provide transaction detail linking the final values, immutable machine extraction, OCR provenance, source object, duplicate group, and audit history.
5. Add summary totals grouped by explicit currency; never combine currencies or treat rejected/unreviewed candidates as final.
6. Add duplicate-reference alerts scoped to the authorized owner/device boundary.
7. Add device/source reporting for collection volume, processing outcomes, review latency, and retention activity.
8. Add an Excel export of reviewed/final data with formula-injection protection, bounded row counts, explicit timezone state, stable column definitions, and export audit.
9. Add administrator retention operations with dry-run counts, explicit authorization, audit events, and safe canonical duplicate promotion.
10. Add pagination/query indexes only after representative PostgreSQL query plans and data volumes are measured.

## Acceptance direction

- All dashboard and export data must derive from reviewed/final snapshots, with machine provenance available separately.
- Search and totals must be deterministic, device/tenant scoped, decimal safe, currency aware, and timezone explicit.
- OCR text must remain excluded from broad list, metric, and export paths unless an explicit privileged action requires it.
- Exports must be generated asynchronously when their bounded cost exceeds an HTTP request budget.
- Existing Sprint 1–5 backend, PostgreSQL, Android, migration, retention, and security tests must remain green.

## Deferred options

Optional AI fallback, additional bank/wallet parsers, multi-user tenant authorization, production object storage, and production deployment/monitoring need separate threat models and acceptance criteria. They are not implied by the operational dashboard scope.
