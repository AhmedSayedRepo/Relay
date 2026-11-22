"""Receipts set aside (rejected, or hidden as not a receipt) and bringing them
back to Review; and the bank SMS still waiting that a receipt may belong to.

A person (or a reader) can reject a real receipt. These checks catch that:
before a reject (``unused_sms_for``), the same evening (``rejected_with_sms``
in the warnings), and afterwards on the Rejected page (``reopen``).
"""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from relay.application.errors import Conflict, NotFound
from relay.domain.processing import ReviewStatus
from relay.infrastructure.binary_models import BinaryObjectRow, UploadSessionRow
from relay.infrastructure.event_models import RawEventRow
from relay.infrastructure.operation_models import OperatorAuditRow
from relay.infrastructure.processing_models import (
    OcrResultRow,
    ProcessingAttemptRow,
    ProcessingJobRow,
    ReceiptCandidateRow,
    ReceiptEvidenceRow,
    ReceiptExceptionCommandRow,
    ReceiptReviewAuditRow,
    ReceiptReviewedFieldRow,
)
from relay.infrastructure.sms_verification_models import SmsVerificationRow

KEPT = "kept_by"  # resolution key: a person brought it back; readers leave it


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def receipt_amounts(row: ReceiptCandidateRow) -> set[Decimal]:
    """Every amount the receipt shows: suggested, confirmed, or read by
    classic PaddleOCR or Tesseract."""
    from relay.infrastructure.cli import _helper_amount, _helper_read, _text_amount

    found: set[Decimal] = set()
    resolution = row.resolution_result
    fields = resolution.get("fields") if isinstance(resolution, dict) else None
    field = fields.get("amount") if isinstance(fields, dict) else None
    values: list[Any] = []
    if isinstance(field, dict):
        listed = field.get("candidates")
        values = [field.get("value")] + [
            item.get("normalized_value") or item.get("value")
            for item in (listed if isinstance(listed, list) else [])
            if isinstance(item, dict)
        ]
    machine = row.machine_result if isinstance(row.machine_result, dict) else {}
    values += [_helper_amount(_helper_read(machine)), _text_amount(machine)]
    for value in values:
        amount = _decimal(value) if value is not None else None
        if amount is not None and amount > 0:
            found.add(amount)
    return found


async def day_sms(
    session: AsyncSession, day: date, zone: ZoneInfo
) -> list[dict[str, Any]]:
    """Incoming bank SMS that day (local time), each with the receipt it
    confirmed (``used_by``), or None while unused."""
    from relay.domain.sms_verification import SmsDirection, parse_bank_sms

    start = datetime.combine(day, time.min, zone).astimezone(UTC)
    rows = await session.execute(
        select(RawEventRow.id, RawEventRow.occurred_at, RawEventRow.payload).where(
            RawEventRow.collector == "sms",
            RawEventRow.occurred_at >= start,
            RawEventRow.occurred_at < start + timedelta(days=1),
        )
    )
    events = rows.all()
    links = await session.execute(
        select(SmsVerificationRow.sms_event_id, SmsVerificationRow.candidate_id).where(
            SmsVerificationRow.sms_event_id.in_([row[0] for row in events])
        )
    )
    used: dict[UUID, UUID] = {
        event: candidate for event, candidate in links.all() if event is not None
    }
    found = []
    for event_id, occurred, payload in events:
        body = payload.get("body") if isinstance(payload, dict) else None
        parsed = parse_bank_sms(body) if isinstance(body, str) else None
        if parsed is None or parsed.direction != SmsDirection.IN:
            continue
        moment = occurred if occurred.tzinfo else occurred.replace(tzinfo=UTC)
        owner = used.get(event_id)
        found.append(
            {
                "event_id": str(event_id),
                "at": moment.isoformat(),
                "amount": f"{parsed.amount:.2f}",
                "currency": parsed.currency,
                "used_by": str(owner) if owner else None,
            }
        )
    return sorted(found, key=lambda item: str(item["at"]))


async def unused_sms(
    session: AsyncSession, day: date, zone: ZoneInfo
) -> list[dict[str, Any]]:
    """Incoming bank SMS that day (local time) that confirmed no receipt."""
    return [sms for sms in await day_sms(session, day, zone) if not sms["used_by"]]


