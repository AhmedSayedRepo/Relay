import argparse
import asyncio
import copy
import getpass
import json
import os
import random
import re
import sys
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import or_, select

from relay.application.auth import create_admin, disable_user
from relay.application.binaries import BinaryService
from relay.application.errors import ApplicationError
from relay.application.events import EventService
from relay.application.operations import OperationsService
from relay.application.processing import ProcessingService
from relay.application.receipts import InstaPayReceiptParser
from relay.application.training import TrainingDatasetService
from relay.domain.binary_objects import MAX_IMAGE_BYTES, BinaryRejected
from relay.domain.processing import ReceiptType, ReviewStatus
from relay.domain.review_trust import initial_resolution
from relay.infrastructure.agent_models import InstallationRow
from relay.infrastructure.binary_models import BinaryObjectRow
from relay.infrastructure.config import Settings, load_settings
from relay.infrastructure.daily_report import send_daily_report
from relay.infrastructure.database import Database
from relay.infrastructure.dataset_store import FilesystemDatasetStore
from relay.infrastructure.datetime_utils import aware
from relay.infrastructure.event_models import RawEventRow
from relay.infrastructure.image_processing import PillowImagePreprocessor
from relay.infrastructure.instapay_adapter import InstaPayReceiptAdapter
from relay.infrastructure.object_storage import FilesystemObjectStorage
from relay.infrastructure.processing_models import ReceiptCandidateRow
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.infrastructure.security import Argon2Passwords
from relay.infrastructure.sms_verification_repository import (
    build_sms_verification_service,
)
from relay.infrastructure.tesseract_ocr import create_tesseract_provider

IMPORT_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMPORT_FILES = 1000


def read_images(folder: Path) -> list[tuple[str, bytes]]:
    """Read candidate images up front; validation happens in the service."""
    if not folder.is_dir():
        raise ValueError("Import folder does not exist.")
    files = sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in IMPORT_SUFFIXES
    )
    if len(files) > MAX_IMPORT_FILES:
        raise ValueError("Import at most 1000 images at a time.")
    return [
        (
            path.name,
            path.read_bytes() if path.stat().st_size <= MAX_IMAGE_BYTES else b"",
        )
        for path in files
    ]


async def import_images(
    uow: SqlUnitOfWork,
    settings: Settings,
    device_id: UUID,
    images: list[tuple[str, bytes]],
) -> dict[str, int]:
    installation_id = await uow.session.scalar(
        select(InstallationRow.id).where(
            InstallationRow.device_id == device_id,
            InstallationRow.revoked_at.is_(None),
        )
    )
    if installation_id is None:
        raise ValueError("Enrol the device before importing images.")
    service = BinaryService(
        uow,
        FilesystemObjectStorage(settings.object_storage_root),
        retention_days=settings.image_object_retention_days,
        receipt_evidence_enabled=settings.receipt_evidence_enabled,
    )
    counts = {"imported": 0, "duplicate": 0, "rejected": 0}
    for name, data in images:
        try:
            counts[await service.import_image(device_id, installation_id, data)] += 1
        except BinaryRejected:
            counts["rejected"] += 1
            print(f"Rejected: {name}", file=sys.stderr)
    print(
        f"Imported {counts['imported']}, duplicates {counts['duplicate']}, "
        f"rejected {counts['rejected']}. Next: process-pending-images."
    )
    return counts


def _reopen(row: ReceiptCandidateRow, by: str, now: datetime) -> dict[str, Any]:
    """Move a machine-rejected image to Review; every field still needs a
    decision (or an independent check) before it counts."""
    resolution = initial_resolution(row.machine_result, supported_receipt=True)
    resolution["classification_resolved"] = True
    resolution["supported_layout"] = True
    resolution["reopened_by"] = by
    blocking = resolution.get("blocking_fields")
    if isinstance(blocking, list):
        resolution["blocking_fields"] = [
            item for item in blocking if item not in ("classification", "layout")
        ]
    row.receipt_type = ReceiptType.INSTAPAY_TRANSFER_RECEIPT.value
    row.review_status = ReviewStatus.NEEDS_REVIEW.value
    row.resolution_result = resolution
    row.updated_at = now
    return resolution


def _machine_only(row: ReceiptCandidateRow) -> bool:
    """No operator has decided anything on this receipt yet."""
    if row.reviewed_result is not None:
        return False
    fields = (row.resolution_result or {}).get("fields")
    return not any(
        isinstance(field, dict)
        and field.get("trust") in {"OPERATOR_CONFIRMED", "OPERATOR_CORRECTED"}
        for field in (fields.values() if isinstance(fields, dict) else ())
    )


TEXT_READER = "text_reader_v2"


# Every receipt prints an amount with cents or a clock time; the text reader
# turns food photos and paper into hundreds of random letters, but almost
# never into these.
_RECEIPT_NUMBER = re.compile(r"[0-9]+[.,][0-9]{2}|[0-9]{1,2}:[0-9]{2}")


def _receipt_like(result: dict[str, Any]) -> bool:
    """Anything that might be a receipt goes to Review; only a photo with
    no InstaPay name, reference, amount or success words and no printed
    amount or time is hidden. (A photo of a phone screen can read too
    poorly for the full receipt test.)"""
    if result.get("receipt"):
        return True
    found = result.get("found")
    found = found if isinstance(found, dict) else {}
    if any(found.get(key) for key in ("brand", "reference", "amount", "success")):
        return True
    text = re.sub(r"\s", "", str(result.get("text") or ""))
    return _RECEIPT_NUMBER.search(text) is not None


def _clear_non_receipt(row: ReceiptCandidateRow) -> bool:
    """A Review item nobody has touched where the text reader saw no receipt
    and nothing but the model's guess offers an amount or reference (the
    model guesses an amount even for a food photo)."""
    from relay.infrastructure import text_recheck

    read = (row.machine_result or {}).get(text_recheck.VERSION)
    if not isinstance(read, dict) or read.get("payment_request"):
        return False
    if _receipt_like(read) or not _machine_only(row):
        return False
    if _helper_receipt(_helper_read(row.machine_result)):
        return False  # PaddleOCR read a transfer the first reader missed
    fields = (row.resolution_result or {}).get("fields")
    fields = fields if isinstance(fields, dict) else {}
    return not any(
        _read_by_more_than_model(fields.get(name)) for name in ("amount", "reference")
    )


def _shop_invoice(row: ReceiptCandidateRow) -> bool:
    """An untouched Review item that either reader shows is a shop's bill."""
    from relay.infrastructure import text_recheck
    from relay.infrastructure.ppocr_reader import latest

    if not _machine_only(row):
        return False
    machine = row.machine_result if isinstance(row.machine_result, dict) else {}
    texts = [
        str(reading.get("text") or "")
        for reading in (machine.get(text_recheck.VERSION), latest(machine))
        if isinstance(reading, dict)
    ]
    return any(text_recheck.shop_invoice(text) for text in texts if text)


def _not_completed(row: ReceiptCandidateRow) -> bool:
    """An untouched Review item that either reader shows is a transfer that
    did not happen (error message, or the send form), with no success
    wording in either reading."""
    from relay.infrastructure import text_recheck

    if not _machine_only(row):
        return False
    machine = row.machine_result if isinstance(row.machine_result, dict) else {}
    texts = [
        str(reading.get("text") or "")
        for reading in (machine.get(text_recheck.VERSION), _helper_read(machine))
        if isinstance(reading, dict)
    ]
    joined = "\n".join(texts)
    return bool(joined.strip()) and text_recheck.not_completed(joined)


def _no_transfer(row: ReceiptCandidateRow) -> bool:
    """An untouched Review item that both readers read, with no sign of a
    money transfer in either (a photo of goods with a price tag). Waits for
    classic PaddleOCR: Tesseract alone reads phone photos too poorly."""
    from relay.infrastructure import text_recheck

    if not _machine_only(row):
        return False
    machine = row.machine_result if isinstance(row.machine_result, dict) else {}
    tesseract = machine.get(text_recheck.VERSION)
    helper = _helper_read(machine)
    if not isinstance(tesseract, dict) or helper is None:
        return False
    if tesseract.get("payment_request"):
        return False
    return not any(
        text_recheck.transfer_words(str(text))
        for text in (tesseract.get("text") or "", helper["text"])
    )


