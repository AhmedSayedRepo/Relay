"""Cashier shifts. One shift is open at a time and stays open until someone
closes it; closing opens the next one. Shifts are numbered per day by the day
they opened (Shift 1, 2, 3 ...). Times are kept to the minute, so a closing
minute ends one shift and starts the next with no overlap."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from relay.infrastructure.operation_models import ShiftRow


def _minute(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def as_dict(row: ShiftRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "day": row.day.isoformat(),
        "number": row.number,
        "opened_at": _aware(row.opened_at).isoformat(),
        "closed_at": _aware(row.closed_at).isoformat() if row.closed_at else None,
        "closed_by": str(row.closed_by) if row.closed_by else None,
        "note": row.note,
        "open": row.closed_at is None,
    }


async def _open(session: AsyncSession, moment: datetime, zone: ZoneInfo) -> ShiftRow:
    day = moment.astimezone(zone).date()
    count = await session.scalar(
        select(func.count()).select_from(ShiftRow).where(ShiftRow.day == day)
    )
    row = ShiftRow(id=uuid4(), day=day, number=int(count or 0) + 1, opened_at=moment)
    session.add(row)
    await session.flush()
    return row


async def current(session: AsyncSession, zone: ZoneInfo) -> ShiftRow:
    """The open shift; the first one starts at the beginning of today."""
    row = await session.scalar(
        select(ShiftRow)
        .where(ShiftRow.closed_at.is_(None))
        .order_by(ShiftRow.opened_at)
    )
    if row is not None:
        return row
    today = datetime.now(UTC).astimezone(zone).date()
    start = datetime.combine(today, time.min, zone).astimezone(UTC)
    row = await _open(session, start, zone)
    await session.commit()
    return row


async def of_day(session: AsyncSession, day: date, zone: ZoneInfo) -> list[ShiftRow]:
    await current(session, zone)
    rows = await session.scalars(
        select(ShiftRow).where(ShiftRow.day == day).order_by(ShiftRow.number)
    )
    return list(rows)


async def close(
    session: AsyncSession, actor_id: UUID, note: str | None, zone: ZoneInfo
) -> tuple[ShiftRow, ShiftRow]:
    """Close the open shift now and open the next one at the same minute."""
    shift = await current(session, zone)
    now = max(_minute(datetime.now(UTC)), _aware(shift.opened_at))
    shift.closed_at = now
    shift.closed_by = actor_id
    shift.note = (note or "").strip()[:500] or None
    await session.flush()
    following = await _open(session, now, zone)
    await session.commit()
    return shift, following
