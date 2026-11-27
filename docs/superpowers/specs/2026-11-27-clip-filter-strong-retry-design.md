# Image filter (CLIP) and stronger second reading — design

Approved by Ahmed, 2026-11-27.

## Why

The 2026-11-26 reconciliation showed two problems:

- Photos that are not receipts (a keyboard, a Windows desktop, shop bills,
  food) reach Review. #158 made it worse: any unclaimed bank SMS that day
  brought every hidden photo of the day back to Review (8 of 11).
- Blurry receipts stay unread: the 960 receipt read "960 EGP" but showed no
  recipient; a 3,750 receipt read "3,75C" (stays a Review case: no fuzzy
  amounts).

## Goals

- Keep non-receipt photos out of Review even when they contain numbers.
- Read blurry receipts that a bank SMS can prove.

## Non-goals / anti-goals

- No training of any model (Ahmed: no more training cycles).
- Free, local, CPU only. No Qwen, no Google Document AI, no paid service.
- Never hide a photo silently when unsure (the 815 lesson): unsure behaves
  as today.
- Images and model weights never go to GitHub.

## Approaches considered

1. **CLIP zero-shot image classifier + PP-OCRv5 server in the second
   reading (picked).** CLIP judges the picture itself, so blur does not
   fool it; it needs only text prompts, no training. Server OCR only on the
   few photos read again, so normal reading speed stays.
2. Train a small receipt/non-receipt classifier on Relay's own data.
   Rejected by Ahmed (no training).
3. Text-only rules (keywords, number patterns). Already in place; fails on
   blurry photos and on screenshots with numbers.

## Design

### 1. CLIP filter (reader container)

- Model: `openai/clip-vit-base-patch32` (pinned revision
  `3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268`), downloaded at image build into
  `/opt/clip`, run offline with the torch/transformers already in
  `Dockerfile.reader`.
- Each photo is scored once against fixed prompts; result stored in
  `machine_result["clip_v2"]` (v1 before the screenshot descriptions): `{"label", "receipt", "scores", "at"}`, where
  `receipt` is the summed probability of the receipt prompts.
- Receipt prompts: a screenshot of a mobile money transfer receipt; a photo
  of a phone screen showing a payment confirmation; a bank transfer
  confirmation screen. Non-receipt prompts: a computer screen or desktop; a
  keyboard; a photo of food; a printed shop or supermarket bill; a chat
  conversation screenshot; a photo of people or a room; a paper document;
  a screenshot of a computer desktop with a taskbar; a screenshot of a phone
  keyboard; a screenshot of a spreadsheet or computer program (added after
  the first run: screenshots of a keyboard and a Windows desktop came out
  "unsure").
- Verdict: `receipt >= 0.6` → "receipt"; `receipt <= 0.15` → "not_receipt";
  else "unsure". Thresholds are constants, logged with each score.
- Use:
  - **not_receipt** (machine-only rows): hidden as `not_a_receipt`; never
    rescued by #158, even with an unclaimed SMS.
  - **receipt**: never hidden by `hide_non_receipts`; when unreadable it goes
    to the second reading (if an SMS waits) and then Review.
  - **unsure**: today's rules.
- One-off: rows #158 brought back and that failed (`retry_read.state ==
  "failed"`) with a `not_receipt` verdict are hidden again (`hidden_as`).
- When the model is missing (dev, tests) the filter returns "unsure".

### 2. Stronger second reading (PaddleOCR container)

- A third engine, PP-OCRv5 **server** detection + recognition (Chinese/English,
  digits), loaded lazily and only used by `retry_pass`; downloaded at build
  by the self test.
- Variant order: the original image with the server engine first, then the
  existing variants (rotations, crop ×2, enhance, enhanced crop), each read
  by the mobile engines and the server engine.
- Amount: exact only. No fuzzy matching or character correction ("3,75C"
  stays unread) — Ahmed, 2026-11-27.
- Recipient: accept "match" or "unclear"; only "different" rejects. The
  normal auto-confirm rule for an unclear recipient (confirm only when no
  other receipt of that day has the amount) still applies.
- Each try's result (rule, amount found, recipient verdict) is stored in
  `retry_read.tries` for diagnosis.

## Constraints

- Reader container: 6 GB, 2 CPUs; CLIP adds ~600 MB RAM, ~1 s per photo on
  Ampere A1. PaddleOCR container memory may need raising for the server
  engine (builder checks `compose.yaml`).

## Left to the builder

- Exact prompt wording within the lists above, file layout, test fakes.
