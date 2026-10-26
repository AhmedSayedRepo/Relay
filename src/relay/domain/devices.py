import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from relay.domain.errors import InvalidDevice


class DeviceStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    PENDING = "PENDING"


@dataclass(frozen=True, slots=True)
class Device:
    id: UUID
    external_device_id: str
    name: str
    platform: str
    app_version: str | None
    status: DeviceStatus
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None = None

    def __post_init__(self) -> None:
        for value, maximum in ((self.external_device_id, 128), (self.name, 120)):
            if not 1 <= len(value) <= maximum or value != value.strip():
                raise InvalidDevice("Device text must be nonempty and within limits.")
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise InvalidDevice("Device text must not contain control characters.")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", self.platform):
            raise InvalidDevice("Platform must be a lowercase identifier.")
        if self.app_version is not None and (
            not 1 <= len(self.app_version) <= 64
            or self.app_version != self.app_version.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in self.app_version)
        ):
            raise InvalidDevice("App version must be nonempty and within limits.")
        if not isinstance(self.status, DeviceStatus):
            raise InvalidDevice("Invalid device status.")
