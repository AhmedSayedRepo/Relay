# Relay Sprint 7 proposal — UI/UX refinement only

Sprint 7 should refine the operator experience after real Sprint 6 operational testing. It must preserve the Sprint 6 API contracts, deterministic duplicate identity, audit semantics, protected evidence access, review decisions, reporting filters, and Help topic IDs.

Proposed scope:

1. Establish a Relay-specific visual identity and a restrained light/dark color system with verified contrast.
2. Refine dashboard composition and information hierarchy using operational evidence from real workflows.
3. Design transaction status cards and a receipt journey/timeline that expose existing states without inventing new business meaning.
4. Improve duplicate investigation layout, canonical/member comparison, provenance scanning, and operator-decision feedback.
5. Improve review workflow ergonomics, validation, keyboard flow, focus management, empty/error states, and recovery guidance.
6. Add meaningful, reduced-motion-aware micro-interactions and verify responsive behavior across supported viewports.
7. Complete an accessibility audit and usability testing with representative operators, then update Help wording and navigation from observed problems.
8. Expand locale catalogs and verify right-to-left readiness without changing stable topic IDs or backend values.

Deliverables should include tested interaction specifications, component updates, accessibility evidence, responsive screenshots, usability findings, and a migration-free rollout plan. Sprint 7 should not add AI or change receipt authenticity semantics.

Any later proposal for image manipulation analysis must be a separate, explicitly approved sprint after real application testing. Such output could only be advisory evidence. Image analysis cannot prove that a bank transaction occurred; authoritative financial-system evidence would be required for that claim.
