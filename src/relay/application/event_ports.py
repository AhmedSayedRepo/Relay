from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from relay.domain.agent import JsonValue
from relay.domain.events import RawEvent


class AddEventResult(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


class EventRepository(Protocol):
    async def add(self, event: RawEvent) -> AddEventResult: ...
    async def by_identity(
        self, installation_id: UUID, event_id: UUID
    ) -> RawEvent | None: ...

    async def configuration(
        self, device_id: UUID, version: int
    ) -> dict[str, JsonValue] | None: ...

    async def list(
        self,
        *,
        device_id: UUID | None,
        collector: str | None,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
        limit: int,
        offset: int,
    ) -> list[RawEvent]: ...

    async def get(self, event_id: UUID) -> RawEvent | None: ...

    async def delete_expired(self, before: datetime) -> int: ...

    async def delete_device(self, device_id: UUID) -> int: ...


class EventUnitOfWork(Protocol):
    @property
    def events(self) -> EventRepository: ...

    async def commit(self) -> None: ...
