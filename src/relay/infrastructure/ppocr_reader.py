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
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any

from sqlalchemy import or_, select

from relay.infrastructure.binary_models import BinaryObjectRow
from relay.infrastructure.database import Database
from relay.infrastructure.object_storage import FilesystemObjectStorage
from relay.infrastructure.processing_models import ReceiptCandidateRow

# v1-v3 readings all failed (numpy results, missing python-bidi, no Arabic
# model in PP-OCRv6); v5 adds the Latin recognizer's lines on their own;
# v6 zooms into photos of a screen when the first reading finds too little.
VERSION = "ppocr_v6"
EARLIER = ("ppocr_v5", "ppocr_v4")
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
    if server_engine() is None:
        raise RuntimeError("PP-OCRv5 server models could not be loaded")


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
    first = _read_picture(picture)
    if _readable(first["latin"]):
        return first
    # Little or nothing read: often a dark photo of another phone's screen,
    # where the text is a few pixels high. Crop to the screen, enlarge and
    # read again; keep whichever reading found more.
    zoomed = zoom_in(picture)
    if zoomed is None:
        return first
    second = _read_picture(zoomed)
    return second if len(second["latin"]) > len(first["latin"]) else first


def _read_picture(picture: Any) -> dict[str, str]:
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


ZOOM_MAX_SIDE = 2600


def _readable(latin: str) -> bool:
    """Enough for a receipt: a reference-like number and an amount in EGP."""
    import re

    return bool(re.search(r"\d{10,}", latin)) and "EGP" in latin.upper()


def zoom_in(picture: Any) -> Any:
    """The bright screen in a photo, cropped, enlarged and sharpened; None
    when there is nothing to gain (already large and filling the frame)."""
    from PIL import ImageFilter, ImageOps

    box = screen_box(picture)
    cropped = picture.crop(box) if box is not None else picture
    side = max(cropped.size)
    scale = min(3.0, ZOOM_MAX_SIDE / side)
    if scale < 1.3 and box is None:
        return None
    if scale > 1:
        cropped = cropped.resize(
            (int(cropped.width * scale), int(cropped.height * scale)),
            resample=3,  # bicubic
        )
    cropped = ImageOps.autocontrast(cropped, cutoff=1)
    return cropped.filter(ImageFilter.UnsharpMask(radius=2, percent=160, threshold=3))


def screen_box(picture: Any) -> tuple[int, int, int, int] | None:
    """Where the bright phone screen (the receipt) is in a photo, or None
    when no part stands out (a lamp or a reflection is too small)."""
    from PIL import ImageOps

    gray = ImageOps.grayscale(picture)
    histogram = gray.histogram()
    total = sum(histogram)
    seen, cut = 0, 255
    for level in range(255, -1, -1):  # the brightest ~35% of pixels
        seen += histogram[level]
        if seen >= total * 0.35:
            cut = level
            break
    box = gray.point(lambda value: 255 if value >= max(cut, 60) else 0).getbbox()
    width, height = picture.size
    if box is not None:
        left, top, right, bottom = box
        pad_x, pad_y = (right - left) // 20, (bottom - top) // 20
        box = (
            max(0, left - pad_x),
            max(0, top - pad_y),
            min(width, right + pad_x),
            min(height, bottom + pad_y),
        )
        area = (box[2] - box[0]) * (box[3] - box[1])
        if area < width * height * 0.08:
            return None  # a lamp or a reflection, not a screen
        return box
    return None


# Reading again before a photo is hidden: a bank SMS of its day is unclaimed,
# so it may be a blurry or sideways receipt. Tried in this order; the first
# reading that shows the SMS amount (sent to the owner) wins.
RETRY_READ = "retry_read"
RETRY_MAX_SIDE = 3200


def _upscaled(picture: Any, factor: float = 2.0) -> Any:
    scale = min(factor, RETRY_MAX_SIDE / max(picture.size))
    if scale <= 1:
        return picture
    return picture.resize(
        (int(picture.width * scale), int(picture.height * scale)), resample=3
    )


