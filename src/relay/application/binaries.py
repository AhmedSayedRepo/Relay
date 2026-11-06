"""Bounded, authenticated, resumable binary ingestion use cases."""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from relay.application.binary_ports import BinaryUnitOfWork, ObjectStoragePort
from relay.application.errors import Conflict, NotFound
from relay.application.event_ports import AddEventResult
from relay.application.evidence_ports import (
    EVIDENCE_PARSER_TYPE,
    EVIDENCE_PARSER_VERSION,
    EVIDENCE_PROCESSOR_VERSION,
)
from relay.domain.agent import JsonValue
from relay.domain.binary_objects import (
    MAX_CHUNK_BYTES,
    MAX_IMAGE_BYTES,
    BinaryObject,
    BinaryRejected,
    ImageUploadRequest,
    UploadSession,
    image_rule_matches,
    inspect_image,
    validate_image_request,
)
from relay.domain.events import RawEvent
from relay.domain.processing import (
    DEFAULT_OCR_LANGUAGES,
    DEFAULT_OCR_PROVIDER,
    DEFAULT_PARSER_TYPE,
    DEFAULT_PARSER_VERSION,
    DEFAULT_PROCESSOR_VERSION,
)

logger = logging.getLogger(__name__)
IMPORT_SOURCE_ID = "operator-import"
IMPORT_RULE_ID = "operator-import"


@dataclass(frozen=True, slots=True)
class InitializeResult:
    session: UploadSession
    status: str
    event_status: str | None = None


@dataclass(frozen=True, slots=True)
class FinalizeResult:
    object: BinaryObject
    event_status: str


@dataclass(frozen=True, slots=True)
class PurgeResult:
    sessions: int
    objects: int


