"""Real PostgreSQL checks; only a caller-supplied disposable test DB is accepted.

Each case owns a random schema. Never derive this URL from DATABASE_URL.
"""

import asyncio
import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from relay.application.agent import AgentService
from relay.application.binaries import BinaryService
from relay.application.devices import CreateDevice, DeviceService, UpdateDevice
from relay.application.errors import AuthenticationFailed, Conflict
from relay.application.events import EventService
from relay.application.processing import ProcessingService
from relay.application.receipts import InstaPayReceiptParser
from relay.domain.binary_objects import ImageUploadRequest
from relay.domain.devices import DeviceStatus
from relay.domain.processing import (
    OcrDocument,
    OcrLine,
    PreprocessedImage,
    ProcessingFailure,
)
from relay.infrastructure.binary_models import BinaryObjectRow, UploadSessionRow
from relay.infrastructure.config import Settings
from relay.infrastructure.database import Base
from relay.infrastructure.device_security import DeviceTokens
from relay.infrastructure.event_models import RawEventRow
from relay.infrastructure.models import UserRow
from relay.infrastructure.object_storage import FilesystemObjectStorage
from relay.infrastructure.processing_models import ProcessingJobRow, ReceiptCandidateRow
from relay.infrastructure.repositories import SqlUnitOfWork

pytestmark = pytest.mark.postgres


@pytest.fixture
async def pg_sessions():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is not set; disposable PostgreSQL required")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+asyncpg":
        pytest.fail("TEST_POSTGRES_URL must use postgresql+asyncpg")
    if not parsed.database or "test" not in parsed.database.lower():
        pytest.fail("Refusing a database whose name does not contain 'test'")
    schema = f"relay_test_{uuid4().hex}"
    engine = create_async_engine(
        url,
        hide_parameters=True,
        execution_options={"schema_translate_map": {None: schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def create_device(sessions, external_id="generic-source-001"):
    async with sessions() as session:
        return await DeviceService(SqlUnitOfWork(session)).create(
            CreateDevice(external_id, f"Test workstation {external_id}", "linux", "1.0")
        )


async def test_postgres_roundtrip_timezone_and_unique_constraint(pg_sessions):
    device = await create_device(pg_sessions)
    async with pg_sessions() as session:
        loaded = await DeviceService(SqlUnitOfWork(session)).get(device.id)
        assert loaded == device
        assert loaded.created_at.tzinfo is not None
    with pytest.raises(Conflict):
        await create_device(pg_sessions)
    # Duplicate transaction rollback must not poison later writes.
    another = await create_device(pg_sessions, "generic-source-002")
    assert another.id != device.id


async def test_postgres_concurrent_duplicate_insert_has_one_winner(pg_sessions):
    results = await asyncio.gather(
        create_device(pg_sessions), create_device(pg_sessions), return_exceptions=True
    )
    assert sum(isinstance(result, Conflict) for result in results) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1


async def test_postgres_concurrent_enrollment_challenge_has_one_consumer(pg_sessions):
    async with pg_sessions() as session:
        device = await DeviceService(SqlUnitOfWork(session)).create(
            CreateDevice("concurrent-agent", "Concurrent agent", "android", "0.2.0")
        )
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=SecretStr(os.environ["TEST_POSTGRES_URL"]),
        jwt_secret=SecretStr(secrets.token_urlsafe(48)),
        allowed_hosts=["testserver"],
    )
    tokens = DeviceTokens(settings)
    async with pg_sessions() as session:
        enrollment = await AgentService(
            SqlUnitOfWork(session), tokens
        ).create_challenge(device.id, False)

    async def consume():
        async with pg_sessions() as session:
            return await AgentService(SqlUnitOfWork(session), tokens).enroll(
                enrollment.challenge,
                uuid4(),
                "Concurrent agent",
                "android",
                "0.2.0",
            )

    results = await asyncio.gather(consume(), consume(), return_exceptions=True)
    assert sum(isinstance(result, AuthenticationFailed) for result in results) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1


async def test_postgres_concurrent_event_duplicate_is_idempotent(pg_sessions):
    async with pg_sessions() as session:
        device = await DeviceService(SqlUnitOfWork(session)).create(
            CreateDevice("concurrent-event", "Concurrent event", "android", "0.3.0")
        )
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=SecretStr(os.environ["TEST_POSTGRES_URL"]),
        jwt_secret=SecretStr(secrets.token_urlsafe(48)),
        allowed_hosts=["testserver"],
    )
    tokens = DeviceTokens(settings)
    async with pg_sessions() as session:
        service = AgentService(SqlUnitOfWork(session), tokens)
        enrollment = await service.create_challenge(device.id, False)
        bundle = await service.enroll(
            enrollment.challenge, uuid4(), "Concurrent event", "android", "0.3.0"
        )
        await service.update_configuration(
            device.id,
            1,
            1,
            {
                "collectors": {
                    "sms": {
                        "enabled": True,
                        "rules": [
                            {
                                "id": "all-notices",
                                "enabled": True,
                                "body": {"contains": "notice"},
                            }
                        ],
                    }
                }
            },
        )
    timestamp = datetime.now(UTC).isoformat()
    identifier = str(uuid4())
    raw = {
        "event_id": identifier,
        "installation_id": str(bundle.installation_id),
        "collector": "sms",
        "schema_version": 1,
        "config_version": 2,
        "occurred_at": timestamp,
        "captured_at": timestamp,
        "payload": {"sender": "GENERIC", "body": "concurrent notice"},
        "metadata": {"rule_id": "all-notices"},
    }

    async def submit():
        async with pg_sessions() as session:
            return await EventService(SqlUnitOfWork(session)).ingest(
                device.id, bundle.installation_id, [raw]
            )

    results = await asyncio.gather(submit(), submit())
    assert sorted(result[0].status for result in results) == ["accepted", "duplicate"]
    async with pg_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(RawEventRow)) == 1


