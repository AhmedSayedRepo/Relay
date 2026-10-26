from dataclasses import asdict
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from relay.domain.agent import JsonValue
from relay.domain.processing import ProcessingJob, ReceiptCandidate
from relay.domain.review_trust import initial_resolution, reviewed_value
from relay.presentation.dependencies import AuthenticatedUser, current_user
from relay.presentation.processing_dependencies import Service
from relay.presentation.processing_schemas import (
    ExceptionRequest,
    ProcessingJobDetail,
    ProcessingJobResponse,
    ProcessingList,
    ProcessingRequest,
    ReceiptCandidateDetail,
    ReceiptCandidateResponse,
    ReceiptList,
    ReviewRequest,
)

router = APIRouter(
    tags=["Receipt processing administration"],
    dependencies=[Depends(current_user)],
)


def job_response(value: ProcessingJob) -> ProcessingJobResponse:
    return ProcessingJobResponse(
        **{
            **asdict(value),
            "requested_languages": list(value.requested_languages),
            "status": value.status.value,
            "failure_code": value.failure_code.value if value.failure_code else None,
        }
    )


def candidate_response(value: ReceiptCandidate) -> ReceiptCandidateResponse:
    resolution = value.resolution_result or initial_resolution(
        value.machine_result,
        supported_receipt=value.receipt_type.value == "INSTAPAY_TRANSFER_RECEIPT",
    )
    amount = reviewed_value(resolution, "amount")
    date_value = reviewed_value(resolution, "date")
    time_value = reviewed_value(resolution, "time")
    return ReceiptCandidateResponse(
        **{
            **asdict(value),
            "receipt_type": value.receipt_type.value,
            "review_status": value.review_status.value,
            "resolution_result": resolution,
            "authoritative_status": resolution.get("state", "UNVERIFIED_HISTORICAL"),
            "amount": Decimal(amount) if amount else None,
            "currency": reviewed_value(resolution, "currency"),
            "transaction_occurred_at": (
                value.transaction_occurred_at if date_value and time_value else None
            ),
            "reference_original": reviewed_value(resolution, "reference_number"),
            "reference_normalized": reviewed_value(resolution, "reference_number"),
            "sender_name": reviewed_value(resolution, "sender")
            or reviewed_value(resolution, "handle"),
            "recipient_name": reviewed_value(resolution, "recipient"),
            "recipient_identifier": reviewed_value(resolution, "recipient_identifier"),
            "transfer_status": reviewed_value(resolution, "transfer_status"),
        }
    )


@router.post("/objects/{object_id}/process", response_model=ProcessingJobResponse)
async def process_object(
    object_id: UUID, body: ProcessingRequest, service: Service
) -> ProcessingJobResponse:
    return job_response(
        await service.enqueue(
            object_id,
            processor_version=body.processor_version,
            parser_type=body.parser_type,
            parser_version=body.parser_version,
            requested_languages=tuple(body.requested_languages),
        )
    )


@router.get("/processing/jobs", response_model=ProcessingList)
async def list_jobs(
    service: Service,
    status: (
        Literal[
            "PENDING", "PROCESSING", "SUCCEEDED", "NEEDS_REVIEW", "FAILED", "SKIPPED"
        ]
        | None
    ) = None,
    object_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> ProcessingList:
    values = await service.list_jobs(
        status=status, object_id=object_id, limit=limit + 1, offset=offset
    )
    return ProcessingList(
        items=[job_response(value) for value in values[:limit]],
        limit=limit,
        offset=offset,
        has_more=len(values) > limit,
    )


@router.get("/processing/jobs/{job_id}", response_model=ProcessingJobDetail)
async def get_job(job_id: UUID, service: Service) -> ProcessingJobDetail:
    job, attempts = await service.get_job(job_id)
    return ProcessingJobDetail(**job_response(job).model_dump(), attempts=attempts)


@router.get("/receipts", response_model=ReceiptList)
async def list_receipts(
    service: Service,
    review_status: Literal["AUTO_ACCEPTED", "NEEDS_REVIEW", "REJECTED"] | None = None,
    receipt_type: Literal["INSTAPAY_TRANSFER_RECEIPT", "UNKNOWN_RECEIPT"] | None = None,
    device_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> ReceiptList:
    values = await service.list_candidates(
        review_status=review_status,
        receipt_type=receipt_type,
        device_id=device_id,
        limit=limit,
        offset=offset,
    )
    return ReceiptList(items=[candidate_response(value) for value in values])


@router.get("/receipts/next-exception", response_model=ReceiptCandidateResponse | None)
async def next_exception(service: Service) -> ReceiptCandidateResponse | None:
    value = await service.next_exception()
    return candidate_response(value) if value else None


@router.get("/receipts/review-metrics")
async def review_metrics(service: Service) -> dict[str, JsonValue]:
    return await service.review_metrics()


@router.get("/receipts/{candidate_id}", response_model=ReceiptCandidateDetail)
async def get_receipt(candidate_id: UUID, service: Service) -> ReceiptCandidateDetail:
    candidate, ocr, audit, reviewed_fields = await service.get_candidate(candidate_id)
    return ReceiptCandidateDetail(
        **candidate_response(candidate).model_dump(),
        ocr=ocr,
        audit=audit,
        reviewed_fields=reviewed_fields,
    )


@router.post("/receipts/{candidate_id}/review", response_model=ReceiptCandidateResponse)
async def review_receipt(
    candidate_id: UUID,
    body: ReviewRequest,
    service: Service,
    reviewer: AuthenticatedUser,
) -> ReceiptCandidateResponse:
    corrections = (
        body.corrections.model_dump(exclude_unset=True, mode="json")
        if body.corrections
        else {}
    )
    return candidate_response(
        await service.review(candidate_id, reviewer.id, body.action, corrections)
    )


@router.post(
    "/receipts/{candidate_id}/exceptions", response_model=ReceiptCandidateResponse
)
async def resolve_receipt_exception(
    candidate_id: UUID,
    body: ExceptionRequest,
    service: Service,
    reviewer: AuthenticatedUser,
) -> ReceiptCandidateResponse:
    return candidate_response(
        await service.resolve_exception(
            candidate_id,
            reviewer.id,
            body.command_id,
            body.expected_updated_at,
            body.field,
            body.action,
            body.value,
            body.reason,
        )
    )
