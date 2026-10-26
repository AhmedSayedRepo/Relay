# Relay Sprint 4 proposal — image and media receipt collection

This document is a proposal only. Sprint 4 is not implemented in this repository.

Sprint 4 is expected to focus on image and media receipt collection:

- explicit user-approved image source strategy
- Android media/file ingestion
- configurable image identification and filtering
- generic binary object storage
- image event metadata
- hashing and deduplication
- secure upload
- retention
- preparation for OCR

OCR and payment-specific extraction must be evaluated separately rather than automatically bundled into Sprint 4. Before implementation, product and security review must approve the Android source and distribution policy, consent and permission flow, allowed metadata, content and object size limits, local/server retention, deduplication scope, storage encryption, upload resumption, deletion behavior, and production abuse controls.

The Sprint 3 generic event and authentication layers may be reused for image metadata, but binary content requires a deliberately separate bounded object-transfer and object-storage design. Sprint 4 must not weaken local consent, installation ownership, device revocation, no-payload logging, or retention guarantees established in earlier sprints.
