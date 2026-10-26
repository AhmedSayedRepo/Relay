# Relay Sprint 3 architecture

Sprint 3 adds the first real collector while preserving the Sprint 1 backend and Sprint 2 enrollment, authentication, configuration, and heartbeat foundations. Its boundary ends at durable raw-event storage. There is no payment interpretation, OCR, AI, WhatsApp, notification listener, image scan, contact access, call-log access, location, microphone, camera, or remote code execution.

## End-to-end flow

```text
Android SMS_RECEIVED broadcast
  -> SmsReceiver (new messages only; multipart assembly)
  -> SmsCollector (consent, permission, credentials, config, rule match)
  -> GenericEvent (collector=sms, schema_version=1)
  -> EncryptedSqliteOutbox (AES-GCM ciphertext, durable and bounded)
  -> WorkManager unique upload work (network constraint and backoff)
  -> POST /api/v1/device/events/batch (device bearer credential)
  -> EventIngestionService (ownership, config/rule, schema and size validation)
  -> EventRepository / raw_events (PostgreSQL JSONB and unique constraint)
  -> per-event accepted, duplicate, or rejected result
```

The Android framework adapter never appears in backend domain code. `SmsReceiver` extracts a newly delivered message, `SmsCollector` applies local policy, and the reusable `GenericEvent` plus `EventOutbox` boundary carries collector-independent events. Future collectors can reuse the envelope, queue, uploader, and backend ingestion service without importing SMS framework types.

## Consent and permission model

Collection is effective only when all of these are true:

```text
effective_collection =
    local_consent
    AND runtime_permission
    AND remote_collector_enabled
    AND device_active
```

`device_active` is represented locally by the presence of valid installation credentials and enforced authoritatively by the backend on every authenticated request. A remote rule cannot set local consent or grant Android permission. The UI exposes a separate consent switch and permission button; denial does not cause repeated prompts. Lifecycle refresh detects permission withdrawal and immediately changes effective state. No inbox query or historical backfill exists.

The manifest adds `android.permission.RECEIVE_SMS` and protects the exported receiver with Android's signature-level `android.permission.BROADCAST_SMS`. `android.hardware.telephony` is declared optional so the APK is not restricted to telephony devices at distribution time. The app does not request `READ_SMS`.

## SMS rule configuration

Sprint 2 configuration schema version 1 remains the outer contract. Its optional `collectors.sms` object is:

```json
{
  "collectors": {
    "sms": {
      "enabled": true,
      "rules": [
        {
          "id": "receipt-sender",
          "enabled": true,
          "sender": {"mode": "contains", "value": "merchant"},
          "body": {
            "contains": "receipt",
            "any_contains": ["paid", "received"],
            "all_contains": ["amount", "reference"]
          }
        }
      ]
    }
  }
}
```

Sender modes are `exact`, `contains`, `prefix`, and `suffix`. Body predicates support one `contains` string and bounded `any_contains` and `all_contains` arrays. Matching is deterministic and case-insensitive with stable locale handling. Rules are evaluated in order and the first enabled matching rule wins. Configuration permits at most 20 SMS rules; IDs are unique; strings and term lists are bounded; term lists cannot be empty; and regex, scripts, shell commands, or downloaded code are unsupported. Both backend publication and Android decoding validate the same safety properties. The backend also revalidates the event against the referenced historical configuration and rule.

## Generic event envelope

```json
{
  "event_id": "2f92a0f2-08cc-44e9-b7cc-a83fe6ebfd26",
  "installation_id": "a6449b0a-1f0b-4408-ad40-3a04c937b799",
  "collector": "sms",
  "schema_version": 1,
  "occurred_at": "2026-09-03T05:40:00Z",
  "captured_at": "2026-09-03T05:40:01Z",
  "payload": {
    "sender": "merchant",
    "body": "synthetic test message",
    "received_at": "2026-09-03T05:40:00Z"
  },
  "metadata": {
    "config_version": 2,
    "rule_id": "receipt-sender"
  }
}
```

The event ID is created once before durable enqueue and remains stable through every retry. The envelope is capped at 8,192 encoded bytes, SMS body at 4,096 characters, and the Android HTTP request at 14 KiB to remain below the backend's 16 KiB request limit. Server receipt and expiry times remain server-owned. The SMS payload contains sender, body, and received timestamp only.

## Encrypted durable outbox

