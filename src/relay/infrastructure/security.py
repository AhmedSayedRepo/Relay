import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from relay.application.errors import AuthenticationFailed
from relay.application.ports import AccessToken
from relay.infrastructure.config import Settings


class Argon2Passwords:
    def __init__(self) -> None:
        # Argon2id; explicit cost keeps policy stable across library upgrades.
        self.hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
        self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))
        self._slots = asyncio.Semaphore(4)

    async def hash(self, password: str) -> str:
        async with self._slots:
            return await asyncio.to_thread(self.hasher.hash, password)

    async def verify(self, password: str, password_hash: str | None) -> bool:
        try:
            async with self._slots:
                valid = await asyncio.to_thread(
                    self.hasher.verify, password_hash or self._dummy_hash, password
                )
            return valid and password_hash is not None
        except (VerificationError, InvalidHashError):
            return False


class JwtTokens:
    def __init__(self, settings: Settings) -> None:
        self.secret = settings.jwt_secret.get_secret_value()
        self.algorithm = settings.jwt_algorithm
        self.issuer = settings.jwt_issuer
        self.audience = settings.jwt_audience
        self.lifetime = timedelta(minutes=settings.access_token_expire_minutes)

    def issue(self, user_id: UUID) -> AccessToken:
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": str(user_id),
                "iat": now,
                "nbf": now,
                "exp": now + self.lifetime,
                "iss": self.issuer,
                "aud": self.audience,
                "jti": str(uuid4()),
                "token_type": "access",
            },
            self.secret,
            algorithm=self.algorithm,
        )
        return AccessToken(token, int(self.lifetime.total_seconds()))

    def subject(self, token: str) -> UUID:
        try:
            claims = jwt.decode(
                token,
                self.secret,
                algorithms=[self.algorithm],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "require": [
                        "sub",
                        "iat",
                        "nbf",
                        "exp",
                        "iss",
                        "aud",
                        "jti",
                        "token_type",
                    ]
                },
            )
            if claims["token_type"] != "access":
                raise ValueError("Wrong token type")
            return UUID(claims["sub"])
        except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError):
            raise AuthenticationFailed("Invalid or expired access token.") from None
