"""Fine-tune Relay's Donut receipt reader on reviewed receipts (Kaggle, one GPU).

Inputs (Kaggle datasets, attached to the notebook):
  relay-training-export/   images/, train-manifest.jsonl, holdout-manifest.jsonl
                           (from: cli export-training --out X --include-holdout)
  relay-donut-start/       adapter/ (Sprint 9c step-2000 LoRA) and tokenizer/

Output: /kaggle/working/relay-model/ and relay-model.zip
  decoder-adapter/, encoder-adapter/, tokenizer/, reader-config.json, metrics.json

What changed from Sprint 10c:
  * Output is read field by field with a lenient parser, so one stray
    character no longer loses the whole receipt (10c counted most cases as
    structure failures because it required byte-exact canonical JSON).
  * The image encoder gets its own small LoRA, so it can adapt to receipts.
  * Input is 1280x960 instead of 2560x1920 (receipts here are 600-1200 px wide).
  * Augmentation imitates WhatsApp copies: downscale, JPEG, crop, aspect.
  * Fields an operator did not verify are written as null and masked from the loss.
  * The holdout is read only once, after training, for the final score.
"""

from __future__ import annotations

import io
import json
import math
import os
import random
import re
import shutil
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import torch
from peft import LoraConfig, PeftModel, get_peft_model
from PIL import Image, ImageEnhance
from transformers import DonutProcessor, VisionEncoderDecoderModel

# ---------------------------------------------------------------- settings
INPUT = Path(os.environ.get("RELAY_INPUT", "/kaggle/input"))
EXPORT = next(INPUT.glob("**/train-manifest.jsonl")).parent
START = next(INPUT.glob("**/adapter/adapter_config.json")).parent.parent
OUT = Path(os.environ.get("RELAY_OUTPUT", "/kaggle/working")) / "relay-model"

BASE = "naver-clova-ix/donut-base"
REVISION = "a959cf33c20e09215873e338299c900f57047c61"
TASK_TOKEN = "<s_relay_receipt>"
FIELDS = (
    "amount",
    "currency",
    "reference",
    "date",
    "time",
    "sender",
    "sender_handle",
    "recipient",
    "recipient_identifier",
    "status_evidence",
)
CRITICAL = ("amount", "currency", "reference", "date", "time")
IMAGE_SIZE = {"height": 1280, "width": 960}
SEED = 20261104
STEPS = int(os.environ.get("RELAY_STEPS", "6000"))
ACCUMULATE = 4
LEARNING_RATE = 1e-4
EVAL_EVERY = 300
VALIDATION_SHARE = 0.15
TRAIN_ENCODER = os.environ.get("RELAY_TRAIN_ENCODER", "1") == "1"
MAX_NEW_TOKENS = 192
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

random.seed(SEED)
torch.manual_seed(SEED)


# ---------------------------------------------------------------- data
def read_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def to_display(name: str, value: str | None) -> str | None:
    """Write a label the way the receipt shows it, so the model copies text
    instead of converting it (run 1 lost most amounts and times to that).
    to_canonical() turns predictions back into Relay's stored format."""
    if value is None:
        return None
    try:
        if name == "amount":
            number = Decimal(value)
            return (
                f"{number:,.0f}" if number == number.to_integral() else f"{number:,.2f}"
            )
        if name == "time":
            return datetime.strptime(value[:8], "%H:%M:%S").strftime("%I:%M %p")
        if name == "date":
            return datetime.strptime(value, "%Y-%m-%d").strftime("%d %b %Y")
    except (InvalidOperation, ValueError):
        return value
    return value