def _read_by_more_than_model(field: Any) -> bool:
    if not isinstance(field, dict) or not field.get("value"):
        return False
    candidates = field.get("candidates")
    sources = {
        candidate.get("source")
        for candidate in (candidates if isinstance(candidates, list) else ())
        if isinstance(candidate, dict)
    }
    return sources != {"model"}


async def hide_non_receipts(database: Database) -> int:
    """Take food photos, posters and notes out of Review."""
    from relay.infrastructure.set_aside import kept

    hidden = 0
    async with database.session() as session:
        rows = await session.scalars(
            select(ReceiptCandidateRow).where(
                ReceiptCandidateRow.review_status == ReviewStatus.NEEDS_REVIEW.value,
                ReceiptCandidateRow.hidden_reason.is_(None),
            )
        )
        for row in rows:
            if kept(row):
                continue  # a person brought it back from the Rejected page
            if _not_completed(row):
                # An error screen or the send form: no money moved. Rejected,
                # with nothing suggested, so no amount is offered.
                resolution = copy.deepcopy(row.resolution_result or {})
                resolution["state"] = "REJECTED_OR_UNKNOWN_RECEIPT"
                resolution["blocking_fields"] = []
                resolution["rejected_reason"] = "not_completed"
                row.resolution_result = resolution
                row.review_status = ReviewStatus.REJECTED.value
                hidden += 1
            elif _clear_non_receipt(row):
                row.hidden_reason = "not_a_receipt"
                hidden += 1
            elif _shop_invoice(row):
                # Its own reason: unhide_receipt_like must not bring it back
                # (a bill prints amounts, so it looks receipt-like).
                row.hidden_reason = "shop_invoice"
                hidden += 1
            elif _no_transfer(row):
                # Own reason too: a price tag prints an amount.
                row.hidden_reason = "no_transfer"
                hidden += 1
        await session.commit()
    if hidden:
        print(f"Hid {hidden} photos in Review that are not receipts.")
    return hidden


async def unhide_receipt_like(database: Database) -> int:
    """Bring back photos hidden by the stricter rule that came before."""
    from relay.infrastructure import text_recheck

    restored = 0
    async with database.session() as session:
        rows = await session.scalars(
            select(ReceiptCandidateRow).where(
                ReceiptCandidateRow.hidden_reason == "not_a_receipt"
            )
        )
        now = datetime.now(UTC)
        for row in rows:
            read = (row.machine_result or {}).get(text_recheck.VERSION)
            if not isinstance(read, dict) or not _receipt_like(read):
                continue
            row.hidden_reason = None
            if row.review_status == ReviewStatus.REJECTED.value and _machine_only(row):
                row.resolution_result = _reopen(row, text_recheck.VERSION, now)
            restored += 1
        await session.commit()
    if restored:
        print(f"Brought back {restored} hidden photos that may be receipts.")
    return restored


def _suggest_missing(
    resolution: dict[str, Any], values: dict[str, str], reader: str = TEXT_READER
) -> int:
    """Offer text readings only for fields nothing has suggested yet."""
    fields = resolution.get("fields")
    if not isinstance(fields, dict):
        return 0
    empty = {
        name: value
        for name, value in values.items()
        if isinstance(fields.get(name), dict) and fields[name].get("value") is None
    }
    applied, _ = apply_suggestions(resolution, empty, reader, "text")
    return applied


HELPER_READER = "ppocr_helper_v1"


def _helper_read(machine: Any) -> dict[str, Any] | None:
    """The classic PaddleOCR reading as the text reader sees it, or None
    until PP-OCR has read the receipt (or when its reading failed)."""
    from relay.infrastructure.ppocr_reader import latest
    from relay.infrastructure.text_recheck import single

    reading = latest(machine)
    if reading is None:
        return None
    read = single(str(reading["text"]))
    latin = reading.get("latin")
    if isinstance(latin, str) and latin.strip():
        read["latin"] = latin
    return read


def _paddle_amount(text: str, latin: str | None) -> str | None:
    """PaddleOCR's amount: from its Latin recognizer's lines first (digits
    are its strength; the Arabic one can drop a digit), else from both."""
    from relay.infrastructure.text_recheck import (
        VERSION,
        bank_app_amount,
        bare_amount,
        single,
    )

    if latin:
        amount = _text_amount({VERSION: single(latin)})
        if amount is not None:
            return amount
    amount = _text_amount({VERSION: single(text)})
    if amount is not None:
        return amount
    # "571" alone on its line: the red currency beside it was not read.
    # A bank app's transfer screen: "1,096" with the currency elsewhere.
    return bare_amount(latin or "") or bare_amount(text) or bank_app_amount(text)


def _helper_amount(helper: dict[str, Any] | None) -> str | None:
    if helper is None:
        return None
    return _paddle_amount(str(helper.get("text") or ""), helper.get("latin"))


def _helper_receipt(helper: dict[str, Any] | None) -> bool:
    """Classic PaddleOCR clearly read a money transfer."""
    from relay.infrastructure.text_recheck import not_completed, transfer_words

    text = str((helper or {}).get("text") or "")
    return bool(
        helper is not None
        and helper.get("receipt")
        and not helper.get("payment_request")
        and not helper.get("failed")
        and transfer_words(text)
        and not not_completed(text)
    )


async def helper_reopen(database: Database) -> int:
    """Bring back to Review what the first reader rejected or hid as "not a
    receipt" when classic PaddleOCR clearly reads a money transfer (photos of
    a phone screen, dark photos). Receipts a person decided on stay as they
    are. The values PaddleOCR read are offered as suggestions."""
    from relay.infrastructure.ppocr_reader import KEYS as PPOCR_KEYS

    async with database.session() as session:
        ids = (
            await session.scalars(
                select(ReceiptCandidateRow.id).where(
                    or_(
                        ReceiptCandidateRow.review_status
                        == ReviewStatus.REJECTED.value,
                        ReceiptCandidateRow.hidden_reason == "not_a_receipt",
                    ),
                    or_(
                        *(
                            ReceiptCandidateRow.machine_result[key]
                            .as_string()
                            .is_not(None)
                            for key in PPOCR_KEYS
                        )
                    ),
                )
            )
        ).all()
    reopened = 0
    for candidate_id in ids:
        async with database.session() as session:
            row = await session.scalar(
                select(ReceiptCandidateRow)
                .where(ReceiptCandidateRow.id == candidate_id)
                .with_for_update()
            )
            if row is None or not _machine_only(row):
                continue
            resolution_now = row.resolution_result or {}
            if resolution_now.get("rejected_reason") == "payment_request":
                continue
            helper = _helper_read(row.machine_result)
            if not _helper_receipt(helper):
                continue
            assert helper is not None
            now = datetime.now(UTC)
            row.hidden_reason = None
            if row.review_status == ReviewStatus.REJECTED.value:
                resolution = _reopen(row, HELPER_READER, now)
            else:
                resolution = copy.deepcopy(row.resolution_result or {})
            values = {
                name: value
                for name, value in (helper.get("values") or {}).items()
                if name not in ("amount", "currency")
            }
            amount = _helper_amount(helper)
            if amount is not None:
                values |= {"amount": amount, "currency": "EGP"}
            if resolution.get("reopened_by") == HELPER_READER:
                # The rejected first reading is not worth keeping.
                apply_suggestions(resolution, values, HELPER_READER, "text")
            else:
                _suggest_missing(resolution, values, HELPER_READER)
            row.resolution_result = resolution
            row.updated_at = now
            await session.commit()
            reopened += 1
    if reopened:
        print(f"PaddleOCR brought {reopened} rejected receipts back to Review.")
    return reopened


def _paddle_fields(resolution: Any) -> set[str]:
    """Fields whose current suggestion came from classic PaddleOCR."""
    fields = resolution.get("fields") if isinstance(resolution, dict) else None
    found: set[str] = set()
    for name, field in (fields.items() if isinstance(fields, dict) else ()):
        listed = field.get("candidates") if isinstance(field, dict) else None
        if (
            isinstance(listed, list)
            and len(listed) == 1
            and isinstance(listed[0], dict)
            and listed[0].get("adapter") == HELPER_READER
        ):
            found.add(name)
    return found


