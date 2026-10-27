from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from relay.application.training import TrainingDatasetService
from relay.infrastructure.dataset_store import FilesystemDatasetStore
from relay.infrastructure.object_storage import FilesystemObjectStorage
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.presentation.dependencies import (
    AuthenticatedUser,
    current_user,
    unit_of_work,
)

router = APIRouter(
    tags=["Training dataset administration"],
    dependencies=[Depends(current_user)],
)


def training_service(
    request: Request, uow: Annotated[SqlUnitOfWork, Depends(unit_of_work)]
) -> TrainingDatasetService:
    return TrainingDatasetService(
        uow,
        cast(FilesystemObjectStorage, request.app.state.object_storage),
        cast(FilesystemDatasetStore, request.app.state.dataset_store),
    )


Service = Annotated[TrainingDatasetService, Depends(training_service)]


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class ConsentResponse(BaseModel):
    device_id: UUID
    enabled: bool


class CaptureResponse(BaseModel):
    captured: int


class TrainingSummary(BaseModel):
    train_examples: int
    holdout_examples: int
    transaction_groups: int
    consenting_devices: int


class DeleteTrainingResponse(BaseModel):
    deleted: int


@router.get("/devices/{device_id}/training-consent", response_model=ConsentResponse)
async def get_consent(device_id: UUID, service: Service) -> ConsentResponse:
    return ConsentResponse(device_id=device_id, enabled=await service.consent(device_id))


@router.put("/devices/{device_id}/training-consent", response_model=ConsentResponse)
async def set_consent(
    device_id: UUID, body: ConsentRequest, service: Service, user: AuthenticatedUser
) -> ConsentResponse:
    enabled = await service.set_consent(device_id, body.enabled, user.id)
    return ConsentResponse(device_id=device_id, enabled=enabled)


@router.delete(
    "/devices/{device_id}/training-examples", response_model=DeleteTrainingResponse
)
async def delete_device_examples(
    device_id: UUID, service: Service
) -> DeleteTrainingResponse:
    return DeleteTrainingResponse(deleted=await service.delete_device(device_id))


@router.post("/training/capture", response_model=CaptureResponse)
async def capture(
    service: Service, limit: Annotated[int, Query(ge=1, le=100)] = 100
) -> CaptureResponse:
    return CaptureResponse(captured=await service.capture(limit=limit))


@router.get("/training/summary", response_model=TrainingSummary)
async def summary(service: Service) -> TrainingSummary:
    return TrainingSummary(**await service.summary())
