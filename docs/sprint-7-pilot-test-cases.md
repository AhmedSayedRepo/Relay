# Relay controlled pilot test cases

Use authorized synthetic or real-world pilot data only. Record the exact sample ID, expected values established before ingestion, timestamps, screenshots/log references, outcome, operator comments, and any intervention.

| ID | Scenario | Procedure | Expected observation |
|---|---|---|---|
| P01 | Normal image receipt | Place one approved image in the selected SAF source and wait through processing. | One collection, one logical upload, terminal processing, evidence and provenance visible. |
| P02 | Distinct transaction | Collect a second receipt with a different reference. | Separate transaction and no false duplicate merge. |
| P03 | Same reference, different images | Collect two different screenshots with the same reference/serial. | One deterministic duplicate group, canonical/member relationship and both evidence records retained. |
| P04 | Review correction | Use a safe sample with one uncertain or intentionally hard-to-read field. | Needs-review state appears when produced by existing rules; operator can correct explicitly and audit history records the decision. |
| P05 | Review rejection | Reject an unsuitable candidate with a reason. | Rejection is explicit, durable, and visible in history without deleting evidence. |
| P06 | Processing failure/retry | In a controlled environment, create an approved recoverable failure or use an existing failed sample. | Safe reason, retry availability, Help link, and terminal outcome are understandable; no stack trace or receipt content leaks. |
| P07 | Dashboard reconciliation | Freeze the sample set and manually calculate counts/totals by currency. | Dashboard matches the reviewed dataset exactly. |
| P08 | Excel reconciliation | Export the same filtered dataset. | Rows, references, reviewed values, currencies, and totals match UI data exactly. |
| P09 | English operator workflow | Complete login, dashboard, transaction, duplicate, review, processing, export, and Help tasks in English. | All task-critical Relay text is English and the workflow completes without developer intervention. |
| P10 | Arabic operator workflow | Repeat the task path with a representative Arabic-speaking operator. | Task-critical text is understandable Arabic, document is RTL, and no essential content is clipped or reordered. |
| P11 | Mixed bidi data | Use Arabic names with a Latin reference, digits, currency code, timestamp, ID, and telephone-like value. | Every value remains readable and copyable in original logical order in lists, cards, details, dialogs, and exports. |
| P12 | Narrow viewport | Use a 375 px viewport in both locales. | Navigation remains operable, tables adapt to labeled cards, and there is no page-level horizontal overflow. |
| P13 | Keyboard-only | Without a pointer, navigate, switch locale/theme, search Help, inspect a transaction, and complete an available review action. | Logical focus, visible focus, named controls, dialog Escape behavior, and task completion. |
| P14 | Theme/system preference | Select light, dark, and system modes; reload after each selection. | Allowed preference persists, system mode follows OS preference, and token remains absent from storage. |
| P15 | Authentication refresh | Login, refresh, and attempt protected navigation. | In-memory token is cleared and re-authentication is required; locale/theme remain. |
| P16 | API unavailable | Stop or isolate the pilot API during a safe UI read. | Localized service error and recovery guidance appear without raw internals. |
| P17 | Empty/filter state | Apply a filter known to match no records. | Localized explanation, filter-reset action, and contextual Help are available. |
| P18 | Protected image access | Open evidence while authorized, then repeat after session expiry. | Authorized image works; expired session does not expose cached protected evidence through the application. |
| P19 | Optional SMS collection | With explicit consent, send one approved test SMS. | One collected event follows existing rules and is traceable to the approved device; omit when SMS is outside pilot scope. |
| P20 | Offline/reconnect | Temporarily remove device connectivity, collect an approved sample, then reconnect. | Existing durable queue behavior produces one logical upload without duplicate loss or provenance loss. |

Relay output must be assessed as extracted and organized evidence. None of these cases validates payment, receipt authenticity, fraud status, or image integrity.
