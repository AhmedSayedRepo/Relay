from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from relay.presentation.agent_dependencies import AuthenticatedDevice, Service
from relay.presentation.agent_schemas import (
    AcknowledgementRequest,
    AcknowledgementResponse,
    AgentStateListResponse,
    AgentStateResponse,
    ChallengeRequest,
    ChallengeResponse,
    ConfigurationRequest,
    ConfigurationResponse,
    DeviceTokenResponse,
    EnrollmentRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    RenewalRequest,
    RevocationResponse,
)
from relay.presentation.dependencies import current_user

admin_router = APIRouter(
    tags=["Device administration"], dependencies=[Depends(current_user)]
)
device_router = APIRouter(prefix="/device", tags=["Device agent"])


@admin_router.post(
    "/devices/{device_id}/enrollment-challenges",
    response_model=ChallengeResponse,
    status_code=201,
)
async def challenge(
    device_id: UUID, body: ChallengeRequest, service: Service
) -> ChallengeResponse:
    return ChallengeResponse.model_validate(
        await service.create_challenge(device_id, body.replace_existing)
    )


@admin_router.get("/devices/{device_id}/state", response_model=AgentStateResponse)
async def state(device_id: UUID, service: Service) -> AgentStateResponse:
    return AgentStateResponse.model_validate(await service.state(device_id))


@admin_router.get("/device-states", response_model=AgentStateListResponse)
async def states(
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> AgentStateListResponse:
    values = await service.states(limit, offset)
    return AgentStateListResponse(
        items=[AgentStateResponse.model_validate(value) for value in values],
        limit=limit,
        offset=offset,
    )


@admin_router.get(
    "/devices/{device_id}/configuration", response_model=ConfigurationResponse
)
async def admin_configuration(
    device_id: UUID, service: Service
) -> ConfigurationResponse:
    return ConfigurationResponse.model_validate(
        await service.admin_configuration(device_id)
    )


@admin_router.put(
    "/devices/{device_id}/configuration", response_model=ConfigurationResponse
)
async def update_configuration(
    device_id: UUID, body: ConfigurationRequest, service: Service
) -> ConfigurationResponse:
    value = await service.update_configuration(
        device_id,
        body.expected_version,
        body.schema_version,
        body.settings,
    )
    return ConfigurationResponse.model_validate(value)


@admin_router.post("/devices/{device_id}/revoke", response_model=RevocationResponse)
async def revoke(device_id: UUID, service: Service) -> RevocationResponse:
    await service.revoke(device_id)
    return RevocationResponse()


@device_router.post("/enroll", response_model=DeviceTokenResponse, status_code=201)
async def enroll(body: EnrollmentRequest, service: Service) -> DeviceTokenResponse:
    value = await service.enroll(
        body.challenge.get_secret_value(),
        body.installation_id,
        body.name,
        body.platform,
        body.app_version,
    )
    return DeviceTokenResponse.model_validate(value)


@device_router.post("/renew", response_model=DeviceTokenResponse)
async def renew(body: RenewalRequest, service: Service) -> DeviceTokenResponse:
    value = await service.renew(
        body.refresh_token.get_secret_value(), body.installation_id, body.request_id
    )
    return DeviceTokenResponse.model_validate(value)


@device_router.get(
    "/configuration",
    response_model=ConfigurationResponse,
    responses={304: {"description": "Known delivered configuration is unchanged"}},
)
async def configuration(
    principal: AuthenticatedDevice,
    service: Service,
    known_version: Annotated[int, Query(ge=0, le=2147483647)] = 0,
) -> ConfigurationResponse | Response:
    value = await service.configuration(principal, known_version)
    return (
        ConfigurationResponse.model_validate(value)
        if value
        else Response(status_code=304)
    )


@device_router.post("/configuration/ack", response_model=AcknowledgementResponse)
async def acknowledge(
    body: AcknowledgementRequest, principal: AuthenticatedDevice, service: Service
) -> AcknowledgementResponse:
    return AcknowledgementResponse(
        acknowledged_version=await service.acknowledge(principal, body.version)
    )


@device_router.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    body: HeartbeatRequest, principal: AuthenticatedDevice, service: Service
) -> HeartbeatResponse:
    return HeartbeatResponse.model_validate(
        await service.heartbeat(
            principal, body.app_version, body.current_config_version
        )
    )
