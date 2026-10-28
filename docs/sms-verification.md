# SMS verification (two phones)

Receipts are collected on one phone (A). The bank SMS for the incoming InstaPay
transfer arrives on another phone (B). Relay marks a receipt as verified only
when a bank SMS from B matches it.

## What is compared

Bank SMS share only the **amount** and the **date/time** with the receipt (the
SMS reference is a different bank identifier), so those are the only match keys:

- Amount and currency must be exactly equal (operator-reviewed receipt values).
- Direction must match the link setting: the SMS phone receives either
  **money received** (credit SMS) or **money sent** (debit SMS). A debit can never
  verify a receipt on a "money received" link, and the reverse.
- Time: when the SMS states the transfer time (CIB: `on 25-10-2026 at 23:00`),
  it must be within 1 minute of the receipt minute. Otherwise the SMS arrival
  time must fall between 2 minutes before and 10 minutes after
  (`SMS_MATCH_BEFORE_MINUTES`, `SMS_MATCH_AFTER_MINUTES`). Receipt and stated
  times are read in `RECEIPT_TIMEZONE` (default `Africa/Cairo`).
- One SMS can verify only one receipt, so one real payment cannot vouch for
  several edited receipts.

Supported wording: the CIB `charged with IPN Outward with amount EGP … on
DD-MM-YYYY at HH:MM` format (from real samples), its assumed `credited with IPN
Inward` counterpart, and English or Arabic messages with one clear direction word
(e.g. received/credited/إضافة/إيداع or charged/debited/خصم), one amount in EGP or
جنيه, and an optional date and time (Arabic digits and ص/م supported). Debits must
mention InstaPay/IPN; card purchases and cash withdrawals are ignored.

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
2. On **Devices**, open phone A's card, tick phone B under *SMS verification*,
   and choose whether its bank SMS are for money received or money sent.
3. Schedule `python -m relay.infrastructure.cli verify-sms` (for example every
   5 minutes) next to `process-pending-images`, or use *Re-check now* on a
   receipt.

## Limits

- Amount and time are weak keys on their own: equal payments close together
  need an operator decision.
- Only the CIB debit format is confirmed by real samples; credit and Arabic
  wording are strict best guesses until samples are added. An SMS the parser
  cannot read never verifies anything.
- Raw SMS expire with event retention (`RAW_EVENT_RETENTION_DAYS`); the matched
  SMS facts are kept with the verification.
