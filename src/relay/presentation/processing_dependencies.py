from typing import Annotated, cast

from fastapi import Depends, Request

from relay.application.processing import ProcessingService
from relay.infrastructure.config import Settings
from relay.infrastructure.object_storage import FilesystemObjectStorage
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.presentation.dependencies import unit_of_work


def processing_service(
    request: Request, uow: Annotated[SqlUnitOfWork, Depends(unit_of_work)]
) -> ProcessingService:
    settings = cast(Settings, request.app.state.settings)
    return ProcessingService(
        uow,
        cast(FilesystemObjectStorage, request.app.state.object_storage),
        stale_job_minutes=settings.processing_stale_minutes,
        ocr_timeout_seconds=settings.ocr_timeout_seconds,
    )


Service = Annotated[ProcessingService, Depends(processing_service)]
