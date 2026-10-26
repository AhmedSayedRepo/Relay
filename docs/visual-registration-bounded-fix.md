# Final bounded InstaPay visual-registration attempt

Date: 2026-09-29. **Decision: STOP_CURRENT_INSTAPAY_IMAGE_PATH_NOT_VIABLE.**

The fixed visual-registration implementation admitted **0/17** frozen positive
receipts. No targeted OCR ran; zero-touch coverage remains **0/17**. Keep
`RECEIPT_EVIDENCE_ENABLED=false`. This is failed product acceptance, not successful
automation or a production-ready registration implementation. No engine research,
threshold adjustment, further image architecture, rollout or P06 follows.

## Bounded change and preserved safety

- `visual_registration.py`: existing OpenCV ORB, Hamming reciprocal/ratio matching,
  RANSAC homography, spatial-support checks, geometric checks and canonical warp.
- `visual_instapay_extraction.py`: canonical profile ROIs, unchanged existing
  Tesseract modes7/6, explicit field parsing and the existing trust thresholds.
- `instapay_adapter.py`: delegates to the visual path. Old text-anchor utilities
  remain characterization helpers only; no admission or fallback calls them.
- `processing.py`: an explicitly non-admitted adapter may persist empty native
  evidence without fabricating an OCR transcript. Genuine empty OCR still fails.
  Unknown receipts retain the existing UNKNOWN_RECEIPT/SKIPPED job semantics.
- `pyproject.toml`: packages the eight final profile JSON/PNG assets.
- Focused visual/crop/conflict tests and three public-path empty-evidence tests.

The generic transaction/evidence domain, duplicate and review semantics were not
redesigned. Domain evidence and original Tesseract source hashes match the pre-fix
snapshot. No new dependency, OCR engine, model, cloud OCR or recognition setting
was introduced. Existing approved source fusion is retained; no SMS was invented.
No Android permissions, frontend source or production configuration changed.

