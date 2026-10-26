# Sprint 4 image pipeline architecture

## Scope and source decision

Sprint 4 uses Android's Storage Access Framework (SAF) with `ACTION_OPEN_DOCUMENT_TREE`. The system picker gives the user a clear, revocable choice and avoids broad storage or media-library permissions. Relay persists read access to one selected document-provider tree and enumerates direct children only. It does not traverse arbitrary storage, access another app's private files, scrape a UI or notification, or call a private app API.

The Android app supports API 26–36 (`minSdk 26`, `targetSdk 36`). SAF tree access needs no runtime storage permission on those versions. The app records the approval time and accepts only candidates whose provider modification time is later, so historical files are not imported by default.

## Consent and collection gates

New collection runs only when all of these are true:

```text
local image consent
AND active installation credentials
AND validated remote images.enabled
AND configured SAF source ID matches the approved source
AND Android still reports persisted read access
```

The server cannot set local consent or grant Android access. Clearing local consent immediately blocks collection and deletes Relay's queued encrypted image copies. Clearing the selected source also releases persisted URI access. A disabled device loses valid credentials and cannot initialize or continue authenticated uploads.

## Candidate and filter contract

`ImageSource -> ImageCandidate -> ImageCollector` keeps acquisition separate from policy and delivery. `SafTreeImageSource` returns bounded metadata, validates the provider MIME using header-only Android bounds decoding, and reads bytes only after a rule matches. Candidate URIs and filenames are not logged.

The declarative `collectors.images` object contains exactly `enabled`, `sources`, and `rules`. Sources have a bounded ID and `type: "saf_tree"`. Rules select a source and may constrain MIME, filename contains/prefix/suffix, byte range, and width/height range. Only JPEG, PNG, and WEBP are supported. Arbitrary code, shell commands, regular expressions, OCR rules, and payment fields are rejected by schema validation.

The current schema deliberately omits the optional time-window filter. The post-approval watermark supplies the required no-backfill behavior; a configurable time window remains a documented extension point.

## Identity and encrypted local queue

After filtering and bounded reading, Android computes SHA-256 over the actual bytes. Event UUID and content hash remain separate identities. The encrypted binary outbox stores the complete queued record in the app's no-backup directory with an Android Keystore-backed AES-GCM key. Only queue keys, ciphertext size, creation time, and the content hash used for local uniqueness remain database columns; bytes, filename, metadata, session ID, and progress are inside ciphertext.

The queue permits 20 images, 32 MiB total encrypted data, 8 MiB per image plus encryption/metadata overhead, and seven days of retention. It deterministically evicts the oldest record when count or byte capacity is reached and exposes dropped counts. It persists server session ID and acknowledged offset after each chunk.

## Resumable upload

1. The authenticated device initializes metadata with stable upload and event UUIDs.
2. The backend validates installation ownership, current configuration/rule, limits, and per-installation dedupe.
3. If bytes are needed, the server returns a stable session and current offset.
4. Android sends chunks of at most 256 KiB at the exact next offset. An exact already-written retry is reported as duplicate; gaps, conflicting overlap, and overflow fail.
5. Finalization requires the declared byte count, verifies full SHA-256, inspects actual JPEG/PNG/WEBP headers and dimensions without decompression, and atomically moves the staging file to its generated object key.
6. The same transaction links image schema v1 metadata to a generic raw event. A confirmed accepted or duplicate completion removes the local encrypted record.

Session and object access always checks device and installation binding. Dedupe is scoped to one installation, avoiding cross-device existence disclosure. PostgreSQL uniqueness protects `(installation_id, upload_id)` and `(installation_id, content_hash)`. Per-device initialization locks the device row before quota decisions so concurrent requests cannot bypass active-session or byte limits.

## Object storage port

Application code depends on `ObjectStoragePort`. The filesystem adapter owns generated staging and `objects/<installation>/<uuid>.blob` paths, rejects absolute or parent-traversal keys, fsyncs appended chunks, bounds reads, and uses atomic replacement on finalization. Compose mounts a named volume at `/var/lib/relay/objects`. S3-compatible or Azure Blob adapters can implement the same port without changing domain or use-case code.

## Database and retention

Migration `0004_binary_objects` creates `binary_objects` and `binary_upload_sessions`, adds checked state/size/MIME/source fields and query/expiry indexes, and adds nullable `raw_events.object_id`. The object foreign key uses cascade deletion so object deletion removes its linked image event. Existing migrations `0001`, `0002`, and `0003` are unchanged.

Finalized objects and linked image events share a configurable expiry, default 14 days. Active sessions expire after 24 hours by default. The purge command removes expired staging uploads, objects, and database links. Administrator APIs can delete one object or erase all image objects and upload sessions for a device.

## Administrator endpoints

Administrators can list/filter generic events with `collector=images`, retrieve event metadata, retrieve object metadata, download content, delete one object, or erase one device's image data. Object responses never return `storage_key`. Content uses the verified MIME, a generated safe filename, `Content-Disposition: attachment`, `Cache-Control: no-store`, and the global `nosniff` header.
