"""Enrollment, rotation and contact transactions. No HTTP/ORM dependencies."""

import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from relay.application.agent_ports import AgentUnitOfWork, DeviceTokenProvider
from relay.application.errors import AuthenticationFailed, Conflict, NotFound
from relay.domain.agent import (
    Challenge,
    Configuration,
    Credential,
    DevicePrincipal,
    Installation,
    JsonValue,
    validate_settings,
)
from relay.domain.devices import Device, DeviceStatus
from relay.domain.errors import InvalidDevice


@dataclass(frozen=True, slots=True)
class EnrollmentValue:
    challenge_id: UUID
    challenge: str = field(repr=False)
    device_id: UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class TokenBundle:
    device_id: UUID
    installation_id: UUID
    device_name: str
    access_token: str = field(repr=False)
    expires_in: int
    refresh_token: str = field(repr=False)
    refresh_expires_at: datetime
    token_type: str = "bearer"


@dataclass(frozen=True, slots=True)
class AgentState:
    device_id: UUID
    name: str
    external_device_id: str
    status: DeviceStatus
    installation_id: UUID | None
    enrolled_at: datetime | None
    last_seen_at: datetime | None
    app_version: str | None
    configuration_version: int
    acknowledged_version: int | None
    credential_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class Contact:
    last_seen_at: datetime
    configuration_version: int
    acknowledged_version: int | None
    status: str = "ok"


def split_secret(value: str) -> tuple[UUID, str]:
    try:
        identifier, secret = value.split(".", 1)
        if len(identifier) != 36 or not re.fullmatch(r"[A-Za-z0-9_-]{43}", secret):
            raise ValueError("Invalid token")
        return UUID(identifier), secret
    except (ValueError, AttributeError):
        raise AuthenticationFailed("Invalid or expired device credential.") from None