def to_canonical(name: str, text: str | None) -> str | None:
    if text is None:
        return None
    text = text.strip()
    try:
        if name == "amount":
            cleaned = text.replace(",", "").replace("٬", "").replace(" ", "")
            return str(Decimal(cleaned).quantize(Decimal("0.01")))
        if name == "time":
            for pattern in ("%I:%M %p", "%H:%M:%S", "%H:%M"):
                try:
                    return datetime.strptime(text.upper(), pattern).strftime("%H:%M:%S")
                except ValueError:
                    continue
            return text
        if name == "date":
            for pattern in ("%d %b %Y", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    return datetime.strptime(text, pattern).strftime("%Y-%m-%d")
                except ValueError:
                    continue
            return text
    except (InvalidOperation, ValueError):
        return text
    return text


def wire(record: dict) -> tuple[str, list[tuple[int, int]]]:
    """Canonical target text plus character spans of values to leave out of the loss.

    Mirrors Sprint 9c's wire format: compact JSON in FIELDS order, with '<', '>'
    and U+2581 escaped so they cannot collide with tokenizer control spellings.
    """

    def dump(value) -> str:
        text = json.dumps(value, ensure_ascii=False)
        return (
            text.replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("\u2581", "\\u2581")
        )

    parts, masked, cursor = ["{"], [], 1
    for index, name in enumerate(FIELDS):
        label = record["fields"][name]
        verified = (
            label["evidence_state"] == "PRESENT_VERIFIED"
            and label["value_loss_mask"] == 1
        )
        key = ("," if index else "") + dump(name) + ":"
        value = dump(to_display(name, label["target_value"]) if verified else None)
        parts.append(key)
        cursor += len(key)
        if not verified:
            masked.append((cursor, cursor + len(value)))
        parts.append(value)
        cursor += len(value)
    parts.append("}")
    return "".join(parts), masked


def augment(image: Image.Image, rng: random.Random) -> Image.Image:
    width, height = image.size
    # Crop up to 4% from each edge (never into the middle of the receipt).
    box = (
        int(width * rng.uniform(0, 0.04)),
        int(height * rng.uniform(0, 0.04)),
        int(width * (1 - rng.uniform(0, 0.04))),
        int(height * (1 - rng.uniform(0, 0.04))),
    )
    image = image.crop(box)
    # WhatsApp-style downscale with aspect jitter.
    scale = rng.uniform(0.45, 1.0)
    aspect = rng.uniform(0.92, 1.08)
    size = (
        max(64, int(image.width * scale * aspect)),
        max(64, int(image.height * scale)),
    )
    image = image.resize(size, Image.BICUBIC)
    image = ImageEnhance.Brightness(image).enhance(rng.uniform(0.9, 1.1))
    image = ImageEnhance.Contrast(image).enhance(rng.uniform(0.9, 1.1))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=rng.randint(35, 90))
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def pixels(processor, item: dict, rng: random.Random | None = None) -> torch.Tensor:
    with Image.open(EXPORT / item["image_path"]) as raw:
        image = raw.convert("RGB")
    if rng is not None:
        image = augment(image, rng)
    return processor(images=image, return_tensors="pt").pixel_values


def labels(processor, item: dict) -> tuple[torch.Tensor, torch.Tensor]:
    """Decoder inputs see the full text (nulls included); the loss skips masked values."""
    text, masked = wire(item)
    encoded = processor.tokenizer(
        text, add_special_tokens=False, return_offsets_mapping=True
    )
    ids = list(encoded["input_ids"])
    if processor.tokenizer.unk_token_id in ids:
        raise RuntimeError(f"Unknown token in target of {item['sample_id']}")
    target = []
    for token_id, (start, end) in zip(ids, encoded["offset_mapping"], strict=True):
        hidden = any(start < m_end and end > m_start for m_start, m_end in masked)
        target.append(-100 if hidden else token_id)
    target.append(processor.tokenizer.eos_token_id)
    task = processor.tokenizer.convert_tokens_to_ids(TASK_TOKEN)
    inputs = [task] + ids
    return torch.tensor([inputs], dtype=torch.long), torch.tensor(
        [target], dtype=torch.long
    )