async def test_postgres_patch_preserves_concurrent_disjoint_fields(pg_sessions):
    device = await create_device(pg_sessions)
    async with pg_sessions() as first:
        uow = SqlUnitOfWork(first)
        await uow.devices.get(device.id, for_update=True)
        attempted = asyncio.Event()

        async def second_patch():
            async with pg_sessions() as second:
                attempted.set()
                return await DeviceService(SqlUnitOfWork(second)).update(
                    device.id, UpdateDevice(status=DeviceStatus.DISABLED)
                )

        pending = asyncio.create_task(second_patch())
        try:
            await asyncio.wait_for(attempted.wait(), timeout=5)
            await DeviceService(uow).update(device.id, UpdateDevice(name="Renamed"))
            await asyncio.wait_for(pending, timeout=5)
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    async with pg_sessions() as session:
        loaded = await DeviceService(SqlUnitOfWork(session)).get(device.id)
        assert loaded.name == "Renamed"
        assert loaded.status == DeviceStatus.DISABLED


async def test_postgres_rejects_invalid_status_at_database_boundary(pg_sessions):
    device = await create_device(pg_sessions)
    # Use the ORM table so schema translation still targets the isolated schema.
    from relay.infrastructure.models import DeviceRow

    async with pg_sessions() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                DeviceRow.__table__.update()
                .where(DeviceRow.id == device.id)
                .values(status=text("'BROKEN'"))
            )
        await session.rollback()


async def test_postgres_binary_quota_and_concurrent_finalize_are_serialized(
    pg_sessions, tmp_path
):
    async with pg_sessions() as session:
        device = await DeviceService(SqlUnitOfWork(session)).create(
            CreateDevice("binary-race", "Binary race", "android", "0.4.0")
        )
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=SecretStr(os.environ["TEST_POSTGRES_URL"]),
        jwt_secret=SecretStr(secrets.token_urlsafe(48)),
        allowed_hosts=["testserver"],
    )
    tokens = DeviceTokens(settings)
    async with pg_sessions() as session:
        service = AgentService(SqlUnitOfWork(session), tokens)
        challenge = await service.create_challenge(device.id, False)
        bundle = await service.enroll(
            challenge.challenge, uuid4(), "Binary race", "android", "0.4.0"
        )
        await service.update_configuration(
            device.id,
            1,
            1,
            {
                "collectors": {
                    "images": {
                        "enabled": True,
                        "sources": [{"id": "folder", "type": "saf_tree"}],
                        "rules": [
                            {
                                "id": "png",
                                "enabled": True,
                                "source_id": "folder",
                                "mime_types": ["image/png"],
                            }
                        ],
                    }
                }
            },
        )
    storage = FilesystemObjectStorage(tmp_path / "objects")
    timestamp = datetime.now(UTC)

    def image_request(data: bytes) -> ImageUploadRequest:
        return ImageUploadRequest(
            upload_id=uuid4(),
            event_id=uuid4(),
            installation_id=bundle.installation_id,
            content_hash=hashlib.sha256(data).hexdigest(),
            content_type="image/png",
            byte_size=len(data),
            width=2,
            height=3,
            source_type="saf_tree",
            source_id="folder",
            display_name="race.png",
            config_version=2,
            rule_id="png",
            occurred_at=timestamp,
            captured_at=timestamp,
            media_at=None,
        )

    first_data = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x02\x00\x00\x00\x03\x08\x06\x00\x00\x00\x00\x00\x00\x00"
    )
    second_data = first_data + b"different"

    async def initialize(data: bytes):
        async with pg_sessions() as session:
            return await BinaryService(
                SqlUnitOfWork(session), storage, max_active_uploads=1
            ).initialize(device.id, bundle.installation_id, image_request(data))

    initialized = await asyncio.gather(
        initialize(first_data), initialize(second_data), return_exceptions=True
    )
    assert sum(isinstance(result, Conflict) for result in initialized) == 1
    winner = next(
        result for result in initialized if not isinstance(result, BaseException)
    )
    uploaded_data = (
        first_data
        if winner.session.content_hash == hashlib.sha256(first_data).hexdigest()
        else second_data
    )

    async with pg_sessions() as session:
        service = BinaryService(SqlUnitOfWork(session), storage)
        await service.append_chunk(
            device.id,
            bundle.installation_id,
            winner.session.id,
            0,
            uploaded_data,
        )

    async def finalize():
        async with pg_sessions() as session:
            return await BinaryService(SqlUnitOfWork(session), storage).finalize(
                device.id, bundle.installation_id, winner.session.id
            )

    finalized = await asyncio.gather(finalize(), finalize())
    assert sorted(result.event_status for result in finalized) == [
        "accepted",
        "duplicate",
    ]
    assert finalized[0].object.id == finalized[1].object.id
    async with pg_sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(BinaryObjectRow)) == 1
        )
        assert (
            await session.scalar(select(func.count()).select_from(UploadSessionRow))
            == 1
        )
        assert await session.scalar(select(func.count()).select_from(RawEventRow)) == 1


