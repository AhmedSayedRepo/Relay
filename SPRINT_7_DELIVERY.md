# Relay Sprint 7 delivery report

## 1. Implementation summary

Sprint 7 transforms the existing Sprint 6 operator frontend into a distinctive bilingual Relay product while preserving all proven business behavior. It adds complete English and Arabic catalogs, true document-level RTL, bidi-safe financial and identifier presentation, a persistent allowlisted theme/locale preference boundary, a new token-based light/dark visual system, responsive operator layouts, transaction journeys, stronger duplicate/review/processing experiences, 25 bilingual Help topics, intentional states, accessibility controls, visual evidence, and controlled-pilot documentation.

No backend source, API contract, database migration, OCR/parser rule, duplicate rule, review semantic, Android source, or Android permission was changed. No AI, image-tampering/authenticity feature, payment/reconciliation feature, external translation API, analytics, or production deployment was introduced.

## 2. Full changed repository tree

Compared byte-for-byte with the 261-file Sprint 6 manifest, Sprint 7 has 29 added files, 11 modified files, and no deleted files. Generated caches/build output are excluded.

```text
relay/
├── SPRINT_7_DELIVERY.md                                      [added]
├── docs/
│   ├── sprint-7-accessibility.md                             [added]
│   ├── sprint-7-architecture.md                              [added]
│   ├── sprint-7-pilot-checklist.md                           [added]
│   ├── sprint-7-pilot-launch.md                              [added]
│   ├── sprint-7-pilot-test-cases.md                          [added]
│   ├── sprint-7-post-pilot-framework.md                      [added]
│   ├── sprint-7-security.md                                  [added]
│   ├── sprint-7-terminology.md                               [added]
│   ├── sprint-7-verification-evidence.json                   [added]
│   └── sprint-7-visual-evidence/
│       ├── 01-dashboard-en-light.png                         [added]
│       ├── 02-dashboard-en-dark.png                          [added]
│       ├── 03-dashboard-ar-light.png                         [added]
│       ├── 04-dashboard-ar-dark.png                          [added]
│       ├── 05-transactions-en.png                            [added]
│       ├── 06-transactions-ar.png                            [added]
│       ├── 07-transaction-detail-en.png                      [added]
│       ├── 08-duplicate-groups-en.png                        [added]
│       ├── 09-duplicate-comparison-en.png                    [added]
│       ├── 10-review-en.png                                  [added]
│       ├── 11-processing-en.png                              [added]
│       ├── 12-help-en.png                                    [added]
│       ├── 13-help-ar.png                                    [added]
│       ├── 14-narrow-ar-transactions.png                     [added]
│       └── browser-accessibility-evidence.json               [added]
└── frontend/src/
    ├── App.tsx                                               [modified]
    ├── components/
    │   ├── HelpDialog.tsx                                    [modified]
    │   ├── Layout.tsx                                        [modified]
    │   └── Product.tsx                                       [added]
    ├── help/topics.ts                                        [modified]
    ├── hooks.ts                                              [modified]
    ├── i18n/
    │   ├── ar.ts                                             [added]
    │   ├── en.ts                                             [modified]
    │   └── index.tsx                                         [added]
    ├── main.tsx                                              [modified]
    ├── operator.test.tsx                                     [modified]
    ├── pages.tsx                                             [modified]
    ├── preferences.tsx                                       [added]
    ├── styles.css                                            [modified]
    └── test/setup.ts                                         [modified]
```

## 3. Sprint 1–6 baseline verification

Before editing, every file in `relay-sprint-6.manifest.json` was re-hashed: 261 expected, 261 present, 0 missing, 0 extra within the manifest, and 0 different. The Sprint 6 frontend baseline passed 10 tests, lint, and its TypeScript/Vite build. The full established backend, PostgreSQL, migration, frontend, Android, emulator, and permission checks were repeated after implementation; results appear below. Existing architecture and security documents were reviewed, and unrelated cleanup was avoided.

## 4. UI architecture changes

