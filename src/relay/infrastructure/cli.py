import argparse
import asyncio
import copy
import getpass
import json
import os
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select

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


def _suggest_missing(resolution: dict[str, Any], values: dict[str, str]) -> int:
    """Offer text readings only for fields nothing has suggested yet."""
    fields = resolution.get("fields")
    if not isinstance(fields, dict):
        return 0
    empty = {
        name: value
        for name, value in values.items()
        if isinstance(fields.get(name), dict) and fields[name].get("value") is None
    }
    applied, _ = apply_suggestions(resolution, empty, TEXT_READER, "text")
    return applied


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
                    ReceiptCandidateRow.reviewed_result.is_(None),
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
            elif rejected and result["receipt"] and not result.get("failed"):
                resolution = _reopen(row, text_recheck.VERSION, now)
                _suggest_missing(resolution, result["values"])
                row.resolution_result = resolution
                counts["reopened"] += 1
            elif (
                rejected
                and not result["receipt"]
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


def _local_day(moment: datetime, settings: Settings) -> str:
    zone = ZoneInfo(settings.receipt_timezone)
    return aware(moment).astimezone(zone).date().isoformat()


async def auto_confirm(
    database: Database, storage: FilesystemObjectStorage, settings: Settings
) -> dict[str, int]:
    """Confirm a receipt when an incoming bank SMS backs it up:

    1. The receipt was sent to the owner (name, and phone on InstaPay
       receipts), when the admin has set who that is.
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
    from relay.infrastructure.text_recheck import VERSION, recipient_matches

    counts = {"sms_day": 0, "waiting": 0, "failed": 0}
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

    # Every receipt of the day, confirmed or waiting, for the repeat check.
    known = [
        (candidate_id, _local_day(folder_at, settings), facts(resolution, machine))
        for candidate_id, _, resolution, machine, _, folder_at in rows
    ]

    def repeated(candidate_id: UUID, day: str, mine: dict[str, Any]) -> bool:
        """Another receipt that day with this amount that could be the same
        transfer: same sender (or one that cannot be read) and no proof by
        two different references."""
        for other_id, other_day, other in known:
            if other_id == candidate_id or other_day != day:
                continue
            if other["amount"] != mine["amount"]:
                continue
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
                return True
        return False

    used: set[UUID] = set()
    for candidate_id, device_id, resolution, machine, status, folder_at in rows:
        if status != ReviewStatus.NEEDS_REVIEW.value:
            continue
        if not isinstance(resolution, dict) or "auto_confirmed" in resolution:
            continue
        text = machine.get(VERSION) if isinstance(machine, dict) else None
        if not isinstance(text, dict) or text.get("payment_request"):
            counts["waiting"] += 1  # wait for the text reading (or a request)
            continue
        if owner is not None:
            name, phone = owner
            found = text.get("found")
            instapay = bool(found.get("brand")) if isinstance(found, dict) else False
            printed = str(text.get("text") or "")
            if not recipient_matches(printed, name, phone, instapay):
                if "recipient_mismatch" not in resolution:
                    await _tag(database, candidate_id, "recipient_mismatch", {})
                counts["waiting"] += 1
                continue
        core = model_values(resolution, ("amount", "currency"))
        if core is None:
            counts["waiting"] += 1
            continue
        day = _local_day(folder_at, settings)
        if repeated(candidate_id, day, facts(resolution, machine)):
            if "possible_repeat" not in resolution:
                await _tag(database, candidate_id, "possible_repeat", {"day": day})
            counts["waiting"] += 1
            continue
        async with database.session() as session:
            sms = await build_sms_verification_service(
                SqlUnitOfWork(session), settings
            ).match_by_day(
                device_id,
                candidate_id,
                core["amount"],
                core["currency"],
                aware(folder_at),
                frozenset(used),
            )
        if sms is None:
            counts["waiting"] += 1
            continue
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
                {"rule": "sms_day", "sms_event_id": str(sms.event_id)},
            )
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
        f"{counts['waiting']} wait for review; {counts['failed']} failed."
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
                await text_pass(
                    database, storage, provider, tuple(settings.ocr_languages), 4
                )
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
    args = parser.parse_args()
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
