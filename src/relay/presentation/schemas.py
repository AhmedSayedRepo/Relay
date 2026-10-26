from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, model_validator

from relay.domain.devices import DeviceStatus


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(InputModel):
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    is_active: bool
    created_at: datetime


Name = Annotated[str, Field(min_length=1, max_length=120)]
Version = Annotated[str, Field(min_length=1, max_length=64)]


class DeviceCreateRequest(InputModel):
    external_device_id: str = Field(min_length=1, max_length=128)
    name: Name
    platform: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    app_version: Version | None = None
    status: DeviceStatus = DeviceStatus.PENDING


class DeviceUpdateRequest(InputModel):
    name: Name | None = None
    app_version: Version | None = None
    status: DeviceStatus | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one mutable field is required.")
        for key in ("name", "status"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError("Only app_version can be null.")
        return self


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    external_device_id: str
    name: str
    platform: str
    app_version: str | None
    status: DeviceStatus
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None


class DeviceListResponse(BaseModel):
    items: list[DeviceResponse]
    limit: int
    offset: int
