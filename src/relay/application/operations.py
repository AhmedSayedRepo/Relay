"""Operator dashboard, transaction, duplicate, retry, and export use cases."""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from uuid import UUID

from relay.application.errors import Conflict, NotFound
from relay.application.operation_ports import OperationsUnitOfWork, WorkbookExporter
from relay.domain.agent import JsonValue
from relay.domain.operations import DateWindow, DuplicateGroupQuery, TransactionQuery
from relay.domain.processing import ProcessingJob

MAX_RANGE_DAYS = 366
MAX_EXPORT_ROWS = 5_000
DUPLICATE_DECISIONS = {
    "CONFIRMED_DUPLICATE",
    "NOT_DUPLICATE",
    "UNRESOLVED",
}


def date_window(
    date_from: date,
    date_to: date,
    *,
    zone: tzinfo = UTC,
    time_from: time | None = None,
    time_to: time | None = None,
    folder_time: bool = False,
) -> DateWindow:
    """Dates (and optional times, inclusive to the minute) in ``zone``."""
    days = (date_to - date_from).days
    if days < 0 or days >= MAX_RANGE_DAYS:
        raise Conflict("Date range must be ordered and no longer than 366 days.")
    start = datetime.combine(date_from, time_from or time.min, tzinfo=zone)
    end = (
        datetime.combine(date_to, time_to.replace(second=0, microsecond=0), zone)
        + timedelta(minutes=1)
        if time_to is not None
        else datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone)
    )
    if end <= start:
        raise Conflict("The end must be after the start.")
    return DateWindow(start, end, folder_time)


class OperationsService:
    def __init__(
        self,
        uow: OperationsUnitOfWork,
        exporter: WorkbookExporter | None = None,
        *,
        audit_retention_days: int = 365,
    ) -> None:
        self.uow = uow
        self.exporter = exporter
        self.audit_retention_days = audit_retention_days

    async def dashboard(self, window: DateWindow) -> dict[str, JsonValue]:
        return await self.uow.operations.dashboard(window)

    async def transactions(
        self, query: TransactionQuery
    ) -> tuple[list[dict[str, JsonValue]], int]:
        return await self.uow.operations.transactions(query)

    async def transaction(self, candidate_id: UUID) -> dict[str, JsonValue]:
        value = await self.uow.operations.transaction(candidate_id)
        if value is None:
            raise NotFound("Transaction not found.")
        return value

    async def duplicate_groups(
        self, query: DuplicateGroupQuery
    ) -> tuple[list[dict[str, JsonValue]], int]:
        return await self.uow.operations.duplicate_groups(query)

    async def duplicate_group(
        self, group_id: UUID, *, limit: int, offset: int
    ) -> dict[str, JsonValue]:
        value = await self.uow.operations.duplicate_group(
            group_id, limit=limit, offset=offset
        )
        if value is None:
            raise NotFound("Duplicate group not found.")
        return value

    async def resolve_duplicate(
        self,
        group_id: UUID,
        candidate_id: UUID,
        actor_id: UUID,
        decision: str,
        note: str | None,
    ) -> dict[str, JsonValue]:
        if decision not in DUPLICATE_DECISIONS:
            raise Conflict("Invalid duplicate resolution.")
        if note is not None:
            note = " ".join(note.split())
            if not 1 <= len(note) <= 500:
                raise Conflict("Invalid duplicate resolution note.")
        now = datetime.now(UTC)
        value = await self.uow.operations.resolve_duplicate(
            group_id,
            candidate_id,
            actor_id,
            decision,
            note,
            now=now,
            expires_at=now + timedelta(days=self.audit_retention_days),
        )
        if value is None:
            await self.uow.rollback()
            raise NotFound("Duplicate group member not found.")
        await self.uow.commit()
        return value

    async def retry_processing(self, job_id: UUID, actor_id: UUID) -> ProcessingJob:
        now = datetime.now(UTC)
        value = await self.uow.operations.retry_processing(
            job_id,
            actor_id,
            now=now,
            expires_at=now + timedelta(days=self.audit_retention_days),
        )
        if value is None:
            await self.uow.rollback()
            raise Conflict("Processing job is not eligible for retry.")
        await self.uow.commit()
        return value

    async def devices(
        self, *, limit: int, offset: int
    ) -> tuple[list[dict[str, JsonValue]], int]:
        return await self.uow.operations.devices(limit=limit, offset=offset)

    async def export(
        self,
        query: TransactionQuery,
        actor_id: UUID,
        filters: dict[str, JsonValue],
    ) -> bytes:
        if self.exporter is None:
            raise RuntimeError("Workbook export is not configured.")
        export_query = replace(query, limit=MAX_EXPORT_ROWS + 1, offset=0)
        rows, _ = await self.uow.operations.transactions(export_query)
        if len(rows) > MAX_EXPORT_ROWS:
            raise Conflict("Export exceeds the 5000-row limit; narrow the filters.")
        summary = await self.uow.operations.dashboard(query.window)
        now = datetime.now(UTC)
        workbook = self.exporter.build(rows, summary, filters, now)
        await self.uow.operations.add_export_audit(
            actor_id,
            {
                "date_from": query.window.start.date().isoformat(),
                "date_to": (
                    query.window.end_exclusive.date() - timedelta(days=1)
                ).isoformat(),
                "filters": filters,
                "row_count": len(rows),
            },
            now=now,
            expires_at=now + timedelta(days=self.audit_retention_days),
        )
        await self.uow.commit()
        return workbook

    async def purge_expired_audit(self, before: datetime | None = None) -> int:
        count = await self.uow.operations.purge_expired_audit(
            before or datetime.now(UTC)
        )
        await self.uow.commit()
        return count
