# Readers

**Classic PaddleOCR is the first reader** (`ppocr` container, all day, a few
seconds per receipt). Its amount, reference, date, time and recipient lead;
Tesseract (the text reader) fills only what PaddleOCR could not read, and
decides alone when PaddleOCR is down: auto-confirm waits
`RELAY_PPOCR_WAIT_MINUTES` (default 10) for PaddleOCR on a new receipt.
PaddleOCR also brings back to Review real receipts Tesseract rejected.
Nothing is confirmed without a bank SMS of the exact amount (or a person).
`eval-reader` scores what is in use (PaddleOCR first) and gates deploys on it.

The night model reader below is off unless `RELAY_NIGHT_MODEL=1`.

A small vision-language model reads each receipt image at night (11 PM to
8 AM, Cairo) on this server. It does not confirm anything: `eval-reader`
compares it with the current reader on confirmed receipts.

## Which model

| Model | `.env` settings |
|---|---|
| PaddleOCR-VL (default) | nothing to set |
| GLM-OCR | `OCR_MODEL=ggml-org/GLM-OCR-GGUF`, `RELAY_VL_VERSION=vl_glm_v1`, `RELAY_VL_PROMPT=Text Recognition:` |

Each model's readings are stored under its own key, so both can be compared
side by side.

## Switch to GLM-OCR

```
ssh -i ~/relay.key ubuntu@84.235.254.121
cd ~/Relay && printf '%s\n' 'OCR_MODEL=ggml-org/GLM-OCR-GGUF' 'RELAY_VL_VERSION=vl_glm_v1' 'RELAY_VL_PROMPT=Text Recognition:' | sudo tee -a .env
cd ~/Relay && sudo git pull && sudo ./deploy/update.sh
```

The next night it reads the confirmed receipts again. In the morning:

```
cd ~/Relay && sudo ./deploy/dc.sh run --rm backend python -m relay.infrastructure.cli eval-reader
```

## Back to PaddleOCR-VL

Remove those three lines from `.env`, then run `update.sh` again.
