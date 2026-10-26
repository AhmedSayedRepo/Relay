from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from relay.domain.events import RawEvent
from relay.presentation.agent_dependencies import AuthenticatedDevice
from relay.presentation.dependencies import current_user
from relay.presentation.event_dependencies import Service
from relay.presentation.event_schemas import (
    DeleteEventsResponse,
    EventBatchRequest,
    EventBatchResponse,
    EventItemResult,
    RawEventListResponse,
    RawEventResponse,
)

device_router = APIRouter(prefix="/device/events", tags=["Device events"])
admin_router = APIRouter(
    prefix="/events",
    tags=["Event administration"],
    dependencies=[Depends(current_user)],
)
device_admin_router = APIRouter(
    tags=["Event administration"], dependencies=[Depends(current_user)]
)


def response(value: RawEvent) -> RawEventResponse:
    return RawEventResponse(
        id=value.id,
        event_id=value.event_id,
        device_id=value.device_id,
        installation_id=value.installation_id,
        collector=value.collector,
        schema_version=value.schema_version,
        config_version=value.config_version,
        rule_id=value.rule_id,
        occurred_at=value.occurred_at,
        captured_at=value.captured_at,
        received_at=value.received_at,
        payload=value.payload,
        metadata=value.metadata,
        delete_after=value.delete_after,
        object_id=value.object_id,
    )


@device_router.post("/batch", response_model=EventBatchResponse)
async def batch(
    body: EventBatchRequest, principal: AuthenticatedDevice, service: Service
) -> EventBatchResponse:
    results = await service.ingest(
        principal.device_id, principal.installation_id, body.events
    )
    return EventBatchResponse(
        results=[
            EventItemResult(
                event_id=item.event_id,
                status=cast(Literal["accepted", "duplicate", "rejected"], item.status),
                reason=item.reason,
            )
            for item in results
        ]
    )


@admin_router.get("", response_model=RawEventListResponse)
async def list_events(
    service: Service,
    device_id: UUID | None = None,
    collector: Literal["sms", "images"] | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> RawEventListResponse:
    for value in (occurred_from, occurred_to):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise HTTPException(status_code=422)
    if (
        occurred_from is not None
        and occurred_to is not None
        and occurred_from > occurred_to
    ):
        raise HTTPException(status_code=422)
    values = await service.list(
        device_id=device_id,
        collector=collector,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        limit=limit,
        offset=offset,
    )
    return RawEventListResponse(
        items=[response(value) for value in values], limit=limit, offset=offset
    )


@admin_router.get("/{event_id}", response_model=RawEventResponse)
async def get_event(event_id: UUID, service: Service) -> RawEventResponse:
    return response(await service.get(event_id))


@device_admin_router.delete(
    "/devices/{device_id}/events", response_model=DeleteEventsResponse
)
async def delete_device_events(
    device_id: UUID, service: Service
) -> DeleteEventsResponse:
    return DeleteEventsResponse(deleted=await service.delete_device(device_id))
