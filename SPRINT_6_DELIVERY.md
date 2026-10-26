# Relay Sprint 6 delivery report

## 1. Implementation summary

Sprint 6 delivers the first practical operator-facing Relay product. An authenticated React application now covers dashboard totals, transaction and evidence inspection, review and processing queues, first-class duplicate investigation, device/configuration visibility, reports, secure Excel export, and integrated contextual Help. FastAPI read models and explicit audited actions support the browser without coupling it to persistence.

The Sprint 1–5 collection, object, local OCR, deterministic parsing, review, and device-scoped duplicate foundations were preserved. One blocking audit-retention defect discovered during security review was corrected before release: operator target UUIDs no longer cascade away with shorter-lived candidates or jobs. No AI, image manipulation analysis, authenticity score, payment action, Android feature, new Android permission, production deployment, or Sprint 7 redesign was added.

## 2. Full changed repository tree

The SHA-256 manifest compares every included source file with the exact Sprint 5 final manifest. It records 43 added files, 22 modified files, no deletions, and 261 included files. Generated dependencies, build outputs, caches, local settings, object data, earlier packaged ZIPs, and the final ZIP are excluded.

```text
A SPRINT_6_DELIVERY.md
A docs/sprint-6-architecture.md
A docs/sprint-6-security-scan/coverage.json
A docs/sprint-6-security-scan/exports/results.sarif
A docs/sprint-6-security-scan/findings.json
A docs/sprint-6-security-scan/report.md
A docs/sprint-6-security-scan/scan-manifest.json
A docs/sprint-6-security.md
A docs/sprint-6-verification-evidence.json
A docs/sprint-7-proposal.md
A frontend/eslint.config.js
A frontend/index.html
A frontend/package-lock.json
A frontend/package.json
A frontend/src/App.tsx
A frontend/src/api.ts
A frontend/src/components/HelpDialog.tsx
A frontend/src/components/Layout.tsx
A frontend/src/help/topics.ts
A frontend/src/hooks.ts
A frontend/src/i18n/en.ts
A frontend/src/main.tsx
A frontend/src/operator.test.tsx
A frontend/src/pages.tsx
A frontend/src/styles.css
A frontend/src/test/setup.ts
A frontend/src/types.ts
A frontend/src/vite-env.d.ts
A frontend/tsconfig.app.json
A frontend/tsconfig.json
A frontend/tsconfig.node.json
A frontend/vite.config.ts
A migrations/versions/0006_operator_product.py
A src/relay/application/operation_ports.py
A src/relay/application/operations.py
A src/relay/domain/operations.py
A src/relay/infrastructure/excel_export.py
A src/relay/infrastructure/operation_models.py
A src/relay/infrastructure/operations_repository.py
A src/relay/presentation/operations_dependencies.py
A src/relay/presentation/operations_schemas.py
A src/relay/presentation/routes/operations.py
A tests/test_operations.py
M .dockerignore
M .env.example
M .github/workflows/ci.yml
M .gitignore
M Dockerfile
M README.md
M artifacts/README.md
M artifacts/relay-agent-0.4.0-debug.apk
M compose.yaml
M migrations/env.py
M pyproject.toml
M requirements-dev.lock
M requirements.lock
M src/relay/infrastructure/cli.py
M src/relay/infrastructure/config.py
M src/relay/infrastructure/processing_models.py
M src/relay/infrastructure/repositories.py
M src/relay/presentation/app.py
M src/relay/presentation/middleware.py
M src/relay/presentation/processing_schemas.py
M src/relay/presentation/routes/processing.py
M tests/test_migrations.py
```

## 3. Sprint 1–5 baseline verification

Before edits, all 218 Sprint 5 manifest entries matched and there were no missing, extra, or changed included files. Black checked 89 files, Ruff passed, strict MyPy checked 67 source files, SQLite with real OCR had 171 passes and eight PostgreSQL deselections, and PostgreSQL had 179 passes. PostgreSQL migration upgrade, drift, downgrade, re-upgrade, second drift check, and 17 live API checks passed. Android completed its clean test/lint/assemble matrix and API 35 instrumentation with six passes plus one expected backend-dependent skip.

