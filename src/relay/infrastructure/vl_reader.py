"""Third receipt reader: PaddleOCR-VL, a small vision-language model.

It runs in its own container (llama.cpp server, see compose.yaml, service
"ocr") on this server, so images never leave it. This module sends each
receipt image once, stores the plain text it reads under
``machine_result["vl_ocr_v1"]``, and nothing else: auto-confirm does not use
it yet. ``eval-reader`` scores it against the current reader on the same
confirmed receipts, and the switch happens only if the numbers say so.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from relay.domain.processing import ReviewStatus
from relay.infrastructure.binary_models import BinaryObjectRow
from relay.infrastructure.database import Database
from relay.infrastructure.object_storage import FilesystemObjectStorage
from relay.infrastructure.processing_models import ReceiptCandidateRow

VERSION = "vl_ocr_v1"
PROMPT = "OCR:"  # PaddleOCR-VL's plain text recognition task
# Image tokens grow with the square of the size and the CPU time with them:
# 1280 px took over 5 minutes on two cores. RELAY_VL_LONGEST_SIDE overrides.
LONGEST_SIDE = int(os.environ.get("RELAY_VL_LONGEST_SIDE", "896"))
TIMEOUT_SECONDS = float(os.environ.get("RELAY_VL_TIMEOUT_SECONDS", "900"))
MAX_IMAGE_BYTES = 16 * 1024 * 1024


def prepare(data: bytes, longest: int = LONGEST_SIDE) -> bytes:
    """The image as a JPEG no larger than ``longest`` on its long side."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as image:
        picture = ImageOps.exif_transpose(image).convert("RGB")
        picture.thumbnail((longest, longest))
        out = io.BytesIO()
        picture.save(out, "JPEG", quality=90)
        return out.getvalue()


def request_body(image: bytes) -> dict[str, Any]:
    encoded = base64.b64encode(image).decode("ascii")
    return {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 1024,
    }


def read_vl(image: bytes, url: str, timeout: float = TIMEOUT_SECONDS) -> str:
    """Plain text of one prepared image, read by the model server at ``url``.

    Raises OSError (connection refused, timeout) while the server is not up,
    e.g. still downloading the model."""
    body = json.dumps(request_body(image)).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - fixed internal URL from settings
        url.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        answer = json.loads(response.read().decode("utf-8"))
    return str(answer["choices"][0]["message"]["content"] or "")


def night(now: datetime | None = None) -> bool:
    """11 PM to 8 AM where the receipts are (RECEIPT_TIMEZONE): no receipts
    come in then, so the model may use the server's two cores for everything."""
    zone = ZoneInfo(os.environ.get("RECEIPT_TIMEZONE", "Africa/Cairo"))
    hour = (now or datetime.now(UTC)).astimezone(zone).hour
    return hour >= NIGHT_STARTS or hour < NIGHT_ENDS


NIGHT_STARTS = 23
NIGHT_ENDS = 8


async def vl_pass(
    database: Database,
    storage: FilesystemObjectStorage,
    url: str,
    limit: int,
    review_only: bool = False,
) -> int:
    """Read up to ``limit`` receipts not read by the model yet, newest first.

    Confirmed receipts are read too (they are what the comparison is scored
    on), except with ``review_only``: then only receipts waiting in Review.
    The slow model call runs outside any database lock."""
    async with database.session() as session:
        rows = (
            await session.execute(
                select(ReceiptCandidateRow.id, BinaryObjectRow.storage_key)
                .join(
                    BinaryObjectRow, BinaryObjectRow.id == ReceiptCandidateRow.object_id
                )
                .where(
                    ReceiptCandidateRow.hidden_reason.is_(None),
                    (
                        ReceiptCandidateRow.review_status
                        == ReviewStatus.NEEDS_REVIEW.value
                        if review_only
                        else ReceiptCandidateRow.review_status
                        != ReviewStatus.REJECTED.value
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
            image = prepare(storage.read(storage_key, MAX_IMAGE_BYTES))
        except Exception as error:  # noqa: BLE001 - expired or unreadable image
            reading: dict[str, Any] = {"text": "", "error": type(error).__name__}
        else:
            try:
                reading = {"text": await asyncio.to_thread(read_vl, image, url)}
            except urllib.error.HTTPError as error:  # the server refused this image
                reading = {"text": "", "error": f"HTTP {error.code}"}
            # Any other OSError (server not up yet) propagates: nothing is
            # stored and the loop tries again later.
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
            f"Model read {str(candidate_id)[:8]} in {reading['seconds']}s"
            + (f" ({reading['error']})" if "error" in reading else ""),
            flush=True,
        )
    return done


async def vl_loop(
    database: Database, storage: FilesystemObjectStorage, url: str
) -> None:
    """Keep reading, two receipts at a time; rest a minute when caught up
    or while the model server is not reachable. By day only receipts
    waiting in Review; from 11 PM to 8 AM all of them."""
    while True:
        try:
            # Daytime: only what waits in Review, so the main reader keeps
            # the cores; at night, everything (the comparison set too).
            done = await vl_pass(database, storage, url, 2, review_only=not night())
        except Exception as error:  # keep the loop alive
            print(f"Model pass failed ({type(error).__name__}).", flush=True)
            done = 0
        if done == 0:
            await asyncio.sleep(60)