`EncryptedSqliteOutbox` stores queue metadata and AES-GCM ciphertext in a SQLite database under the app's no-backup directory. A non-exportable Android Keystore key protects the payload-encryption key boundary. Every encryption uses a fresh IV and authenticated encryption. Plaintext SMS is never stored in SQLite or Android DataStore.

The policy is deliberately bounded:

| Bound | Value |
| --- | ---: |
| Maximum queued events | 500 |
| Maximum encrypted queue bytes | 2 MiB |
| Maximum single encrypted record | 12 KiB |
| Maximum upload batch | 20 events |
| Maximum offline retention | 7 days |

FIFO ordering uses the stable creation sequence. Expired or corrupt records are discarded safely. At capacity the oldest records are removed until the new event fits, and the cumulative dropped count is visible in the UI. A duplicate event ID is detected before eviction, so replaying a local insert cannot displace another record. Retry count and creation time survive process death and reboot. If the Keystore key is lost or invalidated, affected ciphertext cannot be recovered and is safely discarded rather than uploaded as malformed data.

## Delivery and offline behavior

An incoming matching SMS only persists the event and schedules unique WorkManager upload work; it does not perform network I/O in the broadcast receiver. Work requires network connectivity and uses bounded exponential backoff. The worker loads a FIFO batch whose serialized request remains within the HTTP limit.

Accepted and duplicate outcomes remove the corresponding local records. Permanent rejected outcomes are terminal and are removed. Transport failures, HTTP 429, and 5xx responses retain records and increment retry state. One 401 triggers the Sprint 2 credential-renewal path with a durable request ID; a second authorization failure clears credentials while preserving the encrypted queue. Re-enrollment creates a different installation identity, so old installation-bound events are not silently uploaded. The UI provides an explicit queue-discard action.

## Backend domain and API

`relay.domain.events` owns the collector-independent envelope, SMS v1 payload, bounds, canonical content hash, and safe rejection reasons. `relay.application.events` owns per-item admission, ownership and historical-rule checks, retention, listing, reading, deletion, and purge orchestration. `relay.application.event_ports` keeps persistence behind repository and unit-of-work ports. SQLAlchemy implementations live in infrastructure; FastAPI schemas, dependencies, and routes live in presentation.

Device API:

- `POST /api/v1/device/events/batch` accepts 1 to 20 JSON values and returns one bounded result for every input item. A malformed item does not invalidate its valid neighbors.
- Status is `accepted`, `duplicate`, or `rejected`. Rejections use stable reasons without echoing payload content.

Administrator APIs:

- `GET /api/v1/events` provides `limit` 1..100, `offset` 0..1,000,000, and optional device, collector, and timezone-aware time-range filters.
- `GET /api/v1/events/{event_id}` returns one stored event.
- `DELETE /api/v1/devices/{device_id}/events` deletes that device's raw events and returns only a count.

All responses use `Cache-Control: no-store`. Device credentials cannot call administrator event APIs, administrator tokens cannot call device ingestion, and device A cannot claim installation B.

## Database and migration

Migration `0003_raw_events` adds `raw_events` without modifying `0001` or `0002`. PostgreSQL stores payload and metadata as JSONB. The table contains internal UUID, external event UUID, device and installation foreign keys, collector, schema version, configuration version, rule ID, occurred/captured/received timestamps, canonical content hash, payload, metadata, and expiry time.

The unique constraint on `(installation_id, event_id)` is the final idempotency authority. A repeated identical event returns duplicate; reuse of the same ID with changed content is rejected. Nested savepoints handle a uniqueness race without poisoning the surrounding batch transaction. Indexes support device/time administration queries and expiry purge. Check constraints enforce supported collector/schema and timestamp relationships. Real PostgreSQL concurrency verification admitted exactly one durable row.

## Retention and observability

`RAW_EVENT_RETENTION_DAYS` defaults to 30 and accepts 1..365. The backend computes expiry at admission. `python -m relay.infrastructure.cli purge-expired-events` deletes expired events and reports only a count; production scheduling is an operator responsibility. Administrator per-device deletion is immediate and does not retain payloads in audit logs.

Safe structured logs contain request IDs, device/installation/event IDs, collector names, result categories, and counts. They never contain SMS bodies, event payloads, access or refresh credentials, enrollment challenges, or administrator passwords. Metrics-style events cover captured/filtered counts, queue capacity and drops, accepted/duplicate/rejected batches, and retryable delivery failures.
