from datetime import UTC, datetime
from uuid import uuid4

from relay.application.errors import AuthenticationFailed, NotFound
from relay.application.ports import (
    AccessToken,
    PasswordHasher,
    TokenProvider,
    UnitOfWork,
)
from relay.domain.users import User


class AuthService:
    def __init__(
        self, uow: UnitOfWork, passwords: PasswordHasher, tokens: TokenProvider
    ) -> None:
        self.uow = uow
        self.passwords = passwords
        self.tokens = tokens

    async def login(self, email: str, password: str) -> AccessToken:
        user = await self.uow.users.get_by_email(email.strip().lower())
        valid = await self.passwords.verify(
            password, user.password_hash if user else None
        )
        if not valid or user is None or not user.is_active:
            raise AuthenticationFailed("Invalid credentials.")
        return self.tokens.issue(user.id)

    async def refresh(self, user: User) -> AccessToken:
        """Issue a fresh short-lived token to a still-active, signed-in user."""
        if not user.is_active:
            raise AuthenticationFailed("Invalid or expired access token.")
        return self.tokens.issue(user.id)

    async def current_user(self, token: str) -> User:
        user = await self.uow.users.get(self.tokens.subject(token))
        if user is None or not user.is_active:
            raise AuthenticationFailed("Invalid or expired access token.")
        return user


async def create_admin(
    uow: UnitOfWork, passwords: PasswordHasher, email: str, password: str
) -> User:
    # This is an operator-only use case; no public registration endpoint exists.
    if not 12 <= len(password) <= 1024:
        raise ValueError("Password must contain 12 to 1024 characters.")
    user = User(
        id=uuid4(),
        email=email.strip().lower(),
        password_hash=await passwords.hash(password),
        is_active=True,
        created_at=datetime.now(UTC),
    )
    await uow.users.add(user)
    await uow.commit()
    return user


async def disable_user(uow: UnitOfWork, email: str) -> None:
    user = await uow.users.get_by_email(email.strip().lower())
    if user is None:
        raise NotFound("User not found.")
    await uow.users.disable(user.id)
    await uow.commit()
