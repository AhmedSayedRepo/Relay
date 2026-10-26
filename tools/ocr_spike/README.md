# Offline OCR region-discovery spike

This package is an offline benchmark only. It is not imported by Relay's
backend or worker and does not alter stored pilot records.

Iteration 2 keeps full-page `eng+ara` PSM 6 evidence separate and adds bounded
`eng+ara` PSM 7 crops discovered from image content. Discovery is independent
of field values and combines:

- a low-resolution vertical-boundary estimate of the likely display/document
  working region; OCR anchors can validate the estimate but are optional;
- local background subtraction and local gradient density for uneven lighting;
- the Iteration 1 global-darkness projection as a supplemental signal;
- common-baseline and horizontally adjacent component grouping;
- nested-region suppression that preserves an already grouped full row;
- bounded context expansion and optional handle, reference, date, participant,
  brand, and footer anchor bands.

The working-region estimate is applied only when confidence is at least 0.45
and its width is between 45% and 96% of the image. Otherwise discovery uses the
full image. Targeted OCR is limited to six crops when no working region is
applied and eight when one is applied, with an absolute maximum of eight and a
combined crop-pixel budget of 50% of the image.

Primary and targeted token text, confidence, geometry, OCR mode, and provenance
remain separate. Candidate merging retains conflicts. Transfer amounts require
an explicit adjacent supported currency token and receipt-structure evidence;
fee/total rows, phone numbers, references, dates, and times are not promoted to
transfer amounts.

The JSON manifest already accepts `expected_receipt: false`, so generic
non-receipt negatives can be added without changing the implementation. Fields
listed in `uncertain_expected_fields` are excluded from exact-recovery metrics.

