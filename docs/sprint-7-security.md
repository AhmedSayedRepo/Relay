# Relay Sprint 7 security review

## Scope

The review covered the Sprint 7 frontend changes: localization, bidi handling, theme and locale persistence, authentication token handling, Help rendering, protected image access, duplicate evidence presentation, browser caching controls, external resources, CSP compatibility, and dependency state. Backend, migrations, and Android source were checked for unintended Sprint 7 changes.

## Source-backed findings

No actionable security finding was identified in the reviewed Sprint 7 source.

| Area | Evidence and result |
|---|---|
| Localization injection | Catalog strings render through React text interpolation. No translated HTML parser or `dangerouslySetInnerHTML` is used. |
| OCR/receipt XSS | API values render as React text; no `innerHTML`, dynamic script execution, `eval`, or `new Function` was found. |
| Bidi confusion | References, amounts, times, hashes, telephone-like values, and identifiers use an explicit LTR `bdi` presentation component. |
| Preference persistence | Only closed allowlist values for `relay.locale` and `relay.theme` are accepted and stored. |
| Token handling | The administrator token remains in React memory and is not placed in local/session storage. A regression test asserts this boundary. |
| Help | Stable local topic objects render as text; search performs local string matching in the selected catalog. |
| Image access | Protected images continue through the authenticated same-origin API helper using the bearer token. Object URLs are local and revoked by consumers. |
| Duplicate evidence | Existing canonical/member identifiers and provenance are displayed; the frontend does not alter identity, delete evidence, or infer fraud. |
| Browser caching | Existing backend operator responses keep `Cache-Control: no-store`; protected binary evidence also retains no-store handling. |
| External resources | No third-party fonts, analytics, tracking, CDN scripts, or external UI resources were added. |
| CSP compatibility | The implementation does not require inline script, remote script, or dynamic code execution. Existing operator security headers remain unchanged. |
| Dependencies | `npm ls --all --omit=dev` completed cleanly. `npm audit --offline --omit=dev` reported 0 known vulnerabilities from locally available advisory data. |
| Android permissions | Android source is unchanged and the final APK permission sets contain no new personal-content/storage permission. |

## Verification method

The review combined source inspection for dangerous rendering/storage/network patterns, frontend behavioral tests, production build inspection, local dependency-tree validation, offline advisory audit, checks of the existing operator security-header code, and final package comparison against the Sprint 6 manifest.

## Workbench limitation

The standard Codex Security workbench scan was started once, as required by the installed security workflow, but registration failed before a scan could run because the scanner could not read a stale generated Android `desugar_graph` intermediate. The workflow forbids silently replacing or retrying a failed canonical start, so no canonical Sprint 7 workbench report exists and none is claimed. The source-backed review above is the delivered security evidence.

## Residual risk

- Offline dependency audit results depend on advisory data already present locally; a current online audit was not run.
- A real production CSP/browser-cache deployment check was not performed because Sprint 7 does not deploy.
- The in-memory token is deliberately cleared on refresh. A durable session requires a separately reviewed server-side authentication design.
- Relay organizes evidence and does not establish receipt authenticity or prove that a financial transaction occurred.
