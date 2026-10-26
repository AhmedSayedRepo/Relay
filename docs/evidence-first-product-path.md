# Evidence-first product path — bounded implementation decision

Date: 2026-09-29. Decision: **STOP_NOT_VIABLE_FOR_ZERO_TOUCH**.

The architecture is implemented behind `RECEIPT_EVIDENCE_ENABLED=false`. Do not
enable it for a production pilot. The complete frozen product-path validation
resolved **0/17** receipts without operator action. This is a product acceptance
failure, not a successful conservative automation result. No OCR research,
recognizer tuning, cloud OCR, deployment or P06 follows from this report.

## Implemented boundaries

`ReceiptAdapter` / `EvidenceExtractor` supplies `ReceiptEvidence` containing native
OCR, geometry, candidates and provenance. Generic `FieldCandidate`,
`EvidenceSource`, `FieldResolution`, `TransactionCandidate` and `ResolutionPolicy`
separate evidence from trusted business columns. InstaPay format/anchor rules are
outside the generic domain. Existing legacy processing tuples remain readable.

The first adapter performs bounded grayscale/EXIF geometry normalization, practical
screen-quadrilateral perspective mapping and finite right-angle orientation checks.
It recognizes three relative anchor bands, not an authoritative full-page
transcript. It enters label-relative amount/reference/date value regions only
after structural layout admission. Amount localization is above the transfer
amount label and separate from fee/total regions; a telephone is not a reference.

Defined profiles: `instapay_compact_bilingual_v1` and
`instapay_expanded_bilingual_v1`. **Neither was selected on the frozen set.** These
are implemented profile definitions, not empirically validated supported layouts.
Unknown layouts are never forced into a template.

The existing Tesseract executable and existing recognition modes 6/7 are reused,
without changes to `tesseract_ocr.py`, language models or recognition settings.
Two crop modes are an internal consistency check, not independent evidence.
No Paddle/VL model or new OCR provider is integrated.

## Locked trust rules

A field needs format validation, semantic location, provenance, adequate observed
quality and no conflicting structural candidate. A high score alone cannot resolve
it. Conflicts never select a confidence winner or speculate about digit repairs.
Reference observations require 12 nonzero-leading numeric digits and a reference
region, rejecting telephone-shaped values. EGP is never inferred from receipt type.
Explicit failure text alone can produce `TRANSFER_FAILED`; recognition failure is
`PROCESSING_FAILED`, not a failed transfer. `AUTO_FINALIZED` is data resolution,
not settlement verification, authenticity or tamper detection.

The fixed initial receipt policy requires a unique supported semantic region and
exact agreement of existing crop modes for strong critical receipt evidence, plus
minimum matched-token/line quality: reference 0.98, amount 0.90, currency 0.95,
date/time 0.90. This policy was not weakened after the failed validation. These
numbers are not a calibrated probability of correctness or production eligibility.

Existing admitted SMS is queried before receipt extraction: same device, bounded
±24-hour retrieval window, maximum 200 events. Sender and capture timestamp provide
provenance only. A small explicit labeled-body contract parses supplied reference,
transfer amount, currency, date, time, sender handle and recipient identifier; it
does not claim support for arbitrary bank messages. Correlation requires unique
exact reference plus party/amount evidence, or exact amount/currency/party/local
date/time agreement. Capture time is not converted into a transaction time.
Safely correlated explicit fields can skip targeted recognition; disagreements
remain blocking. Correlation is never a canonical duplicate key.

The explicit SMS contract is version `v2`: bare Arabic “pound” (`جنيه`) is
ambiguous and cannot become EGP; `جنيه مصري`, `ج.م`, or an explicit supported ISO
code is required. This post-validation safety guard changes no receipt OCR rule.
There was no SMS in the frozen set, so its product evidence was not rerun or edited.

No real SMS accompanied the frozen images. Fusion/source-first behavior is covered
by controlled contract tests, not measured receipt+SMS field performance.

## Persistence, duplicate and review behavior

Only resolved or explicitly operator-confirmed normalized reference populates the
canonical key. Untrusted references remain in evidence JSON. Device-scoped atomic
reference duplicate evaluation, row-lock ordering and partial uniqueness remain
the authority; amount/time similarity cannot link duplicates. Both original
images and canonical/duplicate relationships are retained. Ordinary trusted
duplicates do not create mandatory review in the new path. Rejection retains
previously trusted identity; it never promotes an unresolved reference.

Migration `0007` is additive: `receipt_evidence` and
`receipt_exception_commands`, with candidate-linked retention. Machine evidence is
immutable. Operator corrections preserve original candidates, actor, timestamp,
exception reason, final value and correction flag; later aggregates can remove
identifying data. No model training is performed.

`POST /api/v1/receipts/{id}/exceptions` accepts one blocking field at a time:
`CONFIRM`, `CORRECT`, or receipt `REJECT`, with a command UUID and expected revision.
Same-command/same-body retries replay the response without another audit entry;
stale or changed-payload requests fail. Resolved fields cannot be reconfirmed.
Once blockers are resolved, review-finalized processing continues automatically.
Legacy blanket Accept/Correct cannot bypass evidence-first exceptions.

The review queue/detail supports candidate selection, protected original image,
original-coordinate evidence polygon, Confirm/Correct/Reject/Next, read-only
resolved values, English/Arabic, RTL and both existing themes. No Android source
access or permission was added.

## One frozen complete product-path validation

