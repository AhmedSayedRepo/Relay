# SMS verification (two phones)

Receipts are collected on one phone (A). The bank SMS for the incoming InstaPay
transfer arrives on another phone (B). Relay marks a receipt as verified only
when a bank SMS from B matches it.

## What is compared

InstaPay credit SMS share only the **amount** and the **date/time** with the
receipt, so those are the only match keys:

- Amount and currency must be exactly equal (operator-reviewed receipt values).
- The SMS time must fall between 2 minutes before and 10 minutes after the
  receipt minute (`SMS_MATCH_BEFORE_MINUTES`, `SMS_MATCH_AFTER_MINUTES`).
  Receipt times are read in `RECEIPT_TIMEZONE` (default `Africa/Cairo`).
- One SMS can verify only one receipt, so one real payment cannot vouch for
  several edited receipts.

## Statuses

| Status | Meaning |
| --- | --- |
| SMS verified | Exactly one SMS fits, and it fits no other receipt. |
| Choose SMS | Several receipts or SMS fit (for example two equal payments in the same minutes). The operator picks. |
| Waiting for SMS | Nothing matched yet; checked again for 24 hours. |
| No matching SMS | Nothing matched within 24 hours. Treat as unverified. |
| Not set up / Review first / Not checked | Device not linked, receipt values not reviewed yet, or not checked since review. |

An operator pick is kept by later automatic runs, unless the receipt's amount
or currency is corrected afterwards or the phones are unlinked.

## Setup

1. Enrol phone B and enable SMS collection with a sender rule for the bank.
2. On **Devices**, open phone A's card and tick phone B under *SMS verification*.
3. Schedule `python -m relay.infrastructure.cli verify-sms` (for example every
   5 minutes) next to `process-pending-images`, or use *Re-check now* on a
   receipt.

## Limits

- Amount and time are weak keys on their own: equal payments close together
  need an operator decision.
- The SMS parser is strict and provisional until real bank SMS samples are
  added. An SMS it cannot read never verifies anything.
- Raw SMS expire with event retention (`RAW_EVENT_RETENTION_DAYS`); the matched
  SMS facts are kept with the verification.
