# Sprint 5 proposal only

Sprint 5 may add an extraction pipeline after separate review and approval. No item in this document is implemented in Sprint 4.

Proposed scope:

1. Introduce a processor interface operating on authorized, finalized binary objects.
2. Define deterministic image preprocessing with explicit resource bounds and reproducible outputs.
3. Evaluate local and open-source OCR first; OCR must not imply a paid AI dependency.
4. Define processor confidence, error, provenance, and version fields.
5. Define a structured extraction schema that preserves the immutable source event/object relationship.
6. Keep providers behind an optional abstraction; any external AI fallback must be separately enabled, bounded, privacy-reviewed, and observable.
7. Prefer deterministic rules where practical before probabilistic extraction.
8. Add payment-receipt candidate processing only after a business and privacy review.
9. Add an explicit InstaPay/payment parser as a separate business module only if approved; keep the generic collector free of provider-specific behavior.
10. Detect duplicate transactions using extracted reference identifiers while keeping content hash, event ID, and transaction identity distinct.
11. Add a review and manual-correction workflow with audit history.

Required preconditions include threat modeling of OCR parsers and external providers, processor sandbox/resource limits, test corpora with no live personal data, retention rules for extracted text, access-control decisions, and explicit product approval. AI remains optional.
