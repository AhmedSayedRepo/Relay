# Exact Sprint 3 proposal — not implemented

Sprint 3 should introduce the first explicitly enabled local collector and a durable event ingestion pipeline on the Sprint 2 enrollment/authentication/configuration foundation. This is a planning document only. No collector, personal-content access, event ingestion, banking rule, AI, OCR, or payment parser is implemented in Sprint 2.

## Proposed outcome and scope

1. **Agree on one first collector before implementation.** Choose its exact source, permission model, user-visible consent, allowable fields, retention, and platform-distribution constraints. Begin with a synthetic event source for pipeline tests; add only the expressly approved real source. Do not silently bundle SMS, notifications, WhatsApp/media, or image access into a generic collector task.
2. **Define a narrow collector boundary.** A local adapter produces generic typed events through a domain interface. Effective server configuration may enable the approved source only after local consent and OS permission. The app must show collection state, allow consent withdrawal, and stop when permission is revoked. No arbitrary commands, source code, or shell are downloaded/executed.
3. **Add a versioned generic event envelope.** Specify event UUID, installation binding, source/type/schema version, observed timestamp and timezone semantics, and bounded payload. Server receipt time remains server-owned. Avoid business-specific payment, transaction, or AI fields. Define payload size/type validation and privacy redaction before allowing real personal content.
4. **Build an Android durable outbox.** Persist an event before acknowledging capture; maintain queued/in-flight/accepted/rejected state, batch size limits, storage quotas, local retention, and crash-safe retry. Use appropriate at-rest protection for the selected payload and document key-loss behavior. Survive reboot, process death, temporary offline periods, and uncertain responses without silent loss.
5. **Add authenticated backend batch ingestion.** Bind requests to the existing authenticated installation, enforce device status/configuration policy, validate each envelope, and provide per-event acceptance/rejection. Establish a database uniqueness key for idempotency so replaying a batch or event after a lost response does not duplicate stored events. Clients may never select another device's ownership.
6. **Persist raw generic events and processing state.** Add new migrations, bounded ingestion queries, explicit retention, and a durable processing handoff. Start with storage and a deterministic validation/normalization step; no OCR, AI, banking extraction, payment classification, or dashboard frontend. A processing failure must be inspectable/retryable without losing the original admitted event.
7. **Connect policy to collection and upload.** Extend the generic configuration schema deliberately for the approved source. Define disabled/revoked-device behavior for pending local events and already accepted server events. Local collection consent is distinct from server enrollment and must not be bypassed by configuration.
8. **Add operational bounds and evidence.** Document throughput/payload targets, rate limits, backpressure, queue-full behavior, poison-message handling, log redaction, and retention/erasure procedures. Expose minimal operator API/metrics where necessary; no frontend is required.

## Acceptance tests required for Sprint 3

- An expressly enabled, permission-granted source captures one event and it reaches durable server storage with the correct installation ownership.
- Permission denial/withdrawal and local collector disable stop capture without blocking unrelated agent status/configuration operations.
- Offline capture persists across process restart; recovery drains the queue under bounded backoff without duplicate server events.
- Lost batch responses, duplicate events, concurrent duplicate submissions, and reordered retries resolve idempotently on real PostgreSQL.
- Device A cannot submit events as device B; disabled/revoked credentials cannot ingest; malformed/oversize/unsupported events have safe per-item outcomes.
- Queue capacity, retention expiry, disk exhaustion, key invalidation, and poison events have documented and tested failure behavior.
- Payloads, tokens, and sensitive source content never appear in normal logs or API errors; a reviewed permission/retention threat model precedes real personal-content testing.
- Existing Sprint 1 and Sprint 2 suites, Android build/lint/unit tests, relevant instrumentation, and a fresh-device end-to-end capture/offline/recovery flow remain green with explicit evidence.

## Decisions required before implementation

The product owner must approve the first source and distribution channel, legal/consent requirements, allowed payload fields, local/server retention, maximum offline backlog, deployment abuse controls, and revocation/erasure policy. Those decisions are prerequisites for selecting personal-content permissions, not an excuse to preinstall them now. Implementation of this proposal requires a separate Sprint 3 instruction.