async def helper_suggest(database: Database) -> int:
    """Classic PaddleOCR is the first reader: its amount, reference, date and
    time replace the other readers' suggestions on receipts waiting for
    review (once per reading; operator decisions stay). The other readers
    fill only what it could not read."""
    from relay.infrastructure.ppocr_reader import KEYS as PPOCR_KEYS
    from relay.infrastructure.ppocr_reader import latest_version

    async with database.session() as session:
        ids = (
            await session.scalars(
                select(ReceiptCandidateRow.id).where(
                    ReceiptCandidateRow.hidden_reason.is_(None),
                    ReceiptCandidateRow.review_status
                    == ReviewStatus.NEEDS_REVIEW.value,
                    or_(
                        *(
                            ReceiptCandidateRow.machine_result[key]
                            .as_string()
                            .is_not(None)
                            for key in PPOCR_KEYS
                        )
                    ),
                )
            )
        ).all()
    filled = 0
    for candidate_id in ids:
        async with database.session() as session:
            row = await session.scalar(
                select(ReceiptCandidateRow)
                .where(ReceiptCandidateRow.id == candidate_id)
                .with_for_update()
            )
            if (
                row is None
                or not isinstance(row.resolution_result, dict)
                or not _machine_only(row)
            ):
                continue
            used = latest_version(row.machine_result)
            if used is None or row.resolution_result.get("primary_reader") == used:
                continue  # applied once per reading
            helper = _helper_read(row.machine_result)
            if helper is None:
                continue
            values = {
                name: value
                for name, value in (helper.get("values") or {}).items()
                if name not in ("amount", "currency")
            }
            amount = _helper_amount(helper)
            if amount is not None:
                values |= {"amount": amount, "currency": "EGP"}
            resolution = copy.deepcopy(row.resolution_result)
            apply_suggestions(resolution, values, HELPER_READER, "text")
            resolution["primary_reader"] = used
            row.resolution_result = resolution
            row.updated_at = datetime.now(UTC)
            await session.commit()
            filled += 1
    if filled:
        print(f"PaddleOCR set the values of {filled} receipts.")
    return filled


async def text_pass(
    database: Database,
    storage: FilesystemObjectStorage,
    provider: Any,
    languages: tuple[str, ...],
    limit: int,
) -> dict[str, int]:
    """Read each new receipt image once as enlarged plain text.

    - A payment request (it moves no money) is rejected and labelled.
    - An image the layout matcher rejected that reads like a receipt goes to
      Review with the reference, date and time offered as suggestions.
    - A rejected image that is not a receipt at all (a photo of food, a
      screenshot) is hidden from Transactions, Review and the dashboard.
    - The text is kept so the bank SMS check can see sender and recipient.
    """
    from relay.infrastructure import text_recheck

    counts = {"read": 0, "reopened": 0, "requests": 0, "hidden": 0}
    async with database.session() as session:
        rows = (
            await session.execute(
                select(ReceiptCandidateRow.id, BinaryObjectRow.storage_key)
                .join(
                    BinaryObjectRow, BinaryObjectRow.id == ReceiptCandidateRow.object_id
                )
                .where(
                    ReceiptCandidateRow.hidden_reason.is_(None),
                    # Not read yet. (Operator decisions are checked per row: an
                    # empty reviewed_result is stored as JSON null, not NULL.)
                    ReceiptCandidateRow.machine_result[text_recheck.VERSION]
                    .as_string()
                    .is_(None),
                    # Machine-rejected of any type: the layout reader can call
                    # a clear InstaPay receipt unknown or failed.
                    ReceiptCandidateRow.review_status.in_(
                        (ReviewStatus.NEEDS_REVIEW.value, ReviewStatus.REJECTED.value)
                    ),
                )
                .order_by(ReceiptCandidateRow.created_at.desc())
            )
        ).all()
    for candidate_id, storage_key in rows:
        if counts["read"] >= limit:
            break
        async with database.session() as session:
            row = await session.scalar(
                select(ReceiptCandidateRow)
                .where(ReceiptCandidateRow.id == candidate_id)
                .with_for_update()
            )
            if (
                row is None
                or not isinstance(row.machine_result, dict)
                or text_recheck.VERSION in row.machine_result
                or not _machine_only(row)
            ):
                continue
            try:
                data = storage.read(storage_key, MAX_IMAGE_BYTES)
                result = await asyncio.to_thread(
                    text_recheck.assess, data, provider, languages
                )
            except Exception as error:  # noqa: BLE001 - one bad image, not the batch
                print(f"Could not read {candidate_id} as text: {type(error).__name__}")
                continue
            now = datetime.now(UTC)
            machine = copy.deepcopy(row.machine_result)
            machine[text_recheck.VERSION] = {
                key: result[key]
                for key in (
                    "receipt",
                    "payment_request",
                    "failed",
                    "found",
                    "sender",
                    "parties",
                    "values",
                    "text",
                )
            } | {"at": now.isoformat()}
            row.machine_result = machine
            row.updated_at = now
            counts["read"] += 1
            rejected = row.review_status == ReviewStatus.REJECTED.value
            if result["payment_request"]:
                resolution = copy.deepcopy(row.resolution_result or {})
                resolution["state"] = "REJECTED_OR_UNKNOWN_RECEIPT"
                resolution["blocking_fields"] = []
                resolution["rejected_reason"] = "payment_request"
                row.resolution_result = resolution
                row.review_status = ReviewStatus.REJECTED.value
                counts["requests"] += 1
            elif rejected and _receipt_like(result) and not result.get("failed"):
                resolution = _reopen(row, text_recheck.VERSION, now)
                _suggest_missing(resolution, result["values"])
                row.resolution_result = resolution
                counts["reopened"] += 1
            elif (
                rejected
                and not _receipt_like(result)
                and row.receipt_type == ReceiptType.UNKNOWN_RECEIPT.value
            ):
                row.hidden_reason = "not_a_receipt"
                counts["hidden"] += 1
            elif isinstance(row.resolution_result, dict):
                resolution = copy.deepcopy(row.resolution_result)
                if _suggest_missing(resolution, result["values"]):
                    row.resolution_result = resolution
            await session.commit()
    if counts["read"]:
        print(
            f"Read {counts['read']} images as text: {counts['reopened']} moved to "
            f"Review, {counts['requests']} payment requests rejected, "
            f"{counts['hidden']} non-receipt photos hidden."
        )
    return counts


async def reopen_rejected(uow: SqlUnitOfWork, device_id: UUID | None) -> int:
    """Send receipts the reader could not classify back to manual review.

    The operator asserts they are InstaPay receipts; every critical field then
    needs an operator decision, exactly like any other receipt under review.
    """
    query = select(ReceiptCandidateRow).where(
        ReceiptCandidateRow.review_status == ReviewStatus.REJECTED.value,
        ReceiptCandidateRow.receipt_type == ReceiptType.UNKNOWN_RECEIPT.value,
    )
    if device_id is not None:
        query = query.where(ReceiptCandidateRow.device_id == device_id)
    now = datetime.now(UTC)
    # An operator's own rejection is kept; only machine rejections reopen.
    rows = [
        row
        for row in await uow.session.scalars(query.with_for_update())
        if row.reviewed_result is None
    ]
    for row in rows:
        _reopen(row, "operator_cli", now)
    await uow.commit()
    print(f"Reopened {len(rows)} receipts for review.")
    return len(rows)


PREFILL_FIELDS = ("amount", "currency", "reference_number", "date", "time")


def apply_suggestions(
    resolution: dict[str, Any], values: dict[str, Any], adapter_name: str, kind: str
) -> tuple[int, int]:
    """Set one machine-only suggestion per field on a resolution (in place).

    Returns (fields set, values skipped as invalid). Operator decisions stay.
    """
    from relay.application.errors import Conflict
    from relay.application.processing import _exception_value

    fields = resolution.get("fields")
    if not isinstance(fields, dict):
        return 0, 0
    adapter = resolution.get("adapter")
    applied = skipped = 0
    for name in PREFILL_FIELDS:
        raw = values.get(name)
        field = fields.get(name)
        if not isinstance(raw, str) or not isinstance(field, dict):
            continue
        if field.get("trust") in {"OPERATOR_CONFIRMED", "OPERATOR_CORRECTED"}:
            continue
        if field.get("reason") == "operator_unavailable":
            continue
        try:
            value = _exception_value(
                name, raw, adapter=adapter if isinstance(adapter, str) else None
            )
        except Conflict:
            skipped += 1
            continue
        previous = field.get("candidates")
        if isinstance(previous, list) and previous:
            field.setdefault("candidates_before_prefill", previous)
        field.update(
            value=value,
            trust="MACHINE_ONLY",
            candidates=[
                {
                    "field": name,
                    "value": value,
                    "normalized_value": value,
                    "original_text": raw,
                    "source": kind,
                    "adapter": adapter_name,
                    "adapter_version": "v1",
                }
            ],
        )
        applied += 1
    return applied, skipped


