from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from relay.application.event_ports import AddEventResult
from relay.domain.agent import JsonValue
from relay.domain.events import RawEvent
from relay.infrastructure.agent_models import ConfigurationRow
from relay.infrastructure.datetime_utils import aware
from relay.infrastructure.event_models import RawEventRow


def to_event(row: RawEventRow) -> RawEvent:
    return RawEvent(
        row.id,
        row.event_id,
        row.device_id,
        row.installation_id,
        row.collector,
        row.schema_version,
        row.config_version,
        row.rule_id,
        aware(row.occurred_at),
        aware(row.captured_at),
        aware(row.received_at),
        row.payload,
        row.metadata_json,
        row.content_hash,
        aware(row.delete_after),
        row.object_id,
    )


class SqlEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, event: RawEvent) -> AddEventResult:
        row = RawEventRow(
            id=event.id,
            event_id=event.event_id,
            device_id=event.device_id,
            installation_id=event.installation_id,
            collector=event.collector,
            schema_version=event.schema_version,
            config_version=event.config_version,
            rule_id=event.rule_id,
            occurred_at=event.occurred_at,
            captured_at=event.captured_at,
            received_at=event.received_at,
            payload=event.payload,
            metadata_json=event.metadata,
            content_hash=event.content_hash,
            ingestion_status="accepted",
            delete_after=event.delete_after,
            object_id=event.object_id,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
            return AddEventResult.ACCEPTED
        except IntegrityError as exc:
            sqlstate = getattr(exc.orig, "sqlstate", None)
            postgres_duplicate = (
                sqlstate == "23505"
                and "uq_raw_events_installation_event" in str(exc.orig)
            )
            sqlite_duplicate = (
                "UNIQUE constraint failed: "
                "raw_events.installation_id, raw_events.event_id"
            ) in str(exc.orig)
            if not (postgres_duplicate or sqlite_duplicate):
                raise
        existing_hash = await self.session.scalar(
            select(RawEventRow.content_hash).where(
                RawEventRow.installation_id == event.installation_id,
                RawEventRow.event_id == event.event_id,
            )
        )
        return (
            AddEventResult.DUPLICATE
            if existing_hash == event.content_hash
            else AddEventResult.CONFLICT
        )

    async def by_identity(
        self, installation_id: UUID, event_id: UUID
    ) -> RawEvent | None:
        row = await self.session.scalar(
            select(RawEventRow).where(
                RawEventRow.installation_id == installation_id,
                RawEventRow.event_id == event_id,
            )
        )
        return to_event(row) if row else None

    async def configuration(
        self, device_id: UUID, version: int
    ) -> dict[str, JsonValue] | None:
        value = await self.session.scalar(
            select(ConfigurationRow.settings).where(
                ConfigurationRow.device_id == device_id,
                ConfigurationRow.version == version,
            )
        )
        return value

    async def list(
        self,
        *,
        device_id: UUID | None,
        collector: str | None,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
        limit: int,
        offset: int,
    ) -> list[RawEvent]:
        query = select(RawEventRow)
        if device_id is not None:
            query = query.where(RawEventRow.device_id == device_id)
        if collector is not None:
            query = query.where(RawEventRow.collector == collector)
        if occurred_from is not None:
            query = query.where(RawEventRow.occurred_at >= occurred_from)
        if occurred_to is not None:
            query = query.where(RawEventRow.occurred_at <= occurred_to)
        rows = await self.session.scalars(
            query.order_by(RawEventRow.occurred_at.desc(), RawEventRow.id)
            .limit(limit)
            .offset(offset)
        )
        return [to_event(row) for row in rows]

    async def get(self, event_id: UUID) -> RawEvent | None:
        row = await self.session.get(RawEventRow, event_id)
        return to_event(row) if row else None

    async def delete_expired(self, before: datetime) -> int:
        result = cast(
            CursorResult[tuple[()]],
            await self.session.execute(
                delete(RawEventRow).where(RawEventRow.delete_after <= before)
            ),
        )
        return int(result.rowcount or 0)

    async def delete_device(self, device_id: UUID) -> int:
        result = cast(
            CursorResult[tuple[()]],
            await self.session.execute(
                delete(RawEventRow).where(RawEventRow.device_id == device_id)
            ),
        )
        return int(result.rowcount or 0)

    async def count(self) -> int:
        value = await self.session.scalar(select(func.count()).select_from(RawEventRow))
        return int(value or 0)
