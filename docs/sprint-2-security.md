# Sprint 2 security design and assumptions

This document explains controls and residual risks. It is not a penetration-test report, vulnerability scan, hardware-attestation claim, or security certification. Actual verification belongs in [SPRINT_2_DELIVERY.md](../SPRINT_2_DELIVERY.md).

## Three separate trust boundaries

| Credential | Authority | Lifetime/storage | Why separate |
| --- | --- | --- | --- |
| Administrator JWT | Manage device records, issue challenges, change configuration, disable/revoke devices | Short-lived Sprint 1 JWT; never installed in Android | A lost or compromised device must not gain management privileges over the fleet. |
| Enrollment challenge | Bind one installation to one administrator-selected device | Random one-time secret, default ten-minute expiry, five-attempt limit; digest only in database | Enrollment proof is narrower than ongoing device authorization and cannot be reused after successful binding. |
| Device credentials | Read/acknowledge this device's configuration and submit minimal contact | Separate short-lived JWT plus rotating refresh credential, absolute expiration; refresh digest only in database; Android Keystore encryption locally | A client cannot cross the administrator boundary or select another device's identity in a device endpoint. |

Device tokens have a separate signing-key domain, audience, and token type; required claim validation is followed by database checks of credential, installation, and device state on every protected operation. A valid signature alone is not enough. Domain-separated keys derive from the same configured server master secret, so compromise of that master still compromises both authentication domains; separation protects protocol confusion, not master-secret theft. Rotate/protect the master through operator-controlled secret management.

## Threats and controls

| Threat | Control | Residual risk / deployment requirement |
| --- | --- | --- |
| Intercepted enrollment value | TLS, high-entropy short-lived challenge, device binding, atomic one-time use | Whoever steals an unused valid challenge can attempt enrollment first. Transfer values through a trusted channel; operator reviews unexpected enrollment and replaces/revokes it. |
| Brute force or replay | Secret digest comparison, bounded attempts, expiry/consume state; PostgreSQL transactional consumption | Per-challenge limits are not global rate limiting. Apply reverse-proxy abuse controls to login, enrollment, and renewal before exposure. |
| Concurrent challenge consumption | Lock/check/consume and installation/credential issuance share a transaction; database uniqueness is authoritative | PostgreSQL integration, not SQLite alone, is required to verify lock behavior. |
| Administrator/device token confusion | Distinct adapter, key domain, audience, type, required claims, and endpoint dependencies | All administrators still share one management boundary; no multi-tenant RBAC exists. |
| Access or refresh theft | Short access lifetime; installation binding; every-request active checks; refresh rotation/replay handling; explicit revoke/disable | Access is bearer-based. Malware executing as the app can use keys/credentials available to the app; no hardware attestation or proof-of-possession protocol is supplied. |
| Lost renewal response | Persist operation UUID before sending; deterministic same-successor recovery for exact retry; store digests only | Exact old secret/request-ID possession can recover that successor while valid. Different-operation replay revokes the family and can require operator re-enrollment. |
| Disabled record reactivated later | Disable transaction revokes authorization; status `ACTIVE` does not un-revoke history | Already-completed/in-flight actions cannot be retroactively undone. Future requests recheck current state; there is no remote wipe of local data. |
| Silent installation replacement | Explicit `replace_existing:true`; transactional old-installation revocation; historic identities retained | An administrator is trusted to replace a device. Operator authorization is not a hardware identity attestation. |
| Cross-device configuration read/write | Device identity comes from token; administrator and device route separation; device-scoped configuration/history | Administrator compromise retains fleet-wide impact by design. |
| Stale writes or false acknowledgements | Expected-version conflict, retained revision history, delivery proof per installation, monotonic acknowledgement | A compromised client can falsely report application of a delivered revision. Acknowledgement is client evidence, not attestation. |
| Invalid configuration destroys offline state | Bounded schema/type/version validation; atomic save before acknowledgement; preserve last-known-good | Future schema changes require explicit compatibility/migration design. Generic settings are inert in Sprint 2. |
| Storage/backup extraction | Android Keystore AES/GCM with random IV; secret bundle under no-backup storage; cloud and transfer exclusions; fail closed on corruption/key invalidation | Rooted devices, debugger-enabled test builds, screenshots, OS compromises, and unlocked app memory remain outside this protection. |
| Secret disclosure in logs/errors | No HTTP logging interceptor; safe domain/error representations; Sprint 1 safe error envelope and allowlisted application logging | Proxy logs, infrastructure traces, crash SDKs, operator terminals, and user screenshots need independent controls. No third-party crash SDK is added. |
| Network retry duplicates enrollment | OkHttp retries/redirects disabled; uncertain enrollment requires new challenge | Enrollment may have succeeded server-side despite the lost response; operator must explicitly replace that installation if needed. |
| Infinite background traffic | Explicit timeouts, shared mutex, bounded retry/backoff, network-constrained WorkManager | Android may defer work for battery/network policy. No guarantee of continuous connectivity or exact timing. |

