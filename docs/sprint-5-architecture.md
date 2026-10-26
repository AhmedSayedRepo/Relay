# Relay Sprint 5 architecture

## Scope and boundaries

Sprint 5 adds a backend-side, provider-neutral receipt-processing pipeline to finalized Sprint 4 image objects. It does not change Android collection, add Android permissions, call an external AI service, reconcile transactions, move money, or implement Sprint 6 reporting.

```text
finalized image object
  -> durable processing job
  -> bounded image derivative
  -> OCR document
  -> deterministic classifier
  -> versioned InstaPay parser
  -> receipt candidate
  -> duplicate link
  -> administrator review
```

The domain types in `relay.domain.processing` do not import Pillow, Tesseract, SQLAlchemy, or FastAPI. Application ports define preprocessing, object reading, OCR, parsing, and persistence. Infrastructure supplies Pillow, a local Tesseract subprocess adapter, and SQLAlchemy repositories. Presentation exposes administrator-only HTTP resources.

## Trigger, worker, and state model

Finalizing an image atomically ensures the default job `receipt_image:v1 / instapay_receipt / v1`. The finalization request does no image decoding or OCR. An administrator can idempotently request another explicit version tuple through `POST /api/v1/objects/{object_id}/process`.

`python -m relay.infrastructure.cli process-pending-images --limit N` claims at most 1–100 due jobs. PostgreSQL `FOR UPDATE SKIP LOCKED` supports concurrent workers. Jobs move among `PENDING`, `PROCESSING`, `SUCCEEDED`, `NEEDS_REVIEW`, `FAILED`, and `SKIPPED`. Each claim creates an immutable attempt number. Transient failures retry at one and two minutes, with three attempts maximum; permanent failures stop immediately.

A processing attempt acts as the lease generation. Completion and failure updates require the same job attempt number, a `PROCESSING` job, and a still-`PROCESSING` attempt. A reclaimed stale worker therefore cannot overwrite a newer result. Work older than the configured stale interval can be reclaimed after a crash.

The uniqueness tuple is object ID, processor version, parser type, and parser version. A changed processor or parser version creates new job, OCR, and candidate records; it does not overwrite historical provenance.

## Hostile-image preprocessing

The original object remains immutable. The Pillow adapter reads only the bounded object bytes and produces an in-memory PNG derivative. It applies EXIF orientation in place, checks dimensions and pixels before full loading, loads under Pillow's decompression-bomb guard, converts to grayscale, applies one-percent autocontrast, and downsizes to a maximum 2,400-pixel edge.

Hard limits are:

| Resource | Limit |
| --- | ---: |
| Source object | 8 MiB |
| Width or height | 12,000 pixels |
| Pixel count | 20,000,000 |
| Estimated decoded surface | 80 MiB |
| OCR normalized text | 64 KiB |
| OCR line count | 5,000 |
| OCR line length | 512 characters |
| OCR timeout | 1–60 seconds; default 20 |

JPEG, PNG, and WEBP are admitted. Unsupported, corrupt, decompression-bomb, or resource-exceeding content becomes an explicit processing failure.

## OCR provider

`OcrProvider` exposes a fixed provider ID, provider version, available languages, and asynchronous extraction of a `PreprocessedImage` into an `OcrDocument`. The document records requested and available language packs, normalized text, lines, optional bounding boxes and confidence, and execution duration.

The default adapter is Tesseract 5, selected for mature offline execution, Apache-2.0 licensing, deterministic command invocation, Python 3.12 independence, and available English/Arabic trained-data packs. Relay requires absolute executable and tessdata paths. The adapter uses a fixed argument vector with no shell, a filtered environment, a private temporary directory, a hard deadline, and size/row bounds on TSV output. It never silently changes the requested languages.

Only English synthetic accuracy was evaluated for this delivery. The `eng` and `ara` packs were confirmed available, but Arabic recognition accuracy was not claimed.

