"""Fourth receipt reader, under trial: classic PaddleOCR (PP-OCR) via RapidOCR.

A small detector finds the text lines and a recognizer reads each one: two
recognizers, Chinese/English (Latin letters and digits) and Arabic, because
receipts mix both. It is fast enough on a CPU to read every receipt within
seconds, so it runs all day in its own container (compose.yaml, "ppocr").

The text is stored under ``machine_result["ppocr_v1"]``, lines top to bottom,
and nothing else: auto-confirm does not use it. ``eval-reader`` scores it
against the current reader on confirmed receipts first.
"""

from __future__ import annotations

import asyncio
import copy
import io
import time
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import select

from relay.domain.processing import ReviewStatus
from relay.infrastructure.binary_models import BinaryObjectRow
from relay.infrastructure.database import Database
from relay.infrastructure.object_storage import FilesystemObjectStorage
from relay.infrastructure.processing_models import ReceiptCandidateRow

VERSION = "ppocr_v1"
MAX_IMAGE_BYTES = 16 * 1024 * 1024


@lru_cache(maxsize=1)
def engines() -> tuple[Any, Any]:
    """(Latin/digits engine, Arabic engine), loaded once per process."""
    from rapidocr import LangRec, RapidOCR

    return RapidOCR(), RapidOCR(params={"Rec.lang_type": LangRec.ARABIC})


def lines(boxes: Any, texts: Any) -> list[str]:
    """Text boxes grouped into lines (similar height on the page), top to
    bottom, each line left to right."""
    items: list[tuple[float, float, float, str]] = []
    for box, text in zip(boxes or (), texts or (), strict=False):
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
    return "\n".join(parts)


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
                    ReceiptCandidateRow.hidden_reason.is_(None),
                    ReceiptCandidateRow.review_status != ReviewStatus.REJECTED.value,
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
            reading: dict[str, Any] = {
                "text": await asyncio.to_thread(read_ppocr, data)
            }
        except Exception as error:  # noqa: BLE001 - one bad image, not the batch
            reading = {"text": "", "error": type(error).__name__}
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