The UI now has explicit presentation boundaries: `PreferencesProvider` owns allowlisted locale/theme state and document attributes; the i18n provider owns typed catalog access; reusable product primitives own glyphs, states, statuses, bidi-safe values, and journeys; layout owns navigation and preferences; pages compose unchanged API data. Styling stays in a token-based CSS layer. See `docs/sprint-7-architecture.md`.

## 5. Relay visual identity/design system

Relay uses a restrained signal-line motif, a dark operational rail, warm neutral work surfaces, tabular financial values, compact status lozenges, strong focus rings, deliberate spacing/elevation/radius tokens, and inline application glyphs. The system avoids external fonts, excessive gradients, glass effects, decorative dashboards, and card-only composition. CSS variables define color, type, space, radius, elevation, status, focus, and theme behavior.

## 6. English localization

All Relay-controlled operator strings are sourced from the English catalog: navigation, pages, data labels, filters, tables/cards, dialogs, actions, accessibility names, confirmations, loading/empty/error states, and all Help content. Tests compare catalog keys and exercise representative pages and workflows.

## 7. Arabic localization

The Arabic catalog has exact key parity with English and includes all 25 Help topics. Terminology is consistent and documented in `docs/sprint-7-terminology.md`. Arabic was authored locally without an external translation service. Representative operator linguistic validation remains a pilot activity and is not pre-claimed.

## 8. RTL implementation

Selecting Arabic sets `html.lang=ar` and `html.dir=rtl`; English sets `lang=en` and `dir=ltr`. Navigation rail/drawer, headings, filters, grids, cards, forms, tables, dialogs, pagination, Help, and the journey follow direction through logical CSS properties. Semantic glyphs that should keep their meaning are not blindly mirrored.

## 9. bidi/mixed-content handling

`Product.tsx` renders references, serials, identifiers, hashes, telephone-like values, timestamps, and amount/currency pairs with LTR bidi isolation so their logical order remains stable inside Arabic. Mixed Arabic/Latin/numeric cases are covered by frontend tests and visible in the Arabic evidence captures.

## 10. Dashboard redesign

The dashboard leads with transaction volume and totals by currency, then review demand, duplicates, processing issues, and source/device health. It uses signal lines and restrained attention treatment so exceptions are easy to spot without making the whole page alarming. Responsive layouts preserve operational priority.

## 11. Transactions UX

Desktop uses a scannable data table emphasizing time, amount/currency, reference, sender/recipient, review, duplicate, and source. Narrow widths replace rows with labeled cards retaining the same important fields. Filters, reset behavior, pagination, loading, empty, service, and authentication states are localized.

## 12. transaction journey/timeline

Transaction detail shows only stages supported by existing record evidence: collected, uploaded, processed, OCR extracted, classified, reviewed, and final, with duplicate evidence where applicable. Missing evidence suppresses a stage. The journey follows LTR/RTL direction and does not fabricate business events.

## 13. duplicate investigation UX

Duplicate groups are a first-class operational queue. Each group exposes the matching reason/reference, member count, canonical record, amounts, sources/devices, receipt times, and review state while preserving all provenance and existing deterministic identity.

## 14. duplicate comparison

The comparison view presents canonical and member evidence side by side at wide widths and sequentially on narrow screens. Field-level match/difference treatment helps operators inspect amount, reference, parties, source, and time without changing duplicate rules or suggesting authenticity/fraud conclusions.

## 15. review workflow

Reviewed/final values lead visually; machine-extracted values, confidence, original evidence, processing provenance, history, duplicate relationship, and source remain available for audit. Actions, validation, feedback, empty state, and recovery guidance are localized and keyboard reachable. Existing review semantics are unchanged.

## 16. processing workflow

The queue separates waiting, active, failed, skipped, and succeeded work with text-backed statuses, safe failure summaries, contextual Help, and retry actions only where the current API supports them. It does not expose stack traces or change stale-worker/attempt ownership rules.

## 17. Help EN/AR

All 25 stable Help topic IDs retain search, contextual opening, related topics, and safe React rendering in both locales. Topics explain Relay, device and SMS/image collection, configuration, processing, review, duplicates, Excel export, and common recovery steps in operator language.

