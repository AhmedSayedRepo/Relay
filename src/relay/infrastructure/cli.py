import argparse
import asyncio
import getpass
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

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
from relay.infrastructure.agent_models import InstallationRow
from relay.infrastructure.config import Settings, load_settings
from relay.infrastructure.database import Database
from relay.infrastructure.dataset_store import FilesystemDatasetStore
from relay.infrastructure.image_processing import PillowImagePreprocessor
from relay.infrastructure.instapay_adapter import InstaPayReceiptAdapter
from relay.infrastructure.object_storage import FilesystemObjectStorage
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
    args = parser.parse_args()
    try:
        email = None
        if args.command in {
            "create-admin",
            "disable-user",
            "reset-password",
            "recovery-codes",
        }:
            if args.email is None:
                raise ValueError("Email is required.")
            email = str(TypeAdapter(EmailStr).validate_python(args.email)).lower()
            if len(email) > 254:
                raise ValueError("Email is too long.")
        elif args.email is not None:
            raise ValueError("Email is not accepted for this command.")
        limited = {"process-pending-images", "capture-training"}
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
        elif args.device is not None or args.dir is not None:
            raise ValueError("Device and folder are accepted only for import-images.")
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
        else:
            asyncio.run(run(args.command, email, password))
    except (ValidationError, ValueError, ApplicationError, RuntimeError):
        print(
            "Operation failed. Check input, configuration, and account state.",
            file=sys.stderr,
        )
        return 1
    except Exception:
        # Driver exceptions can contain database passwords or SQL parameters.
        print(
            "Operation failed. Check database availability and migrations.",
            file=sys.stderr,
        )
        return 1
    print("Operation completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
