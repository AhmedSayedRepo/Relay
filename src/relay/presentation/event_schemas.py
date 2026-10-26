from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from relay.domain.agent import JsonValue
from relay.presentation.schemas import InputModel


class EventBatchRequest(InputModel):
    events: list[JsonValue] = Field(min_length=1, max_length=20)


class EventItemResult(BaseModel):
    event_id: str
    status: Literal["accepted", "duplicate", "rejected"]
    reason: str | None = None


class EventBatchResponse(BaseModel):
    results: list[EventItemResult]


class RawEventResponse(BaseModel):
    id: UUID
    event_id: UUID
    device_id: UUID
    installation_id: UUID
    collector: str
    schema_version: int
    config_version: int
    rule_id: str
    occurred_at: datetime
    captured_at: datetime
    received_at: datetime
    payload: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    delete_after: datetime
    object_id: UUID | None = None


class RawEventListResponse(BaseModel):
    items: list[RawEventResponse]
    limit: int
    offset: int


class DeleteEventsResponse(BaseModel):
    deleted: int