Migration `0001` through `0005` hashes still match the Sprint 5 manifest exactly. Sprint 5 OCR/parser/review/duplicate behavior remained in the final 183-test PostgreSQL run.

## 4. Frontend architecture decision

The repository had no operator frontend, so Sprint 6 introduces React 19, TypeScript 6, Vite 8, React Router, and Radix Dialog. The browser uses a typed API client and FastAPI as its only product boundary. Reusable layout, status, date, paging, and Help components keep behavior independent of Sprint 7 styling. The Dockerfile compiles pinned npm dependencies in a Node stage and copies only `dist` into the non-root Python image.

## 5. Dashboard implementation

The dashboard supports an inclusive, bounded UTC candidate-entry date range. It shows final transactions, review demand, duplicate groups/candidates, processing failures and pending work, receipt activity, SMS activity, and amounts grouped by currency. It never combines unlike currencies. Loading, error, empty, and populated states are distinct.

## 6. Transaction list/detail

The list performs server-side pagination, reference/sender/recipient search, date filtering, review and duplicate filtering, currency filtering, and allowlisted sorting. Each row shows Relay entry time, reference, amount/currency, parties, review state, duplicate state, and device. Details preserve the protected original image, source/object/job IDs, parsed and machine values, field confidence, OCR/provider provenance, duplicate relationship, and retained review/audit history.

## 7. Duplicate investigation workflow

Duplicates are a top-level product area. The group list shows the matching reference, canonical ID, amount/currency, device, and group size. The detail page server-paginates all members and distinguishes canonical from duplicate candidates while showing receipt image, timestamps, amount, reference, device/source, status, confidence, and links to full evidence. Different screenshots remain separate traceable candidates.

## 8. Duplicate resolution/audit model

The Sprint 5 normalized-reference identity within one device and receipt type remains authoritative. An operator may record `UNRESOLVED`, `CONFIRMED_DUPLICATE`, or `NOT_DUPLICATE` with an optional 500-character note. The action records actor, target, decision, note, original deterministic relationship, and time. It never rewrites or deletes the canonical/member link.

## 9. Review queue

The queue returns only candidates needing review and supports bounded date range, search, oldest/newest order, device filtering at the API, and pagination. The transaction detail retains existing `ACCEPT`, `CORRECT`, and `REJECT` behavior and shows machine data beside the reviewed result.

## 10. Processing queue

The processing list now returns explicit paging metadata and supports status filtering. Retry is offered only for failed `OCR_TIMEOUT`, `OCR_FAILED`, or `INTERNAL_ERROR` work and is capped at five attempts. The state change and audit are transactional; other failures display no action.

## 11. Reporting

The report summary uses the same bounded operational date basis as the dashboard. The UI states the workbook scope before download. Currency remains an explicit export filter and all summaries keep currency totals separate.

## 12. Excel export structure

The macro-free workbook contains `Transactions`, `Duplicates`, and `Summary` sheets. Transactions include date/time, decimal amount, currency, reference, parties, review/duplicate status, canonical/group data, reason, source, device, receipt type, received time, processing status, and candidate ID. The duplicate sheet focuses on group/canonical/member evidence, confidence, reason, and protected image path. Summary records scope, filters, counts, failures, groups, candidates, and per-currency totals.

## 13. Excel security/formula-injection verification

OpenPyXL generates at most 5,000 rows in memory. Relay never writes the workbook to server storage and audits only a successful build. Untrusted strings remove unsafe control characters and receive a leading apostrophe when the first non-whitespace character is `=`, `+`, `-`, or `@`. Automated tests reopen the workbook, verify all three sheets and typed values, and exercise every dangerous prefix.

## 14. Help/Guide architecture

Help uses a typed, static registry of stable topic IDs with title, summary, steps, and related IDs. A searchable modal is available globally, relevant screens pass their contextual topic ID, and a full Help page is present. React renders text without HTML interpretation. Tests validate registry uniqueness, every contextual target, every related link, safe rendering, search, and default-locale page/navigation coverage.