# ---------------------------------------------------------------- model
def load_model(
    decoder_adapter: Path, encoder_adapter: Path | None = None, *, train: bool
):
    processor = DonutProcessor.from_pretrained(START / "tokenizer")
    processor.image_processor.size = dict(IMAGE_SIZE)
    original = DonutProcessor.from_pretrained(BASE, revision=REVISION).tokenizer
    model = VisionEncoderDecoderModel.from_pretrained(BASE, revision=REVISION)
    tokenizer = processor.tokenizer
    vocab, base_vocab = tokenizer.get_vocab(), original.get_vocab()
    if any(vocab.get(token) != index for token, index in base_vocab.items()):
        raise RuntimeError("Start tokenizer does not extend the base tokenizer")
    added = sorted(set(vocab.values()) - set(base_vocab.values()))
    model.decoder.resize_token_embeddings(len(tokenizer))
    with (
        torch.no_grad()
    ):  # Same deterministic init as Sprint 9c; the adapter overrides trained rows.
        for embedding in {
            model.decoder.get_input_embeddings(),
            model.decoder.get_output_embeddings(),
        }:
            mean = embedding.weight[: len(original)].mean(dim=0)
            for index in added:
                embedding.weight[index].copy_(mean)
    task = tokenizer.convert_tokens_to_ids(TASK_TOKEN)
    for config in (model.config, model.generation_config):
        config.decoder_start_token_id = task
        config.pad_token_id = tokenizer.pad_token_id
        config.eos_token_id = tokenizer.eos_token_id
    model.config.encoder.image_size = [IMAGE_SIZE["height"], IMAGE_SIZE["width"]]
    model.requires_grad_(False)
    model.decoder = PeftModel.from_pretrained(
        model.decoder, decoder_adapter, is_trainable=train
    )
    if encoder_adapter is not None:
        model.encoder = PeftModel.from_pretrained(
            model.encoder, encoder_adapter, is_trainable=train
        )
    elif train and TRAIN_ENCODER:
        model.encoder = get_peft_model(
            model.encoder,
            LoraConfig(
                r=8, lora_alpha=16, lora_dropout=0.05, target_modules=["query", "value"]
            ),
        )
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Trainable parameters: {trainable:,}")
    return model.to(DEVICE), processor


# ---------------------------------------------------------------- reading
def parse_fields(text: str) -> dict:
    """Pull each critical value out of decoded text, tolerating broken JSON elsewhere."""
    result = {}
    for name in CRITICAL:
        match = re.search('"' + name + r'"\s*:\s*(null|"(?:[^"\\]|\\.)*")', text)
        value = None
        if match:
            try:
                value = json.loads(match.group(1))
            except ValueError:
                value = None
        result[name] = value if isinstance(value, str) else None
    return result


@torch.inference_mode()
def read(model, processor, image_pixels: torch.Tensor) -> dict:
    tokenizer = processor.tokenizer
    task = tokenizer.convert_tokens_to_ids(TASK_TOKEN)
    with torch.autocast(DEVICE, dtype=torch.float16, enabled=DEVICE == "cuda"):
        output = model.generate(
            image_pixels.to(DEVICE),
            decoder_input_ids=torch.tensor([[task]], device=DEVICE),
            max_new_tokens=MAX_NEW_TOKENS,
            num_beams=1,
            do_sample=False,
        )
    text = tokenizer.decode(
        output[0], skip_special_tokens=True, clean_up_tokenization_spaces=False
    )
    return {
        name: to_canonical(name, value) for name, value in parse_fields(text).items()
    }


def score(model, processor, items: list[dict]) -> dict:
    model.eval()
    totals = {name: [0, 0] for name in (*CRITICAL, "full_record")}
    rows = []
    for item in items:
        predicted = read(model, processor, pixels(processor, item))
        full = True
        for name in CRITICAL:
            label = item["fields"][name]
            if label["evidence_state"] != "PRESENT_VERIFIED":
                continue
            hit = predicted.get(name) == label["target_value"]
            totals[name][0] += hit
            totals[name][1] += 1
            full = full and hit
        totals["full_record"][0] += full
        totals["full_record"][1] += 1
        rows.append({"sample_id": item["sample_id"], "predicted": predicted})
    model.train()
    accuracy = {k: round(100 * c / t, 1) if t else None for k, (c, t) in totals.items()}
    return {"accuracy_percent": accuracy, "counts": totals, "predictions": rows}


# ---------------------------------------------------------------- training
def split_validation(items: list[dict]) -> tuple[list[dict], list[dict]]:
    groups = sorted({item["transaction_group_id"] for item in items})
    random.Random(SEED).shuffle(groups)
    held = set(groups[: max(1, math.ceil(len(groups) * VALIDATION_SHARE))])
    return (
        [i for i in items if i["transaction_group_id"] not in held],
        [i for i in items if i["transaction_group_id"] in held],
    )


