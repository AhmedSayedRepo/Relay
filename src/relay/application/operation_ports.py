"""Ports for Sprint 6 operational read models, actions, and exports."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from relay.domain.agent import JsonValue
from relay.domain.operations import DateWindow, DuplicateGroupQuery, TransactionQuery
from relay.domain.processing import ProcessingJob


class OperationsRepository(Protocol):
    async def dashboard(self, window: DateWindow) -> dict[str, JsonValue]: ...

    async def money_totals(self, window: DateWindow) -> list[JsonValue]: ...

    async def transactions(
        self, query: TransactionQuery
    ) -> tuple[list[dict[str, JsonValue]], int]: ...

    async def transaction(self, candidate_id: UUID) -> dict[str, JsonValue] | None: ...

    async def duplicate_groups(
        self, query: DuplicateGroupQuery
    ) -> tuple[list[dict[str, JsonValue]], int]: ...

    async def duplicate_group(
        self, group_id: UUID, *, limit: int, offset: int
    ) -> dict[str, JsonValue] | None: ...

    async def resolve_duplicate(
        self,
        group_id: UUID,
        candidate_id: UUID,
        actor_id: UUID,
        decision: str,
        note: str | None,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> dict[str, JsonValue] | None: ...

    async def retry_processing(
        self,
        job_id: UUID,
        actor_id: UUID,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> ProcessingJob | None: ...

    async def devices(
        self, *, limit: int, offset: int
    ) -> tuple[list[dict[str, JsonValue]], int]: ...

    async def add_export_audit(
        self,
        actor_id: UUID,
        context: dict[str, JsonValue],
        *,
        now: datetime,
        expires_at: datetime,
    ) -> None: ...

    async def purge_expired_audit(self, before: datetime) -> int: ...


class OperationsUnitOfWork(Protocol):
    @property
    def operations(self) -> OperationsRepository: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class WorkbookExporter(Protocol):
    def build(
        self,
        transactions: list[dict[str, JsonValue]],
        summary: dict[str, JsonValue],
        filters: dict[str, JsonValue],
        exported_at: datetime,
    ) -> bytes: ...
