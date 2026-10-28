from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from relay.domain.sms_verification import (
    Decision,
    MatchMethod,
    ReceiptFacts,
    SmsDirection,
    SmsFacts,
    VerificationStatus,
)


@dataclass(frozen=True, slots=True)
class SmsEvent:
    event_id: UUID
    device_id: UUID
    occurred_at: datetime
    body: str


@dataclass(frozen=True, slots=True)
class StoredVerification:
    candidate_id: UUID
    status: VerificationStatus
    method: MatchMethod
    sms: SmsFacts | None
    option_count: int
    decided_by: UUID | None
    updated_at: datetime


class SmsVerificationRepository(Protocol):
    async def existing_devices(self, device_ids: set[UUID]) -> set[UUID]: ...

    async def sms_sources(self, device_id: UUID) -> list[UUID]: ...

    async def sms_direction(self, device_id: UUID) -> SmsDirection: ...

    async def set_sms_sources(
        self,
        device_id: UUID,
        sources: list[UUID],
        direction: SmsDirection,
        actor_id: UUID,
        now: datetime,
    ) -> None: ...

    async def candidate_device(self, candidate_id: UUID) -> UUID | None: ...

    async def eligible_receipts(
        self, *, since: datetime | None = None, candidate_id: UUID | None = None
    ) -> list[ReceiptFacts]: ...

    async def sms_events(
        self, device_ids: set[UUID], since: datetime, until: datetime
    ) -> list[SmsEvent]: ...

    async def get(self, candidate_id: UUID) -> StoredVerification | None: ...

    async def manual_rows(self) -> dict[UUID, StoredVerification]: ...

    async def delete_auto_since(self, since: datetime) -> None: ...

    async def held_events(
        self, *, except_candidate: UUID | None = None
    ) -> set[UUID]: ...

    async def add_decisions(self, decisions: list[Decision], now: datetime) -> None: ...

    async def save(self, verification: StoredVerification) -> None: ...

    async def delete(self, candidate_id: UUID) -> None: ...


class SmsVerificationUnitOfWork(Protocol):
    @property
    def sms_verification(self) -> SmsVerificationRepository: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