## 18. Arabic Help search

Search indexes localized Arabic title, summary, and content strings and was behaviorally tested with an Arabic query. Contextual topic opening and related-topic navigation use the selected locale while preserving IDs.

## 19. light/dark themes

The allowlisted choices are system, light, and dark. Only the choice persists; system resolves through `prefers-color-scheme`. Both themes define intentional surfaces, borders, statuses, focus, rail, dialogs, tables, previews, and Help colors. Representative measured contrast ratios range from 5.43:1 to 15.87:1 for sampled text combinations.

## 20. responsive behavior

Desktop keeps the fixed rail and dense table. Narrow layouts use a drawer, single-column operations, scroll-safe controls, adaptive duplicate/evidence comparison, and labeled transaction cards. Executed 375 px RTL and 720 px 200%-proxy browser checks found no page-level horizontal overflow.

## 21. accessibility verification

Automated browser inspection found the expected landmarks, 0 unnamed actions, 0 unlabeled form controls, 0 missing image alternatives, and 0 undersized scanned controls. Keyboard review covered skip navigation, logical initial order, language/theme/navigation/Help reachability, Help search, and Escape-to-close. Reduced-motion computation showed no animation. Sampled contrast passed the relevant 4.5:1 text threshold. Full WCAG conformance, a dedicated screen-reader session, and representative disabled-operator testing are not claimed. See `docs/sprint-7-accessibility.md`.

## 22. loading/empty/error states

Major pages use localized purpose-built loading, empty, no-match, authentication-expired, API-unavailable, export-failure, and generic error states. Empty states explain the next action and provide filter reset or contextual Help where appropriate.

## 23. session UX decision

The administrator bearer token remains memory-only and is cleared by refresh; it is never stored alongside preferences. Locale and theme persist after strict allowlist validation. Durable authentication would require a separate server-managed secure-cookie/CSRF design and is recorded as post-pilot work rather than weakened storage in this sprint.

## 24. frontend tests

Final frontend verification passed:

- `npm test`: 14 tests passed.
- `npm run lint`: passed.
- `npm run build`: passed, 89 modules; CSS 17.88 kB (4.73 kB gzip), JS 352.92 kB (110.04 kB gzip).

Coverage includes catalog parity, locale/theme persistence, LTR/RTL, bidi values, switching, dashboard, transactions/filter states, journey, review, duplicate comparison, processing retry, export/devices, English/Arabic Help, Arabic search, navigation semantics, and memory-only token handling. Behavioral tests remain primary; screenshots are supplementary.

## 25. visual verification/screenshots

Fourteen Chrome captures cover every requested representative view: dashboard EN/AR in light/dark, transactions EN/AR, transaction detail, duplicate groups, duplicate side-by-side comparison, review, processing, Help EN/AR, and narrow Arabic transactions. Console/page errors were zero during capture. Evidence is in `docs/sprint-7-visual-evidence/`; machine-readable browser observations are in its JSON file.

## 26. backend regression

Backend source was unchanged. Final static checks passed: Black reported 100 files unchanged, Ruff passed, and MyPy passed 76 source files. The non-PostgreSQL suite with real Tesseract 5.5.3 (`eng` and `ara`) passed 175 tests with 8 PostgreSQL tests deselected in 132.37 seconds.

## 27. PostgreSQL regression

A fresh loopback-only PostgreSQL 16.15 cluster was created. Alembic upgrade to head passed, `alembic check` reported no new operations, downgrade to base passed, re-upgrade to head passed, and the second check also reported no new operations. The complete suite passed 183 tests, including 8 PostgreSQL cases and real OCR, in 141.35 seconds. The database stopped cleanly.

## 28. Android regression and final permissions

Android source is unchanged. The clean established matrix `clean testDebugUnitTest testReleaseUnitTest lintDebug lintRelease assembleDebug assembleRelease` passed with Gradle 8.13/JDK 21: 114 actionable tasks, 112 executed, 2 up-to-date, in 3m27s. Instrumentation assembly passed. On the API 35 emulator, the suite ended `OK (7 tests)`: 6 normal passes and 1 expected backend-dependent assumption skip.