async def sms_notes(
    session: AsyncSession, items: list[dict[str, Any]], zone: ZoneInfo
) -> None:
    """For the Rejected page, where the photo is not shown: the bank SMS that
    day with the receipt's amount. Prefers an unused one (the receipt may be
    real); otherwise one already used by another receipt (likely a copy).
    ``sms_match`` stays empty when no SMS that day has this amount."""
    by_day: dict[date, list[tuple[dict[str, Any], Decimal]]] = {}
    for item in items:
        amount = _decimal(item.get("amount") or item.get("suggested_amount"))
        when = item.get("folder_at") or item.get("created_at")
        if amount is None or not when:
            continue
        day = datetime.fromisoformat(str(when)).astimezone(zone).date()
        by_day.setdefault(day, []).append((item, amount))
    for day, entries in by_day.items():
        sms = await day_sms(session, day, zone)
        for item, amount in entries:
            same = [one for one in sms if Decimal(one["amount"]) == amount]
            pick = next((one for one in same if not one["used_by"]), None) or (
                same[0] if same else None
            )
            item["sms_match"] = (
                {"at": pick["at"], "used_by": pick["used_by"]}
                if pick
                else {"at": None, "used_by": None}
            )


async def _folder_day(
    session: AsyncSession, row: ReceiptCandidateRow, zone: ZoneInfo
) -> date:
    occurred = await session.scalar(
        select(RawEventRow.occurred_at).where(RawEventRow.id == row.raw_event_id)
    )
    moment = occurred or row.created_at
    moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return moment.astimezone(zone).date()


async def unused_sms_for(
    session: AsyncSession, candidate_id: UUID, zone: ZoneInfo
) -> list[dict[str, Any]]:
    """Unused bank SMS from the day the receipt reached the phone with an
    amount the receipt shows: rejecting it would leave that money uncounted."""
    row = await session.get(ReceiptCandidateRow, candidate_id)
    if row is None:
        raise NotFound("Receipt candidate not found.")
    amounts = receipt_amounts(row)
    if not amounts:
        return []
    day = await _folder_day(session, row, zone)
    return [
        sms
        for sms in await unused_sms(session, day, zone)
        if Decimal(sms["amount"]) in amounts
    ]


async def rejected_with_sms(
    session: AsyncSession, day: date, zone: ZoneInfo
) -> list[dict[str, Any]]:
    """Receipts rejected that day whose amount an unused bank SMS shows."""
    waiting = await unused_sms(session, day, zone)
    if not waiting:
        return []
    start = datetime.combine(day, time.min, zone).astimezone(UTC)
    rows = (
        await session.execute(
            select(ReceiptCandidateRow, RawEventRow.occurred_at)
            .join(RawEventRow, RawEventRow.id == ReceiptCandidateRow.raw_event_id)
            .where(
                ReceiptCandidateRow.review_status == ReviewStatus.REJECTED.value,
                RawEventRow.occurred_at >= start,
                RawEventRow.occurred_at < start + timedelta(days=1),
            )
        )
    ).all()
    found = []
    for row, occurred in rows:
        amounts = receipt_amounts(row)
        sms = next((s for s in waiting if Decimal(s["amount"]) in amounts), None)
        if sms is None:
            continue
        moment = occurred if occurred.tzinfo else occurred.replace(tzinfo=UTC)
        found.append({"id": str(row.id), "arrived": moment.isoformat(), "sms": sms})
    return found


async def reopen(session: AsyncSession, candidate_id: UUID, actor_id: UUID) -> None:
    """Bring a rejected, hidden or confirmed receipt back to Review, every
    field to be decided again. A confirmed one gives its bank SMS back, so
    the SMS can match again. Recorded in the review audit; readers will not
    reject or hide it again by themselves."""
    from relay.infrastructure.cli import _reopen

    row = await session.scalar(
        select(ReceiptCandidateRow)
        .where(ReceiptCandidateRow.id == candidate_id)
        .with_for_update()
    )
    if row is None:
        raise NotFound("Receipt candidate not found.")
    rejected = row.review_status == ReviewStatus.REJECTED.value
    accepted = row.review_status == ReviewStatus.AUTO_ACCEPTED.value
    if not rejected and not accepted and row.hidden_reason is None:
        raise Conflict("This receipt is already in Review.")
    now = datetime.now(UTC)
    before = {
        "review_status": row.review_status,
        "hidden_reason": row.hidden_reason,
        "resolution_result": copy.deepcopy(row.resolution_result),
    }
    row.hidden_reason = None
    if accepted:
        from relay.infrastructure.sms_verification_models import SmsVerificationRow

        await session.execute(
            delete(SmsVerificationRow).where(SmsVerificationRow.candidate_id == row.id)
        )
    if rejected or accepted:
        resolution = _reopen(row, "operator", now)
    else:
        resolution = copy.deepcopy(row.resolution_result or {})
    resolution.pop("rejected_reason", None)
    resolution[KEPT] = {"user": str(actor_id), "at": now.isoformat()}
    row.resolution_result = resolution
    row.reviewed_result = None
    row.updated_at = now
    session.add(
        ReceiptReviewAuditRow(
            id=uuid4(),
            candidate_id=row.id,
            reviewer_id=actor_id,
            action="REOPEN",
            before_result=before,
            after_result={"review_status": row.review_status, "hidden_reason": None},
            created_at=now,
            expires_at=row.expires_at,
        )
    )
    await session.commit()


