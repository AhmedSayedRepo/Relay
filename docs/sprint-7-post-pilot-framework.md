# Relay post-pilot decision framework

Do not start a new feature sprint when the pilot ends. First freeze the evidence set, reconcile results, and classify every finding with both a severity and a domain.

## Severity

| Severity | Meaning | Gate |
|---|---|---|
| BLOCKER | Security/privacy breach, data loss, audit/provenance corruption, uncontrolled duplicate identity, or core workflow cannot complete | Stop pilot; no rollout or next feature work |
| HIGH | Materially wrong financial/reference data, repeated collection/processing failure, inaccessible critical workflow, or operator cannot safely recover | Mitigation and retest required before expansion |
| MEDIUM | Important task is slower/confusing or has a bounded incorrect state with a safe workaround | Prioritize from frequency and operational cost |
| LOW | Minor wording, polish, or uncommon non-blocking defect | Backlog with evidence |

## Domain

Assign one or more: `UX`, `OCR/PARSER`, `DUPLICATE`, `ANDROID/COLLECTION`, `OPERATIONS`. Keep observed facts separate from hypotheses, attach sample IDs, and avoid copying unnecessary personal data.

## Decision options

| Option | Select when evidence shows |
|---|---|
| A. Pilot hardening sprint | Blocking/high issues span existing workflows and need focused reliability or usability fixes before another pilot. |
| B. Production-readiness sprint | Pilot criteria pass and remaining work is deployment, monitoring, backup/restore, session/authentication, operations, privacy, and support readiness. |
| C. OCR/parser improvement | Material extraction/classification errors dominate and have representative, authorized samples with expected values. |
| D. Additional receipt/provider support | Current supported flows work, while measured demand is concentrated in a specific unsupported provider/format. |
| E. AI image-manipulation research sprint | Only if pilot evidence establishes a specific, valuable advisory use case, representative data, measurable evaluation, privacy/security boundaries, cost limits, and owner approval. It must remain advisory and cannot prove a transaction occurred. |

## Decision meeting inputs

- Completed pilot checklist and all sixteen criteria with denominators.
- Sample-by-sample expected versus observed results.
- Collection/upload/processing latency observations.
- Duplicate true-positive and false-merge observations.
- Review corrections and operator intervention log.
- Dashboard and Excel reconciliation.
- Arabic/RTL and accessibility operator feedback.
- Security, privacy, retention, operational, and deployment gaps.
- Ranked findings with owners and retest conditions.

Record the selected option, rejected options and reasons, risk acceptance, owner, target outcome, and approval date. If evidence is insufficient, run a narrower controlled pilot rather than choosing a feature direction by assumption.
