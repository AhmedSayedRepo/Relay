from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from relay.application.account_recovery_ports import ResetToken
from relay.infrastructure.recovery_models import PasswordResetRow, RecoveryCodeRow


class SqlRecoveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_codes(
        self, user_id: UUID, digests: list[str], now: datetime
    ) -> None:
        await self.session.execute(
            delete(RecoveryCodeRow).where(RecoveryCodeRow.user_id == user_id)
        )
        self.session.add_all(
            RecoveryCodeRow(
                id=uuid4(), user_id=user_id, code_hash=value, created_at=now
            )
            for value in digests
        )
        await self.session.flush()

    async def remaining_codes(self, user_id: UUID) -> int:
        count = await self.session.scalar(
            select(func.count())
            .select_from(RecoveryCodeRow)
            .where(
                RecoveryCodeRow.user_id == user_id, RecoveryCodeRow.used_at.is_(None)
            )
        )
        return int(count or 0)

    async def use_code(self, digest: str, now: datetime) -> UUID | None:
        # One conditional UPDATE: two concurrent requests cannot both use a code.
        user_id: UUID | None = await self.session.scalar(
            update(RecoveryCodeRow)
            .where(
                RecoveryCodeRow.code_hash == digest, RecoveryCodeRow.used_at.is_(None)
            )
            .values(used_at=now)
            .returning(RecoveryCodeRow.user_id)
        )
        return user_id

    async def add_reset_token(self, token: ResetToken) -> None:
        self.session.add(
            PasswordResetRow(
                id=token.id,
                user_id=token.user_id,
                token_hash=token.token_hash,
                created_at=token.created_at,
                expires_at=token.expires_at,
            )
        )
        await self.session.flush()

    async def reset_tokens_since(self, user_id: UUID, since: datetime) -> int:
        count = await self.session.scalar(
            select(func.count())
            .select_from(PasswordResetRow)
            .where(
                PasswordResetRow.user_id == user_id,
                PasswordResetRow.created_at >= since,
            )
        )
        return int(count or 0)

    async def use_reset_token(self, digest: str, now: datetime) -> UUID | None:
        user_id: UUID | None = await self.session.scalar(
            update(PasswordResetRow)
            .where(
                PasswordResetRow.token_hash == digest,
                PasswordResetRow.used_at.is_(None),
                PasswordResetRow.expires_at > now,
            )
            .values(used_at=now)
            .returning(PasswordResetRow.user_id)
        )
        return user_id

    async def close_reset_tokens(self, user_id: UUID, now: datetime) -> None:
        await self.session.execute(
            update(PasswordResetRow)
            .where(
                PasswordResetRow.user_id == user_id, PasswordResetRow.used_at.is_(None)
            )
            .values(used_at=now)
        )