## Credential lifecycle details

Enrollment secrets and refresh credentials have cryptographic entropy sufficient for digest storage without password-style stretching; human administrator passwords retain Argon2id. Secrets are not configuration identifiers or loggable metadata.

Refresh lifetime is absolute from enrollment, not an indefinitely sliding bearer credential. A renewal transaction locks and validates the device/credential, consumes the old credential, and links its successor. Exact-request retries recover the same still-active successor using a domain-separated server HMAC and the presented old secret. PostgreSQL stores only hashes and the operation/successor relationship. A different operation against a consumed credential revokes the active family. Revoked, disabled, expired, or already-consumed successor state cannot be revived by retry.

Android stores a pending renewal operation with the old encrypted credential before any network mutation. On successful renewal it atomically commits the successor bundle; process death or IOException retains the operation so the next attempt is identical. Keystore decryption corruption or invalidation fails closed for authentication, while nonsensitive valid configuration is preserved where possible. A new enrollment, not hidden credential reset, restores access.

Short-lived access does not remove the need for server state checks: rotation, revocation, disabled devices, replaced installations, and expired refresh-family authorization are enforced on future requests. No token or challenge secret is returned by administrator state/list/configuration operations. Enrollment and renewal are the intentional credential-returning endpoints, and their successful response bodies must be treated as secrets.

## Data minimization and permissions

The app stores installation identity, its own credentials, generic configuration, enrollment/status metadata, and synchronization state. The backend receives app version, configuration version, and minimal contact metadata. Phone numbers, IMEI, serial/advertising identifiers, location, messages, notifications, media, images, financial content, and contacts are not used. There is no collector, SMS/storage permission, notification listener, surveillance service, OCR, AI, remote shell, or arbitrary command execution.

Disabling stops future backend access. It does not erase the last-known-good document on the device; that document contains only generic Sprint 2 settings. Collection consent, source-specific retention, event encryption, remote data erasure, and privacy-sensitive payload handling must be designed with the future collector/event scope before implementation.

## Operational requirements and limitations

- Terminate TLS at a trusted deployment boundary; configure concrete backend host allowlists and tightly scoped proxy trust. Release Android forbids cleartext. Debug HTTP is explicit and limited to localhost/emulator use.
- Do not expose local Compose ports publicly. Configure login/enrollment/renewal abuse limits, request-size/time budgets, safe access logs, monitoring, and incident procedures.
- Protect database backups and the JWT master secret; restrict database credentials. Digest storage reduces plaintext exposure but does not make database compromise harmless: an attacker with write access can tamper with state.
- Provide reviewed backup/recovery, data retention, key rotation, release signing, dependency/image scanning, and OS update policies. No production deployment or release-signing key is included.
- Treat challenge exhaustion and refresh replay as operational incidents requiring investigation. No security-event dashboard, centralized immutable audit ledger, attestation, certificate pinning, remote wipe, multi-tenant isolation, MFA, or collector consent system is claimed.
- A successful lint/unit/build run does not establish actual Android Keystore behavior, OS scheduling, device-transfer exclusions, or a fresh-install enrollment flow. Instrumentation and real runtime evidence must be reported separately.
