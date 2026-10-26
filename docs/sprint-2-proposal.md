# Proposed Sprint 2: Android client and trusted device foundation

This is a proposed implementation contract, not completed functionality. Sprint 2 must preserve Relay's standalone backend and generic device model. The Android Agent is a device application; no AI provider is required.

## Goal

One operator can enroll multiple Android installations, each installation authenticates with its own revocable credentials, and each active enrolled device can synchronize an explicitly versioned remote configuration from the backend. No personal content is collected in this sprint.

## In scope

1. **Android application foundation.** Create the Android project with explicit environment/build configuration, a minimal enrollment/status screen, typed networking, bounded retries with backoff, safe logging, and lifecycle-aware background synchronization. Keep transport, credential storage, configuration, and UI concerns separate. Do not request SMS, notification-listener, contacts, accessibility, media, or call permissions.
2. **Operator-authorized enrollment.** Add a backend administration use case to issue a short-lived, one-time enrollment challenge bound to a target device or a clearly specified enrollment intention. Define expiry, attempt limits, replay prevention, atomic consumption, and administrator audit events before implementing the route. The app presents the challenge and installation identity; enrollment never trusts a phone number as proof of identity.
3. **Device authentication distinct from administrator authentication.** Define device credentials with a separate audience and authorization dependency. Select the credential scheme in a short reviewed decision record, using available secure Android storage; prefer per-installation keys when the protocol and test environment support them. Never embed an administrator JWT, shared fleet password, backend signing secret, or bootstrap master credential in the APK. Include expiry/rotation or renewal semantics and revocation from the start.
4. **Enrollment and access lifecycle.** Define allowed transitions from `PENDING` to `ACTIVE`, disable/revoke behavior, re-enrollment, duplicate external IDs, and lost/cleared app state. Disabling a device must block future device-authenticated requests. Existing administrator management must remain protected and behavior-compatible.
5. **Versioned remote configuration pull.** Add authenticated per-device configuration retrieval with an explicit schema version and monotonic revision or equivalent change identifier. Define unchanged responses, validation, safe local persistence, acknowledgement of the applied revision, and rollback to the last valid local configuration after a malformed update. Use non-sensitive synchronization settings only, such as an enabled flag and bounded sync interval; do not add collector definitions.
6. **Minimal device contact state.** Update `last_seen_at` only after a successful authenticated device contact according to a documented rule. Report enrollment state, last successful contact, and last applied configuration revision through administration APIs. No live tracking, telemetry payload stream, or dashboard frontend.
7. **Tests and operator documentation.** Cover enrollment expiry/replay/races, credential rejection and revocation, device isolation, configuration revisions and invalid configuration, offline/retry behavior, safe logs, and recovery after app restart. Add migration upgrade checks, API contract examples, Android build instructions, and an end-to-end local runbook using at least two simulated or physical installations.

## Acceptance criteria

| Scenario | Expected result |
| --- | --- |
| Two separate Android installations enroll | Distinct backend records and credentials; no cross-device authorization |
| Challenge expires or is used twice | Enrollment is rejected without creating extra valid credentials |
| Concurrent challenge redemption | At most one redemption succeeds |
| Administrator token is sent to a device-only API | Rejected by the device authorization boundary |
| Device credential is sent to an administration API | Rejected by the administration authorization boundary |
| One device tries to read another device's configuration | Rejected without leaking that device's configuration |
| Administrator disables/revokes a device | Subsequent device requests fail; other devices continue working |
| Remote configuration is unchanged | Client preserves the current version and avoids redundant writes |
| Remote configuration is invalid or network is offline | Client keeps the last valid configuration and retries within bounded policy |
| Application restarts | Credentials and last valid configuration survive through approved Android storage |
| APK/source/log inspection | No administrator secret, fleet-wide credential, personal payload, or sensitive token is present |
| Existing Sprint 1 administration tests run | They remain green unless an explicitly reviewed contract migration is required |

## Explicit exclusions

- SMS, WhatsApp, notification listener, OCR, images/files ingestion, AI SDKs, receipt/payment parsing, or any banking-specific behavior.
- Full collector execution, event processing, rules engine, transactions, reporting, or dashboard frontend.
- General-purpose remote commands, remote code execution, APK self-update, or background permissions beyond the selected synchronization mechanism.
- Fleet-wide shared credentials, production rollout, automated deployment, and speculative multi-tenancy/RBAC.

## Decisions required before Sprint 2 implementation

- Android language/toolchain and supported minimum API level, verified against the target devices.
- Enrollment handoff UX and trusted operator channel, with a concrete threat model and challenge lifetime.
- Device key/credential format, storage, renewal, revocation, and recovery semantics.
- Remote configuration schema and sync scheduling constraints consistent with Android background execution limits.
- Deployment-side TLS and rate limiting needed to test outside localhost safely.

Keep these choices small and recorded. Sprint 2 should finish with a functioning enrollment/authentication/configuration vertical slice, not an empty framework for future collectors.
