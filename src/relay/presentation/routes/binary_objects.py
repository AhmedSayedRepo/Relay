from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Response

from relay.application.binaries import FinalizeResult, InitializeResult
from relay.domain.binary_objects import BinaryObject, ImageUploadRequest
from relay.presentation.agent_dependencies import AuthenticatedDevice
from relay.presentation.binary_dependencies import Service
from relay.presentation.binary_schemas import (
    BinaryObjectResponse,
    ChunkResponse,
    DeleteBinaryResponse,
    DeleteDeviceImagesResponse,
    FinalizeResponse,
    ImageUploadInitialize,
    UploadInitializeResponse,
)
from relay.presentation.dependencies import current_user

device_router = APIRouter(prefix="/device/objects", tags=["Device binary objects"])
admin_router = APIRouter(
    prefix="/objects",
    tags=["Binary object administration"],
    dependencies=[Depends(current_user)],
)
device_admin_router = APIRouter(
    tags=["Binary object administration"], dependencies=[Depends(current_user)]
)


def object_response(value: BinaryObject) -> BinaryObjectResponse:
    return BinaryObjectResponse(
        id=value.id,
        device_id=value.device_id,
        installation_id=value.installation_id,
        content_hash=value.content_hash,
        content_type=value.content_type,
        byte_size=value.byte_size,
        width=value.width,
        height=value.height,
        source_type=value.source_type,
        source_id=value.source_id,
        created_at=value.created_at,
        finalized_at=value.finalized_at,
        delete_after=value.delete_after,
    )


def initialize_response(value: InitializeResult) -> UploadInitializeResponse:
    return UploadInitializeResponse(
        session_id=value.session.id,
        upload_id=value.session.upload_id,
        status=cast(Literal["upload_required", "finalized"], value.status),
        uploaded_bytes=value.session.uploaded_bytes,
        object_id=value.session.object_id,
        event_status=cast(Literal["accepted", "duplicate"] | None, value.event_status),
    )


@device_router.post("/uploads", response_model=UploadInitializeResponse)
async def initialize(
    body: ImageUploadInitialize,
    principal: AuthenticatedDevice,
    service: Service,
) -> UploadInitializeResponse:
    request = ImageUploadRequest(**body.model_dump())
    return initialize_response(
        await service.initialize(
            principal.device_id, principal.installation_id, request
        )
    )


@device_router.put("/uploads/{session_id}/chunks", response_model=ChunkResponse)
async def chunk(
    session_id: UUID,
    principal: AuthenticatedDevice,
    service: Service,
    offset: Annotated[int, Query(ge=0, le=8 * 1024 * 1024)],
    body: Annotated[bytes, Body(media_type="application/octet-stream")],
) -> ChunkResponse:
    session, status = await service.append_chunk(
        principal.device_id,
        principal.installation_id,
        session_id,
        offset,
        body,
    )
    return ChunkResponse(
        session_id=session.id,
        status=cast(Literal["accepted", "duplicate"], status),
        uploaded_bytes=session.uploaded_bytes,
    )


@device_router.post("/uploads/{session_id}/finalize", response_model=FinalizeResponse)
async def finalize(
    session_id: UUID, principal: AuthenticatedDevice, service: Service
) -> FinalizeResponse:
    result: FinalizeResult = await service.finalize(
        principal.device_id, principal.installation_id, session_id
    )
    return FinalizeResponse(
        object=object_response(result.object),
        event_status=cast(Literal["accepted", "duplicate"], result.event_status),
    )


@admin_router.get("/{object_id}", response_model=BinaryObjectResponse)
async def get_object(object_id: UUID, service: Service) -> BinaryObjectResponse:
    return object_response(await service.get(object_id))


@admin_router.get("/{object_id}/content")
async def get_content(object_id: UUID, service: Service) -> Response:
    value, content = await service.content(object_id)
    extension = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }[value.content_type]
    return Response(
        content=content,
        media_type=value.content_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": (
                f'attachment; filename="relay-image-{value.id}.{extension}"'
            ),
        },
    )


@admin_router.delete("/{object_id}", response_model=DeleteBinaryResponse)
async def delete_object(object_id: UUID, service: Service) -> DeleteBinaryResponse:
    await service.delete(object_id)
    return DeleteBinaryResponse(deleted=True)


@device_admin_router.delete(
    "/devices/{device_id}/image-data", response_model=DeleteDeviceImagesResponse
)
async def delete_device_images(
    device_id: UUID, service: Service
) -> DeleteDeviceImagesResponse:
    return DeleteDeviceImagesResponse(
        deleted_objects=await service.delete_device(device_id)
    )