def save(model, processor, directory: Path) -> None:
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    model.decoder.save_pretrained(directory / "decoder-adapter")
    if TRAIN_ENCODER:
        model.encoder.save_pretrained(directory / "encoder-adapter")
    processor.save_pretrained(directory / "tokenizer")
    (directory / "reader-config.json").write_text(
        json.dumps(
            {
                "base_model": BASE,
                "base_revision": REVISION,
                "task_token": TASK_TOKEN,
                "fields": FIELDS,
                "critical_fields": CRITICAL,
                "image_size": IMAGE_SIZE,
                "encoder_adapter": TRAIN_ENCODER,
                "decoding": "greedy_192_lenient_fields_v1",
                "target_format": "display_v1",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    train_all = read_manifest(EXPORT / "train-manifest.jsonl")
    holdout = read_manifest(EXPORT / "holdout-manifest.jsonl")
    if len(train_all) < 10:
        raise SystemExit(f"Only {len(train_all)} training receipts; review more first.")
    train, validation = split_validation(train_all)
    print(
        f"train={len(train)} validation={len(validation)} holdout={len(holdout)} device={DEVICE}"
    )
    model, processor = load_model(START / "adapter", train=True)
    # Fail fast: every target must tokenize, and one read must run, before hours of training.
    prepared = [(item, labels(processor, item)) for item in train]
    print("Smoke read:", read(model, processor, pixels(processor, validation[0])))

    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=LEARNING_RATE
    )
    schedule = torch.optim.lr_scheduler.LambdaLR(
        optimizer,
        lambda step: min(1.0, (step + 1) / 100)
        * max(0.05, 1 - step / (STEPS / ACCUMULATE)),
    )
    scaler = torch.amp.GradScaler(enabled=DEVICE == "cuda")
    rng = random.Random(SEED)
    best, best_key, history, started = None, (-1.0, 0.0), [], time.monotonic()
    model.train()
    for step in range(1, STEPS + 1):
        item, (inputs, target) = rng.choice(prepared)
        with torch.autocast(DEVICE, dtype=torch.float16, enabled=DEVICE == "cuda"):
            loss = (
                model(
                    pixel_values=pixels(processor, item, rng).to(DEVICE),
                    decoder_input_ids=inputs.to(DEVICE),
                    labels=target.to(DEVICE),
                ).loss
                / ACCUMULATE
            )
        scaler.scale(loss).backward()
        if step % ACCUMULATE == 0:
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(
                [p for p in model.parameters() if p.requires_grad], 1.0
            )
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad(set_to_none=True)
            schedule.step()
        history.append(float(loss) * ACCUMULATE)
        if step % 50 == 0:
            recent = sum(history[-50:]) / 50
            print(
                f"step {step}/{STEPS} loss {recent:.4f} {(time.monotonic() - started) / 60:.1f} min"
            )
        if step % EVAL_EVERY == 0 or step == STEPS:
            result = score(model, processor, validation)
            accuracy = result["accuracy_percent"]
            print(f"validation at {step}: {accuracy}")
            key = (accuracy["full_record"] or 0.0, -sum(history[-EVAL_EVERY:]))
            if key > best_key:
                best_key, best = key, {"step": step, "validation": result}
                save(model, processor, OUT)
    print(
        f"Best validation at step {best['step']}: {best['validation']['accuracy_percent']}"
    )

    # Final score: reload the best saved adapters and read the holdout once.
    del model, optimizer
    torch.cuda.empty_cache()
    final_model, final_processor = load_model(
        OUT / "decoder-adapter",
        OUT / "encoder-adapter" if TRAIN_ENCODER else None,
        train=False,
    )
    holdout_result = score(final_model, final_processor, holdout) if holdout else None
    metrics = {
        "train_receipts": len(train),
        "validation_receipts": len(validation),
        "holdout_receipts": len(holdout),
        "steps": STEPS,
        "best_step": best["step"],
        "validation": best["validation"]["accuracy_percent"],
        "holdout": holdout_result["accuracy_percent"] if holdout_result else None,
        "sprint_10c_full_record_percent": 14.3,
        "minutes": round((time.monotonic() - started) / 60, 1),
    }
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    if holdout_result:  # Predictions only; no images or names leave the notebook.
        (OUT / "holdout-predictions.json").write_text(
            json.dumps(holdout_result["predictions"], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    shutil.make_archive(str(OUT), "zip", OUT)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
