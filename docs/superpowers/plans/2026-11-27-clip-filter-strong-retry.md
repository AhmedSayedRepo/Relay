# CLIP Image Filter and Stronger Second Reading — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep non-receipt photos out of Review with a zero-shot CLIP verdict, and read blurry receipts better in the second reading (PP-OCRv5 server engine, unclear recipient accepted).

**Architecture:** A new `image_filter` module scores each photo once in the reader container (`clip_pass`) and stores `machine_result["clip_v1"]`; `hide_non_receipts`, `rescue_hidden` and a new `rehide_failed` read that verdict. In the PaddleOCR container, `try_variants` also reads each variant with a lazily loaded PP-OCRv5 server engine and records every try.

**Tech Stack:** Python 3.12, SQLAlchemy async, transformers CLIP (CPU, torch already in `Dockerfile.reader`), RapidOCR 3.10 (`ch_PP-OCRv5_det_server`, `ch_PP-OCRv5_rec_server`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-clip-filter-strong-retry-design.md`

## Global Constraints

- No training; free, local, CPU only; no Qwen.
- CLIP model `openai/clip-vit-base-patch32`, revision `3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268`, in `/opt/clip`, offline.
- Verdict thresholds: `receipt >= 0.6` → "receipt"; `receipt <= 0.15` → "not_receipt"; else "unsure".
- Model missing → "unsure" (today's behaviour). Only machine-only rows are ever hidden.
- Amounts must match exactly; no fuzzy matching or character correction.
- Recipient on the second reading: "match" or "unclear" passes, "different" fails.
- Images and model weights never committed.

## Review Focus

- A photo already decided by a person (kept, reviewed fields) is never hidden by CLIP → covered in Task 2 test `test_a_person_s_photo_is_never_hidden_by_clip`.
- CLIP crashes on one image (corrupt file) → that row gets verdict "unsure" with an `error`, the pass continues → Task 1 test `test_a_bad_image_is_unsure`.
- A receipt CLIP calls "receipt" but whose text looks like a shop bill → not hidden → Task 2 test `test_a_receipt_verdict_is_never_hidden`.
- Server engine fails to load in the PaddleOCR container → retries continue with mobile engines only → Task 3 test `test_retries_go_on_without_the_server_engine`.
- Rows #158 rescued that CLIP calls "receipt" and failed stay in Review (not re-hidden) → Task 2 test `test_only_not_receipt_failures_are_hidden_again`.

---

### Task 1: `image_filter` module and `clip_pass`

**Files:**
- Create: `src/relay/infrastructure/image_filter.py`
- Modify: `src/relay/infrastructure/cli.py` (reader loop: `await clip_pass(database, storage, 10)` before `hide_non_receipts`)
- Modify: `Dockerfile.reader` (snapshot_download of CLIP into `/opt/clip`, `allow_patterns=['*.json','*.bin','*.txt']`; `ENV RELAY_CLIP_DIR=/opt/clip`)
- Test: `tests/test_image_filter.py`

**Interfaces:**
- Produces:
  - `KEY = "clip_v1"`
  - `RECEIPT_PROMPTS: tuple[str, ...]`, `OTHER_PROMPTS: tuple[str, ...]` (spec lists)
  - `verdict(receipt: float) -> str` → "receipt" | "not_receipt" | "unsure"
  - `classify(picture: Any) -> dict[str, Any]` → `{"label": str, "receipt": float, "scores": dict[str, float]}`; raises `ModelMissing` when `RELAY_CLIP_DIR` has no model
  - `clip_verdict(machine: Any) -> str` → stored verdict or "unsure"
  - `async clip_pass(database, storage, limit: int, classifier: Callable[[Any], dict[str, Any]] | None = None) -> int` — rows with `hidden_reason` null or in (`not_a_receipt`,`no_transfer`), status NEEDS_REVIEW/REJECTED, no `clip_v1` yet, newest first; stores `classify(...) | {"verdict", "at"}`; on `ModelMissing` stops quietly (returns 0, stores nothing); on other errors stores `{"verdict": "unsure", "error": ...}`.

- [ ] **Step 1: Failing tests** — `test_verdict_thresholds` (`verdict(0.6)=="receipt"`, `verdict(0.59)=="unsure"`, `verdict(0.15)=="not_receipt"`); `test_clip_pass_scores_each_photo_once` (fake classifier returning `receipt=0.9`; second pass returns 0; stored verdict "receipt"); `test_a_bad_image_is_unsure` (classifier raises `ValueError` → verdict "unsure", `error` set); `test_no_model_changes_nothing` (classifier raises `ModelMissing` → returns 0, no `clip_v1`).
- [ ] **Step 2: Implement** — `classify` loads `CLIPModel`/`CLIPProcessor` from `RELAY_CLIP_DIR` once (`lru_cache`), softmax over all prompts, `receipt` = sum over receipt prompts, `label` = best prompt.
- [ ] **Step 3: Lint (black, flake8) and commit.**

### Task 2: Use the verdict when hiding and rescuing

**Files:**
- Modify: `src/relay/infrastructure/cli.py` (`hide_non_receipts`, `rescue_hidden`, new `rehide_failed`, loop)
- Test: `tests/test_image_filter.py`

**Interfaces:**
- Consumes: `clip_verdict(machine) -> str`, `KEY` (Task 1).
- Produces: `async rehide_failed(database: Database) -> int` — rows with `retry_read.state == "failed"` and verdict "not_receipt", machine-only → `hidden_reason = retry_read["hidden_as"]`; called in the loop after `rescue_hidden`.

- [ ] **Step 1: Failing tests** — `test_not_receipt_is_hidden_even_with_numbers` (row with receipt-like text + verdict not_receipt → `hidden_reason=="not_a_receipt"`); `test_a_receipt_verdict_is_never_hidden` (text that `_shop_invoice` would hide + verdict receipt → not hidden); `test_a_person_s_photo_is_never_hidden_by_clip` (reviewed_result set + not_receipt → not hidden); `test_rescue_skips_not_receipt` (hidden row, unclaimed SMS, verdict not_receipt → `rescue_hidden == 0`); `test_only_not_receipt_failures_are_hidden_again` (two failed retries: not_receipt → hidden as `hidden_as`; receipt → stays in Review).
- [ ] **Step 2: Implement** — in `hide_non_receipts`, before the existing checks: verdict "receipt" → `continue`; verdict "not_receipt" (and not kept/machine-only) → hide `not_a_receipt`. In `rescue_hidden`'s locked block, skip verdict "not_receipt".
- [ ] **Step 3: Lint and commit.**

### Task 3: Stronger second reading

**Files:**
- Modify: `src/relay/infrastructure/ppocr_reader.py` (`server_engine`, `_read_server`, `try_variants`, `proves`, `self_test`)
- Modify: `compose.yaml` (ppocr `mem_limit: 2500m`)
- Test: `tests/test_retry_before_hide.py`

**Interfaces:**
- Produces:
  - `server_engine() -> Any | None` (`lru_cache`; RapidOCR with `Det.ocr_version/Rec.ocr_version = PPOCRV5`, `Det.model_type/Rec.model_type = SERVER`; None when it cannot load)
  - `proves(...)` returns the amount for recipient "match" or "unclear"
  - `try_variants(picture, amounts, owner, read=None, server=None) -> tuple[str | None, dict[str, str], str | None, list[dict[str, str | None]]]` — tries ordered: `server` on the original, then for each existing variant the mobile reader then `server`; each try recorded as `{"rule": "<variant>[+server]", "amount": <parsed or None>, "recipient": <verdict>}`; rule of a server success ends in `+server`.
  - `retry_pass` stores the try list as `retry_read["tries"]` (replacing the name list).
- [ ] **Step 1: Failing tests** — `test_unclear_recipient_passes_different_fails` (text "960 EGP" alone → "960.00"; text with another phone and name → None); `test_the_server_engine_reads_first` (server fake returns the receipt → rule `"original+server"`, one try); `test_retries_go_on_without_the_server_engine` (`server=None`, mobile succeeds on crop → rule `"crop_2x"`); `test_each_try_is_recorded` (all fail → tries list of dicts with keys rule/amount/recipient); update existing tests for the new tries shape; `test_exact_amount_only` ("3,75C EGP" with waiting 3750 → None).
- [ ] **Step 2: Implement**; `self_test` also builds `server_engine()` so the build downloads the server models.
- [ ] **Step 3: Lint and commit.**

### Task 4: Ship

- [ ] Push branch `strong-reader-clip`, open PR (body: what changed, deploy note: images rebuild, then `retry-hidden` not needed — `rehide_failed` cleans the 8 photos by itself after `clip_pass` scores them).
- [ ] Wait for CI `verify` and `android` to pass before reporting; fix and re-push on failure.
