# Relay Sprint 5 security review

## Assets and trust boundaries

Receipt images and OCR text can contain personal and financial data. Device uploads, image decoders, OCR output, and review requests are untrusted. Administrator authentication, the database, object store, worker configuration, and audit records are privileged boundaries. OCR confidence is treated only as recognition quality; it is not transaction authenticity.

## Controls implemented

- Finalization only queues work, preventing native OCR from consuming HTTP worker capacity.
- Source bytes, dimensions, pixels, estimated decoded memory, derivative dimensions, OCR duration, output bytes, row count, text bytes, and line length are bounded.
- EXIF transpose is in place to avoid a second full-size decoded surface.
- Tesseract uses an operator-configured absolute executable and tessdata directory, a fixed argument vector, `shell=False`, no inherited sensitive environment, a private temporary directory, output suppression, a hard timeout, and guaranteed cleanup.
- OCR documents are validated against the claimed provider and exact requested language tuple before persistence. Missing packs fail closed.
- Normalization is bounded and parser regular expressions contain no attacker-controlled pattern.
- OCR text and extracted values are excluded from logs and errors. Content-bearing administrator APIs use `Cache-Control: no-store`.
- All processing and review APIs require an active administrator. Corrections use an allowlist, schema limits, domain validation, parameterized SQL, and typed JSON.
- Every supported upload remains `NEEDS_REVIEW` until a human acts. Machine output records `source_trust=UNVERIFIED_UPLOAD`.
- Machine extraction remains immutable; review stores a separate snapshot and reviewer-attributed before/after audit.
- Duplicate lookup is device-scoped. A partial unique index and device lock prevent concurrent canonical duplicates.
- Attempt ownership conditions prevent a stale worker from writing success or failure after job reclamation.
- Retention explicitly promotes a surviving duplicate before deleting an expired canonical, preventing a partial-index conflict that could stop cleanup.
- The Compose worker has no published port, receives a secret distinct from the backend JWT key, mounts objects read-only, uses an internal network, drops capabilities, enables `no-new-privileges`, uses a read-only root filesystem and bounded tmpfs, and sets 512 MiB memory, one CPU, and 64 PID limits.

## Verification performed

The review covered hostile image decoding, decompression bombs, subprocess command injection, executable selection, temporary files, traversal, CPU/memory/output limits, timeouts, malformed provider output, parser regexes, SQL/JSON handling, authentication, cache policy, logs, review mutation, audit preservation, duplicate races, stale worker leases, retention, and external-provider privacy.

Security-focused tests exercise corrupt/oversized inputs, Tesseract timeout and language failures, provider-result validation, no-text logging, unauthorized APIs, correction validation, immutable machine results, audit history, cross-device duplicate isolation, concurrent same-reference processing, stale result rejection, and multi-duplicate retention promotion on real PostgreSQL.

## Residual risks and operating requirements

- Pillow and Tesseract are native-code parsers for attacker-controlled images. Compose limits blast radius but is not a strong per-job sandbox. Production should run disposable jobs under a hardened seccomp/AppArmor or equivalent profile.
- The supplied worker shares the broad application database role and object volume. Production should create a narrow worker role and an object-read broker or per-object capability.
- The internal Compose network prevents outbound internet access through that network, but host/runtime policy remains responsible for proving egress denial.
- The filesystem object adapter is intended for local or single-node operation. Production object storage, encryption policy, backups, lifecycle enforcement, and access logging remain future deployment work.
- Tesseract accuracy was measured only on four English synthetic variations. Arabic packs were present, but Arabic accuracy and mixed-layout robustness remain unverified.
- Raw pixel rotation without EXIF orientation is not corrected in Sprint 5 and is rejected in the measured fixture. Future rotation detection must stay bounded and deterministic.
- A reviewer can make a wrong decision. Sprint 5 records identity and before/after values but has no four-eyes approval, tenant roles, or tamper-evident external audit sink.
- No bank API or signed transaction source establishes authenticity. Review decisions must not be interpreted as payment reconciliation.
- Dependency and container vulnerability scanning was not performed because this workspace has no installed Docker engine or hosted immutable diff.

No external OCR, AI, or LLM adapter is enabled. Adding one later requires explicit opt-in, secret-safe configuration, egress control, provider audit, privacy review, and the same validation/review path.
