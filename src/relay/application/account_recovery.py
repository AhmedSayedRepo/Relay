"""Account recovery: one-time recovery codes and optional email reset links.

Recovery codes carry ~60 random bits and are stored only as SHA-256 digests;
a code identifies its owner, so it also answers "I forgot my username".
Email reset links are optional and only exist when SMTP and a public URL are
configured. Every public response is generic so it never reveals accounts.
"""

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from relay.application.account_recovery_ports import (
    OutgoingEmail,
    RecoveryUnitOfWork,
    ResetToken,
)
from relay.application.errors import AuthenticationFailed
from relay.application.ports import PasswordHasher
from relay.domain.users import User

RECOVERY_CODE_COUNT = 10
# Crockford-style alphabet without 0/O/1/I/L so codes survive handwriting.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 12
MAX_RESET_EMAILS_PER_HOUR = 3
MIN_PASSWORD, MAX_PASSWORD = 12, 1024


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_recovery_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[index : index + 4] for index in range(0, CODE_LENGTH, 4))


def normalize_code(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


def check_password(password: str) -> None:
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise ValueError("Password must contain 12 to 1024 characters.")


class AccountRecoveryService:
    def __init__(
        self,
        uow: RecoveryUnitOfWork,
        passwords: PasswordHasher,
        *,
        public_url: str | None = None,
        reset_minutes: int = 15,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.uow = uow
        self.passwords = passwords
        self.public_url = public_url.rstrip("/") if public_url else None
        self.reset_minutes = reset_minutes
        self.clock = clock

    async def generate_codes(self, user_id: UUID) -> list[str]:
        codes = [new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
        await self.uow.recovery.replace_codes(
            user_id, [digest(normalize_code(code)) for code in codes], self.clock()
        )
        await self.uow.commit()
        return codes

    async def regenerate_codes(self, user: User, password: str) -> list[str]:
        await self._confirm_password(user, password)
        return await self.generate_codes(user.id)

    async def remaining_codes(self, user_id: UUID) -> int:
        return await self.uow.recovery.remaining_codes(user_id)

    async def change_password(self, user: User, current: str, new: str) -> None:
        check_password(new)
        await self._confirm_password(user, current)
        await self._set_password(user.id, new)

    async def redeem_code(self, code: str, new_password: str | None) -> str:
        """Use one recovery code; return the username, optionally resetting."""
        if new_password is not None:
            check_password(new_password)
        normalized = normalize_code(code)
        user_id = (
            await self.uow.recovery.use_code(digest(normalized), self.clock())
            if len(normalized) == CODE_LENGTH
            else None
        )
        user = await self.uow.users.get(user_id) if user_id else None
        if user is None or not user.is_active:
            # Nothing is committed, so a consumed code for a disabled user rolls back.
            raise AuthenticationFailed("Invalid recovery code.")
        if new_password is not None:
            await self._set_password(user.id, new_password)
        else:
            await self.uow.commit()
        return user.email

    async def request_reset(self, email: str) -> OutgoingEmail | None:
        if self.public_url is None:
            return None
        now = self.clock()
        user = await self.uow.users.get_by_email(email.strip().lower())
        if user is None or not user.is_active:
            return None
        recent = await self.uow.recovery.reset_tokens_since(
            user.id, now - timedelta(hours=1)
        )
        if recent >= MAX_RESET_EMAILS_PER_HOUR:
            return None
        secret = secrets.token_urlsafe(32)
        await self.uow.recovery.add_reset_token(
            ResetToken(
                uuid4(),
                user.id,
                digest(secret),
                now,
                now + timedelta(minutes=self.reset_minutes),
            )
        )
        await self.uow.commit()
        # The token lives in the URL fragment, so it never reaches server logs.
        link = f"{self.public_url}/#/reset-password?token={secret}"
        return OutgoingEmail(user.email, "Reset your Relay password", self._body(link))

    async def confirm_reset(self, token: str, new_password: str) -> None:
        check_password(new_password)
        user_id = await self.uow.recovery.use_reset_token(digest(token), self.clock())
        user = await self.uow.users.get(user_id) if user_id else None
        if user is None or not user.is_active:
            raise AuthenticationFailed("Invalid or expired reset link.")
        await self._set_password(user.id, new_password)

    async def _confirm_password(self, user: User, password: str) -> None:
        if not await self.passwords.verify(password, user.password_hash):
            raise AuthenticationFailed("Invalid credentials.")

    async def _set_password(self, user_id: UUID, password: str) -> None:
        await self.uow.users.set_password(user_id, await self.passwords.hash(password))
        await self.uow.recovery.close_reset_tokens(user_id, self.clock())
        await self.uow.commit()

    def _body(self, link: str) -> str:
        minutes = self.reset_minutes
        return (
            "Someone asked to reset your Relay password.\n"
            f"Open this link within {minutes} minutes to choose a new one:\n\n"
            f"{link}\n\n"
            "If it wasn't you, ignore this email. Your password stays the same.\n\n"
            "---\n\n"
            "طلب أحدهم إعادة تعيين كلمة مرور Relay الخاصة بك.\n"
            f"افتح هذا الرابط خلال {minutes} دقيقة لاختيار كلمة مرور جديدة:\n\n"
            f"{link}\n\n"
            "إذا لم تكن أنت، تجاهل هذه الرسالة وستبقى كلمة المرور كما هي.\n"
        )
