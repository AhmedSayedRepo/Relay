from dataclasses import asdict
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from relay.application.errors import Conflict
from relay.domain.agent import Challenge, Configuration, Credential, Installation
from relay.infrastructure.agent_models import (
    ChallengeRow,
    ConfigurationRow,
    CredentialRow,
    DeliveryRow,
    InstallationRow,
)
from relay.infrastructure.datetime_utils import aware
from relay.infrastructure.models import DeviceRow


class SqlAgentRepository:
    """Caller holds device row lock for every mutation and authorization transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def challenge(self, identifier: UUID) -> Challenge | None:
        row = await self.session.get(ChallengeRow, identifier, populate_existing=True)
        if row is None:
            return None
        return Challenge(
            row.id,
            row.device_id,
            row.secret_hash,
            aware(row.expires_at),
            row.replace_existing,
            row.attempts,
            aware(row.consumed_at) if row.consumed_at else None,
            aware(row.revoked_at) if row.revoked_at else None,
        )

    async def add_challenge(self, value: Challenge) -> None:
        self.session.add(ChallengeRow(**asdict(value)))
        await self.session.flush()

    async def save_challenge(self, value: Challenge) -> None:
        await self.session.execute(
            update(ChallengeRow)
            .where(ChallengeRow.id == value.id)
            .values(**asdict(value))
        )

    async def installation(self, identifier: UUID) -> Installation | None:
        row = await self.session.get(
            InstallationRow, identifier, populate_existing=True
        )
        return self._installation(row) if row else None

    @staticmethod
    def _installation(row: InstallationRow) -> Installation:
        return Installation(
            row.id,
            row.device_id,
            aware(row.enrolled_at),
            aware(row.revoked_at) if row.revoked_at else None,
            row.acknowledged_version,
            row.reported_config_version,
        )

    async def current_installation(self, device_id: UUID) -> Installation | None:
        row = await self.session.scalar(
            select(InstallationRow).where(
                InstallationRow.device_id == device_id,
                InstallationRow.revoked_at.is_(None),
            )
        )
        return self._installation(row) if row else None

    async def add_installation(self, value: Installation) -> None:
        # A nested transaction lets cross-device UUID collisions fail safely without
        # rolling back the challenge attempt already recorded in the parent transaction.
        from sqlalchemy.exc import IntegrityError

        try:
            async with self.session.begin_nested():
                self.session.add(InstallationRow(**asdict(value)))
                await self.session.flush()
        except IntegrityError:
            raise Conflict("Installation identity is already registered.") from None

    async def save_installation(self, value: Installation) -> None:
        await self.session.execute(
            update(InstallationRow)
            .where(InstallationRow.id == value.id)
            .values(**asdict(value))
        )

    async def credential(self, identifier: UUID) -> Credential | None:
        row = await self.session.get(CredentialRow, identifier, populate_existing=True)
        return self._credential(row) if row else None

    @staticmethod
    def _credential(row: CredentialRow) -> Credential:
        return Credential(
            row.id,
            row.installation_id,
            row.device_id,
            row.secret_hash,
            aware(row.expires_at),
            aware(row.created_at),
            aware(row.consumed_at) if row.consumed_at else None,
            aware(row.revoked_at) if row.revoked_at else None,
            row.consumed_request_id,
            row.successor_id,
        )

    async def active_credential(self, installation_id: UUID) -> Credential | None:
        row = await self.session.scalar(
            select(CredentialRow).where(
                CredentialRow.installation_id == installation_id,
                CredentialRow.revoked_at.is_(None),
                CredentialRow.consumed_at.is_(None),
            )
        )
        return self._credential(row) if row else None

    async def add_credential(self, value: Credential) -> None:
        self.session.add(CredentialRow(**asdict(value)))
        await self.session.flush()

    async def save_credential(self, value: Credential) -> None:
        await self.session.execute(
            update(CredentialRow)
            .where(CredentialRow.id == value.id)
            .values(**asdict(value))
        )

    async def revoke(
        self, device_id: UUID, now: datetime, *, challenges: bool = True
    ) -> None:
        await self.session.execute(
            update(InstallationRow)
            .where(
                InstallationRow.device_id == device_id,
                InstallationRow.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await self.session.execute(
            update(CredentialRow)
            .where(
                CredentialRow.device_id == device_id, CredentialRow.revoked_at.is_(None)
            )
            .values(revoked_at=now)
        )
        if challenges:
            await self.session.execute(
                update(ChallengeRow)
                .where(
                    ChallengeRow.device_id == device_id,
                    ChallengeRow.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )

    async def configuration(self, device_id: UUID) -> Configuration:
        row = await self.session.scalar(
            select(ConfigurationRow)
            .where(ConfigurationRow.device_id == device_id)
            .order_by(ConfigurationRow.version.desc())
            .limit(1)
        )
        if row is None:
            # Version 1 is also materialized for pre-enrollment S1 identities.
            return Configuration(device_id, 1, 1, {})
        return Configuration(
            row.device_id, row.version, row.schema_version, row.settings
        )

    async def add_configuration(self, value: Configuration) -> None:
        self.session.add(ConfigurationRow(**asdict(value)))
        await self.session.flush()

    async def mark_delivered(self, installation_id: UUID, value: Configuration) -> None:
        if not await self.was_delivered(installation_id, value.version):
            self.session.add(
                DeliveryRow(
                    installation_id=installation_id,
                    device_id=value.device_id,
                    version=value.version,
                )
            )
            await self.session.flush()

    async def was_delivered(self, installation_id: UUID, version: int) -> bool:
        return (
            await self.session.get(DeliveryRow, (installation_id, version)) is not None
        )

    async def contact(
        self,
        device_id: UUID,
        now: datetime,
        app_version: str | None,
        coalesce_seconds: int,
    ) -> datetime:
        row = await self.session.get(DeviceRow, device_id, populate_existing=True)
        assert row is not None
        prior = aware(row.last_seen_at) if row.last_seen_at else None
        changed = app_version is not None and row.app_version != app_version
        if (
            prior is None
            or now - prior >= timedelta(seconds=coalesce_seconds)
            or changed
        ):
            row.last_seen_at = now
            if app_version is not None:
                row.app_version = app_version
            await self.session.flush()
            return now
        return prior
