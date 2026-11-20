from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr

from relay.domain.devices import DeviceStatus
from relay.presentation.schemas import InputModel, Name, Version


class ChallengeRequest(InputModel):
    replace_existing: bool = False


class ChallengeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    challenge_id: UUID
    challenge: str = Field(repr=False)
    device_id: UUID
    expires_at: datetime


class EnrollmentRequest(InputModel):
    challenge: SecretStr = Field(min_length=1, max_length=256)
    installation_id: UUID
    name: Name
    platform: Literal["android"]
    app_version: Version


class RenewalRequest(InputModel):
    refresh_token: SecretStr = Field(min_length=1, max_length=256)
    installation_id: UUID
    request_id: UUID


class DeviceTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    device_id: UUID
    installation_id: UUID
    device_name: str
    access_token: str = Field(repr=False)
    token_type: Literal["bearer"]
    expires_in: int
    refresh_token: str = Field(repr=False)
    refresh_expires_at: datetime


PositiveVersion = Annotated[int, Field(strict=True, ge=1, le=2147483647)]


class ConfigurationRequest(InputModel):
    expected_version: PositiveVersion
    schema_version: Literal[1]
    settings: dict[str, JsonValue]


class ConfigurationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    schema_version: int
    version: int
    settings: dict[str, JsonValue]


class AcknowledgementRequest(InputModel):
    version: PositiveVersion


class AcknowledgementResponse(BaseModel):
    acknowledged_version: int


class ExitReport(BaseModel):
    model_config = ConfigDict(extra="ignore")
    at: datetime
    reason: str = Field(max_length=32)


class PhoneHealth(BaseModel):
    """What the app reports about the phone (0.8.1+). Unknown keys from a
    newer app are dropped, so an old server still accepts it."""

    model_config = ConfigDict(extra="ignore")
    reported_at: datetime | None = None
    boot_at: datetime | None = None
    app_started_at: datetime | None = None
    battery_unrestricted: bool | None = None
    background_restricted: bool | None = None
    standby_bucket: str | None = Field(default=None, max_length=16)
    power_save: bool | None = None
    battery_percent: int | None = Field(default=None, ge=-1, le=100)
    charging: bool | None = None
    folders_ok: bool | None = None
    sms_allowed: bool | None = None
    android: int | None = Field(default=None, ge=0, le=1000)
    maker: str | None = Field(default=None, max_length=40)
    exits: list[ExitReport] = Field(default_factory=list, max_length=10)


class HeartbeatRequest(InputModel):
    app_version: Version
    current_config_version: int = Field(strict=True, ge=0, le=2147483647)
    health: PhoneHealth | None = None


class HeartbeatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: str
    last_seen_at: datetime
    configuration_version: int
    acknowledged_version: int | None


class AgentStateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
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


class AgentStateListResponse(BaseModel):
    items: list[AgentStateResponse]
    limit: int
    offset: int


class RevocationResponse(BaseModel):
    status: str = "revoked"