@pytest.mark.parametrize("evidence_mode", [False, True])
async def test_postgres_concurrent_same_reference_preserves_one_canonical(
    pg_sessions, tmp_path, evidence_mode
):
    async with pg_sessions() as session:
        device = await DeviceService(SqlUnitOfWork(session)).create(
            CreateDevice("receipt-race", "Receipt race", "android", "0.5.0")
        )
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=SecretStr(os.environ["TEST_POSTGRES_URL"]),
        jwt_secret=SecretStr(secrets.token_urlsafe(48)),
        allowed_hosts=["testserver"],
    )
    tokens = DeviceTokens(settings)
    async with pg_sessions() as session:
        service = AgentService(SqlUnitOfWork(session), tokens)
        challenge = await service.create_challenge(device.id, False)
        bundle = await service.enroll(
            challenge.challenge, uuid4(), "Receipt race", "android", "0.5.0"
        )
        await service.update_configuration(
            device.id,
            1,
            1,
            {
                "collectors": {
                    "images": {
                        "enabled": True,
                        "sources": [{"id": "folder", "type": "saf_tree"}],
                        "rules": [
                            {
                                "id": "png",
                                "enabled": True,
                                "source_id": "folder",
                                "mime_types": ["image/png"],
                            }
                        ],
                    }
                }
            },
        )
    storage = FilesystemObjectStorage(tmp_path / "receipt-race-objects")
    timestamp = datetime.now(UTC)
    prefix = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x02\x00\x00\x00\x03\x08\x06\x00\x00\x00"
        b"\x00\x00\x00\x00"
    )

    async def add_object(data: bytes) -> None:
        request = ImageUploadRequest(
            uuid4(),
            uuid4(),
            bundle.installation_id,
            hashlib.sha256(data).hexdigest(),
            "image/png",
            len(data),
            2,
            3,
            "saf_tree",
            "folder",
            "race.png",
            2,
            "png",
            timestamp,
            timestamp,
            None,
        )
        async with pg_sessions() as session:
            service = BinaryService(
                SqlUnitOfWork(session),
                storage,
                receipt_evidence_enabled=evidence_mode,
            )
            initialized = await service.initialize(
                device.id, bundle.installation_id, request
            )
            await service.append_chunk(
                device.id,
                bundle.installation_id,
                initialized.session.id,
                0,
                data,
            )
            await service.finalize(
                device.id, bundle.installation_id, initialized.session.id
            )

    await add_object(prefix + b"first-rendering")
    await add_object(prefix + b"second-rendering")
    await add_object(prefix + b"third-rendering")

    class PassPreprocessor:
        def preprocess(self, data: bytes) -> PreprocessedImage:
            assert data
            return PreprocessedImage(b"bounded", "image/png", 1, 1, ())

    class SameReferenceOcr:
        provider_id = "tesseract"
        provider_version = "race-test"
        available_languages = ("ara", "eng")

        async def extract(self, image, requested_languages, timeout_seconds):
            lines = (
                OcrLine("InstaPay", 0.99),
                OcrLine("Transfer successful", 0.99),
                OcrLine("Amount: EGP 20.00", 0.99),
                OcrLine("Reference Number: RACE-123456", 0.99),
            )
            return OcrDocument(
                self.provider_id,
                self.provider_version,
                requested_languages,
                self.available_languages,
                "\n".join(line.text for line in lines),
                lines,
                0.99,
                1,
            )

    async def work_one():
        from tests.test_evidence_backend import EvidenceFixture

        async with pg_sessions() as session:
            return await ProcessingService(
                SqlUnitOfWork(session),
                storage,
                PassPreprocessor(),
                SameReferenceOcr(),
                InstaPayReceiptParser(),
                evidence_extractor=EvidenceFixture() if evidence_mode else None,
            ).process_one()

    now = datetime.now(UTC)
    async with pg_sessions() as session:
        uow = SqlUnitOfWork(session)
        first_claim = await uow.processing.claim_next(now, now - timedelta(minutes=5))
        assert first_claim is not None
        first_job, first_attempt = first_claim
        await uow.commit()
        await session.execute(
            update(ProcessingJobRow)
            .where(ProcessingJobRow.id == first_job.id)
            .values(updated_at=now - timedelta(minutes=10))
        )
        await session.commit()
        second_claim = await uow.processing.claim_next(now, now - timedelta(minutes=5))
        assert second_claim is not None
        second_job, second_attempt = second_claim
        await uow.commit()
        assert second_job.id == first_job.id
        assert not await uow.processing.save_failure(
            first_job,
            first_attempt,
            ProcessingFailure.INTERNAL_ERROR,
            transient=False,
            now=now,
        )
        await uow.rollback()
        assert await uow.processing.save_failure(
            second_job,
            second_attempt,
            ProcessingFailure.OCR_TIMEOUT,
            transient=True,
            now=now,
        )
        await uow.commit()
        await session.execute(
            update(ProcessingJobRow)
            .where(ProcessingJobRow.id == first_job.id)
            .values(next_attempt_at=now - timedelta(seconds=1))
        )
        await session.commit()

    processed = await asyncio.gather(work_one(), work_one(), work_one())
    assert all(processed)
    async with pg_sessions() as session:
        rows = list(
            await session.scalars(
                select(ReceiptCandidateRow).order_by(ReceiptCandidateRow.created_at)
            )
        )
        assert len(rows) == 3
        assert all(row.duplicate_of_candidate_id is None for row in rows)
        assert all(row.review_status == "NEEDS_REVIEW" for row in rows)
        reviewer_id = uuid4()
        session.add(
            UserRow(
                id=reviewer_id,
                email="reviewer@example.test",
                password_hash="test-only-unused-password-hash",
            )
        )
        await session.commit()

    async def review_reference(row):
        async with pg_sessions() as session:
            return await ProcessingService(
                SqlUnitOfWork(session), storage
            ).resolve_exception(
                row.id,
                reviewer_id,
                uuid4(),
                row.updated_at.isoformat(),
                "reference_number",
                "CORRECT",
                "RACE123456",
            )

    await asyncio.gather(*(review_reference(row) for row in rows))
    async with pg_sessions() as session:
        rows = list(
            await session.scalars(
                select(ReceiptCandidateRow).order_by(ReceiptCandidateRow.created_at)
            )
        )
        canonical = next(row for row in rows if row.duplicate_of_candidate_id is None)
        duplicates = [row for row in rows if row.duplicate_of_candidate_id is not None]
        assert len(duplicates) == 2
        assert all(row.duplicate_of_candidate_id == canonical.id for row in duplicates)
        assert all(row.duplicate_reason == "REFERENCE_NUMBER" for row in duplicates)
        assert all(row.object_id != canonical.object_id for row in duplicates)
        assert all(row.review_status == "NEEDS_REVIEW" for row in rows)
        canonical.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        job = await session.get(ProcessingJobRow, canonical.job_id)
        assert job is not None
        job.expires_at = canonical.expires_at
        await session.commit()

    async with pg_sessions() as session:
        purged = await ProcessingService(
            SqlUnitOfWork(session), storage
        ).purge_expired()
        assert purged == 1

    async with pg_sessions() as session:
        survivors = list(
            await session.scalars(
                select(ReceiptCandidateRow).order_by(ReceiptCandidateRow.created_at)
            )
        )
        assert len(survivors) == 2
        promoted = next(
            row for row in survivors if row.duplicate_of_candidate_id is None
        )
        remaining_duplicate = next(
            row for row in survivors if row.duplicate_of_candidate_id is not None
        )
        assert promoted.duplicate_reason is None
        assert remaining_duplicate.duplicate_of_candidate_id == promoted.id
