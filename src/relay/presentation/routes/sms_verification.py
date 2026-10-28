from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from relay.application.sms_verification import SmsVerificationService
from relay.domain.sms_verification import MAX_SMS_SOURCES
from relay.infrastructure.config import Settings
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.infrastructure.sms_verification_repository import (
    build_sms_verification_service,
)
from relay.presentation.dependencies import (
    AuthenticatedUser,
    current_user,
    unit_of_work,
)

router = APIRouter(
    tags=["SMS verification"],
    dependencies=[Depends(current_user)],
)


def verification_service(
    request: Request, uow: Annotated[SqlUnitOfWork, Depends(unit_of_work)]
) -> SmsVerificationService:
    return build_sms_verification_service(
        uow, cast(Settings, request.app.state.settings)
    )


Service = Annotated[SmsVerificationService, Depends(verification_service)]


class SourcesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sms_device_ids: list[UUID] = Field(max_length=MAX_SMS_SOURCES)


class SourcesResponse(BaseModel):
    device_id: UUID
    sms_device_ids: list[UUID]


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sms_event_id: UUID


class RunResponse(BaseModel):
    verified: int
    ambiguous: int
    waiting: int
    unmatched: int


@router.get("/devices/{device_id}/sms-sources", response_model=SourcesResponse)
async def get_sources(device_id: UUID, service: Service) -> SourcesResponse:
    return SourcesResponse(
        device_id=device_id, sms_device_ids=await service.sources(device_id)
    )


@router.put("/devices/{device_id}/sms-sources", response_model=SourcesResponse)
async def set_sources(
    device_id: UUID, body: SourcesRequest, service: Service, user: AuthenticatedUser
) -> SourcesResponse:
    sources = await service.set_sources(device_id, body.sms_device_ids, user.id)
    return SourcesResponse(device_id=device_id, sms_device_ids=sources)


@router.post("/verification/sms/run", response_model=RunResponse)
async def run(service: Service) -> RunResponse:
    return RunResponse(**await service.run())


@router.get("/receipts/{candidate_id}/sms-verification")
async def detail(candidate_id: UUID, service: Service) -> dict[str, Any]:
    return await service.detail(candidate_id)


@router.put("/receipts/{candidate_id}/sms-verification")
async def confirm(
    candidate_id: UUID, body: ConfirmRequest, service: Service, user: AuthenticatedUser
) -> dict[str, Any]:
    return await service.confirm(candidate_id, body.sms_event_id, user.id)


@router.delete("/receipts/{candidate_id}/sms-verification")
async def clear(candidate_id: UUID, service: Service) -> dict[str, Any]:
    return await service.clear(candidate_id)
