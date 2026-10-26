from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from relay.application.devices import CreateDevice, DeviceService, UpdateDevice
from relay.presentation.dependencies import current_user, device_service
from relay.presentation.schemas import (
    DeviceCreateRequest,
    DeviceListResponse,
    DeviceResponse,
    DeviceUpdateRequest,
)

router = APIRouter(
    prefix="/devices", tags=["Devices"], dependencies=[Depends(current_user)]
)
Service = Annotated[DeviceService, Depends(device_service)]


@router.post("", response_model=DeviceResponse, status_code=201)
async def create_device(
    body: DeviceCreateRequest, service: Service, response: Response
) -> DeviceResponse:
    device = await service.create(CreateDevice(**body.model_dump()))
    response.headers["Location"] = f"/api/v1/devices/{device.id}"
    return DeviceResponse.model_validate(device)


@router.get("", response_model=DeviceListResponse)
async def list_devices(
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> DeviceListResponse:
    devices = await service.list(limit=limit, offset=offset)
    return DeviceListResponse(
        items=[DeviceResponse.model_validate(d) for d in devices],
        limit=limit,
        offset=offset,
    )


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(device_id: UUID, service: Service) -> DeviceResponse:
    return DeviceResponse.model_validate(await service.get(device_id))


@router.patch("/{device_id}", response_model=DeviceResponse)
async def update_device(
    device_id: UUID, body: DeviceUpdateRequest, service: Service
) -> DeviceResponse:
    command = UpdateDevice(
        name=body.name,
        status=body.status,
        app_version=body.app_version,
        app_version_supplied="app_version" in body.model_fields_set,
    )
    return DeviceResponse.model_validate(await service.update(device_id, command))
