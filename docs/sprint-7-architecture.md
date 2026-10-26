# Relay Sprint 7 UI architecture

## Scope and invariants

Sprint 7 changes the operator frontend only. It introduces a bilingual presentation layer, a preference boundary, reusable product primitives, responsive layouts, and a Relay-specific visual system. Backend services, API paths and schemas, migrations through `0006`, OCR and parsing rules, deterministic duplicate identity, review semantics, evidence retention, Android collection, and Android permissions are unchanged.

The frontend continues to call the existing same-origin `/api/v1` endpoints through `api.ts`. The administrator token remains React state held in memory. Locale and theme are the only preferences persisted in browser storage.

## Presentation layers

1. `main.tsx` mounts `PreferencesProvider` outside the application.
2. `preferences.tsx` validates the stored locale and theme against closed allowlists, resolves the system theme, and updates the document `lang`, `dir`, and `data-theme` attributes.
3. `i18n/index.tsx` exposes typed catalog lookup. `i18n/en.ts` and `i18n/ar.ts` have compile-time and test-time key parity.
4. `components/Product.tsx` contains presentation primitives for icons, references, amounts, timestamps, statuses, empty/loading/error states, and receipt journeys. Machine identifiers and financial values use bidi isolation.
5. `components/Layout.tsx` owns the desktop rail, narrow-screen drawer, skip link, navigation, language/theme controls, and Help entry.
6. `pages.tsx` composes existing API data into operational views without changing business decisions or inventing workflow stages.
7. `styles.css` defines the design tokens and responsive, RTL-aware visual rules separately from business logic.

## Data and control flow

Locale and theme values are read only after allowlist validation. Catalog strings render as React text nodes; no translated HTML is interpreted. Receipt/OCR values also render as text. Protected images are fetched through the authenticated API helper and represented by browser object URLs. The helper releases object URLs when consumers unmount.

The transaction journey is derived only from fields already present in the transaction and processing records. Missing evidence suppresses a stage. Duplicate presentation shows the existing canonical/member relationship and reason; it does not recalculate identity or infer fraud.

## Responsive and RTL model

The document direction controls layout direction. Logical CSS properties (`margin-inline`, `padding-inline`, `inset-inline`, and logical borders) allow the rail, drawer, tables, filters, dialogs, and journeys to follow LTR or RTL. On narrow screens the rail becomes a drawer and dense tables become labeled data cards while retaining amount, currency, reference, status, source, and time.

## Session decision

Sprint 7 intentionally retains in-memory authentication. Persisting the token would increase exposure to browser-storage compromise and would require a broader authentication design. The secure post-pilot option is a server-managed, short-lived session using secure, HttpOnly, SameSite cookies plus CSRF controls; it is not part of this sprint.

## Deployment effect

There is no database migration, API contract change, worker change, Android source change, external font, analytics call, CDN dependency, or production deployment in Sprint 7.