All 23 original-byte SHA-256 values matched the preserved frozen manifest before
upload: P01–P05, S01–S12 and N01–N06. Reviewed ground truth and uncertain-field
exclusions were unchanged. Samples/IDs/hashes/values do not enter adapter rules.

Each image used actual API upload/finalization, asynchronous processing service,
adapter, deterministic resolution, isolated SQLite persistence and receipt/job
API output. No live backend or worker was contacted. Raw regional recognition,
all orientations, confidence, geometry and normalized decisions are retained in
individual `*-product-evidence.json` files. No synthetic SMS was added.

The harness paused at S04 because it sent a chunk larger than the existing 256KiB
limit. S01–S03 results were retained; only the remaining twenty images were then
processed. No image was recognized twice and no policy changed between segments.
Both isolated databases are retained. Dataset/field denominators are unchanged.

| Product metric | Result |
| --- | --- |
| Zero-touch receipts | 0/17, 0% |
| Correct fully resolved transactions | 0/17 |
| Exception review | 13/17, 76.470588% |
| Unknown positive receipts | 4/17: S04, S05, S10, S12 |
| Processing failures | 0 |
| Critical fields per exception | 5.0 |
| Additional admission decisions per exception | 2: classification and layout |
| Correct reference resolution | 0/17 verified |
| Correct amount/currency resolution | 0/11 verified |
| Correct date/time resolution | 0/17 verified |
| Unsafe negative transaction promotions | 0/6 synthetic |
| False negative-set receipt classifications | 0/6 synthetic |
| Critical errors in auto-finalized receipts | 0 observations; automatic denominator = 0 |

An accuracy/safety rate among zero-touch transactions cannot be estimated from
zero accepted cases. Missing/uncertain ground truth is not scored as wrong.
Sender/handle and recipient identifiers remain unresolved in this run; this is
not evidence of successful participant extraction. No arithmetic recovers absent
transfer amounts from fees/totals in the cropped cases.

| Subset | Receipts | Auto | Review | Unknown |
| --- | ---: | ---: | ---: | ---: |
| Arabic | 6 | 0 | 4 | 2 |
| English | 5 | 0 | 4 | 1 |
| Mixed | 6 | 0 | 5 | 1 |
| Photographed screen | 10 | 0 | 7 | 3 |
| Cropped/compressed | 6 | 0 | 4 | 2 |

Six negatives are synthetic. `REAL_NEGATIVE_SAMPLE_MISSING=yes`. This does not
establish production false-positive safety. The separate offline
`tools.evidence_validation.real_negatives` accepts original-byte, SHA-256 verified
manifests requiring explicit reviewed `expected_receipt=false` and
`synthetic=false`; it preserves a separate denominator and rejects overwrites.
No real-negative set was invented or evaluated.

## Product bottleneck and stop

All receipts failed structural layout admission. Existing local recognition often
retained numeric values but omitted or corrupted the low-contrast semantic labels
required to identify their roles. Even P04/P05 had native header evidence of 790
and EGP, but the layout gate did not admit them; the targeted critical-field path
was never entered and the final amount/currency recovery was **not achieved**.
S02/S06 also remained review cases. S04 was held as unknown. A separate
wrong-reference conflict regression prevents selecting a confidence winner;
that unit test is not a claim that the frozen run recognized the same wrong value.

This implementation depends on reliable anchor admission before targeted field
recognition. The frozen results show that dependency fails with the present
available evidence. Five critical corrections plus two admission decisions per
exception does not satisfy minimal intervention. Real approved structured-source
availability is also unmeasured; it cannot be assumed to repair this result.

**Hard stop:** do not enable production, adjust thresholds/templates against these
cases, start another engine/configuration benchmark or begin P06. A product-level
decision about evidence availability and safely supported receipt layouts is
required before any further implementation authority.

## CPU evidence

Sequential Windows CPU run, no GPU. Complete worker processing, including claims,
storage reads, adapter, resolution and persistence: average **4387.63ms**, nearest
rank p95 **5290.24ms**, max **5505.76ms**, n=23. Upload time is excluded; this is not
a paid-provider latency comparison.

Mean stage times, n=23: layout/normalization together **262.87ms**, of which
geometry is **73.39ms**; regional recognition **4060.48ms**; fusion/resolution
**0.086ms**. The recognition stage is anchor-band recognition here, not successful
critical-field recognition. Residual total time includes database/storage work.
Geometry is a subset of layout timing and must not be double-counted.

Observed peak process-tree working set was **196.24MB / 187.15MiB** during the
resumed twenty-image segment. The first segment's peak was not retained when the
upload harness stopped, so full-23 peak RAM is **unverified**, not silently inferred.
This path is lightweight; its failure is useful zero-touch coverage, not CPU/RAM.

Recognizer version: Tesseract `5.5.3.20260724`, existing `eng+ara` models. Model
SHA-256: eng `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2`;
ara `e3206d3dc87fd50c24a0fb9f01838615911d25168f4e64415244b67d2bb3e729`.

## Verification and handoff

See the workspace product-validation `verification.md` for commands, successes,
failed environment runs, skipped/blocked checks and final status. No Git metadata
exists in this source export; branch/commit/diff status is not fabricated. A
pre-edit source/migration/test/frontend snapshot is retained outside the product.
Stored pilot results, original images, old OCR evidence and ground truth were not
modified.

Rollback/retention: keep the feature disabled; retain immutable evidence and audit.
Only apply schema migrations in an approved rollout. Disabling new evidence job
enqueueing does not erase already-created versioned jobs or audit records. Do not
drop `0007` tables on a populated production database as a rollback shortcut.