## Classification and parser

Classification is deterministic. `INSTAPAY_TRANSFER_RECEIPT` requires both an InstaPay brand marker and a receipt-field marker. Everything else is `UNKNOWN_RECEIPT`. Evidence names are stored with the machine result.

`InstaPayReceiptParser v1` consumes only `OcrDocument`. It extracts amount and explicit currency, local date and time, original and normalized reference, optional sender and recipient details, optional recipient identifier, and transfer status. Monetary values use `Decimal` and PostgreSQL `NUMERIC(18,2)`. Receipt time has no invented offset; `timezone_state` remains `UNKNOWN` unless an administrator supplies supported knowledge.

Reference normalization uppercases, converts Arabic/Persian digits, and removes non-alphanumeric separators. The original OCR span remains stored. A normalized reference must contain 8–40 characters; the glare fixture demonstrated why shorter damaged values must remain invalid.

Every extracted field stores value, original text, confidence, status (`CONFIDENT`, `UNCERTAIN`, `MISSING`, or `INVALID`), and deterministic evidence labels. OCR confidence measures legibility. It cannot prove receipt authenticity, so every supported uploaded receipt starts in `NEEDS_REVIEW`. Unknown receipts are `REJECTED` and their jobs are `SKIPPED`.

## Review and duplicate handling

Administrators can list candidates, inspect the original object through the existing authenticated object endpoint, inspect OCR and machine extraction, then `ACCEPT`, `CORRECT`, or `REJECT`. Corrections are allowlisted and validated. Machine extraction never changes. The final snapshot is stored separately and each action records reviewer ID, before/after snapshots, action, and timestamp.

An accepted or corrected candidate is only marked `AUTO_ACCEPTED` after human review when amount, explicit currency, and normalized reference are present. The name is retained as an existing state value; it means accepted into the final set after the review action, not machine-authenticated.

Duplicate identity is `(device_id, receipt_type, reference_normalized)`. It neither relies on image hash nor crosses the device boundary. One candidate is canonical under a partial unique index. Other source objects remain as candidates linked by `duplicate_of_candidate_id` with reason `REFERENCE_NUMBER`. Device-row locking serializes candidate creation and review changes. When retention removes a canonical candidate before its duplicates, the repository deterministically promotes one surviving duplicate and relinks the rest in the same transaction.

## Database and retention

Migration `0005` follows unchanged revision `0004` and creates:

- `processing_jobs` for versioned work, state, retry, lease, and expiry;
- `processing_attempts` for each execution and failure;
- `ocr_results` for provider provenance, text, blocks, confidence, and expiry;
- `receipt_candidates` for searchable business columns, machine/review JSON, and duplicate linkage;
- `receipt_review_audit` for reviewer-attributed changes.

Foreign keys connect each result to its object, raw event, device, job, reviewer, and canonical candidate. Object, raw-event, device, or job deletion cascades through dependent processing records. Review audit deletion follows candidate deletion, while user deletion is restricted. Expiry follows the source image. The processing purge promotes surviving duplicates as needed, then removes expired candidates and jobs; image/device erasure also cascades OCR text and parsed data.

## Administrator API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/objects/{object_id}/process` | Idempotently enqueue a version tuple |
| `GET` | `/api/v1/processing/jobs` | Bounded status/object filtered list |
| `GET` | `/api/v1/processing/jobs/{job_id}` | Job plus attempt history |
| `GET` | `/api/v1/receipts` | Bounded review/type/device filtered list |
| `GET` | `/api/v1/receipts/{candidate_id}` | Candidate, OCR document, and audit |
| `POST` | `/api/v1/receipts/{candidate_id}/review` | Accept, correct, or reject |

Every route requires an active administrator bearer token. Processing and receipt responses are marked `Cache-Control: no-store`. Pagination limits are 1–100 and offsets are bounded to 1,000,000. OCR text and receipt values are never logged.