async def model_suggest(
    database: Database,
    storage: FilesystemObjectStorage,
    reader: Any,
    limit: int,
) -> dict[str, int]:
    """Read receipts waiting for review with the trained model, once each,
    and offer its readings as suggestions."""
    counts = {"receipts": 0, "fields": 0, "failed": 0}
    async with database.session() as session:
        rows = (
            await session.execute(
                select(ReceiptCandidateRow.id, BinaryObjectRow.storage_key)
                .join(
                    BinaryObjectRow, BinaryObjectRow.id == ReceiptCandidateRow.object_id
                )
                .where(
                    ReceiptCandidateRow.review_status == ReviewStatus.NEEDS_REVIEW.value
                )
                .order_by(ReceiptCandidateRow.created_at)
            )
        ).all()
    for candidate_id, storage_key in rows:
        if counts["receipts"] + counts["failed"] >= limit:
            break
        async with database.session() as session:
            row = await session.scalar(
                select(ReceiptCandidateRow)
                .where(ReceiptCandidateRow.id == candidate_id)
                .with_for_update()
            )
            if row is None or not isinstance(row.resolution_result, dict):
                continue
            if "model_reader" in row.resolution_result:
                continue
            resolution = copy.deepcopy(row.resolution_result)
            try:
                values = reader.read(storage.read(storage_key, MAX_IMAGE_BYTES))
            except (
                Exception
            ) as error:  # noqa: BLE001 - one bad image must not stop the batch
                counts["failed"] += 1
                values = {}
                print(f"Could not read {candidate_id}: {type(error).__name__}")
            # Classic PaddleOCR leads: the model fills only what it left.
            values = {
                name: value
                for name, value in values.items()
                if name not in _paddle_fields(resolution)
            }
            applied, _ = apply_suggestions(resolution, values, reader.version, "model")
            resolution["model_reader"] = {
                "version": reader.version,
                "read_at": datetime.now(UTC).isoformat(),
                "fields": applied,
            }
            row.resolution_result = resolution
            row.updated_at = datetime.now(UTC)
            await session.commit()
            counts["receipts"] += 1
            counts["fields"] += applied
    print(
        f"Model read {counts['receipts']} receipts, suggested {counts['fields']} "
        f"fields; {counts['failed']} failed."
    )
    return counts


AUTO_REASONS = {
    "sms_day": "auto-confirmed: a bank SMS with this amount arrived the same day",
}


def model_values(
    resolution: dict[str, Any],
    names: tuple[str, ...] | None = None,
    sources: tuple[str, ...] = ("model", "text"),
) -> dict[str, str] | None:
    """The model's reading of the named key fields (all five by default), if
    each is still a machine-only suggestion from the model and passes the
    sanity checks."""
    from relay.domain.evidence import CRITICAL_FIELDS
    from relay.infrastructure.donut_reader import plausible

    fields = resolution.get("fields")
    if not isinstance(fields, dict):
        return None
    values: dict[str, str] = {}
    for name in names or CRITICAL_FIELDS:
        field = fields.get(name)
        if not isinstance(field, dict) or field.get("trust") != "MACHINE_ONLY":
            return None
        if field.get("reason") == "operator_unavailable":
            return None
        listed = field.get("candidates")
        if not (
            isinstance(listed, list)
            and len(listed) == 1
            and isinstance(listed[0], dict)
            and listed[0].get("source") in sources
        ):
            return None
        value = field.get("value")
        if not isinstance(value, str) or plausible(name, value) != value:
            return None
        values[name] = value
    return values


async def _tag(
    database: Database, candidate_id: UUID, key: str, value: dict[str, Any]
) -> None:
    async with database.session() as session:
        row = await session.scalar(
            select(ReceiptCandidateRow)
            .where(ReceiptCandidateRow.id == candidate_id)
            .with_for_update()
        )
        if row is None or not isinstance(row.resolution_result, dict):
            return
        tagged = copy.deepcopy(row.resolution_result)
        tagged[key] = value | {"at": datetime.now(UTC).isoformat()}
        row.resolution_result = tagged
        row.updated_at = datetime.now(UTC)
        await session.commit()


async def _untag(database: Database, candidate_id: UUID, key: str) -> None:
    async with database.session() as session:
        row = await session.scalar(
            select(ReceiptCandidateRow)
            .where(ReceiptCandidateRow.id == candidate_id)
            .with_for_update()
        )
        if row is None or not isinstance(row.resolution_result, dict):
            return
        if key in row.resolution_result:
            untagged = copy.deepcopy(row.resolution_result)
            untagged.pop(key)
            row.resolution_result = untagged
            row.updated_at = datetime.now(UTC)
            await session.commit()


async def _confirm(
    database: Database,
    storage: FilesystemObjectStorage,
    reviewer: UUID,
    candidate_id: UUID,
    name: str,
    value: str,
    rule: str,
    action: str = "CONFIRM",
) -> None:
    async with database.session() as session:
        uow = SqlUnitOfWork(session)
        candidate = await uow.processing.candidate(candidate_id)
        if candidate is None:
            return
        await ProcessingService(uow, storage).resolve_exception(
            candidate_id,
            reviewer,
            uuid4(),
            candidate.updated_at.isoformat(),
            name,
            action,
            value,
            AUTO_REASONS[rule],
        )


def _verified_fields(values: dict[str, str], machine: Any) -> list[str]:
    from relay.infrastructure.text_recheck import VERSION

    read = machine.get(VERSION) if isinstance(machine, dict) else None
    agreed = read.get("values") if isinstance(read, dict) else None
    agreed = agreed if isinstance(agreed, dict) else {}
    return ["amount", "currency"] + [
        name
        for name in ("reference_number", "date", "time")
        if name in values and agreed.get(name) == values[name]
    ]


def _text_amount(machine: Any) -> str | None:
    """The one amount the text reader saw (None when it saw none or several)."""
    from relay.infrastructure.text_recheck import (
        VERSION,
        amounts,
        hero_amount,
        transfer_from_fee,
        transfer_from_total,
    )

    read = machine.get(VERSION) if isinstance(machine, dict) else None
    if not isinstance(read, dict):
        return None
    printed = str(read.get("text") or "")
    hero = hero_amount(printed)
    if hero is not None:
        return hero
    found = amounts(printed)
    derived = transfer_from_fee(found) or transfer_from_total(printed)
    if derived is not None:
        return derived
    values = read.get("values")
    if isinstance(values, dict) and isinstance(values.get("amount"), str):
        alone: str | None = str(values["amount"])
    else:
        alone = next(iter(found)) if len(found) == 1 else None
    # A lone amount under 20 is the fee (at most 20 EGP) or a misread
    # ("425" as 4.25), never the transfer: leave the amount to the model.
    if alone is not None and float(alone) < FEE_CEILING:
        return None
    return alone


FEE_CEILING = 20.0


def _primary_wait() -> timedelta:
    """How long auto-confirm waits for classic PaddleOCR on a new receipt;
    set where the PaddleOCR reader runs (compose), 0 elsewhere."""
    try:
        return timedelta(minutes=float(os.environ.get("RELAY_PPOCR_WAIT_MINUTES", "0")))
    except ValueError:
        return timedelta(0)


async def _suggest_amount(
    database: Database, candidate_id: UUID, amount: str, reader: str = TEXT_READER
) -> dict[str, Any] | None:
    """Offer the text reader's amount (and EGP) as the suggestion."""
    async with database.session() as session:
        row = await session.scalar(
            select(ReceiptCandidateRow)
            .where(ReceiptCandidateRow.id == candidate_id)
            .with_for_update()
        )
        if row is None or not isinstance(row.resolution_result, dict):
            return None
        resolution = copy.deepcopy(row.resolution_result)
        applied, _ = apply_suggestions(
            resolution, {"amount": amount, "currency": "EGP"}, reader, "text"
        )
        if not applied:
            return None
        row.resolution_result = resolution
        row.updated_at = datetime.now(UTC)
        await session.commit()
        return resolution


