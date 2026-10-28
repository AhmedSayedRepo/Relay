# Reviewed-receipt training dataset

Relay can turn normal operator review into a training dataset for the field model.
Nothing is captured unless a device is explicitly opted in.

## Rules

| Rule | Behaviour |
|---|---|
| Consent | Per device, **off by default**. `PUT /api/v1/devices/{id}/training-consent {"enabled": true}` |
| What is captured | Only InstaPay receipts where every critical field (reference, amount, currency, date, time) has an operator decision, and at least one was confirmed or corrected |
| Labels | Only operator `CONFIRM` / `CORRECT` values become targets. `UNAVAILABLE` is stored as `UNREADABLE` and excluded from all loss (it does not say whether the field was cropped or illegible). Machine suggestions are never targets |
| Image copy | Content-addressed copy under `TRAINING_DATASET_ROOT`; it survives the normal image/receipt retention purge |
| Retention | Kept until deleted: `DELETE /api/v1/devices/{id}/training-examples` removes the device's rows and any image no longer referenced |
| Holdout | Deterministic by transaction group: ~1 in 5 transactions are `HOLDOUT`. All variants of one transaction share a split |

## Operating it

```bash
python -m relay.infrastructure.cli capture-training --limit 100   # idempotent
python -m relay.infrastructure.cli export-training --out ./dataset-export
python -m relay.infrastructure.cli export-training --out ./final-eval --include-holdout
```

`GET /api/v1/training/summary` reports train/holdout examples, distinct transaction
groups and consenting devices; `POST /api/v1/training/capture` runs a capture batch.

Export writes `images/` plus `train-manifest.jsonl` (and `holdout-manifest.jsonl`
only with `--include-holdout`). Each line uses the field-model record contract
(`relay-field-value-development-v1`): ten fields with `evidence_state`,
`target_value`, `value_loss_mask` and `presence_loss_mask`.

**Do not open the holdout manifest during development or tuning.** It is the
independent real test set required before any production decision.

## Importing existing receipt images

Images already saved on a computer can be loaded without sending them through
the phone. They go through the same records, duplicate check and processing as
phone uploads, and are attributed to an enrolled device (normally phone A):

```
python -m relay.infrastructure.cli import-images --device <device-id> --dir ./receipts
python -m relay.infrastructure.cli process-pending-images --limit 100   # repeat until done
```

- JPEG, PNG and WebP up to 8 MB; other files are skipped and reported.
- Byte-identical files are imported once; re-running the command is safe.
- Imported images follow normal image retention (`IMAGE_OBJECT_RETENTION_DAYS`),
  so review them and run `capture-training` before they expire.