def validate_metadata(value: str, maximum: int) -> None:
    if (
        not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise InvalidDevice("Device metadata must be nonempty and within limits.")


class AgentService:
    def __init__(
        self,
        uow: AgentUnitOfWork,
        tokens: DeviceTokenProvider,
        *,
        challenge_minutes: int = 10,
        refresh_days: int = 30,
        coalesce_seconds: int = 60,
    ) -> None:
        self.uow = uow
        self.tokens = tokens
        self.challenge_minutes = challenge_minutes
        self.refresh_days = refresh_days
        self.coalesce_seconds = coalesce_seconds

    async def _device(self, device_id: UUID) -> Device:
        device = await self.uow.devices.get(device_id, for_update=True)
        if device is None:
            raise NotFound("Device not found.")
        return device

    async def create_challenge(
        self, device_id: UUID, replace_existing: bool
    ) -> EnrollmentValue:
        device = await self._device(device_id)
        if device.status == DeviceStatus.DISABLED:
            raise Conflict("Disabled devices cannot be enrolled.")
        if (
            await self.uow.agents.current_installation(device_id)
            and not replace_existing
        ):
            raise Conflict("Replacing an installation requires explicit authorization.")
        now = datetime.now(UTC)
        secret = self.tokens.random_secret()
        challenge = Challenge(
            uuid4(),
            device_id,
            self.tokens.digest(secret),
            now + timedelta(minutes=self.challenge_minutes),
            replace_existing,
        )
        await self.uow.agents.add_challenge(challenge)
        await self.uow.commit()
        return EnrollmentValue(
            challenge.id, f"{challenge.id}.{secret}", device_id, challenge.expires_at
        )

    def _bundle(
        self, device: Device, credential: Credential, secret: str
    ) -> TokenBundle:
        principal = DevicePrincipal(
            device.id, credential.installation_id, credential.id
        )
        access = self.tokens.issue(principal)
        return TokenBundle(
            device.id,
            credential.installation_id,
            device.name,
            access.access_token,
            access.expires_in,
            f"{credential.id}.{secret}",
            credential.expires_at,
        )

    async def enroll(
        self,
        challenge_value: str,
        installation_id: UUID,
        name: str,
        platform: str,
        app_version: str,
    ) -> TokenBundle:
        validate_metadata(name, 120)
        validate_metadata(app_version, 64)
        identifier, secret = split_secret(challenge_value)
        candidate = await self.uow.agents.challenge(identifier)
        if candidate is None:
            raise AuthenticationFailed("Invalid or expired enrollment challenge.")
        device = await self._device(candidate.device_id)
        # Re-read after locking: another transaction may have consumed this challenge.
        challenge = await self.uow.agents.challenge(identifier)
        now = datetime.now(UTC)
        if (
            challenge is None
            or challenge.consumed_at
            or challenge.revoked_at
            or challenge.expires_at <= now
            or challenge.attempts >= 5
            or device.status == DeviceStatus.DISABLED
        ):
            raise AuthenticationFailed("Invalid or expired enrollment challenge.")
        if not self.tokens.matches(secret, challenge.secret_hash):
            challenge.attempts += 1
            await self.uow.agents.save_challenge(challenge)
            await self.uow.commit()  # Invalid attempts must survive the public 401.
            raise AuthenticationFailed("Invalid or expired enrollment challenge.")
        if platform != "android" or device.platform != platform:
            raise Conflict("Enrollment platform does not match the device.")
        if await self.uow.agents.installation(installation_id):
            raise Conflict("Installation identity is already registered.")
        existing = await self.uow.agents.current_installation(device.id)
        if existing and not challenge.replace_existing:
            raise Conflict("Replacing an installation requires explicit authorization.")
        if existing:
            await self.uow.agents.revoke(device.id, now, challenges=False)
        await self.uow.agents.add_installation(
            Installation(installation_id, device.id, now)
        )
        challenge.consumed_at = now
        await self.uow.agents.save_challenge(challenge)
        refresh_secret = self.tokens.random_secret()
        credential = Credential(
            uuid4(),
            installation_id,
            device.id,
            self.tokens.digest(refresh_secret),
            now + timedelta(days=self.refresh_days),
            now,
        )
        await self.uow.agents.add_credential(credential)
        # Administrator-owned identity fields are never replaced by agent metadata.
        device = replace(
            device, status=DeviceStatus.ACTIVE, app_version=app_version, updated_at=now
        )
        await self.uow.devices.save(device)
        await self.uow.agents.contact(
            device.id, now, app_version, self.coalesce_seconds
        )
        bundle = self._bundle(device, credential, refresh_secret)
        await self.uow.commit()
        return bundle

    async def renew(
        self, refresh_token: str, installation_id: UUID, request_id: UUID
    ) -> TokenBundle:
        identifier, secret = split_secret(refresh_token)
        candidate = await self.uow.agents.credential(identifier)
        if candidate is None:
            raise AuthenticationFailed("Invalid or expired device credential.")
        device = await self._device(candidate.device_id)
        credential = await self.uow.agents.credential(identifier)
        installation = await self.uow.agents.installation(installation_id)
        now = datetime.now(UTC)
        if (
            credential is None
            or credential.revoked_at
            or credential.expires_at <= now
            or credential.installation_id != installation_id
            or installation is None
            or installation.revoked_at
            or installation.device_id != device.id
            or device.status != DeviceStatus.ACTIVE
            or not self.tokens.matches(secret, credential.secret_hash)
        ):
            raise AuthenticationFailed("Invalid or expired device credential.")
        next_secret = self.tokens.successor_secret(secret, request_id)
        if credential.consumed_at:
            successor = (
                await self.uow.agents.credential(credential.successor_id)
                if credential.successor_id
                else None
            )
            if (
                credential.consumed_request_id == request_id
                and successor is not None
                and successor.revoked_at is None
                and successor.consumed_at is None
                and successor.expires_at > now
                and successor.installation_id == installation_id
                and successor.device_id == device.id
                and self.tokens.matches(next_secret, successor.secret_hash)
            ):
                await self.uow.agents.contact(
                    device.id, now, None, self.coalesce_seconds
                )
                bundle = self._bundle(device, successor, next_secret)
                await self.uow.commit()
                return bundle
            # A consumed credential presented for another operation is a family replay.
            await self.uow.agents.revoke(device.id, now)
            await self.uow.commit()
            raise AuthenticationFailed("Invalid or expired device credential.")
        successor = Credential(
            uuid4(),
            installation_id,
            device.id,
            self.tokens.digest(next_secret),
            credential.expires_at,
            now,
        )
        credential.consumed_at = now
        credential.consumed_request_id = request_id
        credential.successor_id = successor.id
        await self.uow.agents.save_credential(credential)
        await self.uow.agents.add_credential(successor)
        await self.uow.agents.contact(device.id, now, None, self.coalesce_seconds)
        bundle = self._bundle(device, successor, next_secret)
        await self.uow.commit()
        return bundle

    async def authenticate(self, access_token: str) -> DevicePrincipal:
        principal = self.tokens.principal(access_token)
        await self._authorize(principal)
        return principal

    async def _authorize(self, principal: DevicePrincipal) -> Installation:
        device = await self.uow.devices.get(principal.device_id, for_update=True)
        credential = await self.uow.agents.credential(principal.credential_id)
        installation = await self.uow.agents.installation(principal.installation_id)
        now = datetime.now(UTC)
        if (
            device is None
            or device.status != DeviceStatus.ACTIVE
            or credential is None
            or credential.revoked_at
            or credential.consumed_at
            or credential.expires_at <= now
            or credential.device_id != principal.device_id
            or credential.installation_id != principal.installation_id
            or installation is None
            or installation.revoked_at
            or installation.device_id != principal.device_id
        ):
            raise AuthenticationFailed("Invalid or expired device credential.")
        await self.uow.agents.contact(device.id, now, None, self.coalesce_seconds)
        return installation

    async def revoke(self, device_id: UUID) -> None:
        device = await self._device(device_id)
        now = datetime.now(UTC)
        await self.uow.agents.revoke(device_id, now)
        if device.status != DeviceStatus.DISABLED:
            await self.uow.devices.save(
                replace(device, status=DeviceStatus.PENDING, updated_at=now)
            )
        await self.uow.commit()

    async def state(self, device_id: UUID) -> AgentState:
        device = await self._device(device_id)
        installation = await self.uow.agents.current_installation(device_id)
        credential = (
            await self.uow.agents.active_credential(installation.id)
            if installation
            else None
        )
        config = await self.uow.agents.configuration(device_id)
        return AgentState(
            device.id,
            device.name,
            device.external_device_id,
            device.status,
            installation.id if installation else None,
            installation.enrolled_at if installation else None,
            device.last_seen_at,
            device.app_version,
            config.version,
            installation.acknowledged_version if installation else None,
            credential.expires_at if credential else None,
        )

    async def states(self, limit: int, offset: int) -> list[AgentState]:
        devices = await self.uow.devices.list(limit=limit, offset=offset)
        # Deterministic lock order prevents two listing transactions deadlocking.
        states = {
            device.id: await self.state(device.id)
            for device in sorted(devices, key=lambda item: item.id)
        }
        return [states[device.id] for device in devices]

    async def admin_configuration(self, device_id: UUID) -> Configuration:
        await self._device(device_id)
        return await self.uow.agents.configuration(device_id)

    async def update_configuration(
        self,
        device_id: UUID,
        expected_version: int,
        schema_version: int,
        settings: dict[str, JsonValue],
    ) -> Configuration:
        validate_settings(settings)
        if schema_version != 1:
            raise InvalidDevice("Unsupported configuration schema.")
        await self._device(device_id)
        current = await self.uow.agents.configuration(device_id)
        if expected_version != current.version:
            raise Conflict("Configuration version has changed.")
        result = Configuration(device_id, current.version + 1, schema_version, settings)
        await self.uow.agents.add_configuration(result)
        await self.uow.commit()
        return result

    async def configuration(
        self, principal: DevicePrincipal, known_version: int
    ) -> Configuration | None:
        await self._authorize(principal)
        config = await self.uow.agents.configuration(principal.device_id)
        if known_version > config.version:
            await self.uow.commit()
            raise Conflict("Known configuration version is ahead of the server.")
        if known_version == config.version and await self.uow.agents.was_delivered(
            principal.installation_id, config.version
        ):
            await self.uow.commit()
            return None
        await self.uow.agents.mark_delivered(principal.installation_id, config)
        await self.uow.commit()
        return config

    async def acknowledge(self, principal: DevicePrincipal, version: int) -> int:
        installation = await self._authorize(principal)
        if (
            installation.acknowledged_version is not None
            and version < installation.acknowledged_version
        ) or not await self.uow.agents.was_delivered(installation.id, version):
            await self.uow.commit()
            raise Conflict("Configuration acknowledgement is not valid.")
        if installation.acknowledged_version != version:
            installation.acknowledged_version = version
            await self.uow.agents.save_installation(installation)
        await self.uow.commit()
        return version

    async def heartbeat(
        self, principal: DevicePrincipal, app_version: str, current_config_version: int
    ) -> Contact:
        validate_metadata(app_version, 64)
        installation = await self._authorize(principal)
        config = await self.uow.agents.configuration(principal.device_id)
        if (
            current_config_version < 0
            or current_config_version > config.version
            or (
                current_config_version > 0
                and not await self.uow.agents.was_delivered(
                    installation.id, current_config_version
                )
            )
        ):
            await self.uow.commit()
            raise Conflict("Reported configuration version is not valid.")
        if installation.reported_config_version != current_config_version:
            installation.reported_config_version = current_config_version
            await self.uow.agents.save_installation(installation)
        seen = await self.uow.agents.contact(
            principal.device_id, datetime.now(UTC), app_version, self.coalesce_seconds
        )
        await self.uow.commit()
        return Contact(seen, config.version, installation.acknowledged_version)
