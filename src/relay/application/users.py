"""Operator accounts: admins add, change and remove users."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from relay.application.errors import Conflict, NotFound
from relay.application.ports import PasswordHasher, UnitOfWork
from relay.domain.users import Role, User

MIN_PASSWORD = 12


class UserAdminService:
    def __init__(self, uow: UnitOfWork, passwords: PasswordHasher) -> None:
        self.uow = uow
        self.passwords = passwords

    async def list(self) -> list[User]:
        return await self.uow.users.list_all()

    async def create(self, email: str, role: Role, password: str) -> User:
        if not MIN_PASSWORD <= len(password) <= 1024:
            raise Conflict("Password must contain 12 to 1024 characters.")
        user = User(
            id=uuid4(),
            email=email.strip().lower(),
            password_hash=await self.passwords.hash(password),
            is_active=True,
            created_at=datetime.now(UTC),
            role=role,
        )
        await self.uow.users.add(user)
        await self.uow.commit()
        return user

    async def update(
        self,
        actor: User,
        user_id: UUID,
        *,
        role: Role | None = None,
        is_active: bool | None = None,
        password: str | None = None,
    ) -> User:
        target = await self._get(user_id)
        if target.id == actor.id and (
            (role is not None and role != Role.ADMIN) or is_active is False
        ):
            raise Conflict("You cannot remove your own admin access.")
        losing_admin = target.role == Role.ADMIN and target.is_active
        if losing_admin and (
            (role is not None and role != Role.ADMIN) or is_active is False
        ):
            await self._keep_an_admin()
        if password is not None:
            if not MIN_PASSWORD <= len(password) <= 1024:
                raise Conflict("Password must contain 12 to 1024 characters.")
            await self.uow.users.set_password(
                target.id, await self.passwords.hash(password)
            )
        await self.uow.users.update(target.id, role=role, is_active=is_active)
        await self.uow.commit()
        return await self._get(user_id)

    async def delete(self, actor: User, user_id: UUID) -> bool:
        target = await self._get(user_id)
        if target.id == actor.id:
            raise Conflict("You cannot delete your own account.")
        if target.role == Role.ADMIN and target.is_active:
            await self._keep_an_admin()
        removed = await self.uow.users.delete(target.id)
        await self.uow.commit()
        return removed

    async def _get(self, user_id: UUID) -> User:
        user = await self.uow.users.get(user_id)
        if user is None or "#deleted-" in user.email:
            raise NotFound("User not found.")
        return user

    async def _keep_an_admin(self) -> None:
        if await self.uow.users.active_admin_count() <= 1:
            raise Conflict("Relay needs at least one active admin.")
