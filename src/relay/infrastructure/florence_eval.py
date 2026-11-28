"""Experiment: does Florence-2 read amounts PaddleOCR could not?

Read-only. Nothing in the database changes and no live rule uses it.

1. Known answers: confirmed receipts (by a bank SMS or a reviewer). Florence
   reads each photo; its amount is graded ok / miss / wrong against the
   confirmed amount. "wrong" (a different amount read) must stay near zero.
2. PaddleOCR failures: photos whose strong retry failed. An amount counts as
   found only when it is exactly one of the waiting bank-SMS amounts and the
   recipient is not someone else (same check as the PaddleOCR retry).

Run inside the reader image (it has torch + transformers); the model is
downloaded once into /tmp, so allow the hub for this run:

    ./deploy/dc.sh --profile reader run --rm -e HF_HUB_OFFLINE=0 \
        -e TRANSFORMERS_OFFLINE=0 reader \
        python -m relay.infrastructure.florence_eval --known 60 --failed 40
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import io
import json
import time
from decimal import Decimal
from functools import lru_cache
from typing import Any

from sqlalchemy import select

from relay.domain.processing import ReviewStatus
from relay.infrastructure.binary_models import BinaryObjectRow
from relay.infrastructure.config import load_settings
from relay.infrastructure.database import Database
from relay.infrastructure.object_storage import FilesystemObjectStorage
from relay.infrastructure.processing_models import ReceiptCandidateRow

MODELS = {
    "base": "florence-community/Florence-2-base",
    "large": "florence-community/Florence-2-large",
}
MAX_IMAGE_BYTES = 16 * 1024 * 1024
RETRY_READ = "retry_read"


@lru_cache(maxsize=2)
def _model(name: str) -> tuple[Any, Any]:
    transformers: Any = importlib.import_module("transformers")
    processor = transformers.AutoProcessor.from_pretrained(MODELS[name])
    model = transformers.Florence2ForConditionalGeneration.from_pretrained(MODELS[name])
    model.eval()
    return processor, model


def _ocr(picture: Any, name: str) -> str:
    torch: Any = importlib.import_module("torch")
    processor, model = _model(name)
    inputs = processor(text="<OCR>", images=picture, return_tensors="pt")
    with torch.inference_mode():
        ids = model.generate(**inputs, max_new_tokens=512, num_beams=3)
    return str(processor.batch_decode(ids, skip_special_tokens=True)[0])


def _tiles(picture: Any) -> list[Any]:
    """A tall phone screenshot squashed into 768x768 blurs its digits:
    read it again as overlapping, nearly square slices."""
    width, height = picture.size
    if height <= width * 1.3:
        return []
    step = int(width * 0.8)
    tops = list(range(0, max(1, height - width), step)) + [max(0, height - width)]
    return [picture.crop((0, top, width, min(height, top + width))) for top in tops]


def read(picture: Any, name: str) -> tuple[str, str]:
    """(text, mode): the whole photo first, slices when no amount is seen."""
    from relay.infrastructure.cli import _paddle_amount

    text = _ocr(picture, name)
    if _paddle_amount(text, None) is not None:
        return text, "full"
    parts = [_ocr(tile, name) for tile in _tiles(picture)]
    return (text + "\n" + "\n".join(parts), "tiles") if parts else (text, "full")


def _open(data: bytes) -> Any:
    pil: Any = importlib.import_module("PIL.Image")
    ops: Any = importlib.import_module("PIL.ImageOps")
    with pil.open(io.BytesIO(data)) as image:
        return ops.exif_transpose(image).convert("RGB")


def _money(value: object) -> str | None:
    try:
        return f"{Decimal(str(value).replace(',', '')):.2f}" if value else None
    except ArithmeticError:
        return None


async def known(
    database: Database, storage: FilesystemObjectStorage, limit: int, name: str
) -> dict[str, Any]:
    from relay.infrastructure.cli import _paddle_amount
    from relay.infrastructure.reader_eval import expected

    async with database.session() as session:
        rows = (
            await session.execute(
                select(
                    ReceiptCandidateRow.id,
                    BinaryObjectRow.storage_key,
                    ReceiptCandidateRow.resolution_result,
                )
                .join(
                    BinaryObjectRow, BinaryObjectRow.id == ReceiptCandidateRow.object_id
                )
                .where(
                    ReceiptCandidateRow.review_status
                    == ReviewStatus.AUTO_ACCEPTED.value
                )
                .order_by(ReceiptCandidateRow.created_at.desc())
                .limit(limit * 3)
            )
        ).all()
    counts = {"ok": 0, "miss": 0, "wrong": 0}
    cases: list[dict[str, Any]] = []
    seconds = 0.0
    for candidate_id, storage_key, resolution in rows:
        want = _money((expected(resolution) or {}).get("amount"))
        if want is None:
            continue
        started = time.monotonic()
        try:
            text, mode = await asyncio.to_thread(
                read, _open(storage.read(storage_key, MAX_IMAGE_BYTES)), name
            )
        except Exception as error:  # noqa: BLE001 - one bad image, not the run
            text, mode = f"error {type(error).__name__}", "error"
        spent = time.monotonic() - started
        seconds += spent
        got = _money(_paddle_amount(text, None))
        verdict = "miss" if got is None else "ok" if got == want else "wrong"
        counts[verdict] += 1
        cases.append(
            {
                "id": str(candidate_id),
                "want": want,
                "got": got,
                "verdict": verdict,
                "mode": mode,
                "seconds": round(spent, 1),
            }
        )
        print(
            f"known {candidate_id} want={want} got={got} {verdict} {mode} {spent:.1f}s",
            flush=True,
        )
        if len(cases) >= limit:
            break
    return {
        "counts": counts,
        "cases": cases,
        "seconds_each": round(seconds / len(cases), 1) if cases else None,
    }


async def failed(
    database: Database, storage: FilesystemObjectStorage, limit: int, name: str
) -> dict[str, Any]:
    from relay.infrastructure.email_settings import recipient
    from relay.infrastructure.ppocr_reader import examine

    async with database.session() as session:
        owner = await recipient(session)
        rows = (
            await session.execute(
                select(
                    ReceiptCandidateRow.id,
                    BinaryObjectRow.storage_key,
                    ReceiptCandidateRow.resolution_result,
                )
                .join(
                    BinaryObjectRow, BinaryObjectRow.id == ReceiptCandidateRow.object_id
                )
                .where(
                    ReceiptCandidateRow.resolution_result[RETRY_READ][
                        "state"
                    ].as_string()
                    == "failed"
                )
                .order_by(ReceiptCandidateRow.created_at.desc())
                .limit(limit)
            )
        ).all()
    found = 0
    cases: list[dict[str, Any]] = []
    for candidate_id, storage_key, resolution in rows:
        marked = (resolution or {}).get(RETRY_READ) or {}
        amounts = [Decimal(str(value)) for value in marked.get("amounts") or ()]
        started = time.monotonic()
        try:
            text, mode = await asyncio.to_thread(
                read, _open(storage.read(storage_key, MAX_IMAGE_BYTES)), name
            )
        except Exception as error:  # noqa: BLE001 - one bad image, not the run
            text, mode = f"error {type(error).__name__}", "error"
        spent = time.monotonic() - started
        seen, who, proven = examine({"text": text}, amounts, owner)
        found += proven is not None
        cases.append(
            {
                "id": str(candidate_id),
                "waiting": [f"{a:.2f}" for a in amounts],
                "read": seen,
                "recipient": who,
                "proven": proven,
                "mode": mode,
                "seconds": round(spent, 1),
            }
        )
        print(
            f"failed {candidate_id} read={seen} recipient={who} proven={proven} "
            f"{mode} {spent:.1f}s",
            flush=True,
        )
    return {"tried": len(cases), "found": found, "cases": cases}


def _save(path: str, result: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=1)


async def main(known_limit: int, failed_limit: int, name: str, out: str) -> None:
    settings = load_settings()
    database = Database(settings)
    storage = FilesystemObjectStorage(settings.object_storage_root)
    try:
        result: dict[str, Any] = {
            "model": MODELS[name],
            "known": await known(database, storage, known_limit, name),
            "failed": await failed(database, storage, failed_limit, name),
        }
    finally:
        await database.close()
    _save(out, result)
    k, f = result["known"], result["failed"]
    total = sum(k["counts"].values()) or 1
    print(
        f"\nFlorence-2 {name}: known {sum(k['counts'].values())} -> "
        f"ok {k['counts']['ok']} ({100 * k['counts']['ok'] // total}%), "
        f"miss {k['counts']['miss']}, WRONG {k['counts']['wrong']}, "
        f"{k['seconds_each']}s each | PaddleOCR failures: "
        f"{f['found']} of {f['tried']} found | details {out}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--known", type=int, default=60)
    parser.add_argument("--failed", type=int, default=40)
    parser.add_argument("--model", choices=sorted(MODELS), default="base")
    parser.add_argument("--out", default="/tmp/florence-eval.json")
    args = parser.parse_args()
    asyncio.run(main(args.known, args.failed, args.model, args.out))
