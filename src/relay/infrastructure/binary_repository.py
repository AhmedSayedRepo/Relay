"""SQLAlchemy persistence for binary objects and resumable upload sessions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.binary_objects import BinaryObject, UploadSession
from relay.infrastructure.binary_models import BinaryObjectRow, UploadSessionRow
from relay.infrastructure.datetime_utils import aware
from relay.infrastructure.event_models import RawEventRow
from relay.infrastructure.models import DeviceRow


def to_session(row: UploadSessionRow) -> UploadSession:
    return UploadSession(
        row.id,
        row.upload_id,
        row.event_id,
        row.device_id,
        row.installation_id,
        row.request_fingerprint,
        row.content_hash,
        row.content_type,
        row.byte_size,
        row.width,
        row.height,
        row.source_type,
        row.source_id,
        row.config_version,
        row.rule_id,
        aware(row.occurred_at),
        aware(row.captured_at),
        aware(row.media_at) if row.media_at else None,
        row.uploaded_bytes,
        row.status,
        row.object_id,
        aware(row.created_at),
        aware(row.expires_at),
    )


def to_object(row: BinaryObjectRow) -> BinaryObject:
    return BinaryObject(
        row.id,
        row.device_id,
        row.installation_id,
        row.content_hash,
        row.content_type,
        row.byte_size,
        row.width,
        row.height,
        row.source_type,
        row.source_id,
        row.storage_backend,
        row.storage_key,
        aware(row.created_at),
        aware(row.finalized_at),
        aware(row.delete_after),
    )


class SqlBinaryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.db = session

    async def lock_device(self, device_id: UUID) -> None:
        # Serialize quota decisions for one device. PostgreSQL honors this row lock;
        # SQLite already serializes writes at the database level.
        await self.db.scalar(
            select(DeviceRow.id).where(DeviceRow.id == device_id).with_for_update()
        )

    async def session(
        self, installation_id: UUID, upload_id: UUID, *, for_update: bool = False
    ) -> UploadSession | None:
        query = select(UploadSessionRow).where(
            UploadSessionRow.installation_id == installation_id,
            UploadSessionRow.upload_id == upload_id,
        )
        if for_update:
            query = query.with_for_update()
        row = await self.db.scalar(query)
        return to_session(row) if row else None

    async def session_by_id(
        self, session_id: UUID, *, for_update: bool = False
    ) -> UploadSession | None:
        query = select(UploadSessionRow).where(UploadSessionRow.id == session_id)
        if for_update:
            query = query.with_for_update()
        row = await self.db.scalar(query)
        return to_session(row) if row else None

    async def add_session(self, value: UploadSession) -> None:
        self.db.add(UploadSessionRow(**self._session_values(value), id=value.id))
        await self.db.flush()

    async def save_session(self, value: UploadSession) -> None:
        await self.db.execute(
            update(UploadSessionRow)
            .where(UploadSessionRow.id == value.id)
            .values(**self._session_values(value))
        )

    @staticmethod
    def _session_values(value: UploadSession) -> dict[str, object]:
        return {
            "upload_id": value.upload_id,
            "event_id": value.event_id,
            "device_id": value.device_id,
            "installation_id": value.installation_id,
            "request_fingerprint": value.request_fingerprint,
            "content_hash": value.content_hash,
            "content_type": value.content_type,
            "byte_size": value.byte_size,
            "width": value.width,
            "height": value.height,
            "source_type": value.source_type,
            "source_id": value.source_id,
            "config_version": value.config_version,
            "rule_id": value.rule_id,
            "occurred_at": value.occurred_at,
            "captured_at": value.captured_at,
            "media_at": value.media_at,
            "uploaded_bytes": value.uploaded_bytes,
            "status": value.status,
            "object_id": value.object_id,
            "created_at": value.created_at,
            "expires_at": value.expires_at,
        }

    async def active_sessions(self, device_id: UUID, now: datetime) -> int:
        value = await self.db.scalar(
            select(func.count())
            .select_from(UploadSessionRow)
            .where(
                UploadSessionRow.device_id == device_id,
                UploadSessionRow.status == "active",
                UploadSessionRow.expires_at > now,
            )
        )
        return int(value or 0)

    async def recent_sessions(self, device_id: UUID, since: datetime) -> int:
        value = await self.db.scalar(
            select(func.count())
            .select_from(UploadSessionRow)
            .where(
                UploadSessionRow.device_id == device_id,
                UploadSessionRow.created_at >= since,
            )
        )
        return int(value or 0)

    async def reserved_bytes(self, device_id: UUID, now: datetime) -> int:
        value = await self.db.scalar(
            select(func.coalesce(func.sum(UploadSessionRow.byte_size), 0)).where(
                UploadSessionRow.device_id == device_id,
                UploadSessionRow.status == "active",
                UploadSessionRow.expires_at > now,
            )
        )
        return int(value or 0)

    async def stored_bytes(self, device_id: UUID) -> int:
        value = await self.db.scalar(
            select(func.coalesce(func.sum(BinaryObjectRow.byte_size), 0)).where(
                BinaryObjectRow.device_id == device_id
            )
        )
        return int(value or 0)

    async def object_by_hash(
        self, installation_id: UUID, content_hash: str
    ) -> BinaryObject | None:
        row = await self.db.scalar(
            select(BinaryObjectRow).where(
                BinaryObjectRow.installation_id == installation_id,
                BinaryObjectRow.content_hash == content_hash,
            )
        )
        return to_object(row) if row else None

    async def object(self, object_id: UUID) -> BinaryObject | None:
        row = await self.db.get(BinaryObjectRow, object_id)
        return to_object(row) if row else None

    async def add_object(self, value: BinaryObject) -> bool:
        row = BinaryObjectRow(
            id=value.id,
            device_id=value.device_id,
            installation_id=value.installation_id,
            content_hash=value.content_hash,
            content_type=value.content_type,
            byte_size=value.byte_size,
            width=value.width,
            height=value.height,
            source_type=value.source_type,
            source_id=value.source_id,
            storage_backend=value.storage_backend,
            storage_key=value.storage_key,
            status="available",
            created_at=value.created_at,
            finalized_at=value.finalized_at,
            delete_after=value.delete_after,
        )
        # Finalization holds the owning device row lock, so another session for
        # this installation cannot race the preceding object_by_hash lookup.
        # Keeping this insert in the outer transaction also avoids SQLite's
        # legacy SAVEPOINT behavior committing it independently.
        self.db.add(row)
        await self.db.flush()
        return True

    async def list_expired_sessions(self, before: datetime) -> list[UploadSession]:
        rows = await self.db.scalars(
            select(UploadSessionRow).where(
                UploadSessionRow.status == "active",
                UploadSessionRow.expires_at <= before,
            )
        )
        return [to_session(row) for row in rows]

    async def list_expired_objects(self, before: datetime) -> list[BinaryObject]:
        rows = await self.db.scalars(
            select(BinaryObjectRow).where(BinaryObjectRow.delete_after <= before)
        )
        return [to_object(row) for row in rows]

    async def list_device_sessions(self, device_id: UUID) -> list[UploadSession]:
        rows = await self.db.scalars(
            select(UploadSessionRow).where(UploadSessionRow.device_id == device_id)
        )
        return [to_session(row) for row in rows]

    async def list_device_objects(self, device_id: UUID) -> list[BinaryObject]:
        rows = await self.db.scalars(
            select(BinaryObjectRow).where(BinaryObjectRow.device_id == device_id)
        )
        return [to_object(row) for row in rows]

    async def delete_session(self, session_id: UUID) -> None:
        await self.db.execute(
            delete(UploadSessionRow).where(UploadSessionRow.id == session_id)
        )

    async def delete_object(self, object_id: UUID) -> None:
        # Explicit dependent deletion also preserves behavior in SQLite tests, where
        # foreign-key cascades are not enabled by default.
        await self.db.execute(
            delete(RawEventRow).where(RawEventRow.object_id == object_id)
        )
        await self.db.execute(
            delete(UploadSessionRow).where(UploadSessionRow.object_id == object_id)
        )
        await self.db.execute(
            delete(BinaryObjectRow).where(BinaryObjectRow.id == object_id)
        )

    async def count(self) -> int:
        value = await self.db.scalar(select(func.count()).select_from(BinaryObjectRow))
        return int(value or 0)
