"""Is this photo a money-transfer receipt? A zero-shot CLIP verdict.

CLIP (openai/clip-vit-base-patch32, downloaded at image build into
RELAY_CLIP_DIR) compares the picture with short descriptions; no training.
It judges the picture itself, so a blurry receipt still looks like a phone
screen and a keyboard still looks like a keyboard even when it shows digits.

Stored once per photo under ``machine_result[KEY]``. The verdict decides
only the clear cases: "not_receipt" photos are hidden (and never read again
for a waiting bank SMS), "receipt" photos are never hidden; "unsure", and any
photo when the model is missing, keeps the earlier rules.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import io
import os
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import or_, select

from relay.domain.processing import ReviewStatus
from relay.infrastructure.binary_models import BinaryObjectRow
from relay.infrastructure.database import Database
from relay.infrastructure.object_storage import FilesystemObjectStorage
from relay.infrastructure.processing_models import ReceiptCandidateRow

KEY = "clip_v1"
RECEIPT_AT = 0.6  # summed probability of the receipt descriptions
OTHER_AT = 0.15
MAX_IMAGE_BYTES = 16 * 1024 * 1024
RECEIPT_PROMPTS = (
    "a screenshot of a mobile banking money transfer receipt",
    "a photo of a phone screen showing a payment confirmation",
    "a bank transfer confirmation screen with an amount",
)
OTHER_PROMPTS = (
    "a photo of a computer screen or a desktop",
    "a photo of a keyboard",
    "a photo of food",
    "a printed shop or supermarket bill",
    "a screenshot of a chat conversation",
    "a photo of people or a room",
    "a photo of a paper document",
)


class ModelMissing(RuntimeError):
    """No CLIP model in RELAY_CLIP_DIR (development, tests)."""


def verdict(receipt: float) -> str:
    if receipt >= RECEIPT_AT:
        return "receipt"
    if receipt <= OTHER_AT:
        return "not_receipt"
    return "unsure"


def clip_verdict(machine: Any) -> str:
    """The stored verdict, or "unsure" when there is none."""
    stored = machine.get(KEY) if isinstance(machine, dict) else None
    value = stored.get("verdict") if isinstance(stored, dict) else None
    return value if value in ("receipt", "not_receipt") else "unsure"


@lru_cache(maxsize=1)
def _model() -> tuple[Any, Any]:
    folder = os.environ.get("RELAY_CLIP_DIR", "")
    if not folder or not os.path.isfile(os.path.join(folder, "config.json")):
        raise ModelMissing(folder or "RELAY_CLIP_DIR not set")
    transformers: Any = importlib.import_module("transformers")
    model = transformers.CLIPModel.from_pretrained(folder)
    model.eval()
    return model, transformers.CLIPProcessor.from_pretrained(folder)


def classify(picture: Any) -> dict[str, Any]:
    """Probabilities over all descriptions; ``receipt`` sums the receipt ones."""
    torch: Any = importlib.import_module("torch")
    model, processor = _model()
    prompts = [*RECEIPT_PROMPTS, *OTHER_PROMPTS]
    inputs = processor(text=prompts, images=picture, return_tensors="pt", padding=True)
    with torch.no_grad():
        probabilities = model(**inputs).logits_per_image.softmax(dim=1)[0].tolist()
    scores = {
        prompt: round(float(value), 4)
        for prompt, value in zip(prompts, probabilities, strict=True)
    }
    receipt = round(sum(scores[prompt] for prompt in RECEIPT_PROMPTS), 4)
    return {
        "label": max(scores, key=lambda p: scores[p]),
        "receipt": receipt,
        "scores": scores,
    }


def _open(data: bytes) -> Any:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


async def clip_pass(
    database: Database,
    storage: FilesystemObjectStorage,
    limit: int,
    classifier: Callable[[Any], dict[str, Any]] | None = None,
) -> int:
    """Score up to ``limit`` photos not scored yet, newest first."""
    judge = classifier or classify
    async with database.session() as session:
        rows = (
            await session.execute(
                select(ReceiptCandidateRow.id, BinaryObjectRow.storage_key)
                .join(
                    BinaryObjectRow, BinaryObjectRow.id == ReceiptCandidateRow.object_id
                )
                .where(
                    or_(
                        ReceiptCandidateRow.hidden_reason.is_(None),
                        ReceiptCandidateRow.hidden_reason.in_(
                            ("not_a_receipt", "no_transfer")
                        ),
                    ),
                    ReceiptCandidateRow.review_status.in_(
                        (ReviewStatus.NEEDS_REVIEW.value, ReviewStatus.REJECTED.value)
                    ),
                    ReceiptCandidateRow.machine_result[KEY].as_string().is_(None),
                )
                .order_by(ReceiptCandidateRow.created_at.desc())
                .limit(limit)
            )
        ).all()
    done = 0
    for candidate_id, storage_key in rows:
        try:
            picture = _open(storage.read(storage_key, MAX_IMAGE_BYTES))
            result = dict(await asyncio.to_thread(judge, picture))
            result["verdict"] = verdict(float(result.get("receipt") or 0))
        except ModelMissing:
            return done  # nothing to judge with: the earlier rules apply
        except Exception as error:  # noqa: BLE001 - one bad image, not the batch
            result = {"verdict": "unsure", "error": f"{type(error).__name__}"[:100]}
        result["at"] = datetime.now(UTC).isoformat()
        async with database.session() as session:
            row = await session.scalar(
                select(ReceiptCandidateRow)
                .where(ReceiptCandidateRow.id == candidate_id)
                .with_for_update()
            )
            if row is None:
                continue
            machine = copy.deepcopy(row.machine_result or {})
            machine[KEY] = result
            row.machine_result = machine
            await session.commit()
        done += 1
    if done:
        print(f"CLIP judged {done} photos.", flush=True)
    return done
