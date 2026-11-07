import argparse
import asyncio
import copy
import getpass
import json
import os
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select

from relay.application.account_recovery import AccountRecoveryService
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
from relay.infrastructure.database import Database
from relay.infrastructure.dataset_store import FilesystemDatasetStore
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
        resolution = initial_resolution(row.machine_result, supported_receipt=True)
        resolution["classification_resolved"] = True
        resolution["supported_layout"] = True
        resolution["reopened_by"] = "operator_cli"
        blocking = resolution.get("blocking_fields")
        if isinstance(blocking, list):
            resolution["blocking_fields"] = [
                item for item in blocking if item not in ("classification", "layout")
            ]
        row.receipt_type = ReceiptType.INSTAPAY_TRANSFER_RECEIPT.value
        row.review_status = ReviewStatus.NEEDS_REVIEW.value
        row.resolution_result = resolution
        row.updated_at = now
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
    "sms": "auto-confirmed: matches a bank SMS",
    "readers": "auto-confirmed: model and text reader agree",
}


SMS_FIELDS = ("amount", "currency", "date", "time")


def model_values(
    resolution: dict[str, Any], names: tuple[str, ...] | None = None
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
            and listed[0].get("source") == "model"
        ):
            return None
        value = field.get("value")
        if not isinstance(value, str) or plausible(name, value) != value:
            return None
        values[name] = value
    return values


def readers_agree(resolution: dict[str, Any], values: dict[str, str]) -> bool:
    """The original text reader saw the same reference and amount."""
    fields = resolution.get("fields")
    if not isinstance(fields, dict):
        return False
    for name in ("reference_number", "amount"):
        field = fields.get(name)
        listed = (
            field.get("candidates_before_prefill") if isinstance(field, dict) else None
        )
        seen = {
            item.get("normalized_value")
            for item in (listed if isinstance(listed, list) else [])
            if isinstance(item, dict) and item.get("source") == "receipt"
        }
        if values[name] not in seen:
            return False
    return True


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
            "CONFIRM",
            value,
            AUTO_REASONS[rule],
        )


async def auto_confirm(
    database: Database, storage: FilesystemObjectStorage, settings: Settings
) -> dict[str, int]:
    """Confirm model readings that an independent check backs up: a matching
    bank SMS, or the old text reader reading the same reference and amount.
    Everything else stays in Review. Auto-confirmed receipts are tagged and
    never used for training."""
    from relay.application.errors import Conflict
    from relay.infrastructure.models import UserRow

    counts = {"sms": 0, "readers": 0, "waiting": 0, "failed": 0}
    async with database.session() as session:
        reviewer = await session.scalar(
            select(UserRow.id)
            .where(UserRow.is_active.is_(True))
            .order_by(UserRow.created_at)
            .limit(1)
        )
        rows = (
            await session.execute(
                select(
                    ReceiptCandidateRow.id,
                    ReceiptCandidateRow.device_id,
                    ReceiptCandidateRow.resolution_result,
                ).where(
                    ReceiptCandidateRow.review_status == ReviewStatus.NEEDS_REVIEW.value
                )
            )
        ).all()
    if reviewer is None:
        return counts
    used: set[UUID] = set()  # SMS already spent in this pass
    for candidate_id, device_id, resolution in rows:
        if not isinstance(resolution, dict) or "model_reader" not in resolution:
            continue
        if "auto_confirmed" in resolution:
            continue
        rule = None
        values = model_values(resolution, SMS_FIELDS)
        if values is not None:
            async with database.session() as session:
                event = await build_sms_verification_service(
                    SqlUnitOfWork(session), settings
                ).matches_reading(device_id, candidate_id, values, frozenset(used))
            if event is not None:
                rule = "sms"
                used.add(event)
                # The SMS makes the reference optional: keep it only if the
                # model read a plausible one.
                reference = model_values(resolution, ("reference_number",))
                values |= reference or {}
        if rule is None:
            values = model_values(resolution)
            if values is not None and readers_agree(resolution, values):
                rule = "readers"
        if values is None or rule is None:
            counts["waiting"] += 1
            continue
        try:
            await _tag(database, candidate_id, "auto_confirmed", {"rule": rule})
            # Optional-once-verified fields first: they stop being reviewable
            # once the SMS marker leaves only amount and currency blocking.
            order = ["reference_number", "date", "time", "currency", "amount"]
            for name in order:
                if name not in values:
                    continue
                if rule == "sms" and name == "currency":
                    await _tag(database, candidate_id, "sms_verified", {})
                await _confirm(
                    database, storage, reviewer, candidate_id, name, values[name], rule
                )
            if rule == "sms":
                # Record the verification now so the SMS cannot vouch twice.
                async with database.session() as session:
                    await build_sms_verification_service(
                        SqlUnitOfWork(session), settings
                    ).run()
            counts[rule] += 1
        except Conflict as error:
            counts["failed"] += 1
            print(f"Left {candidate_id} for review: {error}")
    print(
        f"Auto-confirmed {counts['sms']} by bank SMS and {counts['readers']} by "
        f"two readers; {counts['waiting']} wait for review; "
        f"{counts['failed']} failed."
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
            recovery = AccountRecoveryService(uow, passwords)
            if command == "create-admin" and password is not None:
                user = await create_admin(uow, passwords, str(email), password)
                print_codes(await recovery.generate_codes(user.id))
            elif command in {"reset-password", "recovery-codes"} and email:
                found = await uow.users.get_by_email(email)
                if found is None or not found.is_active:
                    raise ValueError("No active account with that email.")
                if command == "reset-password" and password is not None:
                    await uow.users.set_password(
                        found.id, await passwords.hash(password)
                    )
                    await uow.recovery.close_reset_tokens(found.id, datetime.now(UTC))
                    await uow.commit()
                else:
                    print_codes(await recovery.generate_codes(found.id))
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


def print_codes(codes: list[str]) -> None:
    print("Recovery codes (each works once; store them somewhere safe):")
    for code in codes:
        print("  " + code)


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
            "recovery-codes",
            "list-admins",
        ],
    )
    parser.add_argument("--email")
    parser.add_argument("--limit", type=int, choices=range(1, 101))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--include-holdout", action="store_true")
    parser.add_argument("--device", type=UUID)
    parser.add_argument("--dir", type=Path)
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    try:
        email = None
        if args.command in {
            "create-admin",
            "disable-user",
            "reset-password",
            "recovery-codes",
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
