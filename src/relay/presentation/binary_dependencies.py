from typing import Annotated, cast

from fastapi import Depends, Request

from relay.application.binaries import BinaryService
from relay.infrastructure.config import Settings
from relay.infrastructure.object_storage import FilesystemObjectStorage
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.presentation.dependencies import unit_of_work


def binary_service(
    request: Request, uow: Annotated[SqlUnitOfWork, Depends(unit_of_work)]
) -> BinaryService:
    settings = cast(Settings, request.app.state.settings)
    return BinaryService(
        uow,
        cast(FilesystemObjectStorage, request.app.state.object_storage),
        retention_days=settings.image_object_retention_days,
        session_hours=settings.image_upload_session_hours,
        max_active_uploads=settings.image_max_active_uploads,
        uploads_per_hour=settings.image_uploads_per_hour,
        stored_bytes_per_device=settings.image_stored_bytes_per_device,
        receipt_evidence_enabled=settings.receipt_evidence_enabled,
    )


Service = Annotated[BinaryService, Depends(binary_service)]