Debug and release APK inspection produced the same permission set: `INTERNET`, `ACCESS_NETWORK_STATE`, `RECEIVE_SMS`, `WAKE_LOCK`, `RECEIVE_BOOT_COMPLETED`, `FOREGROUND_SERVICE`, and the app-scoped `DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION`. There is no new storage or personal-content permission. Debug SHA-256 is `75A02C0E1A6A2A670B5EB16EE0B82E4CE4DD3E56CE445E60619E15795DF5A399`; unsigned release SHA-256 is `A851CA0298B4F19CAD564F64D3E9591F714B2F599AAA446AE3D6909226045249`.

## 29. security review

Source inspection found no actionable Sprint 7 issue in localization rendering, OCR/receipt text rendering, bidi handling, preference persistence, token handling, Help, protected images, duplicate evidence, cache controls, external resources, or CSP compatibility. Production dependency listing was clean and the offline production audit reported 0 known vulnerabilities from local advisory data. No third-party tracking/CDN was added.

The canonical Codex Security workbench registration was attempted once but failed before scanning because it could not read a stale generated Android `desugar_graph` intermediate. The installed workflow prohibits silently retrying or substituting a canonical start, so no workbench scan is claimed. Details and residual risk are in `docs/sprint-7-security.md`.

## 30. known limitations

- Real operator outcomes, Arabic linguistic acceptance, real receipt accuracy, and all sixteen pilot criteria remain unverified until the controlled pilot.
- Full WCAG conformance, dedicated screen-reader testing, forced-colors testing, and a broader assistive-technology matrix were not executed.
- Docker/container build was not verified because Docker was unavailable; source-only deployment configuration remains unchanged.
- No production deployment, public-network test, or production security-header/cache check was performed.
- The production dependency audit was offline; current online advisory data was not queried.
- The canonical security workbench scan did not start due to the recorded stale generated-file read failure.
- The unsigned release APK is build evidence, not a distributable production APK.
- Authentication intentionally requires login after refresh.
- Relay does not detect image tampering, score authenticity, classify fraud, reconcile payments, or prove that a transaction occurred.

## 31. pilot checklist

`docs/sprint-7-pilot-checklist.md` provides a reviewable gate for safety/authorization, backend, Android, operator UI, test data, observations, sixteen measurable success criteria, and exit/sign-off. Blank items remain unverified; no accuracy percentage has been invented.

## 32. exact instructions for launching the controlled real-world pilot

Use `docs/sprint-7-pilot-launch.md` exactly as the run sequence: approve the limited scope; create a secret-free observation record; prepare private infrastructure; verify migrations/health/worker/object access; verify package hashes; prepare a pilot-signed Android build and approved devices; verify operator UI/session behavior; pre-record expected sample values; execute test cases from the smallest set; reconcile results; stop on safety triggers; then close and sign the run. Sprint 7 prepares these instructions but does not execute deployment or the pilot.

## 33. recommended pilot test cases

`docs/sprint-7-pilot-test-cases.md` defines 20 cases covering normal/distinct receipts, same-reference different-image duplicates, review correction/rejection, recoverable processing failure, dashboard/Excel reconciliation, English/Arabic workflows, bidi, responsive layout, keyboard use, theme/system persistence, authentication refresh, API/empty states, protected images, optional SMS, and offline reconnect.

## 34. post-pilot decision framework

After the evidence set is frozen, classify each finding by severity (`BLOCKER`, `HIGH`, `MEDIUM`, `LOW`) and domain (`UX`, `OCR/PARSER`, `DUPLICATE`, `ANDROID/COLLECTION`, `OPERATIONS`). Then explicitly select: A pilot hardening, B production readiness, C OCR/parser improvement, D provider support, or E AI image-manipulation research. Option E requires specific pilot evidence and separate owner approval; it is advisory only and cannot prove payment. The full gate is in `docs/sprint-7-post-pilot-framework.md`. No next sprint starts automatically.
