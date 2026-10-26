# Offline OCR region-discovery spike

This folder is an offline engineering spike. Nothing in src imports it and it
is not connected to the backend or worker.

- samples: place only explicitly staged, sanitized benchmark images here.
- manifests: one JSON manifest per controlled benchmark set.
- results: machine-readable benchmark output. Private pilot output stays in
  the pilot evidence directory.
- diagnostics: annotated candidate-region images. Private images stay in the
  pilot evidence directory.

Naming: S01-ar-clean.png through S12-negative-numeric.png. Each manifest must
record expected visible values and target bounding boxes. Participant names
may be masked, but any field being tested and its layout anchors must remain
visible.

Iteration 2 estimates an optional display/document working region from visual
boundaries, uses local darkness and gradient evidence, groups fragments on a
common text baseline, and retains the prior global projection as supplemental
evidence. Anchors guide ranking but are not required for discovery.

Safety bounds are enforced by DiscoveryBounds: six targeted OCR crops without
an applied working region, up to eight with one, an absolute maximum of eight,
a 50 percent total cropped-pixel budget, bounded relative dimensions and
density, a 20-second per-sample OCR deadline, and provenance-preserving value
merging. Conflicts remain unresolved. A manifest can add generic negatives by
setting `expected_receipt` to `false`; no implementation change is required.

Run from the repository root with PYTHONPATH including src and the repository:
python -m tools.ocr_spike.benchmark --manifest MANIFEST --output OUTPUT
  --tesseract TESSERACT --tessdata TESSDATA
