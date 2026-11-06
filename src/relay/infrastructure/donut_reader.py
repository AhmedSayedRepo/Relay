"""Relay's fine-tuned Donut receipt reader (optional; CPU inference).

Loaded only by the `model-suggest` command in the reader image, which adds
torch, transformers and peft. Its readings are suggestions: they pre-fill
Review and are never trusted on their own.
"""

from __future__ import annotations

import importlib
import io
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# Training names -> Relay field names.
FIELD_NAMES = {
    "amount": "amount",
    "currency": "currency",
    "reference": "reference_number",
    "date": "date",
    "time": "time",
}
REFERENCE = re.compile(r"^(?:\d{12}|FT[0-9A-Z]{10})$")


def parse_fields(text: str, names: tuple[str, ...]) -> dict[str, str | None]:
    """Pull each value out of decoded text, tolerating broken JSON elsewhere."""
    result: dict[str, str | None] = {}
    for name in names:
        match = re.search('"' + name + r'"\s*:\s*(null|"(?:[^"\\]|\\.)*")', text)
        value: Any = None
        if match:
            try:
                value = json.loads(match.group(1))
            except ValueError:
                value = None
        result[name] = value if isinstance(value, str) else None
    return result


def to_canonical(name: str, text: str | None) -> str | None:
    """Receipt display text (as trained) -> Relay's stored format."""
    if text is None:
        return None
    text = text.strip()
    if name == "amount":
        try:
            cleaned = text.replace(",", "").replace("٬", "").replace(" ", "")
            return str(Decimal(cleaned).quantize(Decimal("0.01")))
        except InvalidOperation:
            return None
    patterns = {
        "time": (("%I:%M %p", "%H:%M:%S", "%H:%M"), "%H:%M:%S"),
        "date": (("%d %b %Y", "%Y-%m-%d", "%d/%m/%Y"), "%Y-%m-%d"),
    }
    if name in patterns:
        inputs, output = patterns[name]
        for pattern in inputs:
            try:
                return datetime.strptime(
                    text.upper() if name == "time" else text, pattern
                ).strftime(output)
            except ValueError:
                continue
        return None
    return text


def plausible(name: str, value: str | None, today: date | None = None) -> str | None:
    """Drop readings that cannot be right, e.g. a phone number as reference."""
    if value is None:
        return None
    if name == "reference_number":
        return value if REFERENCE.match(value) else None
    if name == "amount":
        try:
            number = Decimal(value)
        except InvalidOperation:
            return None
        return value if Decimal("0.01") <= number <= Decimal("10000000") else None
    if name == "currency":
        return value if value == "EGP" else None
    if name == "date":
        try:
            day = date.fromisoformat(value)
        except ValueError:
            return None
        today = today or datetime.now(UTC).date()
        return value if date(2020, 1, 1) <= day <= today + timedelta(days=1) else None
    if name == "time":
        return value if re.match(r"^\d{2}:\d{2}:\d{2}$", value) else None
    return value


class DonutReader:
    def __init__(self, model_dir: Path, base_dir: Path, threads: int = 2) -> None:
        self.torch: Any = importlib.import_module("torch")
        transformers: Any = importlib.import_module("transformers")
        peft: Any = importlib.import_module("peft")
        self.torch.set_num_threads(threads)
        self.config = json.loads((model_dir / "reader-config.json").read_text("utf-8"))
        if self.config.get("target_format") != "display_v1":
            raise ValueError("Unsupported reader target format.")
        processor = transformers.DonutProcessor.from_pretrained(model_dir / "tokenizer")
        processor.image_processor.size = dict(self.config["image_size"])
        model = transformers.VisionEncoderDecoderModel.from_pretrained(base_dir)
        tokenizer = processor.tokenizer
        base_vocab = model.decoder.get_input_embeddings().weight.shape[0]
        model.decoder.resize_token_embeddings(len(tokenizer))
        with self.torch.no_grad():
            for embedding in {
                model.decoder.get_input_embeddings(),
                model.decoder.get_output_embeddings(),
            }:
                mean = embedding.weight[:base_vocab].mean(dim=0)
                embedding.weight[base_vocab:] = mean
        size = self.config["image_size"]
        model.config.encoder.image_size = [size["height"], size["width"]]
        self.task = tokenizer.convert_tokens_to_ids(self.config["task_token"])
        for config in (model.config, model.generation_config):
            config.decoder_start_token_id = self.task
            config.pad_token_id = tokenizer.pad_token_id
            config.eos_token_id = tokenizer.eos_token_id
        model.decoder = peft.PeftModel.from_pretrained(
            model.decoder, model_dir / "decoder-adapter"
        )
        if (model_dir / "encoder-adapter").exists():
            model.encoder = peft.PeftModel.from_pretrained(
                model.encoder, model_dir / "encoder-adapter"
            )
        self.model = model.eval()
        self.processor = processor
        self.version = (
            str(self.config.get("decoding", "donut"))
            + "/"
            + str(self.config.get("target_format"))
        )

    def read(self, image: bytes) -> dict[str, str]:
        pil: Any = importlib.import_module("PIL.Image")
        with pil.open(io.BytesIO(image)) as raw:
            picture = raw.convert("RGB")
        pixels = self.processor(images=picture, return_tensors="pt").pixel_values
        with self.torch.inference_mode():
            output = self.model.generate(
                pixels,
                decoder_input_ids=self.torch.tensor([[self.task]]),
                max_new_tokens=192,
                num_beams=1,
                do_sample=False,
            )
        text = self.processor.tokenizer.decode(
            output[0], skip_special_tokens=True, clean_up_tokenization_spaces=False
        )
        readings: dict[str, str] = {}
        for trained, value in parse_fields(text, tuple(FIELD_NAMES)).items():
            name = FIELD_NAMES[trained]
            kept = plausible(name, to_canonical(trained, value))
            if kept is not None:
                readings[name] = kept
        return readings
