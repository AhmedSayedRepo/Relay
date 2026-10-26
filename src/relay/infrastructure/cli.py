import argparse
import asyncio
import getpass
import sys

from pydantic import EmailStr, TypeAdapter, ValidationError

from relay.application.auth import create_admin, disable_user
from relay.application.binaries import BinaryService
from relay.application.errors import ApplicationError
from relay.application.events import EventService
from relay.application.operations import OperationsService
from relay.application.processing import ProcessingService
from relay.application.receipts import InstaPayReceiptParser
from relay.infrastructure.config import load_settings
from relay.infrastructure.database import Database
from relay.infrastructure.image_processing import PillowImagePreprocessor
from relay.infrastructure.instapay_adapter import InstaPayReceiptAdapter
from relay.infrastructure.object_storage import FilesystemObjectStorage
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.infrastructure.security import Argon2Passwords
from relay.infrastructure.tesseract_ocr import create_tesseract_provider


async def run(
    command: str,
    email: str | None,
    password: str | None,
    limit: int | None = None,
) -> None:
    settings = load_settings()
    database = Database(settings)
    try:
        async with database.session() as session:
            uow = SqlUnitOfWork(session)
            if command == "create-admin" and password is not None:
                await create_admin(uow, Argon2Passwords(), str(email), password)
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
        ],
    )
    parser.add_argument("--email")
    parser.add_argument("--limit", type=int, choices=range(1, 101))
    args = parser.parse_args()
    try:
        email = None
        if args.command in {"create-admin", "disable-user"}:
            if args.email is None:
                raise ValueError("Email is required.")
            email = str(TypeAdapter(EmailStr).validate_python(args.email)).lower()
            if len(email) > 254:
                raise ValueError("Email is too long.")
        elif args.email is not None:
            raise ValueError("Email is not accepted for this command.")
        if args.command != "process-pending-images" and args.limit is not None:
            raise ValueError("Limit is accepted only for image processing.")
        password = None
        if args.command == "create-admin":
            if not sys.stdin.isatty():
                raise ValueError("Use an interactive terminal for password entry.")
            password = getpass.getpass("Password (12-1024 characters): ")
            confirmation = getpass.getpass("Confirm password: ")
            if password != confirmation:
                raise ValueError("Passwords do not match.")
            if not 12 <= len(password) <= 1024:
                raise ValueError("Password must contain 12 to 1024 characters.")
        if args.command == "process-pending-images":
            asyncio.run(run(args.command, email, password, args.limit))
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
