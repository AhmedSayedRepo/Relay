"""Audit trail: who confirmed, rejected or sent back each receipt, and when."""

from datetime import date, datetime, time
from typing import Annotated, Any, Literal, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response

from relay.application.operations import date_window
from relay.infrastructure.audit_trail import trail, workbook
from relay.infrastructure.config import Settings
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.presentation.dependencies import current_user, unit_of_work

router = APIRouter(tags=["Audit trail"], dependencies=[Depends(current_user)])

Action = Literal["confirmed", "rejected", "rejected_copy", "sent_back", "not_a_copy"]


async def _items(
    request: Request,
    uow: SqlUnitOfWork,
    date_from: date | None,
    date_to: date | None,
    time_from: time | None,
    time_to: time | None,
    actor: str | None,
    action: str | None,
) -> tuple[list[dict[str, Any]], ZoneInfo, date, date]:
    settings = cast(Settings, request.app.state.settings)
    zone = ZoneInfo(settings.receipt_timezone)
    today = datetime.now(zone).date()
    first = date_from or today
    last = date_to or date_from or today
    window = date_window(first, last, zone=zone, time_from=time_from, time_to=time_to)
    items = await trail(
        uow.session, window.start, window.end_exclusive, actor=actor, action=action
    )
    return items, zone, first, last


@router.get("/audit-trail")
async def audit_trail(
    request: Request,
    uow: Annotated[SqlUnitOfWork, Depends(unit_of_work)],
    date_from: date | None = None,
    date_to: date | None = None,
    time_from: time | None = None,
    time_to: time | None = None,
    actor: Annotated[str | None, Query(max_length=254)] = None,
    action: Action | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> dict[str, Any]:
    """Decisions in the range (today by default), newest first; ``actor`` is
    a user's email or "automatic". ``actors`` lists who decided in the range."""
    everyone, _, _, _ = await _items(
        request, uow, date_from, date_to, time_from, time_to, None, action
    )
    items = [
        item
        for item in everyone
        if not actor
        or (item["automatic"] if actor == "automatic" else item["actor"] == actor)
    ]
    counts = {
        "automatic": sum(item["automatic"] for item in items),
        "people": sum(not item["automatic"] for item in items),
    }
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "counts": counts,
        "actors": sorted({str(item["actor"]) for item in everyone if item["actor"]}),
    }


@router.get("/audit-trail/export.xlsx")
async def export_audit_trail(
    request: Request,
    uow: Annotated[SqlUnitOfWork, Depends(unit_of_work)],
    date_from: date | None = None,
    date_to: date | None = None,
    time_from: time | None = None,
    time_to: time | None = None,
    actor: Annotated[str | None, Query(max_length=254)] = None,
    action: Action | None = None,
    language: Literal["en", "ar"] = "en",
) -> Response:
    items, zone, first, last = await _items(
        request, uow, date_from, date_to, time_from, time_to, actor, action
    )
    content = workbook(items, zone)
    if language == "ar":
        from relay.infrastructure.report_language import arabic_workbook

        content = arabic_workbook(content)
    filename = f"relay-audit-{first.isoformat()}-to-{last.isoformat()}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
