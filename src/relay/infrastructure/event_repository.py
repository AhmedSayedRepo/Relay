from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select, update
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

    async def nearby_sms(
        self, device_id: UUID, start: datetime, end: datetime
    ) -> list[tuple[UUID, str, str]]:
        """Bank SMS and InstaPay notifications from one phone in a window:
        (event id, sender, text)."""
        rows = await self.session.execute(
            select(RawEventRow.id, RawEventRow.payload).where(
                RawEventRow.device_id == device_id,
                RawEventRow.collector == "sms",
                RawEventRow.occurred_at >= start,
                RawEventRow.occurred_at <= end,
            )
        )
        return [
            (event_id, str(payload.get("sender")), str(payload.get("body")))
            for event_id, payload in rows.all()
            if isinstance(payload, dict)
        ]

    async def replace_sms(self, old_id: UUID, new_id: UUID) -> None:
        """The bank SMS takes the place of the InstaPay notification for the
        same transfer: a receipt it confirmed stays confirmed, by the SMS."""
        from relay.infrastructure.sms_verification_models import SmsVerificationRow

        await self.session.execute(
            update(SmsVerificationRow)
            .where(SmsVerificationRow.sms_event_id == old_id)
            .values(sms_event_id=new_id)
        )
        await self.session.execute(delete(RawEventRow).where(RawEventRow.id == old_id))

    async def mark_instapay_seen(self, event_id: UUID) -> None:
        """The InstaPay notification of this bank SMS's transfer also came."""
        row = await self.session.get(RawEventRow, event_id)
        if row is not None:
            metadata = dict(row.metadata_json or {})
            metadata["instapay_seen"] = True
            row.metadata_json = metadata

    async def same_sms_seen(
        self, occurred_at: datetime, sender: str, body: str
    ) -> bool:
        """The same bank SMS (sender, text, time received) already stored,
        e.g. from before the app was reinstalled and caught up its inbox."""
        rows = await self.session.scalars(
            select(RawEventRow.payload).where(
                RawEventRow.collector == "sms",
                RawEventRow.occurred_at == occurred_at,
            )
        )
        return any(
            isinstance(payload, dict)
            and payload.get("sender") == sender
            and payload.get("body") == body
            for payload in rows
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