def enhance(picture: Any) -> Any:
    """Grayscale, local contrast (CLAHE where OpenCV is there, as in the
    PaddleOCR container), denoise and sharpen."""
    from PIL import Image, ImageFilter, ImageOps

    gray = ImageOps.grayscale(picture)
    try:
        import cv2
        import numpy

        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        gray = Image.fromarray(clahe.apply(numpy.asarray(gray)))
    except ImportError:
        gray = ImageOps.equalize(gray)
    gray = gray.filter(ImageFilter.MedianFilter(3))
    gray = gray.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=2))
    return gray.convert("RGB")


def variants(picture: Any) -> Iterator[tuple[str, Any]]:
    """The photo turned, cropped to the screen and enlarged, and enhanced."""
    for angle in (90, 180, 270):
        yield f"rotate_{angle}", picture.rotate(angle, expand=True)
    box = screen_box(picture)
    crop = _upscaled(picture.crop(box) if box is not None else picture)
    yield "crop_2x", crop
    yield "enhance", enhance(picture)
    yield "crop_enhance", enhance(crop)


def examine(
    reading: dict[str, str],
    amounts: list[Decimal],
    owner: tuple[str, str | None] | None,
) -> tuple[str | None, str, str | None]:
    """(amount read, recipient verdict, proven amount). Proven when the
    amount read is exactly one of the waiting SMS amounts and the recipient
    is not someone else ("match" or "unclear": a blurry photo often hides
    it; auto-confirm still treats an unclear recipient carefully)."""
    from relay.infrastructure.cli import _paddle_amount
    from relay.infrastructure.text_recheck import recipient_check

    text = str(reading.get("text") or "")
    amount = _paddle_amount(text, reading.get("latin") or None)
    try:
        value = (
            Decimal(str(amount).replace(",", "")).quantize(Decimal("0.01"))
            if amount
            else None
        )
    except InvalidOperation:
        value = None
    seen = f"{value:.2f}" if value is not None else None
    recipient = (
        # As an InstaPay screen: another phone printed means another person.
        recipient_check(text, owner[0], owner[1], True)
        if owner
        else "unchecked"
    )
    proven = seen if value in amounts and recipient != "different" else None
    return seen, recipient, proven


def proves(
    reading: dict[str, str],
    amounts: list[Decimal],
    owner: tuple[str, str | None] | None,
) -> str | None:
    return examine(reading, amounts, owner)[2]


@lru_cache(maxsize=1)
def server_engine() -> Any | None:
    """PP-OCRv5 server detection and recognition (Chinese/English, digits):
    slower and stronger than the mobile models; only for reading again.
    None when it cannot be loaded (the mobile engines still try)."""
    try:
        from rapidocr import (
            ModelType,
            OCRVersion,
            RapidOCR,
        )

        return RapidOCR(
            params={
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Det.model_type": ModelType.SERVER,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.SERVER,
            }
        )
    except Exception:  # noqa: BLE001 - missing model or library
        return None


def _read_server(picture: Any) -> dict[str, str]:
    import numpy

    engine = server_engine()
    if engine is None:
        return {"text": "", "latin": ""}
    result = engine(numpy.asarray(picture))
    text = "\n".join(
        lines(getattr(result, "boxes", None), getattr(result, "txts", None))
    )
    return {"text": text, "latin": text}


Try = dict[str, str | None]


def try_variants(
    picture: Any,
    amounts: list[Decimal],
    owner: tuple[str, str | None] | None,
    read: Callable[[Any], dict[str, str]] | None = None,
    server: Callable[[Any], dict[str, str]] | None = None,
) -> tuple[str | None, dict[str, str], str | None, list[Try]]:
    """(rule, reading, amount, tries): the first reading that proves a
    waiting amount. The server engine reads the photo as it is first, then
    each variant is read by the mobile engines and by the server engine."""
    reader = read or _read_picture
    tries: list[Try] = []
    attempts: list[tuple[str, Any, Callable[[Any], dict[str, str]]]] = []
    if server is not None:
        attempts.append(("original+server", picture, server))
    for rule, variant in variants(picture):
        attempts.append((rule, variant, reader))
        if server is not None:
            attempts.append((f"{rule}+server", variant, server))
    for rule, variant, engine in attempts:
        reading = engine(variant)
        seen, recipient, proven = examine(reading, amounts, owner)
        tries.append({"rule": rule, "amount": seen, "recipient": recipient})
        if proven is not None:
            return rule, reading, proven, tries
    return None, {}, None, tries


