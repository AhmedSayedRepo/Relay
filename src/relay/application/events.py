"""Generic ingestion and retention use cases."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from relay.application.errors import NotFound
from relay.application.event_ports import AddEventResult, EventUnitOfWork
from relay.domain.agent import JsonValue
from relay.domain.events import (
    EventRejected,
    RawEvent,
    parse_submission,
    sms_rule_matches,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ItemResult:
    event_id: str
    status: str
    reason: str | None = None


class EventService:
    def __init__(self, uow: EventUnitOfWork, retention_days: int = 30) -> None:
        self.uow = uow
        self.retention_days = retention_days

    async def ingest(
        self,
        device_id: UUID,
        installation_id: UUID,
        items: list[JsonValue],
    ) -> list[ItemResult]:
        now = datetime.now(UTC)
        results: list[ItemResult] = []
        counts = {"accepted": 0, "duplicate": 0, "rejected": 0}
        for raw in items:
            supplied_id = (
                str(raw.get("event_id", "invalid"))[:64]
                if isinstance(raw, dict)
                else "invalid"
            )
            try:
                value = parse_submission(raw, now)
                supplied_id = str(value.event_id)
                if value.installation_id != installation_id:
                    raise EventRejected("wrong_installation")
                settings = await self.uow.events.configuration(
                    device_id, value.config_version
                )
                if settings is None or not sms_rule_matches(
                    settings, value.rule_id, value.sender, value.body
                ):
                    raise EventRejected("collector_not_enabled_or_unmatched")
                event = RawEvent(
                    uuid4(),
                    value.event_id,
                    device_id,
                    installation_id,
                    value.collector,
                    value.schema_version,
                    value.config_version,
                    value.rule_id,
                    value.occurred_at,
                    value.captured_at,
                    now,
                    {"sender": value.sender, "body": value.body},
                    {"rule_id": value.rule_id},
                    value.content_hash,
                    now + timedelta(days=self.retention_days),
                )
                stored = await self.uow.events.add(event)
                if stored == AddEventResult.CONFLICT:
                    raise EventRejected("event_id_conflict")
                status = stored.value
                counts[status] += 1
                results.append(ItemResult(supplied_id, status))
            except EventRejected as exc:
                counts["rejected"] += 1
                results.append(ItemResult(supplied_id, "rejected", exc.reason))
        await self.uow.commit()
        logger.info(
            "events.batch_processed",
            extra={
                "accepted_count": counts["accepted"],
                "duplicate_count": counts["duplicate"],
                "rejected_count": counts["rejected"],
            },
        )
        return results

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
        return await self.uow.events.list(
            device_id=device_id,
            collector=collector,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
            limit=limit,
            offset=offset,
        )

    async def get(self, event_id: UUID) -> RawEvent:
        value = await self.uow.events.get(event_id)
        if value is None:
            raise NotFound("Event not found.")
        return value

    async def delete_device(self, device_id: UUID) -> int:
        count = await self.uow.events.delete_device(device_id)
        await self.uow.commit()
        logger.info(
            "events.device_deleted", extra={"device_id": str(device_id), "count": count}
        )
        return count

    async def purge_expired(self, before: datetime | None = None) -> int:
        count = await self.uow.events.delete_expired(before or datetime.now(UTC))
        await self.uow.commit()
        logger.info("events.retention_purged", extra={"count": count})
        return count
