from dataclasses import asdict, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response

from relay.application.operations import date_window
from relay.domain.agent import JsonValue
from relay.domain.operations import DateWindow, DuplicateGroupQuery, TransactionQuery
from relay.domain.review_categories import CATEGORIES, categorize
from relay.infrastructure.config import Settings
from relay.presentation.dependencies import AuthenticatedUser, current_user
from relay.presentation.operations_dependencies import Service
from relay.presentation.operations_schemas import (
    DuplicateResolutionRequest,
    OperationData,
    OperationPage,
    RetryResponse,
)

router = APIRouter(tags=["Operator product"], dependencies=[Depends(current_user)])


def _dates(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = datetime.now(UTC).date()
    return date_from or today - timedelta(days=29), date_to or today


def _transaction_query(
    *,
    date_from: date | None,
    date_to: date | None,
    search: str | None,
    review_status: str | None,
    duplicate_status: str | None,
    receipt_type: str | None,
    currency: str | None,
    device_id: UUID | None,
    source_type: str | None,
    processing_status: str | None,
    sort_by: str,
    sort_direction: str,
    limit: int,
    offset: int,
) -> TransactionQuery:
    start, end = _dates(date_from, date_to)
    return TransactionQuery(
        window=date_window(start, end),
        search=" ".join(search.split()) if search else None,
        review_status=review_status,
        duplicate_status=duplicate_status,
        receipt_type=receipt_type,
        currency=currency.upper() if currency else None,
        device_id=device_id,
        source_type=source_type,
        processing_status=processing_status,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )


@router.get("/dashboard", response_model=OperationData)
async def dashboard(
    service: Service,
    request: Request,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, JsonValue]:
    # Same basis as Reports: when receipts reached the phone's folder.
    return await service.dashboard(
        _folder_window(request, date_from, date_to, None, None)
    )


@router.get("/transactions", response_model=OperationPage)
async def transactions(
    service: Service,
    date_from: date | None = None,
    date_to: date | None = None,
    search: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    review_status: Literal["AUTO_ACCEPTED", "NEEDS_REVIEW", "REJECTED"] | None = None,
    duplicate_status: (
        Literal["DUPLICATE", "CANONICAL", "GROUPED", "UNIQUE"] | None
    ) = None,
    receipt_type: Literal["INSTAPAY_TRANSFER_RECEIPT", "UNKNOWN_RECEIPT"] | None = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$")] = None,
    device_id: UUID | None = None,
    source_type: Literal["saf_tree"] | None = None,
    processing_status: (
        Literal[
            "PENDING", "PROCESSING", "SUCCEEDED", "NEEDS_REVIEW", "FAILED", "SKIPPED"
        ]
        | None
    ) = None,
    sort_by: Literal[
        "created_at", "transaction_occurred_at", "amount", "reference", "review_status"
    ] = "created_at",
    sort_direction: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> OperationPage:
    values = locals().copy()
    values.pop("service")
    query = _transaction_query(**values)
    rows, total = await service.transactions(query)
    return OperationPage(items=rows, total=total, limit=limit, offset=offset)


@router.get("/transactions/{candidate_id}", response_model=OperationData)
async def transaction(candidate_id: UUID, service: Service) -> dict[str, JsonValue]:
    return await service.transaction(candidate_id)


@router.get("/duplicate-groups", response_model=OperationPage)
async def duplicate_groups(
    service: Service,
    date_from: date | None = None,
    date_to: date | None = None,
    search: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    device_id: UUID | None = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$")] = None,
    receipt_type: Literal["INSTAPAY_TRANSFER_RECEIPT", "UNKNOWN_RECEIPT"] | None = None,
    sort_by: Literal["created_at", "reference", "size"] = "created_at",
    sort_direction: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> OperationPage:
    start, end = _dates(date_from, date_to)
    query = DuplicateGroupQuery(
        window=date_window(start, end),
        search=" ".join(search.split()) if search else None,
        device_id=device_id,
        currency=currency.upper() if currency else None,
        receipt_type=receipt_type,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )
    rows, total = await service.duplicate_groups(query)
    return OperationPage(items=rows, total=total, limit=limit, offset=offset)


@router.get("/duplicate-groups/{group_id}", response_model=OperationData)
async def duplicate_group(
    group_id: UUID,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> dict[str, JsonValue]:
    return await service.duplicate_group(group_id, limit=limit, offset=offset)


@router.post(
    "/duplicate-groups/{group_id}/members/{candidate_id}/resolution",
    response_model=OperationData,
)
async def resolve_duplicate(
    group_id: UUID,
    candidate_id: UUID,
    body: DuplicateResolutionRequest,
    service: Service,
    actor: AuthenticatedUser,
) -> dict[str, JsonValue]:
    return await service.resolve_duplicate(
        group_id, candidate_id, actor.id, body.decision, body.note
    )


ReviewCategory = Literal["ready", "missing", "duplicate", "in_progress", "done"]
MAX_CATEGORIZED = 5000


def _review_query(
    date_from: date | None,
    date_to: date | None,
    search: str | None,
    device_id: UUID | None,
    sort_by: str,
    sort_direction: str,
    limit: int,
    offset: int,
) -> TransactionQuery:
    return _transaction_query(
        date_from=date_from,
        date_to=date_to,
        search=search,
        review_status="NEEDS_REVIEW",
        duplicate_status=None,
        receipt_type=None,
        currency=None,
        device_id=device_id,
        source_type=None,
        processing_status=None,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )


async def _categorized(
    service: Service, query: TransactionQuery
) -> tuple[list[dict[str, JsonValue]], dict[str, str]]:
    rows: list[dict[str, JsonValue]] = []
    while len(rows) < MAX_CATEGORIZED:
        page, total = await service.transactions(
            replace(query, limit=100, offset=len(rows))
        )
        rows.extend(page)
        if not page or len(rows) >= total:
            break
    return rows, categorize(rows)


@router.get("/review-queue", response_model=OperationPage)
async def review_queue(
    service: Service,
    date_from: date | None = None,
    date_to: date | None = None,
    search: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    device_id: UUID | None = None,
    category: ReviewCategory | None = None,
    sort_by: Literal[
        "created_at", "transaction_occurred_at", "amount", "reference"
    ] = "created_at",
    sort_direction: Literal["asc", "desc"] = "asc",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> OperationPage:
    query = _review_query(
        date_from, date_to, search, device_id, sort_by, sort_direction, limit, offset
    )
    if category is None:
        rows, total = await service.transactions(query)
        return OperationPage(items=rows, total=total, limit=limit, offset=offset)
    everything, categories = await _categorized(service, query)
    matching = [row for row in everything if categories[str(row["id"])] == category]
    for row in matching:
        row["review_category"] = category
    return OperationPage(
        items=matching[offset : offset + limit],
        total=len(matching),
        limit=limit,
        offset=offset,
    )


@router.get("/review-queue/categories", response_model=OperationData)
async def review_categories(
    service: Service,
    date_from: date | None = None,
    date_to: date | None = None,
    search: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    device_id: UUID | None = None,
) -> dict[str, JsonValue]:
    query = _review_query(
        date_from, date_to, search, device_id, "created_at", "asc", 100, 0
    )
    _, categories = await _categorized(service, query)
    values = list(categories.values())
    return {name: values.count(name) for name in CATEGORIES}


@router.post("/processing/jobs/{job_id}/retry", response_model=RetryResponse)
async def retry_processing(
    job_id: UUID, service: Service, actor: AuthenticatedUser
) -> RetryResponse:
    job = await service.retry_processing(job_id, actor.id)
    value = asdict(job)
    return RetryResponse(
        id=str(value["id"]),
        status=job.status.value,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        next_attempt_at=job.next_attempt_at.isoformat(),
    )


@router.get("/operations/devices", response_model=OperationPage)
async def devices(
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> OperationPage:
    rows, total = await service.devices(limit=limit, offset=offset)
    return OperationPage(items=rows, total=total, limit=limit, offset=offset)


def _folder_window(
    request: Request,
    date_from: date | None,
    date_to: date | None,
    time_from: time | None,
    time_to: time | None,
) -> DateWindow:
    """Reports filter by when the image reached the phone's receipts folder,
    in the receipts' local time zone."""
    start, end = _dates(date_from, date_to)
    settings = cast(Settings, request.app.state.settings)
    return date_window(
        start,
        end,
        zone=ZoneInfo(settings.receipt_timezone),
        time_from=time_from,
        time_to=time_to,
        folder_time=True,
    )


@router.get("/reports/summary", response_model=OperationData)
async def report_summary(
    service: Service,
    request: Request,
    date_from: date | None = None,
    date_to: date | None = None,
    time_from: time | None = None,
    time_to: time | None = None,
) -> dict[str, JsonValue]:
    return await service.dashboard(
        _folder_window(request, date_from, date_to, time_from, time_to)
    )


@router.get("/reports/export.xlsx")
async def export_report(
    service: Service,
    actor: AuthenticatedUser,
    request: Request,
    date_from: date | None = None,
    date_to: date | None = None,
    time_from: time | None = None,
    time_to: time | None = None,
    search: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    review_status: Literal["AUTO_ACCEPTED", "NEEDS_REVIEW", "REJECTED"] | None = None,
    duplicate_status: (
        Literal["DUPLICATE", "CANONICAL", "GROUPED", "UNIQUE"] | None
    ) = None,
    receipt_type: Literal["INSTAPAY_TRANSFER_RECEIPT", "UNKNOWN_RECEIPT"] | None = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$")] = None,
    device_id: UUID | None = None,
    source_type: Literal["saf_tree"] | None = None,
    processing_status: (
        Literal[
            "PENDING", "PROCESSING", "SUCCEEDED", "NEEDS_REVIEW", "FAILED", "SKIPPED"
        ]
        | None
    ) = None,
    sort_by: Literal[
        "created_at", "transaction_occurred_at", "amount", "reference", "review_status"
    ] = "created_at",
    sort_direction: Literal["asc", "desc"] = "desc",
) -> Response:
    values = locals().copy()
    values.update({"limit": 100, "offset": 0})
    for key in ("service", "actor", "request", "time_from", "time_to"):
        values.pop(key)
    query = replace(
        _transaction_query(**values),
        window=_folder_window(request, date_from, date_to, time_from, time_to),
    )
    values.update({"time_from": time_from, "time_to": time_to})
    filters: dict[str, JsonValue] = {
        key: value.isoformat() if isinstance(value, date | time) else str(value)
        for key, value in values.items()
        if value is not None and key not in {"limit", "offset"}
    }
    content = await service.export(query, actor.id, filters)
    filename = f"relay-transactions-{query.window.start.date().isoformat()}-to-"
    filename += (
        f"{(query.window.end_exclusive.date() - timedelta(days=1)).isoformat()}.xlsx"
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
