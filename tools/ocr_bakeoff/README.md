# Relay OCR engine bake-off

This package is an isolated benchmark. It does not register a production OCR
provider or alter the parser, duplicate handling, review behavior, Android,
worker, dashboard, reporting, or pilot data.

It compares the current frozen Tesseract evidence with Google Enterprise
Document OCR and Azure Document Intelligence `prebuilt-layout`. Google and
Azure adapters return Relay's existing `OcrDocument` model, demonstrating that
the current `OcrProvider` boundary can carry their text, confidence, token, and
geometry evidence.

Cloud credentials are read only from environment variables and are never
written to results:

- Google: `GOOGLE_DOCUMENT_AI_PROCESSOR_NAME` plus either
  `GOOGLE_OAUTH_ACCESS_TOKEN`, or `GOOGLE_APPLICATION_CREDENTIALS` with the
  optional `google-auth` package installed.
- Azure: `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` and
  `AZURE_DOCUMENT_INTELLIGENCE_KEY`.

Per-image result files make cloud execution resumable so a failed run does not
repeat already successful billed requests.

The Google-only entry point is `tools.ocr_bakeoff.google_benchmark`. It writes
raw provider evidence to `raw-google-evidence/` and the conservative Relay
selection/promotion result to `relay-extraction-results/`. The adapter sends
the original file bytes; base64 is used only as the Document AI REST transport
encoding. The benchmark does not normalize OCR-confused `ECP` into `EGP`.