class BinaryService:
    def __init__(
        self,
        uow: BinaryUnitOfWork,
        storage: ObjectStoragePort,
        *,
        retention_days: int = 14,
        session_hours: int = 24,
        max_active_uploads: int = 3,
        uploads_per_hour: int = 30,
        stored_bytes_per_device: int = 64 * 1024 * 1024,
        receipt_evidence_enabled: bool = False,
    ) -> None:
        self.uow = uow
        self.storage = storage
        self.retention_days = retention_days
        self.session_hours = session_hours
        self.max_active_uploads = max_active_uploads
        self.uploads_per_hour = uploads_per_hour
        self.stored_bytes_per_device = stored_bytes_per_device
        self.receipt_evidence_enabled = receipt_evidence_enabled

    async def initialize(
        self, device_id: UUID, installation_id: UUID, request: ImageUploadRequest
    ) -> InitializeResult:
        now = datetime.now(UTC)
        validate_image_request(request, now)
        if request.installation_id != installation_id:
            raise BinaryRejected("wrong_installation")
        existing_session = await self.uow.binaries.session(
            installation_id, request.upload_id, for_update=True
        )
        if existing_session is not None:
            if existing_session.request_fingerprint != request.fingerprint():
                raise Conflict("Upload identifier was reused with different metadata.")
            return InitializeResult(
                existing_session,
                (
                    "finalized"
                    if existing_session.status == "finalized"
                    else "upload_required"
                ),
                "duplicate" if existing_session.status == "finalized" else None,
            )
        settings = await self.uow.events.configuration(
            device_id, request.config_version
        )
        if settings is None or not image_rule_matches(settings, request):
            raise BinaryRejected("collector_not_enabled_or_unmatched")
        await self.uow.binaries.lock_device(device_id)
        if (
            await self.uow.binaries.recent_sessions(device_id, now - timedelta(hours=1))
            >= self.uploads_per_hour
        ):
            raise Conflict("Upload initialization limit reached.")
        existing_object = await self.uow.binaries.object_by_hash(
            installation_id, request.content_hash
        )
        if existing_object is not None:
            self._same_metadata(existing_object, request)
            session = self._new_session(device_id, request, now)
            session.status = "finalized"
            session.object_id = existing_object.id
            await self.uow.binaries.add_session(session)
            event_status, raw_event = await self._link_event(
                session, existing_object, now
            )
            await self._ensure_processing_job(existing_object, raw_event, now)
            await self.uow.commit()
            self._log_completed(existing_object, event_status, duplicate_object=True)
            return InitializeResult(session, "finalized", event_status)
        if (
            await self.uow.binaries.active_sessions(device_id, now)
            >= self.max_active_uploads
        ):
            raise Conflict("Active upload session limit reached.")
        reserved, stored = (
            await self.uow.binaries.reserved_bytes(device_id, now),
            await self.uow.binaries.stored_bytes(device_id),
        )
        if reserved + stored + request.byte_size > self.stored_bytes_per_device:
            raise Conflict("Device binary storage quota reached.")
        session = self._new_session(device_id, request, now)
        self.storage.create_upload(session.id)
        try:
            await self.uow.binaries.add_session(session)
            await self.uow.commit()
        except Exception:
            self.storage.delete_upload(session.id)
            raise
        logger.info(
            "binary.upload_initialized",
            extra={
                "session_id": str(session.id),
                "device_id": str(device_id),
                "byte_size": request.byte_size,
            },
        )
        return InitializeResult(session, "upload_required")

    def _new_session(
        self, device_id: UUID, request: ImageUploadRequest, now: datetime
    ) -> UploadSession:
        return UploadSession(
            uuid4(),
            request.upload_id,
            request.event_id,
            device_id,
            request.installation_id,
            request.fingerprint(),
            request.content_hash,
            request.content_type,
            request.byte_size,
            request.width,
            request.height,
            request.source_type,
            request.source_id,
            request.config_version,
            request.rule_id,
            request.occurred_at.astimezone(UTC),
            request.captured_at.astimezone(UTC),
            request.media_at.astimezone(UTC) if request.media_at else None,
            0,
            "active",
            None,
            now,
            now + timedelta(hours=self.session_hours),
        )

    async def append_chunk(
        self,
        device_id: UUID,
        installation_id: UUID,
        session_id: UUID,
        offset: int,
        data: bytes,
    ) -> tuple[UploadSession, str]:
        if offset < 0 or not data or len(data) > MAX_CHUNK_BYTES:
            raise BinaryRejected("invalid_chunk")
        session = await self._owned_session(
            device_id, installation_id, session_id, for_update=True
        )
        if session.status != "active":
            raise Conflict("Upload session is already finalized.")
        if session.expires_at <= datetime.now(UTC):
            raise Conflict("Upload session has expired.")
        end = offset + len(data)
        if end > session.byte_size:
            raise BinaryRejected("chunk_exceeds_object")
        status = "accepted"
        if offset == session.uploaded_bytes:
            self.storage.append(session.id, offset, data)
            session.uploaded_bytes = end
            await self.uow.binaries.save_session(session)
            await self.uow.commit()
        elif offset < session.uploaded_bytes and end <= session.uploaded_bytes:
            if not self.storage.matches(session.id, offset, data):
                raise Conflict("Chunk overlaps uploaded data with different content.")
            status = "duplicate"
        else:
            raise Conflict("Chunk offset must equal the next expected byte.")
        logger.info(
            "binary.chunk_processed",
            extra={
                "session_id": str(session.id),
                "byte_size": len(data),
                "status": status,
            },
        )
        return session, status

    async def finalize(
        self, device_id: UUID, installation_id: UUID, session_id: UUID
    ) -> FinalizeResult:
        now = datetime.now(UTC)
        session = await self._owned_session(
            device_id, installation_id, session_id, for_update=True
        )
        if session.status == "finalized" and session.object_id is not None:
            value = await self.uow.binaries.object(session.object_id)
            if value is None:
                raise BinaryRejected("object_state_missing")
            return FinalizeResult(value, "duplicate")
        if session.expires_at <= now:
            raise Conflict("Upload session has expired.")
        if session.uploaded_bytes != session.byte_size:
            raise Conflict("Upload is incomplete.")
        data = self.storage.read_upload(session.id, MAX_IMAGE_BYTES)
        if len(data) != session.byte_size:
            raise BinaryRejected("final_size_mismatch")
        if hashlib.sha256(data).hexdigest() != session.content_hash:
            raise BinaryRejected("hash_mismatch")
        content_type, width, height = inspect_image(data)
        if (content_type, width, height) != (
            session.content_type,
            session.width,
            session.height,
        ):
            raise BinaryRejected("metadata_mismatch")
        finalized_storage_key: str | None = None
        try:
            existing = await self.uow.binaries.object_by_hash(
                installation_id, session.content_hash
            )
            if existing is not None:
                self.storage.delete_upload(session.id)
                value = existing
                duplicate_object = True
            else:
                object_id = uuid4()
                value = BinaryObject(
                    object_id,
                    device_id,
                    installation_id,
                    session.content_hash,
                    session.content_type,
                    session.byte_size,
                    session.width,
                    session.height,
                    session.source_type,
                    session.source_id,
                    "filesystem",
                    f"objects/{installation_id}/{object_id}.blob",
                    now,
                    now,
                    now + timedelta(days=self.retention_days),
                )
                duplicate_object = not await self.uow.binaries.add_object(value)
                if duplicate_object:
                    raced = await self.uow.binaries.object_by_hash(
                        installation_id, session.content_hash
                    )
                    if raced is None:
                        raise Conflict("Concurrent upload could not be resolved.")
                    value = raced
                    self.storage.delete_upload(session.id)
                else:
                    self.storage.finalize(session.id, value.storage_key)
                    finalized_storage_key = value.storage_key
            event_status, raw_event = await self._link_event(session, value, now)
            await self._ensure_processing_job(value, raw_event, now)
            session.status = "finalized"
            session.object_id = value.id
            await self.uow.binaries.save_session(session)
            await self.uow.commit()
        except Exception:
            await self.uow.rollback()
            if finalized_storage_key is not None:
                self.storage.delete(finalized_storage_key)
            raise
        self._log_completed(value, event_status, duplicate_object)
        return FinalizeResult(value, event_status)

    async def import_image(
        self, device_id: UUID, installation_id: UUID, data: bytes
    ) -> str:
        """Operator bulk import through the same object, event and job records.

        Device upload limits do not apply (an operator runs it deliberately),
        but image validation and per-installation content deduplication do.
        """
        now = datetime.now(UTC)
        if not 1 <= len(data) <= MAX_IMAGE_BYTES:
            raise BinaryRejected("invalid_metadata")
        content_type, width, height = inspect_image(data)
        content_hash = hashlib.sha256(data).hexdigest()
        if await self.uow.binaries.object_by_hash(installation_id, content_hash):
            return "duplicate"
        session = UploadSession(
            uuid4(),
            uuid4(),
            uuid4(),
            device_id,
            installation_id,
            "operator-import",
            content_hash,
            content_type,
            len(data),
            width,
            height,
            "saf_tree",
            IMPORT_SOURCE_ID,
            1,
            IMPORT_RULE_ID,
            now,
            now,
            None,
            len(data),
            "finalized",
            None,
            now,
            now,
        )
        object_id = uuid4()
        value = BinaryObject(
            object_id,
            device_id,
            installation_id,
            content_hash,
            content_type,
            len(data),
            width,
            height,
            "saf_tree",
            IMPORT_SOURCE_ID,
            "filesystem",
            f"objects/{installation_id}/{object_id}.blob",
            now,
            now,
            now + timedelta(days=self.retention_days),
        )
        self.storage.create_upload(session.id)
        try:
            self.storage.append(session.id, 0, data)
            self.storage.finalize(session.id, value.storage_key)
        except Exception:
            self.storage.delete_upload(session.id)
            raise
        try:
            if not await self.uow.binaries.add_object(value):
                self.storage.delete(value.storage_key)
                await self.uow.rollback()
                return "duplicate"
            event_status, raw_event = await self._link_event(session, value, now)
            await self._ensure_processing_job(value, raw_event, now)
            await self.uow.commit()
        except Exception:
            await self.uow.rollback()
            self.storage.delete(value.storage_key)
            raise
        self._log_completed(value, event_status, False)
        return "imported"

    async def _owned_session(
        self,
        device_id: UUID,
        installation_id: UUID,
        session_id: UUID,
        *,
        for_update: bool,
    ) -> UploadSession:
        value = await self.uow.binaries.session_by_id(session_id, for_update=for_update)
        if (
            value is None
            or value.device_id != device_id
            or value.installation_id != installation_id
        ):
            raise NotFound("Upload session not found.")
        return value

    @staticmethod
    def _same_metadata(value: BinaryObject, request: ImageUploadRequest) -> None:
        if (
            value.content_type,
            value.byte_size,
            value.width,
            value.height,
        ) != (
            request.content_type,
            request.byte_size,
            request.width,
            request.height,
        ):
            raise Conflict("Content hash metadata does not match the existing object.")

    async def _link_event(
        self, session: UploadSession, value: BinaryObject, now: datetime
    ) -> tuple[str, RawEvent]:
        payload: dict[str, JsonValue] = {
            "object_id": str(value.id),
            "content_hash": value.content_hash,
            "content_type": value.content_type,
            "byte_size": value.byte_size,
            "width": value.width,
            "height": value.height,
            "source_type": session.source_type,
            "source_id": session.source_id,
            "media_at": session.media_at.isoformat() if session.media_at else None,
        }
        metadata: dict[str, JsonValue] = {"rule_id": session.rule_id}
        canonical = json.dumps(
            {"payload": payload, "metadata": metadata},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        raw_event = RawEvent(
            uuid4(),
            session.event_id,
            session.device_id,
            session.installation_id,
            "images",
            1,
            session.config_version,
            session.rule_id,
            session.occurred_at,
            session.captured_at,
            now,
            payload,
            metadata,
            hashlib.sha256(canonical).hexdigest(),
            value.delete_after,
            value.id,
        )
        result = await self.uow.events.add(raw_event)
        if result == AddEventResult.CONFLICT:
            raise Conflict("Event identifier was reused with different content.")
        if result == AddEventResult.DUPLICATE:
            existing = await self.uow.events.by_identity(
                session.installation_id, session.event_id
            )
            if existing is None:
                raise Conflict("Duplicate event could not be resolved.")
            raw_event = existing
        return result.value, raw_event

    async def _ensure_processing_job(
        self, value: BinaryObject, raw_event: RawEvent, now: datetime
    ) -> None:
        await self.uow.processing.ensure_job(
            object_id=value.id,
            raw_event_id=raw_event.id,
            device_id=value.device_id,
            processor_version=(
                EVIDENCE_PROCESSOR_VERSION
                if self.receipt_evidence_enabled
                else DEFAULT_PROCESSOR_VERSION
            ),
            parser_type=(
                EVIDENCE_PARSER_TYPE
                if self.receipt_evidence_enabled
                else DEFAULT_PARSER_TYPE
            ),
            parser_version=(
                EVIDENCE_PARSER_VERSION
                if self.receipt_evidence_enabled
                else DEFAULT_PARSER_VERSION
            ),
            ocr_provider=DEFAULT_OCR_PROVIDER,
            requested_languages=DEFAULT_OCR_LANGUAGES,
            expires_at=value.delete_after,
            now=now,
        )

    async def get(self, object_id: UUID) -> BinaryObject:
        value = await self.uow.binaries.object(object_id)
        if value is None:
            raise NotFound("Binary object not found.")
        return value

    async def content(self, object_id: UUID) -> tuple[BinaryObject, bytes]:
        value = await self.get(object_id)
        return value, self.storage.read(value.storage_key, MAX_IMAGE_BYTES)

    async def delete(self, object_id: UUID) -> None:
        value = await self.get(object_id)
        self.storage.delete(value.storage_key)
        await self.uow.binaries.delete_object(object_id)
        await self.uow.commit()
        logger.info("binary.object_deleted", extra={"object_id": str(object_id)})

    async def delete_device(self, device_id: UUID) -> int:
        sessions = await self.uow.binaries.list_device_sessions(device_id)
        objects = await self.uow.binaries.list_device_objects(device_id)
        for session in sessions:
            self.storage.delete_upload(session.id)
            await self.uow.binaries.delete_session(session.id)
        for value in objects:
            self.storage.delete(value.storage_key)
            await self.uow.binaries.delete_object(value.id)
        await self.uow.commit()
        logger.info(
            "binary.device_deleted",
            extra={"device_id": str(device_id), "object_count": len(objects)},
        )
        return len(objects)

    async def purge_expired(self, before: datetime | None = None) -> PurgeResult:
        cutoff = before or datetime.now(UTC)
        sessions = await self.uow.binaries.list_expired_sessions(cutoff)
        objects = await self.uow.binaries.list_expired_objects(cutoff)
        for session in sessions:
            self.storage.delete_upload(session.id)
            await self.uow.binaries.delete_session(session.id)
        for value in objects:
            self.storage.delete(value.storage_key)
            await self.uow.binaries.delete_object(value.id)
        await self.uow.commit()
        logger.info(
            "binary.retention_purged",
            extra={"session_count": len(sessions), "object_count": len(objects)},
        )
        return PurgeResult(len(sessions), len(objects))

    @staticmethod
    def _log_completed(
        value: BinaryObject, event_status: str, duplicate_object: bool
    ) -> None:
        logger.info(
            "binary.upload_completed",
            extra={
                "object_id": str(value.id),
                "device_id": str(value.device_id),
                "byte_size": value.byte_size,
                "event_status": event_status,
                "duplicate_object": duplicate_object,
            },
        )
