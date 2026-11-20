"""First receipt reader: classic PaddleOCR (PP-OCR) via RapidOCR.

A small detector finds the text lines and a recognizer reads each one: two
recognizers, Chinese/English (Latin letters and digits) and Arabic, because
receipts mix both. It is fast enough on a CPU to read every receipt within
seconds, so it runs all day in its own container (compose.yaml, "ppocr").

Stored under ``machine_result[VERSION]``: ``text`` (both recognizers'
lines, top to bottom) and ``latin`` (the Chinese/English recognizer alone,
which reads digits best: the Arabic one can drop a digit, 577 read as 57).
``latest`` gives the newest reading, falling back to earlier versions while
receipts are read again.
"""

from __future__ import annotations

import asyncio
import copy
import io
import time
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import or_, select

from relay.infrastructure.binary_models import BinaryObjectRow
from relay.infrastructure.database import Database
from relay.infrastructure.object_storage import FilesystemObjectStorage
from relay.infrastructure.processing_models import ReceiptCandidateRow

# v1-v3 readings all failed (numpy results, missing python-bidi, no Arabic
# model in PP-OCRv6); v5 adds the Latin recognizer's lines on their own.
VERSION = "ppocr_v5"
EARLIER = ("ppocr_v4",)
KEYS = (VERSION, *EARLIER)


def latest_version(machine: Any) -> str | None:
    """Which reading ``latest`` gives: the newest successful version."""
    if not isinstance(machine, dict):
        return None
    for key in KEYS:
        reading = machine.get(key)
        if (
            isinstance(reading, dict)
            and not reading.get("error")
            and str(reading.get("text") or "").strip()
        ):
            return key
    return None


def latest(machine: Any) -> dict[str, Any] | None:
    """The newest successful PaddleOCR reading of a receipt, if any."""
    key = latest_version(machine)
    return machine[key] if key is not None else None


MAX_IMAGE_BYTES = 16 * 1024 * 1024


@lru_cache(maxsize=1)
def engines() -> tuple[Any, Any]:
    """(Latin/digits engine, Arabic engine), loaded once per process."""
    from rapidocr import (  # type: ignore[import-not-found]
        LangRec,
        ModelType,
        OCRVersion,
        RapidOCR,
    )

    # The default (PP-OCRv6) has no Arabic recogniser; PP-OCRv5 mobile does.
    arabic = {
        "Rec.lang_type": LangRec.ARABIC,
        "Rec.ocr_version": OCRVersion.PPOCRV5,
        "Rec.model_type": ModelType.MOBILE,
    }
    return RapidOCR(), RapidOCR(params=arabic)


def self_test() -> None:
    """Run both engines on a drawn line of text; used at image build so a
    missing model or dependency fails the build, not every reading."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (480, 120), "white")
    ImageDraw.Draw(image).text((20, 40), "550.00 EGP", fill="black", font_size=40)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    read_ppocr(buffer.getvalue())


def lines(boxes: Any, texts: Any) -> list[str]:
    """Text boxes grouped into lines (similar height on the page), top to
    bottom, each line left to right."""
    items: list[tuple[float, float, float, str]] = []
    # RapidOCR gives numpy arrays: "boxes or ()" would raise ValueError.
    boxes = () if boxes is None else boxes
    texts = () if texts is None else texts
    for box, text in zip(boxes, texts, strict=False):
        if not text:
            continue
        ys = [float(point[1]) for point in box]
        xs = [float(point[0]) for point in box]
        items.append(((min(ys) + max(ys)) / 2, max(ys) - min(ys), min(xs), str(text)))
    items.sort()
    grouped: list[list[tuple[float, float, float, str]]] = []
    for item in items:
        if grouped and abs(item[0] - grouped[-1][0][0]) <= max(item[1], 8) / 2:
            grouped[-1].append(item)
        else:
            grouped.append([item])
    return [
        " ".join(part[3] for part in sorted(line, key=lambda p: p[2]))
        for line in grouped
    ]


def read_ppocr(data: bytes) -> str:
    """Both recognizers' lines, one reading after the other."""
    return str(read_parts(data)["text"])


def read_parts(data: bytes) -> dict[str, str]:
    """``text``: both recognizers' lines; ``latin``: the first alone."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as image:
        picture = ImageOps.exif_transpose(image).convert("RGB")
    import numpy

    array = numpy.asarray(picture)
    parts = []
    for engine in engines():
        result = engine(array)
        parts.append(
            "\n".join(
                lines(getattr(result, "boxes", None), getattr(result, "txts", None))
            )
        )
    return {"text": "\n".join(parts), "latin": parts[0] if parts else ""}


async def ppocr_pass(
    database: Database, storage: FilesystemObjectStorage, limit: int
) -> int:
    """Read up to ``limit`` receipts not read yet, newest first (confirmed
    ones too: they are what the comparison is scored on)."""
    async with database.session() as session:
        rows = (
            await session.execute(
                select(ReceiptCandidateRow.id, BinaryObjectRow.storage_key)
                .join(
                    BinaryObjectRow, BinaryObjectRow.id == ReceiptCandidateRow.object_id
                )
                .where(
                    # Rejected and "not a receipt" photos too: the first
                    # reader misses real receipts (photos of a phone screen),
                    # and helper_reopen brings those back to Review.
                    or_(
                        ReceiptCandidateRow.hidden_reason.is_(None),
                        ReceiptCandidateRow.hidden_reason == "not_a_receipt",
                    ),
                    ReceiptCandidateRow.machine_result[VERSION].as_string().is_(None),
                )
                .order_by(ReceiptCandidateRow.created_at.desc())
                .limit(limit)
            )
        ).all()
    done = 0
    for candidate_id, storage_key in rows:
        started = time.monotonic()
        try:
            data = storage.read(storage_key, MAX_IMAGE_BYTES)
            reading: dict[str, Any] = dict(await asyncio.to_thread(read_parts, data))
        except Exception as error:  # noqa: BLE001 - one bad image, not the batch
            reading = {"text": "", "error": f"{type(error).__name__}: {error}"[:300]}
        reading |= {
            "seconds": round(time.monotonic() - started, 1),
            "at": datetime.now(UTC).isoformat(),
        }
        async with database.session() as session:
            row = await session.scalar(
                select(ReceiptCandidateRow)
                .where(ReceiptCandidateRow.id == candidate_id)
                .with_for_update()
            )
            if row is None:
                continue
            machine = copy.deepcopy(row.machine_result or {})
            machine[VERSION] = reading
            row.machine_result = machine
            await session.commit()
        done += 1
        print(
            f"PP-OCR read {str(candidate_id)[:8]} in {reading['seconds']}s", flush=True
        )
    return done


async def ppocr_loop(database: Database, storage: FilesystemObjectStorage) -> None:
    """Read five at a time; rest 30 seconds when caught up."""
    while True:
        try:
            done = await ppocr_pass(database, storage, 5)
        except Exception as error:  # keep the loop alive
            print(f"PP-OCR pass failed ({type(error).__name__}).", flush=True)
            done = 0
        if done == 0:
            await asyncio.sleep(30)
