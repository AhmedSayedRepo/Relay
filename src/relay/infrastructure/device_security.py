import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt

from relay.application.errors import AuthenticationFailed
from relay.application.ports import AccessToken
from relay.domain.agent import DevicePrincipal
from relay.infrastructure.config import Settings


class DeviceTokens:
    """Distinct signing domain, audience and claims from administrator tokens."""

    def __init__(self, settings: Settings) -> None:
        master = settings.jwt_secret.get_secret_value().encode()
        self.key = hmac.digest(master, b"relay/device/access/v1", "sha256")
        self.renewal_key = hmac.digest(master, b"relay/device/renewal/v1", "sha256")
        self.issuer = settings.jwt_issuer
        self.lifetime = timedelta(minutes=settings.device_access_expire_minutes)

    def issue(self, principal: DevicePrincipal) -> AccessToken:
        now = datetime.now(UTC)
        value = jwt.encode(
            {
                "sub": str(principal.installation_id),
                "device_id": str(principal.device_id),
                "credential_id": str(principal.credential_id),
                "iat": now,
                "nbf": now,
                "exp": now + self.lifetime,
                "iss": self.issuer,
                "aud": "relay-device",
                "jti": str(uuid4()),
                "token_type": "device_access",
            },
            self.key,
            algorithm="HS256",
        )
        return AccessToken(value, int(self.lifetime.total_seconds()))

    def principal(self, token: str) -> DevicePrincipal:
        try:
            claims = jwt.decode(
                token,
                self.key,
                algorithms=["HS256"],
                issuer=self.issuer,
                audience="relay-device",
                options={
                    "require": [
                        "sub",
                        "device_id",
                        "credential_id",
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
            if claims["token_type"] != "device_access":
                raise ValueError("Invalid token type")
            return DevicePrincipal(
                UUID(claims["device_id"]),
                UUID(claims["sub"]),
                UUID(claims["credential_id"]),
            )
        except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError):
            raise AuthenticationFailed(
                "Invalid or expired device credential."
            ) from None

    def random_secret(self) -> str:
        return secrets.token_urlsafe(32)

    def digest(self, secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()

    def matches(self, secret: str, digest: str) -> bool:
        return hmac.compare_digest(self.digest(secret), digest)

    def successor_secret(self, old_secret: str, request_id: UUID) -> str:
        value = hmac.digest(
            self.renewal_key,
            b"relay-refresh-v1\x00" + old_secret.encode() + request_id.bytes,
            "sha256",
        )
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