async def retry_pass(
    database: Database,
    storage: FilesystemObjectStorage,
    limit: int = 2,
) -> int:
    """Read again the photos marked by rescue_hidden. A success becomes this
    receipt's PaddleOCR reading (the normal rules then apply, so it can be
    confirmed); a failure keeps it in Review."""
    from PIL import Image, ImageOps

    from relay.infrastructure.email_settings import recipient

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
                    ReceiptCandidateRow.resolution_result[RETRY_READ]["state"]
                    .as_string()
                    .in_(("pending",))
                )
                .order_by(ReceiptCandidateRow.created_at)
                .limit(limit)
            )
        ).all()
    done = 0
    for candidate_id, storage_key, resolution in rows:
        marked = (resolution or {}).get(RETRY_READ) or {}
        amounts = [Decimal(str(value)) for value in marked.get("amounts") or ()]
        started = time.monotonic()
        error: str | None = None
        try:
            data = storage.read(storage_key, MAX_IMAGE_BYTES)
            with Image.open(io.BytesIO(data)) as image:
                picture = ImageOps.exif_transpose(image).convert("RGB")
            server = _read_server if server_engine() is not None else None
            rule, reading, amount, tried = await asyncio.to_thread(
                try_variants, picture, amounts, owner, None, server
            )
        except Exception as failure:  # noqa: BLE001 - one bad image, not the batch
            rule, reading, amount, tried = None, {}, None, []
            error = f"{type(failure).__name__}: {failure}"[:300]
        now = datetime.now(UTC).isoformat()
        async with database.session() as session:
            row = await session.scalar(
                select(ReceiptCandidateRow)
                .where(ReceiptCandidateRow.id == candidate_id)
                .with_for_update()
            )
            if row is None:
                continue
            updated = copy.deepcopy(row.resolution_result or {})
            state = dict(updated.get(RETRY_READ) or {}) | {
                "state": "read" if rule else "failed",
                "tries": tried,
                "seconds": round(time.monotonic() - started, 1),
                "done_at": now,
            }
            if rule:
                state |= {"rule": rule, "amount": amount}
                machine = copy.deepcopy(row.machine_result or {})
                machine[VERSION] = dict(reading) | {"retry": rule, "at": now}
                row.machine_result = machine
                updated.pop("primary_reader", None)  # its values apply again
            if error:
                state["error"] = error
            updated[RETRY_READ] = state
            row.resolution_result = updated
            row.updated_at = datetime.now(UTC)
            await session.commit()
        done += 1
        print(
            f"Read again {str(candidate_id)[:8]}: "
            + (f"{rule} shows {amount}" if rule else "no variant matched"),
            flush=True,
        )
    return done


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
        if await _carry_over(database, candidate_id):
            done += 1  # the v5 reading was already good: no need to zoom
            continue
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


async def _carry_over(database: Database, candidate_id: Any) -> bool:
    """Keep a good earlier reading as this version's, without reading again:
    only photos the earlier version read poorly are read again (zoomed)."""
    async with database.session() as session:
        row = await session.scalar(
            select(ReceiptCandidateRow)
            .where(ReceiptCandidateRow.id == candidate_id)
            .with_for_update()
        )
        if row is None:
            return True
        machine = row.machine_result if isinstance(row.machine_result, dict) else {}
        earlier = machine.get("ppocr_v5")
        if not isinstance(earlier, dict) or earlier.get("error"):
            return False
        if not _readable(str(earlier.get("latin") or earlier.get("text") or "")):
            return False
        machine = copy.deepcopy(machine)
        machine[VERSION] = dict(earlier) | {"carried_from": "ppocr_v5"}
        row.machine_result = machine
        await session.commit()
    return True


async def ppocr_loop(database: Database, storage: FilesystemObjectStorage) -> None:
    """Read five at a time, then the photos to read again; rest 30 seconds
    when caught up."""
    while True:
        try:
            done = await ppocr_pass(database, storage, 5)
            done += await retry_pass(database, storage)
        except Exception as error:  # keep the loop alive
            print(f"PP-OCR pass failed ({type(error).__name__}).", flush=True)
            done = 0
        if done == 0:
            await asyncio.sleep(30)