def kept(row: ReceiptCandidateRow) -> bool:
    """A person brought this receipt back: no reader rejects or hides it."""
    resolution = row.resolution_result
    return isinstance(resolution, dict) and isinstance(resolution.get(KEPT), dict)


async def delete_set_aside(
    session: AsyncSession,
    ids: list[UUID],
    actor_id: UUID,
    audit_days: int,
) -> tuple[int, list[str]]:
    """Delete rejected or hidden receipts for good: the receipt, its readings
    and, when no other receipt uses it, its photo. Each deletion is kept in
    the operator audit (who, when, which receipt, why it was set aside).
    Returns how many were deleted and the photo storage keys to remove once
    the deletion is committed. Receipts still in Review or accepted are
    skipped."""
    from relay.infrastructure.operations_repository import _status_reason

    now = datetime.now(UTC)
    deleted = 0
    files: list[str] = []
    for candidate_id in dict.fromkeys(ids):
        row = await session.get(ReceiptCandidateRow, candidate_id)
        if row is None:
            continue
        if row.review_status != ReviewStatus.REJECTED.value and not row.hidden_reason:
            continue
        resolution = (
            row.resolution_result if isinstance(row.resolution_result, dict) else {}
        )
        fields = resolution.get("fields")
        field = fields.get("amount") if isinstance(fields, dict) else None
        context = {
            "candidate_id": str(row.id),
            "reason": _status_reason(row, None),
            "review_status": row.review_status,
            "hidden_reason": row.hidden_reason,
            "amount": field.get("value") if isinstance(field, dict) else None,
            "device_id": str(row.device_id),
            "received_at": row.created_at.isoformat(),
        }
        await session.execute(
            update(ReceiptCandidateRow)
            .where(ReceiptCandidateRow.duplicate_of_candidate_id == row.id)
            .values(duplicate_of_candidate_id=None)
        )
        for model in (
            SmsVerificationRow,
            ReceiptReviewAuditRow,
            ReceiptReviewedFieldRow,
            ReceiptExceptionCommandRow,
            ReceiptEvidenceRow,
        ):
            await session.execute(delete(model).where(model.candidate_id == row.id))
        object_id, job_id = row.object_id, row.job_id
        await session.delete(row)
        await session.flush()
        for child in (OcrResultRow, ProcessingAttemptRow):
            await session.execute(delete(child).where(child.job_id == job_id))
        await session.execute(
            delete(ProcessingJobRow).where(ProcessingJobRow.id == job_id)
        )
        others = await session.scalar(
            select(func.count())
            .select_from(ReceiptCandidateRow)
            .where(ReceiptCandidateRow.object_id == object_id)
        )
        photo = await session.get(BinaryObjectRow, object_id)
        if not others and photo is not None:
            files.append(photo.storage_key)
            await session.execute(
                delete(RawEventRow).where(RawEventRow.object_id == object_id)
            )
            await session.execute(
                delete(UploadSessionRow).where(UploadSessionRow.object_id == object_id)
            )
            await session.delete(photo)
        session.add(
            OperatorAuditRow(
                id=uuid4(),
                actor_id=actor_id,
                action="RECEIPT_DELETE",
                candidate_id=None,
                job_id=None,
                context=context,
                created_at=now,
                expires_at=now + timedelta(days=audit_days),
            )
        )
        deleted += 1
    await session.commit()
    return deleted, files
