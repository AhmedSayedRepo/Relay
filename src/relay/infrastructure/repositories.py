from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from relay.application.errors import Conflict
from relay.domain.devices import Device, DeviceStatus
from relay.domain.users import Role, User
from relay.infrastructure.agent_models import ConfigurationRow
from relay.infrastructure.agent_repository import SqlAgentRepository
from relay.infrastructure.binary_repository import SqlBinaryRepository
from relay.infrastructure.datetime_utils import aware
from relay.infrastructure.event_repository import SqlEventRepository
from relay.infrastructure.models import DeviceRow, UserRow
from relay.infrastructure.operations_repository import SqlOperationsRepository
from relay.infrastructure.processing_repository import SqlProcessingRepository
from relay.infrastructure.recovery_repository import SqlRecoveryRepository
from relay.infrastructure.sms_verification_repository import (
    SqlSmsVerificationRepository,
)
from relay.infrastructure.training_repository import SqlTrainingRepository


def to_user(row: UserRow) -> User:
    return User(
        row.id,
        row.email,
        row.password_hash,
        row.is_active,
        aware(row.created_at),
        Role(row.role) if row.role in Role.__members__ else Role.VIEWER,
    )


def to_device(row: DeviceRow) -> Device:
    return Device(
        row.id,
        row.external_device_id,
        row.name,
        row.platform,
        row.app_version,
        row.status,
        aware(row.created_at),
        aware(row.updated_at),
        aware(row.last_seen_at) if row.last_seen_at else None,
    )


async def flush_unique(session: AsyncSession, *, constraint: str, column: str) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        # Handle only the known uniqueness constraint, not arbitrary integrity failures.
        original = exc.orig
        sqlstate = getattr(original, "sqlstate", None)
        postgres_duplicate = sqlstate == "23505" and constraint in str(original)
        sqlite_duplicate = str(original) == f"UNIQUE constraint failed: {column}"
        await session.rollback()
        if postgres_duplicate or sqlite_duplicate:
            raise Conflict("The unique identifier is already registered.") from None
        raise


class SqlUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_email(self, email: str) -> User | None:
        row = await self.session.scalar(select(UserRow).where(UserRow.email == email))
        return to_user(row) if row else None

    async def get(self, user_id: UUID) -> User | None:
        row = await self.session.get(UserRow, user_id)
        return to_user(row) if row else None

    async def add(self, user: User) -> None:
        self.session.add(
            UserRow(
                id=user.id,
                email=user.email,
                password_hash=user.password_hash,
                is_active=user.is_active,
                created_at=user.created_at,
                role=user.role.value,
            )
        )
        await flush_unique(
            self.session, constraint="uq_users_email", column="users.email"
        )

    async def list_all(self) -> list[User]:
        rows = await self.session.scalars(
            select(UserRow).where(UserRow.deleted_at.is_(None)).order_by(UserRow.email)
        )
        return [to_user(row) for row in rows]

    async def update(
        self, user_id: UUID, *, role: Role | None = None, is_active: bool | None = None
    ) -> None:
        values: dict[str, object] = {}
        if role is not None:
            values["role"] = role.value
        if is_active is not None:
            values["is_active"] = is_active
        if values:
            await self.session.execute(
                update(UserRow).where(UserRow.id == user_id).values(**values)
            )

    async def active_admin_count(self) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(UserRow)
                .where(
                    UserRow.is_active.is_(True),
                    UserRow.deleted_at.is_(None),
                    UserRow.role == Role.ADMIN.value,
                )
            )
            or 0
        )

    async def delete(self, user_id: UUID) -> bool:
        """Remove a user; one with review history is hidden and disabled
        instead, and their email freed. Returns True when fully removed."""
        row = await self.session.get(UserRow, user_id)
        if row is None:
            return True
        try:
            async with self.session.begin_nested():
                await self.session.delete(row)
            return True
        except IntegrityError:
            row = await self.session.get(UserRow, user_id)
            if row is None:
                return True
            row.is_active = False
            row.deleted_at = datetime.now(UTC)
            row.email = f"{row.email}#deleted-{str(row.id)[:8]}"[:254]
            return False

    async def disable(self, user_id: UUID) -> None:
        await self.session.execute(
            update(UserRow).where(UserRow.id == user_id).values(is_active=False)
        )

    async def set_password(self, user_id: UUID, password_hash: str) -> None:
        await self.session.execute(
            update(UserRow)
            .where(UserRow.id == user_id)
            .values(password_hash=password_hash)
        )

    async def list_active(self) -> list[User]:
        rows = await self.session.scalars(
            select(UserRow).where(UserRow.is_active.is_(True)).order_by(UserRow.email)
        )
        return [to_user(row) for row in rows]


class SqlDeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, device_id: UUID, *, for_update: bool = False) -> Device | None:
        query = select(DeviceRow).where(DeviceRow.id == device_id)
        if for_update:
            query = query.with_for_update()
        row = await self.session.scalar(query)
        return to_device(row) if row else None

    async def list(self, *, limit: int, offset: int) -> list[Device]:
        rows = await self.session.scalars(
            select(DeviceRow)
            .order_by(DeviceRow.created_at, DeviceRow.id)
            .limit(limit)
            .offset(offset)
        )
        return [to_device(row) for row in rows]

    async def name_in_use(self, name: str, exclude: UUID | None = None) -> bool:
        query = select(DeviceRow.id).where(
            func.lower(func.trim(DeviceRow.name)) == name.strip().lower(),
            DeviceRow.status != DeviceStatus.DISABLED,
        )
        if exclude is not None:
            query = query.where(DeviceRow.id != exclude)
        return await self.session.scalar(query.limit(1)) is not None

    async def add(self, device: Device) -> None:
        self.session.add(
            DeviceRow(
                id=device.id,
                external_device_id=device.external_device_id,
                name=device.name,
                platform=device.platform,
                app_version=device.app_version,
                status=device.status,
                created_at=device.created_at,
                updated_at=device.updated_at,
                last_seen_at=device.last_seen_at,
            )
        )
        await flush_unique(
            self.session,
            constraint="uq_devices_external_device_id",
            column="devices.external_device_id",
        )
        self.session.add(
            ConfigurationRow(
                device_id=device.id, version=1, schema_version=1, settings={}
            )
        )
        await self.session.flush()

    async def save(self, device: Device) -> None:
        await self.session.execute(
            update(DeviceRow)
            .where(DeviceRow.id == device.id)
            .values(
                name=device.name,
                status=device.status,
                app_version=device.app_version,
                updated_at=device.updated_at,
            )
        )


class SqlUnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = SqlUserRepository(session)
        self.devices = SqlDeviceRepository(session)
        self.agents = SqlAgentRepository(session)
        self.events = SqlEventRepository(session)
        self.binaries = SqlBinaryRepository(session)
        self.processing = SqlProcessingRepository(session)
        self.operations = SqlOperationsRepository(session)
        self.training = SqlTrainingRepository(session)
        self.sms_verification = SqlSmsVerificationRepository(session)
        self.recovery = SqlRecoveryRepository(session)

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