## 15. Help topics delivered

The 25 topics cover getting started; device setup and states; SMS collection; image collection; configuration; receipt processing; duplicate detection; manual review; processing statuses and errors; reporting and Excel export; privacy and security; troubleshooting and FAQ; dashboard overview; date ranges; transaction search; transaction evidence; duplicate groups; duplicate evidence; duplicate decisions; review queue; processing queue; safe retry; reports/export; device/configuration status; and audit history.

## 16. Contextual Help behavior

Dashboard, transactions, transaction detail, duplicate list, duplicate comparison, review queue, processing queue, reports, devices, and Help each open directly to a registered relevant topic. Operators can search or follow related-topic buttons without leaving their work screen.

## 17. Localization readiness

Navigation and page strings start in `i18n/en.ts`; Help content has stable language-independent IDs and no route or database dependency. Tests assert the English navigation/page key set and registry integrity. Additional locale catalogs and a locale selector remain future work; Sprint 6 ships English only.

## 18. Configuration/device operational views

The device view shows status, last contact, external installation ID, app and configuration version, collection configuration, transaction count, duplicates, and review demand. The API projection does not include device credentials, refresh tokens, administrator secrets, or storage keys.

## 19. Backend read models/APIs

New authenticated endpoints are:

- `GET /api/v1/dashboard`
- `GET /api/v1/transactions`
- `GET /api/v1/transactions/{candidate_id}`
- `GET /api/v1/duplicate-groups`
- `GET /api/v1/duplicate-groups/{group_id}`
- `POST /api/v1/duplicate-groups/{group_id}/members/{candidate_id}/resolution`
- `GET /api/v1/review-queue`
- `POST /api/v1/processing/jobs/{job_id}/retry`
- `GET /api/v1/operations/devices`
- `GET /api/v1/reports/summary`
- `GET /api/v1/reports/export.xlsx`

Typed schemas expose product projections rather than SQLAlchemy rows. Lists cap `limit` at 100 and offsets at 1,000,000; search is at most 64 characters; date spans are at most 366 days.

## 20. Database migration/schema if any

`0006_operator_product` adds `operator_audit` with actor, action, candidate/job target, JSON context, creation/expiry, target-shape checks, and actor/target/expiry indexes. It also adds candidate created-time, currency/date, and duplicate/date indexes for operator queries. Upgrade, downgrade, re-upgrade, autogeneration drift checks, and PostgreSQL behavior passed. Revisions `0001`–`0005` were not edited.

## 21. Authorization/privacy

All operator endpoints require an active administrator. Receipt content remains behind the existing protected endpoint. Operator responses are `no-store`; `/operator` adds a restrictive CSP, no-referrer policy, and restricted browser permissions. The token stays in memory and is cleared on refresh/sign-out. The header-token model does not use ambient cookies, so cookie CSRF is not applicable. No OCR or receipt content is sent to an external service or included in safe errors/logs.

## 22. Accessibility/responsive behavior

The UI uses semantic headings, navigation, forms, labels, tables, definition lists, status/alert roles, accessible dialog primitives, visible keyboard focus, descriptive image alternatives, disabled paging states, and reduced-motion handling. Grid and navigation layouts adapt at 850 px and 520 px. Automated behavior tests use roles and labels; a formal WCAG audit and manual assistive-technology test were not performed.

## 23. Backend tests

Final results:

- Black: 100 files unchanged.
- Ruff: all checks passed.
- strict MyPy: no issues in 76 source files.
- focused Sprint 6 operations/static/migration tests: eight passed in 24.13 seconds.
- SQLite/non-PostgreSQL suite with real local Tesseract: 175 passed, eight deselected in 113.99 seconds.
- full suite with real PostgreSQL and real local Tesseract: 183 passed in 136.37 seconds.

Coverage includes authorization, dashboard/date/currency summaries, pagination/filter/sort/search, duplicate group and canonical relations, device isolation, review/processing queues, eligible retry, Excel contents and filters, formula defense, audit, source-retention compatibility, and sensitive-log protection.

## 24. PostgreSQL results

