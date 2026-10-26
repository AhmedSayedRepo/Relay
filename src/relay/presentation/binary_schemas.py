from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from relay.presentation.schemas import InputModel


class ImageUploadInitialize(InputModel):
    upload_id: UUID
    event_id: UUID
    installation_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    byte_size: int = Field(ge=1, le=8 * 1024 * 1024)
    width: int = Field(ge=1, le=12_000)
    height: int = Field(ge=1, le=12_000)
    source_type: Literal["saf_tree"]
    source_id: str = Field(min_length=1, max_length=64)
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    config_version: int = Field(ge=1, le=2_147_483_647)
    rule_id: str = Field(min_length=1, max_length=64)
    occurred_at: datetime
    captured_at: datetime
    media_at: datetime | None = None


class UploadInitializeResponse(BaseModel):
    session_id: UUID
    upload_id: UUID
    status: Literal["upload_required", "finalized"]
    uploaded_bytes: int
    object_id: UUID | None
    event_status: Literal["accepted", "duplicate"] | None = None
    max_chunk_bytes: int = 256 * 1024


class ChunkResponse(BaseModel):
    session_id: UUID
    status: Literal["accepted", "duplicate"]
    uploaded_bytes: int


class BinaryObjectResponse(BaseModel):
    id: UUID
    status: Literal["available"] = "available"
    device_id: UUID
    installation_id: UUID
    content_hash: str
    content_type: str
    byte_size: int
    width: int
    height: int
    source_type: str
    source_id: str
    created_at: datetime
    finalized_at: datetime
    delete_after: datetime


class FinalizeResponse(BaseModel):
    object: BinaryObjectResponse
    event_status: Literal["accepted", "duplicate"]


class DeleteBinaryResponse(BaseModel):
    deleted: bool


class DeleteDeviceImagesResponse(BaseModel):
    deleted_objects: int