def _lost_point(amount: Any) -> str | None:
    """4.25 -> "425.00": an amount under 20 EGP with cents is a separator the
    readers turned into a decimal point, not a transfer. Whole small amounts
    ("5.00") are left alone: they may be real."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return None
    if not 0 < value < Decimal(str(FEE_CEILING)) or value == value.to_integral_value():
        return None
    return f"{value * 100:.2f}"


def _same_amount(first: Any, second: Any) -> bool:
    try:
        return float(first) == float(second)
    except (TypeError, ValueError):
        return False


OLD_RECEIPT_DAYS = 2  # a printed payment date this many days before arrival


def _without_at(value: Any) -> Any:
    return (
        {key: item for key, item in value.items() if key != "at"}
        if isinstance(value, dict)
        else value
    )


def _local_day(moment: datetime, settings: Settings) -> str:
    zone = ZoneInfo(settings.receipt_timezone)
    return aware(moment).astimezone(zone).date().isoformat()


async def auto_confirm(
    database: Database, storage: FilesystemObjectStorage, settings: Settings
) -> dict[str, int]:
    """Confirm a receipt when an incoming bank SMS backs it up:

    1. The receipt was sent to the owner (name, and phone on InstaPay
       receipts), when the admin has set who that is. When the recipient
       cannot be read (a photo of a screen) but nothing shows someone else,
       the SMS below still confirms it, provided no other receipt that day
       shows the same amount.
    2. A free incoming bank SMS with the same amount arrived on the same day
       (Cairo time) as the receipt image. Time of day does not matter; each
       SMS confirms one receipt only.
    3. When the same amount comes more than once that day, receipts from the
       same sender (or an unreadable one) need different references.

    Everything else stays in Review. Auto-confirmed receipts are tagged and
    never used for training."""
    from relay.application.errors import Conflict
    from relay.infrastructure.email_settings import recipient
    from relay.infrastructure.models import UserRow
    from relay.infrastructure.text_recheck import (
        VERSION,
        recipient_check,
        shows_a_phone,
    )

    counts = {"sms_day": 0, "waiting": 0, "failed": 0, "copies": 0, "old": 0}
    wait = _primary_wait()
    async with database.session() as session:
        reviewer = await session.scalar(
            select(UserRow.id)
            .where(UserRow.is_active.is_(True), UserRow.role == "ADMIN")
            .order_by(UserRow.created_at)
            .limit(1)
        )
        owner = await recipient(session)
        rows = (
            await session.execute(
                select(
                    ReceiptCandidateRow.id,
                    ReceiptCandidateRow.device_id,
                    ReceiptCandidateRow.resolution_result,
                    ReceiptCandidateRow.machine_result,
                    ReceiptCandidateRow.review_status,
                    RawEventRow.occurred_at,
                    ReceiptCandidateRow.created_at,
                )
                .join(RawEventRow, RawEventRow.id == ReceiptCandidateRow.raw_event_id)
                .where(
                    ReceiptCandidateRow.hidden_reason.is_(None),
                    ReceiptCandidateRow.review_status != ReviewStatus.REJECTED.value,
                )
                .order_by(RawEventRow.occurred_at, ReceiptCandidateRow.id)
            )
        ).all()
    if reviewer is None:
        return counts

    def facts(resolution: Any, machine: Any) -> dict[str, Any]:
        resolution = resolution if isinstance(resolution, dict) else {}
        fields = resolution.get("fields")
        values = {
            name: field.get("value")
            for name, field in (fields.items() if isinstance(fields, dict) else ())
            if isinstance(field, dict)
        }
        text = machine.get(VERSION) if isinstance(machine, dict) else None
        return {
            "amount": values.get("amount"),
            "reference": values.get("reference_number"),
            "sender": text.get("sender") if isinstance(text, dict) else None,
        }

    status_of = {row[0]: row[4] for row in rows}
    arrived = {row[0]: row[6] for row in rows}

    # Every receipt of the day, confirmed or waiting, for the repeat check.
    known = [
        (candidate_id, _local_day(folder_at, settings), facts(resolution, machine))
        for candidate_id, _, resolution, machine, _, folder_at, _ in rows
    ]

    def repeated(
        candidate_id: UUID, day: str, mine: dict[str, Any]
    ) -> tuple[str, UUID] | None:
        """Another receipt that day with this amount that could be the same
        transfer: "reference" when both show the same reference, "sender"
        when the sender is the same (or unreadable) and the references do not
        prove two transfers; None otherwise. Returned with the other receipt."""
        for other_id, other_day, other in known:
            if other_id == candidate_id or other_day != day:
                continue
            if other["amount"] != mine["amount"]:
                continue
            if (
                mine["reference"] is not None
                and mine["reference"] == other["reference"]
            ):
                return "reference", other_id
            same_sender = (
                mine["sender"] is None
                or other["sender"] is None
                or mine["sender"] == other["sender"]
            )
            different_refs = (
                mine["reference"] is not None
                and other["reference"] is not None
                and mine["reference"] != other["reference"]
            )
            if same_sender and not different_refs:
                return "sender", other_id
        return None

    first_seen = {
        candidate_id: aware(folder_at)
        for candidate_id, _, _, _, _, folder_at, _ in rows
    }

    def stale(
        candidate_id: UUID,
        folder_at: datetime,
        resolution: dict[str, Any],
        machine: Any,
    ) -> dict[str, Any] | None:
        """An old receipt shown again: its reference was on a receipt that
        reached a phone on an earlier day (same-day copies are hidden below),
        or its printed payment date is OLD_RECEIPT_DAYS or more before it
        reached the phone. Either can be a receipt reused to claim a payment
        twice, so a person checks it."""
        reference = facts(resolution, machine)["reference"]
        day = _local_day(folder_at, settings)
        if reference:
            for other_id, other_day, other in known:
                if (
                    other_id != candidate_id
                    and other["reference"] == reference
                    and other_day < day
                ):
                    return {
                        "kind": "reference",
                        "with": str(other_id),
                        "first_seen": first_seen[other_id].isoformat(),
                    }
        printed = (model_values(resolution, ("date",), ("text",)) or {}).get("date")
        try:
            paid = date.fromisoformat(printed) if printed else None
        except ValueError:
            paid = None
        if paid is None:
            return None
        if (date.fromisoformat(day) - paid).days >= OLD_RECEIPT_DAYS:
            return {"kind": "date", "paid_on": paid.isoformat()}
        return None

    def alone(candidate_id: UUID, day: str, amount: Any) -> bool:
        """No other receipt that day, waiting or confirmed, shows this amount."""
        return not any(
            other_id != candidate_id
            and other_day == day
            and other["amount"] is not None
            and _same_amount(other["amount"], amount)
            for other_id, other_day, other in known
        )

    used: set[UUID] = set()
    for (
        candidate_id,
        device_id,
        resolution,
        machine,
        status,
        folder_at,
        created_at,
    ) in rows:
        if status != ReviewStatus.NEEDS_REVIEW.value:
            continue
        if not isinstance(resolution, dict) or "auto_confirmed" in resolution:
            continue
        text = machine.get(VERSION) if isinstance(machine, dict) else None
        if not isinstance(text, dict) or text.get("payment_request"):
            counts["waiting"] += 1  # wait for the text reading (or a request)
            continue
        helper = _helper_read(machine)
        if (
            helper is None
            and created_at is not None
            and (datetime.now(UTC) - aware(created_at) < wait)
        ):
            # Give classic PaddleOCR, the first reader, time to read it; after
            # that, Tesseract alone decides (PaddleOCR may be down).
            counts["waiting"] += 1
            continue
        old = stale(candidate_id, folder_at, resolution, machine)
        if old is not None:
            # Never confirmed by itself: it waits in Review with a red warning
            # (dashboard, email, and the Copies list on Transactions).
            if _without_at(resolution.get("old_receipt")) != old:
                await _tag(database, candidate_id, "old_receipt", old)
            counts["old"] += 1
            continue
        if resolution.get("old_receipt") is not None:
            await _untag(database, candidate_id, "old_receipt")
        unclear = False
        if owner is not None:
            name, phone = owner
            found = text.get("found")
            instapay = bool(found.get("brand")) if isinstance(found, dict) else False
            printed = str(text.get("text") or "")
            verdict = recipient_check(printed, name, phone, instapay)
            if helper is not None:
                # Classic PaddleOCR is the first reader: its verdict stands;
                # Tesseract's counts only when PaddleOCR could not read the
                # recipient.
                first = recipient_check(
                    str(helper["text"]),
                    name,
                    phone,
                    instapay or bool((helper.get("found") or {}).get("brand")),
                )
                if first != "unclear":
                    verdict = first
            tagged = resolution.get("recipient_mismatch")
            if verdict != "match":
                if not isinstance(tagged, dict) or tagged.get("kind") != verdict:
                    await _tag(
                        database, candidate_id, "recipient_mismatch", {"kind": verdict}
                    )
                if (
                    verdict != "unclear"
                    or shows_a_phone(printed)
                    or (helper is not None and shows_a_phone(str(helper["text"])))
                ):
                    # Someone else's, or a readable phone that is not the
                    # owner's: a person decides.
                    counts["waiting"] += 1
                    continue
                # Unreadable, not someone else: the bank SMS can still prove
                # the money arrived (checked below, more strictly).
                unclear = True
            elif tagged is not None:
                await _untag(database, candidate_id, "recipient_mismatch")
        tesseract_amount, helper_amount = _text_amount(machine), _helper_amount(helper)
        # Shown amount: classic PaddleOCR's when it read one (99% right on
        # confirmed receipts against Tesseract's 80%), else Tesseract's. When
        # they disagree, the bank SMS below decides between them.
        text_amount: str | None
        if helper_amount is not None:
            text_amount, amount_reader = helper_amount, HELPER_READER
        else:
            text_amount, amount_reader = tesseract_amount, TEXT_READER
        read_amount = model_values(resolution, ("amount",))
        if text_amount and (read_amount or {}).get("amount") != text_amount:
            # The trained model sometimes drops a digit ("1,140" -> 114). The text
            # reader only offers an amount both of its readings agree on, so its
            # amount leads; the SMS check below then decides.
            fixed = await _suggest_amount(
                database, candidate_id, text_amount, amount_reader
            )
            if fixed is not None:
                resolution = fixed
        core = model_values(resolution, ("amount", "currency"))
        if core is None:
            counts["waiting"] += 1
            continue
        # "425" read as 4.25: try the amount without the lost separator. Only
        # the SMS check below can make it stick.
        read_as = core["amount"]
        scaled = _lost_point(read_as)
        if scaled is not None:
            core = core | {"amount": scaled}
        day = _local_day(folder_at, settings)
        repeat = (
            None
            if isinstance(resolution.get("repeat_cleared"), dict)
            else repeated(candidate_id, day, facts(resolution, machine))
        )
        previous = resolution.get("possible_repeat")
        if repeat is not None and repeat[0] == "reference":
            other_id = repeat[1]
            other_arrived = arrived.get(other_id)
            earlier = other_arrived is not None and (
                (other_arrived, str(other_id)) < (created_at, str(candidate_id))
            )
            if not earlier and status_of.get(other_id) != (
                ReviewStatus.AUTO_ACCEPTED.value
            ):
                # The first of the pair goes on as usual; the later one (the
                # copy) waits in Review, shown side by side with this one.
                repeat = None
        if repeat is not None:
            kind, other_id = repeat
            tag = {"day": day, "kind": kind, "with": str(other_id)}
            if _without_at(previous) != tag:
                await _tag(database, candidate_id, "possible_repeat", tag)
            counts["waiting"] += 1
            continue
        if previous is not None:
            await _untag(database, candidate_id, "possible_repeat")
        if (unclear or scaled) and not alone(candidate_id, day, core["amount"]):
            counts["waiting"] += 1  # two receipts could claim the one SMS
            continue
        # The shown amount first, then what the other reader saw (each only
        # if no other receipt that day shows it: one SMS, one receipt).
        tries: list[tuple[str, str | None, str | None]] = [
            (core["amount"], scaled, None)
        ]
        for other, other_reader in (
            (tesseract_amount, TEXT_READER),
            (helper_amount, HELPER_READER),
        ):
            if (
                other is not None
                and not any(_same_amount(other, tried) for tried, _, _ in tries)
                and alone(candidate_id, day, other)
            ):
                tries.append((other, None, other_reader))
        sms = None
        amount, lifted, reader = tries[0]
        for attempt in tries:
            amount, lifted, reader = attempt
            async with database.session() as session:
                sms = await build_sms_verification_service(
                    SqlUnitOfWork(session), settings
                ).match_by_day(
                    device_id,
                    candidate_id,
                    amount,
                    core["currency"],
                    aware(folder_at),
                    frozenset(used),
                )
            if sms is not None:
                break
        if sms is None:
            counts["waiting"] += 1
            continue
        if lifted is not None:
            # The SMS proves the corrected amount: offer it before confirming.
            await _suggest_amount(database, candidate_id, lifted)
        elif reader is not None:
            # The SMS proves the other reader's amount.
            await _suggest_amount(database, candidate_id, amount, reader)
            core = core | {"amount": amount}
            read_as, scaled = amount, None
        values = dict(core)
        # The SMS makes the rest optional: keep what was read plausibly.
        for name in ("reference_number", "date", "time"):
            values |= model_values(resolution, (name,)) or {}
        try:
            used.add(sms.event_id)
            async with database.session() as session:
                await build_sms_verification_service(
                    SqlUnitOfWork(session), settings
                ).record_day_match(candidate_id, sms, reviewer)
            await _tag(
                database,
                candidate_id,
                "auto_confirmed",
                {
                    "rule": "sms_day",
                    "sms_event_id": str(sms.event_id),
                    # Fields fit to train on: the amount the bank SMS proves,
                    # plus readings both text passes agreed on independently.
                    "trained_fields": _verified_fields(values, machine),
                }
                | ({"recipient": "unclear"} if unclear else {})
                | ({"amount_read_as": read_as} if scaled else {}),
            )
            if unclear:
                await _untag(database, candidate_id, "recipient_mismatch")
            # Optional-once-verified fields first: they stop being reviewable
            # once the SMS marker leaves only amount and currency blocking.
            for name in ["reference_number", "date", "time", "currency", "amount"]:
                if name not in values:
                    continue
                if name == "currency":
                    await _tag(database, candidate_id, "sms_verified", {})
                await _confirm(
                    database,
                    storage,
                    reviewer,
                    candidate_id,
                    name,
                    values[name],
                    "sms_day",
                )
            counts["sms_day"] += 1
        except Conflict as error:
            counts["failed"] += 1
            print(f"Left {candidate_id} for review: {error}")
    print(
        f"Auto-confirmed {counts['sms_day']} by a same-day bank SMS; "
        f"{counts['waiting']} wait for review; {counts['old']} old receipts "
        f"shown again; {counts['failed']} failed."
    )
    return counts


async def prefill_suggestions(
    uow: SqlUnitOfWork, document: dict[str, Any]
) -> dict[str, int]:
    """Attach suggested values (keyed by image SHA-256) to receipts under review.

    Suggestions are machine-only: every field still needs an operator to
    Confirm or Correct it, and fields an operator already decided are kept.
    """
    receipts = document.get("receipts")
    if not isinstance(receipts, dict):
        raise ValueError("Prefill file needs a receipts object.")
    source = str(document.get("source") or "prefill")[:64]
    rows = await uow.session.execute(
        select(ReceiptCandidateRow, BinaryObjectRow.content_hash)
        .join(BinaryObjectRow, BinaryObjectRow.id == ReceiptCandidateRow.object_id)
        .where(
            ReceiptCandidateRow.review_status == ReviewStatus.NEEDS_REVIEW.value,
            BinaryObjectRow.content_hash.in_(list(receipts)),
        )
        .with_for_update(of=ReceiptCandidateRow)
    )
    counts = {"receipts": 0, "fields": 0, "skipped": 0}
    now = datetime.now(UTC)
    for row, digest in rows:
        # Work on a copy: editing the loaded JSON in place would make the old
        # and new values compare equal, and SQLAlchemy would skip the UPDATE.
        resolution = copy.deepcopy(row.resolution_result)
        values = receipts.get(digest)
        if not isinstance(resolution, dict) or not isinstance(values, dict):
            continue
        fields, skipped = apply_suggestions(resolution, values, source, "prefill")
        counts["skipped"] += skipped
        counts["fields"] += fields
        changed = fields > 0
        if changed:
            row.resolution_result = resolution
            row.updated_at = now
            counts["receipts"] += 1
    await uow.commit()
    print(
        f"Prefilled {counts['fields']} fields on {counts['receipts']} receipts; "
        f"skipped {counts['skipped']} invalid values."
    )
    return counts


BULK_REASON = "bulk-confirmed from assistant readings"


async def confirm_suggestions(
    database: Database,
    storage: FilesystemObjectStorage,
    reviewer_id: UUID,
    document: dict[str, Any],
    public_url: str | None = None,
    spot_checks: int = 15,
) -> dict[str, int]:
    """Decide every remaining field from a reviewed readings file, as the
    given operator: Confirm/Correct a reading, Unavailable where none."""
    from relay.application.errors import Conflict
    from relay.application.processing import _exception_value

    receipts = document.get("receipts")
    if not isinstance(receipts, dict):
        raise ValueError("File needs a receipts object.")
    async with database.session() as session:
        rows = (
            await session.execute(
                select(ReceiptCandidateRow.id, BinaryObjectRow.content_hash)
                .join(
                    BinaryObjectRow, BinaryObjectRow.id == ReceiptCandidateRow.object_id
                )
                .where(
                    ReceiptCandidateRow.review_status
                    == ReviewStatus.NEEDS_REVIEW.value,
                    BinaryObjectRow.content_hash.in_(list(receipts)),
                )
            )
        ).all()
    counts = {"CONFIRM": 0, "CORRECT": 0, "UNAVAILABLE": 0, "receipts": 0, "failed": 0}
    finished: list[tuple[str, str]] = []
    for candidate_id, digest in rows:
        values = receipts.get(digest)
        if not isinstance(values, dict):
            continue
        try:
            for _ in range(len(PREFILL_FIELDS) + 2):
                async with database.session() as session:
                    service = ProcessingService(SqlUnitOfWork(session), storage)
                    candidate = await SqlUnitOfWork(session).processing.candidate(
                        candidate_id
                    )
                    if candidate is None:
                        break
                    resolution = candidate.resolution_result or {}
                    fields = resolution.get("fields")
                    blocking = resolution.get("blocking_fields")
                    if not isinstance(fields, dict) or not isinstance(blocking, list):
                        break
                    todo = [
                        name
                        for name in blocking
                        if isinstance(name, str)
                        and isinstance(field := fields.get(name), dict)
                        and field.get("reason") != "operator_unavailable"
                        and field.get("trust")
                        not in {"OPERATOR_CONFIRMED", "OPERATOR_CORRECTED"}
                    ]
                    if not todo:
                        break
                    name = todo[0]
                    field = fields[name]
                    if not isinstance(field, dict):
                        break
                    raw = values.get(name)
                    action, value = "UNAVAILABLE", None
                    if isinstance(raw, str) and raw:
                        adapter = resolution.get("adapter")
                        value = _exception_value(
                            name,
                            raw,
                            adapter=adapter if isinstance(adapter, str) else None,
                        )
                        listed = field.get("candidates")
                        offered = [field.get("value")] + [
                            item.get("normalized_value")
                            for item in (listed if isinstance(listed, list) else [])
                            if isinstance(item, dict)
                        ]
                        action = "CONFIRM" if value in offered else "CORRECT"
                    await service.resolve_exception(
                        candidate_id,
                        reviewer_id,
                        uuid4(),
                        candidate.updated_at.isoformat(),
                        name,
                        action,
                        value,
                        BULK_REASON,
                    )
                    counts[action] += 1
            counts["receipts"] += 1
            amount = values.get("amount") or "?"
            reference = values.get("reference_number") or "no reference"
            finished.append((str(candidate_id), f"{amount} EGP, {reference}"))
        except Conflict as error:
            counts["failed"] += 1
            print(f"Skipped {candidate_id}: {error}")
    print(
        f"Decided {counts['receipts']} receipts: {counts['CONFIRM']} confirmed, "
        f"{counts['CORRECT']} corrected, {counts['UNAVAILABLE']} unavailable; "
        f"{counts['failed']} skipped."
    )
    if finished:
        print(f"Spot-check these {min(spot_checks, len(finished))} in the portal:")
        base = (public_url or "").rstrip("/")
        for identifier, summary in random.sample(
            finished, min(spot_checks, len(finished))
        ):
            link = f"{base}/#/transactions/{identifier}" if base else identifier
            print(f"  {summary}  {link}")
    return counts


async def run(
    command: str,
    email: str | None,
    password: str | None,
    limit: int | None = None,
    *,
    out: Path | None = None,
    include_holdout: bool = False,
    device_id: UUID | None = None,
    images: list[tuple[str, bytes]] | None = None,
    prefill: dict[str, Any] | None = None,
) -> None:
    settings = load_settings()
    database = Database(settings)
    try:
        async with database.session() as session:
            uow = SqlUnitOfWork(session)
            passwords = Argon2Passwords()
            if command == "create-admin" and password is not None:
                await create_admin(uow, passwords, str(email), password)
            elif command == "reset-password" and email:
                found = await uow.users.get_by_email(email)
                if found is None or not found.is_active:
                    raise ValueError("No active account with that email.")
                if password is not None:
                    await uow.users.set_password(
                        found.id, await passwords.hash(password)
                    )
                    await uow.recovery.close_reset_tokens(found.id, datetime.now(UTC))
                    await uow.commit()
            elif command == "list-admins":
                for admin in await uow.users.list_active():
                    print(admin.email)
            elif command == "disable-user" and email is not None:
                await disable_user(uow, email)
            elif command == "purge-expired-events":
                await OperationsService(
                    uow,
                    audit_retention_days=settings.operator_audit_retention_days,
                ).purge_expired_audit()
                await ProcessingService(
                    uow,
                    FilesystemObjectStorage(settings.object_storage_root),
                ).purge_expired()
                await BinaryService(
                    uow,
                    FilesystemObjectStorage(settings.object_storage_root),
                    retention_days=settings.image_object_retention_days,
                    session_hours=settings.image_upload_session_hours,
                    max_active_uploads=settings.image_max_active_uploads,
                    uploads_per_hour=settings.image_uploads_per_hour,
                    stored_bytes_per_device=settings.image_stored_bytes_per_device,
                ).purge_expired()
                await EventService(
                    uow, retention_days=settings.raw_event_retention_days
                ).purge_expired()
            elif command == "process-pending-images":
                provider = create_tesseract_provider(
                    settings.ocr_tesseract_executable,
                    settings.ocr_tessdata_dir,
                )
                await ProcessingService(
                    uow,
                    FilesystemObjectStorage(settings.object_storage_root),
                    PillowImagePreprocessor(),
                    provider,
                    InstaPayReceiptParser(),
                    ocr_timeout_seconds=settings.ocr_timeout_seconds,
                    stale_job_minutes=settings.processing_stale_minutes,
                    evidence_extractor=InstaPayReceiptAdapter(provider),
                ).process_pending(limit=limit or settings.processing_batch_limit)
            elif command == "reopen-rejected":
                await reopen_rejected(uow, device_id)
            elif command == "model-suggest":
                from relay.infrastructure.donut_reader import DonutReader

                model_dir = Path(os.environ.get("RELAY_MODEL_DIR", "/model"))
                if not (model_dir / "reader-config.json").exists():
                    print("No trained model found; nothing to do.")
                else:
                    await model_suggest(
                        database,
                        FilesystemObjectStorage(settings.object_storage_root),
                        DonutReader(
                            model_dir,
                            Path(
                                os.environ.get(
                                    "RELAY_BASE_MODEL_DIR", "/opt/donut-base"
                                )
                            ),
                        ),
                        limit or 20,
                    )
                    await auto_confirm(
                        database,
                        FilesystemObjectStorage(settings.object_storage_root),
                        settings,
                    )
            elif command == "prefill-suggestions" and prefill is not None:
                await prefill_suggestions(uow, prefill)
            elif command == "confirm-suggestions" and prefill is not None and email:
                reviewer = await uow.users.get_by_email(email)
                if reviewer is None or not reviewer.is_active:
                    raise ValueError("No active account with that email.")
                await confirm_suggestions(
                    database,
                    FilesystemObjectStorage(settings.object_storage_root),
                    reviewer.id,
                    prefill,
                    settings.operator_public_url,
                )
            elif command == "import-images" and device_id is not None:
                await import_images(uow, settings, device_id, images or [])
            elif command == "verify-sms":
                await build_sms_verification_service(uow, settings).run()
            elif command in {"capture-training", "export-training"}:
                training = TrainingDatasetService(
                    uow,
                    FilesystemObjectStorage(settings.object_storage_root),
                    FilesystemDatasetStore(settings.training_dataset_root),
                )
                if command == "capture-training":
                    await training.capture(limit=limit or 100)
                elif out is not None:
                    await training.export(out, include_holdout=include_holdout)
    finally:
        await database.close()


async def reader_loop(settings: Settings, interval_seconds: int = 60) -> None:
    """Keep the receipt model loaded and, every minute, read new receipts,
    match bank SMS and auto-confirm. One pass at a time."""
    from relay.infrastructure.copies import unhide_copies

    storage = FilesystemObjectStorage(settings.object_storage_root)
    model_dir = Path(os.environ.get("RELAY_MODEL_DIR", "/model"))
    reader: Any = None
    if (model_dir / "reader-config.json").exists():
        from relay.infrastructure.donut_reader import DonutReader

        reader = DonutReader(
            model_dir, Path(os.environ.get("RELAY_BASE_MODEL_DIR", "/opt/donut-base"))
        )
    provider = create_tesseract_provider(
        settings.ocr_tesseract_executable, settings.ocr_tessdata_dir
    )
    database = Database(settings)
    try:
        while True:
            started = time.monotonic()
            try:
                async with database.session() as session:
                    await ProcessingService(
                        SqlUnitOfWork(session),
                        storage,
                        PillowImagePreprocessor(),
                        provider,
                        InstaPayReceiptParser(),
                        ocr_timeout_seconds=settings.ocr_timeout_seconds,
                        stale_job_minutes=settings.processing_stale_minutes,
                        evidence_extractor=InstaPayReceiptAdapter(provider),
                    ).process_pending(limit=settings.processing_batch_limit)
                await unhide_receipt_like(database)
                await text_pass(
                    database, storage, provider, tuple(settings.ocr_languages), 4
                )
                await helper_reopen(database)
                await hide_non_receipts(database)
                await unhide_copies(database)
                await helper_suggest(database)
                async with database.session() as session:
                    await build_sms_verification_service(
                        SqlUnitOfWork(session), settings
                    ).run()
                if reader is not None:
                    await model_suggest(database, storage, reader, 20)
                await auto_confirm(database, storage, settings)
            except Exception as error:  # keep the loop alive; no details logged
                print(f"Reader pass failed ({type(error).__name__}).", flush=True)
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(5.0, interval_seconds - elapsed))
    finally:
        await database.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Relay operator maintenance")
    parser.add_argument(
        "command",
        choices=[
            "create-admin",
            "disable-user",
            "purge-expired-events",
            "process-pending-images",
            "capture-training",
            "export-training",
            "verify-sms",
            "import-images",
            "reopen-rejected",
            "prefill-suggestions",
            "confirm-suggestions",
            "model-suggest",
            "reset-password",
            "list-admins",
            "daily-report",
            "reader-loop",
            "eval-reader",
            "reconcile",
            "vl-loop",
            "ppocr-loop",
            "check-alerts",
        ],
    )
    parser.add_argument("--email")
    parser.add_argument("--limit", type=int, choices=range(1, 101))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--include-holdout", action="store_true")
    parser.add_argument("--device", type=UUID)
    parser.add_argument("--dir", type=Path)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--day", type=date.fromisoformat)
    args = parser.parse_args()
    if args.command == "check-alerts":
        from relay.infrastructure.alerts import check_alerts

        try:
            sent = asyncio.run(check_alerts(load_settings()))
            print(f"Emailed {sent} new warning(s).")
            return 0
        except Exception as error:
            print(f"Operation failed ({type(error).__name__}).", file=sys.stderr)
            return 1
    if args.command == "ppocr-loop":
        from relay.infrastructure.ppocr_reader import ppocr_loop

        settings = load_settings()

        async def text_lines() -> None:
            database = Database(settings)
            try:
                await ppocr_loop(
                    database, FilesystemObjectStorage(settings.object_storage_root)
                )
            finally:
                await database.close()

        asyncio.run(text_lines())
        return 0
    if args.command == "vl-loop":
        from relay.infrastructure.vl_reader import vl_loop

        settings = load_settings()

        async def model_reader() -> None:
            database = Database(settings)
            try:
                await vl_loop(
                    database,
                    FilesystemObjectStorage(settings.object_storage_root),
                    os.environ.get("RELAY_VL_URL", "http://ocr:8080"),
                )
            finally:
                await database.close()

        asyncio.run(model_reader())
        return 0
    if args.command == "reconcile":
        from relay.infrastructure.reconcile import reconcile

        settings = load_settings()
        zone = ZoneInfo(settings.receipt_timezone)
        day = args.day or (datetime.now(zone) - timedelta(days=1)).date()
        try:
            print("\n".join(asyncio.run(reconcile(settings, day))))
            return 0
        except Exception as error:
            print(f"Operation failed ({type(error).__name__}).", file=sys.stderr)
            return 1
    if args.command == "eval-reader":
        from relay.infrastructure.reader_eval import evaluate_reader

        try:
            return evaluate_reader(load_settings(), args.file, args.force)
        except Exception as error:
            print(f"Operation failed ({type(error).__name__}).", file=sys.stderr)
            return 1
    if args.command in {"daily-report", "reader-loop"}:
        try:
            if args.command == "reader-loop":
                asyncio.run(reader_loop(load_settings()))
            else:
                asyncio.run(send_daily_report(load_settings(), force=args.force))
            return 0
        except Exception as error:
            print(f"Operation failed ({type(error).__name__}).", file=sys.stderr)
            return 1
    try:
        email = None
        if args.command in {
            "create-admin",
            "disable-user",
            "reset-password",
            "confirm-suggestions",
        }:
            if args.email is None:
                raise ValueError("Email is required.")
            email = str(TypeAdapter(EmailStr).validate_python(args.email)).lower()
            if len(email) > 254:
                raise ValueError("Email is too long.")
        elif args.email is not None:
            raise ValueError("Email is not accepted for this command.")
        limited = {"process-pending-images", "capture-training", "model-suggest"}
        if args.command not in limited and args.limit is not None:
            raise ValueError("Limit is accepted only for bounded batch commands.")
        if args.command == "export-training":
            if args.out is None:
                raise ValueError("Export requires --out.")
        elif args.out is not None or args.include_holdout:
            raise ValueError("Export options are accepted only for export-training.")
        if args.command == "import-images":
            if args.device is None or args.dir is None:
                raise ValueError("Import requires --device and --dir.")
        elif args.dir is not None or (
            args.device is not None and args.command != "reopen-rejected"
        ):
            raise ValueError("Device and folder are accepted only for import-images.")
        file_commands = {"prefill-suggestions", "confirm-suggestions"}
        if args.file is not None and args.command not in file_commands:
            raise ValueError("--file is accepted only for suggestion commands.")
        password = None
        if args.command in {"create-admin", "reset-password"}:
            if not sys.stdin.isatty():
                raise ValueError("Use an interactive terminal for password entry.")
            password = getpass.getpass("Password (12-1024 characters): ")
            confirmation = getpass.getpass("Confirm password: ")
            if password != confirmation:
                raise ValueError("Passwords do not match.")
            if not 12 <= len(password) <= 1024:
                raise ValueError("Password must contain 12 to 1024 characters.")
        if args.command in limited:
            asyncio.run(run(args.command, email, password, args.limit))
        elif args.command == "import-images":
            if not isinstance(args.dir, Path):
                raise ValueError("Import requires --dir.")
            images = read_images(args.dir)
            asyncio.run(
                run(args.command, None, None, device_id=args.device, images=images)
            )
        elif args.command == "export-training":
            asyncio.run(
                run(
                    args.command,
                    None,
                    None,
                    out=args.out,
                    include_holdout=args.include_holdout,
                )
            )
        elif args.command == "reopen-rejected":
            asyncio.run(run(args.command, None, None, device_id=args.device))
        elif args.command in file_commands:
            if not isinstance(args.file, Path):
                raise ValueError("Prefill requires --file.")
            document = json.loads(args.file.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ValueError("Prefill file must be a JSON object.")
            asyncio.run(run(args.command, email, None, prefill=document))
        else:
            asyncio.run(run(args.command, email, password))
    except (ValidationError, ValueError, ApplicationError, RuntimeError):
        print(
            "Operation failed. Check input, configuration, and account state.",
            file=sys.stderr,
        )
        return 1
    except Exception as error:
        # Driver exceptions can contain database passwords or SQL parameters,
        # so only the error type is shown.
        print(
            "Operation failed. Check database availability and migrations. "
            f"({type(error).__name__})",
            file=sys.stderr,
        )
        return 1
    print("Operation completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
