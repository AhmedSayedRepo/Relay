from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from relay.application.ports import UserRepository


@dataclass(frozen=True, slots=True)
class ResetToken:
    id: UUID
    user_id: UUID
    token_hash: str = field(repr=False)
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str = field(repr=False)


class RecoveryRepository(Protocol):
    async def replace_codes(
        self, user_id: UUID, digests: list[str], now: datetime
    ) -> None: ...
    async def remaining_codes(self, user_id: UUID) -> int: ...
    async def use_code(self, digest: str, now: datetime) -> UUID | None: ...
    async def add_reset_token(self, token: ResetToken) -> None: ...
    async def reset_tokens_since(self, user_id: UUID, since: datetime) -> int: ...
    async def use_reset_token(self, digest: str, now: datetime) -> UUID | None: ...
    async def close_reset_tokens(self, user_id: UUID, now: datetime) -> None: ...


class RecoveryUnitOfWork(Protocol):
    @property
    def users(self) -> UserRepository: ...
    @property
    def recovery(self) -> RecoveryRepository: ...
    async def commit(self) -> None: ...


class Mailer(Protocol):
    async def send(self, email: OutgoingEmail) -> None: ...