The design references were [OpenCV feature matching/homography](https://docs.opencv.org/4.x/d1/de0/tutorial_py_feature_homography.html),
[OpenCV ORB/Hamming](https://docs.opencv.org/4.x/d1/d89/tutorial_py_orb.html) and
[invoice2data field-area templates](https://invoice2data.readthedocs.io/latest/tutorial.html#fields-parsers).
Only the pattern was used; no external package/code was copied into Relay and no
invoice-style static currency default was adopted.

## Profiles, privacy and calibration limitation

The two family names remain versioned:
`instapay_compact_bilingual_v1` and `instapay_expanded_bilingual_v1`.
Each has Arabic/English visual variants in a720×1440 canonical coordinate system.
Adapter provenance isv2; extraction provenance is`visual_canonical_v1`.

Only whitelisted static UI labels/artwork were retained in final template images.
Transaction amounts, currency values, references, dates, times, handles, account
identifiers, participant names and notes were excluded from registration assets.
Descriptor neighborhoods were constrained to static authoring regions. All four
final template images were inspected. The initial private-pixel authoring draft
was moved out of product source and preserved locally; neither it nor raw
calibration previews are bundled in the wheel.

P01, S06, S07 and S08 supplied template calibration images. Template-authoring
screen quadrilaterals align those reference images only. Runtime receives original
image bytes and provenance, never a benchmark ID, source-specific coordinates,
hash lookup, reviewed truth or hardcoded transaction value. Runtime ROIs are
canonical template coordinates. **This is not an independent held-out test**;
results separately identify the four calibration cases and thirteen other
positive cases. Both groups achieved zero admissions and zero automation.

These are candidate profile definitions, **not validated supported layouts**.
No per-sample profile or additional family was created. P02/S05 also show an
arrow/action-row variation not specifically calibrated here; its existence alone
does not prove the cause of their matching failures.

## Fixed admission policy

Input uses the existing bounded grayscale/normalization path. ORB retains at most
5000 input features. Descriptor distance≤64, reciprocal nearest match and
ratio<0.75 are required. At least18 good matches,12 inliers and an inlier
ratio≥0.65 are required. At least3 inliers must occur in each upper-field,
participant and detail zone, with horizontal span≥25% and vertical span≥45%
of the canonical document. Matching only a footer/logo or picking the highest
score cannot admit a layout. Multiple passing variants are ambiguous/review.

Subsequent geometric checks reject invalid/singular/reflected homographies,
projective poles, implausible document area/aspect/scale, degenerate receipt
quadrilaterals, severe perspective, truncation and ROIs outside visible pixels.
No rectangle-only or loose OCR-anchor fallback was implemented.

All profile attempts retain counts, inlier ratios, spatial zone counts and
rejection reasons in immutable machine evidence. No receipt reached the final
geometric/ROI-visibility stage in this run. Rejected pre-spatial homographies are
not exported as complete matrices; do not claim complete transform replay from
their count diagnostics alone.

## One frozen complete product-path run

The same23 originals were SHA-256 verified before real local API upload,
ProcessingService extraction/resolution, isolated database persistence and
receipt/job API readback. All original hashes and reviewed ground truth match the
prior product run. Uncertain/unavailable fields were excluded exactly as before.
There was no live backend/worker, fabricated SMS, image-specific enhancement,
repeat benchmark or score-driven policy change. Pre-run policy/asset fingerprints
were captured before validation and confirmed unchanged afterward.

Evidence is in the workspace `outputs/relay-visual-registration-product-path`:
`product-results.json`,23 per-image evidence files and
`pre-run-policy-fingerprints.json`. Prior product and pilot results remain intact.

| Metric | Previous text-anchor path | Fixed visual path |
| --- | ---: | ---: |
| Positive layout admission | 0/17 | 0/17 |
| Zero-touch | 0/17 | 0/17 |
| Exception review | 13/17 | 0/17 |
| Unknown positives | 4/17 | 17/17 |
| Processing failures | 0 | 0 |
| Correct reference resolution | 0/17 | 0/17 |
| Correct amount+currency resolution | 0/11 | 0/11 |
| Correct date+time resolution | 0/17 | 0/17 |
| Synthetic negative false receipts/promotions | 0/6 | 0/6 |

The lower review count is **not an improvement**: all positives became unknown.
Resolution among admitted receipts is **N/A (denominator0)** for all three field
metrics, not0% accuracy. Full-transaction resolution is0/17 overall andN/A among
admitted cases. Zero automatic errors were observed, but the automatic error rate
is unmeasurable because no transaction was accepted.

All language/photo/crop subsets have zero admission, resolution and automation.
Arabic6/6, English5/5, mixed6/6, photographed10/10 and cropped/compressed6/6
positives are unknown. Sender/handle and recipient identifiers were not resolved.
No real negatives or actual SMS were available. Six synthetic negatives cannot
establish production false-positive safety. No amount/currency/reference column
or unsafe transaction was promoted on any negative.

## Every positive registration miss

No image selected either family or any variant. “Other” below is specifically
insufficient multi-region inlier support, distinct from OCR recognition failure.

| Image | Primary failure category | Observed diagnostic/context |
| --- | --- | --- |
| P01 | Other: spatial support | Calibration source; compact_ar63 matches/47 inliers; zones0/14/0 |
| P02 | Other: spatial support | Tall arrow variant; compact_ar35/28; zones0/3/0 |
| P03 | Other: spatial support | Compressed alternative; compact_ar83/70; zones0/15/0 |
| P04 | Other: spatial support | compact_ar58/46; zones0/11/0; amount OCR never entered |
| P05 | Other: spatial support | compact_ar54/39; zones0/9/0; amount OCR never entered |
| S01 | Other: spatial support | Top cropped; compact_ar28/23; zones0/0/0 |
| S02 | Other: spatial support | Photograph; compact_en21/14; zones0/4/0 |
| S03 | Insufficient visual matches | Glare photograph; every variant below18 matches |
| S04 | Insufficient visual matches | Photographed small screen/glare; maximum6 matches |
| S05 | Insufficient visual matches | Shadow/arrow variant; maximum16 matches |
| S06 | Other: spatial support | Calibration source; expanded_en120/108; zones0/24/36 |
| S07 | Other: spatial support | Calibration source; expanded_ar110/107; zones0/1/19 |
| S08 | Other: spatial support | Calibration source; compact_en52/42; zones0/26/0 |
| S09 | Insufficient visual matches | Cropped top/total-only; maximum7 matches |
| S10 | Insufficient visual matches | Cropped lower-only; maximum14 matches |
| S11 | Other: spatial support | compact_ar40/28; zones0/1/0 |
| S12 | Other: spatial support | Notification/crop; compact_ar28/21; zones0/0/0 |

Counts:12 spatial-support failures,5 insufficient-match failures;0 primary
homography failures and0 post-spatial geometric rejections. The number of genuinely
unsupported visual families is undetermined: failed matching cannot establish
that a layout is intrinsically unsupported. There are no admitted extraction
failures to categorize as ROI/OCR/parser/conflict; those categories areN/A.

### Exact measured blocker, including implementation defect

Not one real receipt produced the required upper-field-zone inliers. Many robust
inliers were confined to footer, participant or detail artwork; they do not safely
locate all critical fields. This failed even on the four calibration originals.

The descriptor inventory additionally reveals a **template adequacy defect**:
expanded_ar contains zero upper-zone descriptors,6 participant-zone descriptors,
68 detail and193 footer descriptors. It cannot satisfy the fixed upper-zone rule
on any image. The other assets contain upper-zone descriptors (compact_ar48,
compact_en31, expanded_en129), but no real-case accepted homography retained
upper-zone inliers. The exact pixel/scale/contrast mechanism for every lost match
was not independently isolated; do not attribute all misses merely to poor photos.

Fixture tests prove idealized static-template matching and safe rejection, not
real layout readiness. The calibration/template mismatch and missing descriptor
support remain unresolved. **The hard stop applies; no template or threshold
repair was attempted after the measured failure.**

P04/P05 did not recover790EGP in the final product result. Their controlled
canonical-crop regression passes with a fixture recognizer, not real frozen OCR.
S02/S06 are unknown; S04 is unknown, with no reference promoted. Separate conflict
tests retain both reference candidates and prevent a confidence winner.

## CPU performance

Measured during the single23-image sequential Windows CPU path; upload time is
excluded, while worker claim/storage/registration/resolution/persistence is
included. Registration's first-use imports and asset loading are included.

| Stage, n=23 | Average ms | Nearest-rank p95 ms | Max ms |
| --- | ---: | ---: | ---: |
| Registration | 558.30 | 593.00 | 1890.00 |
| Targeted OCR charged to total | 0.00 | 0.00 | 0.00 |
| Complete product processing | 624.49 | 666.27 | 2011.53 |

Targeted recognition latency **cannot be estimated**: no ROI recognition was
executed. Zero elapsed OCR is not evidence of fast recognition. Peak observed
process-tree RSS was320.004096 decimalMB (305.18MiB), monitored throughout all23
cases. No GPU or heavy VLM/model is required. Faster rejection is not a product
success.

## Verification / limits / handoff

Fresh results:346 non-PostgreSQL backend tests plus9 PostgreSQL tests passed in
complementary runs (355 total),22 frontend tests passed,46 debug plus46 release
Android unit tests passed, and standard Android lint/builds passed. Ruff and strict
MyPy84 source files pass. Black leaves118 files unchanged but still fails the
same two unchanged closed-research tests. Kotlin lint is blocked by three uncached
dependencies; Android instrumentation and manual browser checks were not run.
The first migration helper failed without a useful diagnostic; its isolated retry
passed the full migration cycle and all PostgreSQL checks. Its precise first-run
failure cause is unisolated. The disposable database is stopped; data/logs remain.

The wheel was built locally from an isolated source copy, and all eight profile
assets are present; calibration/draft images are absent. Nothing was installed,
published or deployed. No Git metadata exists in this source export, so no branch,
commit, push or PR is claimed. The pre-fix snapshot is preserved in workspace
`work/visual-registration-baseline-20260929`.

The code-work verification rules kept prior work intact and separated passing
regressions from the failed product requirement. **Next action: keep disabled and
request a product-level decision. Do not begin another current-image-path fix,
engine experiment, threshold/layout iteration or P06 automatically.**
