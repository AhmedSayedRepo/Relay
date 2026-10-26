# One bounded PaddleOCR CPU candidate

This provider implements Relay's existing `OcrProvider` interface without
registering or switching a production provider. It sends no image to an OCR API.
Tesseract is not invoked. GPU support is optional; this measurement uses CPU only.

## Fixed implementation

- PaddleOCR 3.7.0 / PaddlePaddle CPU 3.3.1.
- PP-OCRv5_server_det + arabic_PP-OCRv5_mobile_rec.
- Arabic recognizer dictionary includes Latin letters and digits.
- Four inference threads; MKL-DNN disabled; optional image enhancement models off.
- Provider-native detection resize/crop only; original bytes are decoded locally.
- Zero recognition filtering threshold retains low-confidence observations.
- Native recognition text, polygons, rectangular word geometry, and span scores.
- Word confidence is unavailable; it is not fabricated. Existing Relay extraction
  uses native line/span confidence where independent word confidence is absent.
- Model repository revisions, file checksums, and installed versions are retained.

Only official free packages and model artifacts require download during setup.
With local model directories, inference operates offline. The benchmark blocks
outbound Python socket connections and permits only numeric loopback for Windows
asyncio's internal socketpair. Rendering uses a local font, not a remote asset.

## Scoring and safety

Use the frozen 23-image manifest and reviewed ground truth unchanged. Final
deterministic Relay extraction determines the decision. Raw coverage is separate.
No sample-specific tuning, reference repair, currency repair, or invented fields.

Pass: reference and amount/currency pair each >=95%, with zero unsafe negative
promotions. Close: both >=90%, below pass, with zero unsafe promotions. Otherwise
reject. Ratios are checked before rounding. Rejection ends work on this provider;
there is no automatic tuning cycle.

The separate trust layer verifies all five critical fields, confidence >=0.95,
valid structure, actual source text/geometry, receipt evidence, and conflicts.
Those thresholds do not establish calibrated correctness. Automatic acceptance
and duplicate-link authority remain disabled until independently validated.
Operator-confirmed references remain the current authority. No authenticity or
transfer-success inference is performed.

## Evidence and reproducibility

The benchmark saves raw native responses, mapped OCR evidence, final selected
fields/conflicts, and trust decisions. It refuses to overwrite an existing run.
Model weights and ground-truth manifests must pass SHA-256 verification.

Image latency includes CPU decoding and detection/recognition, with the first
image included. Startup and initial artifact download are separate. Windows
process-lifetime peak resident working set includes initialization. Runtime timeout
is checked after native inference completes; hard cancellation is not supported
by an in-process Python thread. Any future production integration must use a
supervised process for enforceable execution/memory limits.

`paddle_benchmark` is the isolated entry point. `paddle_provider` is the adapter.
The measured output's dependency/version manifest and model manifest are the
reproduction lock; no change to Relay's production dependency list is made.