A fresh loopback-only PostgreSQL 16.15 cluster passed:

```text
upgrade head -> no drift -> downgrade base -> re-upgrade head -> no drift
17/17 live Uvicorn/PostgreSQL API checks
183/183 full tests
clean database shutdown
```

This is disposable local PostgreSQL evidence, not production-provider validation.

## 25. Frontend tests/build/lint

Vitest passed 10 behavior/accessibility tests covering loading/error/empty dashboard states, currency totals, transaction search/date/status/currency/sort, protected details, duplicate navigation/comparison/decision, review, processing/retry, export, Help registry/navigation/context, device configuration, in-memory authentication, and authorization errors. ESLint passed. TypeScript and Vite production compilation passed with 85 modules; output was 307.83 kB JavaScript (96.18 kB gzip) and 7.67 kB CSS (2.52 kB gzip). `npm audit` reported zero vulnerabilities when the lock was created.

## 26. Android regression/build/permissions

The unchanged Android project completed `clean`, debug/release JVM tests, debug/release lint, and debug/release assembly: build successful in 10 minutes 23 seconds, 114 tasks (112 executed, two up-to-date). API 35 instrumentation recorded seven cases, six passed, zero failures/errors, and one expected backend-dependent round-trip skip.

Both final APK manifests contain only `INTERNET`, `ACCESS_NETWORK_STATE`, `RECEIVE_SMS`, `WAKE_LOCK`, `RECEIVE_BOOT_COMPLETED`, `FOREGROUND_SERVICE`, and the application-scoped non-exported dynamic-receiver permission. No storage permission or new personal-content permission was added. Debug APK SHA-256 is `7d4dca4e0d84cf750238a3ee9caf496da0a6e0f2d56c89794c44b59929a030d0`; unsigned release APK SHA-256 is `a851ca0298b4f19cad564f64d3e9591f714b2f599aaa446ae3d6909226045249`.

## 27. Security review

The canonical standard scan reviewed authorization, image/OCR/receipt exposure, device-scoped duplicates, workbook injection and generation, browser XSS, Help rendering, bounds, export abuse, audit integrity, configuration exposure, token/cache/error behavior, and Android permissions. It reports zero remaining findings. During review, candidate/job cascade foreign keys were identified as incompatible with longer audit retention and removed before final verification; a regression test proves the audit survives source deletion.

This was not penetration testing. Dependency/container scanning was unavailable. The supplied directory is not a Git repository, so SHA-256 manifests provide local change-control evidence rather than a hosted immutable diff.

## 28. Known limitations

- Docker is not installed on the verification host. The multi-stage Dockerfile and generated frontend were reviewed, but an actual container build is unverified.
- No production deployment, external object store, production database, reverse proxy, TLS, edge rate limiting, backup/restore, monitoring, or administrator lifecycle was exercised.
- Sprint 6 has one administrator visibility level across devices and English-only UI content.
- Server-side bounds do not replace deployment-level rate/concurrency limits.
- A formal WCAG audit, manual browser/device matrix, assistive-technology session, and real-operator usability test remain unverified.
- The frontend requires sign-in again after refresh because the token is intentionally not persisted.
- OCR confidence and receipt images are evidence for human review, not proof that a financial transaction occurred.

## 29. Exact Sprint 7 proposal

Sprint 7 is proposal-only and must begin after real Sprint 6 operational testing. Preserve all API contracts and business behavior while evaluating Relay-specific visual identity, distinctive dashboard composition, refined information hierarchy, transaction status cards, a receipt journey/timeline, stronger duplicate investigation presentation, improved review ergonomics, meaningful reduced-motion interactions, restrained light/dark color, responsive behavior, accessibility verification, usability testing, and locale expansion.

Required Sprint 7 outputs should be tested interaction specifications, component refinements, accessibility evidence, representative responsive screenshots, operator-usability findings, and a migration-free rollout plan. It must not add AI as part of UI polish. Any later image-manipulation study requires a separate explicitly approved sprint and must treat model output only as advisory evidence. Image analysis cannot verify that a bank transaction occurred; only authoritative financial-system evidence could support that conclusion.
