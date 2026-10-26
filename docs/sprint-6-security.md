# Relay Sprint 6 security and privacy review

## Result and scope

The standard Sprint 6 review examined the operator APIs and read models, authorization, protected object access, duplicate scope, audit persistence, retry, Excel generation, Help content, browser rendering and token handling, pagination/search/export bounds, configuration projection, deployment files, and Android permission regression. No reportable finding remained after remediation.

The canonical scan contract is in `docs/sprint-6-security-scan/`: `scan-manifest.json`, `findings.json`, `coverage.json`, and the generated `report.md`. This was a source and automated-behavior review, not a penetration test. A container/dependency vulnerability scan was not available, and Docker was not installed on the verification host.

## Controls reviewed

- Every Sprint 6 API requires an active administrator bearer token. Public liveness/readiness and login behavior are unchanged.
- Operational responses inherit `Cache-Control: no-store`. `/operator` adds a restrictive content security policy, `Referrer-Policy: no-referrer`, and a restrictive `Permissions-Policy` without breaking development API documentation.
- Receipt bytes remain behind the existing administrator-only content route. The frontend fetches the blob with authorization and creates only a short-lived object URL.
- Duplicate identity remains normalized reference plus device and receipt type. Different devices cannot form one deterministic group. The operator is authorized to view all enrolled devices and every item retains its device provenance.
- OCR and user-derived strings use React text nodes or JSON string rendering. There is no `dangerouslySetInnerHTML`, raw HTML parser, dynamic evaluation, or external script dependency in application code.
- Search text is capped at 64 characters. Dates are capped at 366 days. Lists use server-side limits and bounded offsets. Export is capped at 5,000 rows. Retry has an allowlist and attempt cap.
- Excel output has no macros, external links, templates, or saved server file. All untrusted strings neutralize formula prefixes after leading whitespace; automated tests cover `=`, `+`, `-`, and `@`.
- Bearer tokens remain in component memory and are never written to local or session storage. This requires sign-in again after refresh and limits browser persistence.
- Device configuration projections contain status, versions, counts, rule settings, and collection state but exclude credentials and secrets. API errors use stable messages and tests ensure logs do not contain receipt/OCR content.
- Duplicate decisions, retry, and exports write attributable audit rows. A pre-release review found that initial candidate/job foreign keys would cascade-delete those rows; `0006` was corrected to retain stable target UUIDs without those foreign keys, and a deletion test verifies the audit survives source retention.

## CSRF and deployment

The operator API uses an explicit `Authorization` header rather than cookie authentication, so browser ambient-cookie CSRF is not applicable to these calls. CORS remains restrictive by default and production configuration rejects wildcard origins and local-only hosts. TLS, reverse-proxy hardening, rate limiting across multiple processes, administrator provisioning policy, database roles, backups, and monitoring remain deployment responsibilities.

## Residual limitations

- One administrator role can view all devices; finer role-based access control is not part of Sprint 6.
- Browser memory reduces token persistence but does not protect a token from a same-origin script compromise. CSP and dependency review remain important.
- Application bounds limit one request but do not replace edge-level rate limiting or concurrency controls for hostile internet exposure.
- The filesystem object adapter and in-memory workbook are suitable for the current bounded deployment; production scale still needs capacity monitoring and a reviewed object-storage design.
- No claim is made that an image or OCR output proves a financial transaction, or that security review constitutes penetration testing.
