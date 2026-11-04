# Training the receipt reader

Fine-tunes the Sprint 9c Donut model (2000-step LoRA) on receipts you reviewed in
Relay. Runs free on a Kaggle GPU. No receipt images are stored in this repository.

| File | Purpose |
|---|---|
| `relay-training.ipynb` | Kaggle notebook (wraps the script below) |
| `relay_donut_train.py` | Training, validation and the one-time holdout score |
| `../deploy/export-training.sh` | Server: capture reviewed receipts and zip them |

## Steps

1. Review receipts in the portal with **Model training data** on for the device.
2. Server: `sudo ./deploy/export-training.sh` → `~/relay-training-export.zip`.
3. Kaggle → **Datasets → New dataset** (keep **Private**):
   - `relay-training-export` from that zip
   - `relay-donut-start` from `relay-donut-start.zip` (adapter + tokenizer)
4. Kaggle → **Code → New notebook → File → Import notebook** → `relay-training.ipynb`.
   Attach both datasets, set **Accelerator: GPU** and **Internet: On**, **Run all**.
5. Download `relay-model.zip` from the Output tab.

`metrics.json` reports per-field and full-receipt accuracy on validation and on
the holdout (Sprint 10c full-receipt accuracy was 14.3%).

Environment overrides: `RELAY_STEPS` (default 3000), `RELAY_TRAIN_ENCODER=0`
if the GPU runs out of memory.
