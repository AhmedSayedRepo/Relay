# Relay controlled pilot checklist

Record owner, timestamp, environment, device identifier, build/package hash, and pass/fail evidence for every checked item. A blank box means unverified. Stop for any security, privacy, data-loss, or audit-provenance problem.

## Safety and authorization

- [ ] Pilot owner and limited participant list approved.
- [ ] Only approved devices and authorized receipt/SMS data will be used.
- [ ] Service is private or protected by appropriate production controls; it is not casually exposed to the public internet.
- [ ] Data retention, deletion, backups, and incident contacts are understood.
- [ ] Participants understand that Relay extracts and organizes evidence; it does not prove payment or receipt authenticity.

## Backend

- [ ] PostgreSQL is available and backup/restore responsibility is assigned.
- [ ] `alembic current` shows migration `0006` at head and `alembic check` reports no pending operations.
- [ ] The operator account was created through the approved process; credentials are not shared in evidence notes.
- [ ] Object storage is configured, private, and readable only through the protected path.
- [ ] Worker execution and retry ownership are configured and monitored.
- [ ] Retention behavior and duplicate-provenance preservation are understood.
- [ ] Health/readiness and worker logs can be inspected by the pilot owner.

## Android

- [ ] The approved APK hash matches the recorded pilot build.
- [ ] APK is installed on each approved pilot device.
- [ ] Relay enrollment completed and the device is ACTIVE.
- [ ] If SMS is in scope, consent and SMS permission were verified on-device.
- [ ] If image collection is in scope, the approved SAF image source was selected.
- [ ] Configuration synchronized and the device last-seen state is current.
- [ ] A collection stop/restart and offline/reconnect expectation was explained to the operator.

## Operator UI

- [ ] Login works and refreshing the browser correctly requires re-authentication.
- [ ] English navigation, data, states, dialogs, and Help work.
- [ ] Arabic navigation, data, states, dialogs, and Help work.
- [ ] Arabic uses true RTL and mixed identifiers remain in logical order.
- [ ] Dashboard totals and attention queues load.
- [ ] Protected receipt image access works for an authorized operator.
- [ ] Review accept/correct/reject workflow works and audit history remains visible.
- [ ] Duplicate group and side-by-side evidence comparison work.
- [ ] Excel export opens and contains the selected records.
- [ ] Contextual Help and Arabic Help search work.
- [ ] A keyboard-only operator path succeeds for login, navigation, Help, transaction inspection, and review.

## Test data

- [ ] One normal authorized receipt.
- [ ] One distinct second transaction.
- [ ] Two different screenshots carrying the same authorized reference/serial.
- [ ] One receipt likely to require review, if safe and practical.
- [ ] One SMS sample when SMS collection is explicitly in scope.
- [ ] Expected source values are recorded separately for reference, amount, date/time, sender, recipient, and currency.

## Observation record

- [ ] Record collection, upload, and processing timestamps/latencies.
- [ ] Record OCR text and classification against the expected sample.
- [ ] Record reference, amount, date/time, sender, and recipient extraction outcomes.
- [ ] Record duplicate true-positive and false-merge behavior.
- [ ] Record review corrections, operator steps, confusion, and recovery.
- [ ] Reconcile dashboard totals and Excel output against reviewed transactions.
- [ ] Preserve relevant request IDs, transaction IDs, device IDs, and audit evidence without copying secrets or unnecessary personal data.

## Sixteen pilot success criteria

For each criterion record `pass`, `fail`, or `not applicable`, the denominator/sample IDs, and evidence. Do not insert percentages until real observations exist.

1. Every in-scope image sample was collected once by the approved device, or a documented failure explains why it was not.
2. Every collected sample produced one logical upload; retries remained idempotent and did not create unintended independent evidence records.
3. Processing reached a terminal state for every sample within the pilot observation window; failures remained visible and actionable.
4. Receipt classification matched the independently recorded expected class for each evaluable sample.
5. Extracted amount and currency matched the source receipt after normalization, or the item was correctly routed for review.
6. Extracted reference/serial matched the visible source value in exact logical order, or the item was correctly routed for review.
7. Date/time fields matched the source where present and were absent rather than fabricated where missing.
8. Sender/recipient fields were correct and useful where present, and missing fields were not fabricated.
9. The two distinct images with the same reference appeared in the expected deterministic duplicate group with provenance retained.
10. Samples with different references were not falsely merged into the same deterministic duplicate group.
11. An operator could inspect evidence and complete accept, correct, or reject decisions with an auditable result.
12. Dashboard counts and currency totals reconciled exactly to the agreed set of reviewed transactions.
13. Excel rows, values, filters, and totals reconciled exactly to the same dashboard/transaction dataset.
14. A representative Arabic-speaking operator judged every task-critical Arabic instruction and label understandable, with issues recorded verbatim.
15. Arabic pages rendered RTL without horizontal overflow and kept references, amounts, timestamps, IDs, and telephone-like values readable and copyable.
16. A representative operator completed the end-to-end assigned workflow without developer intervention; any intervention and its reason were recorded.

## Exit gate

- [ ] No BLOCKER remains open.
- [ ] Every HIGH issue has an accepted mitigation or the pilot is stopped.
- [ ] Findings are classified by severity and domain.
- [ ] Pilot owner signs the observation record.
- [ ] A post-pilot decision is made explicitly; no next sprint begins automatically.
