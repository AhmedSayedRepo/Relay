"""One day, side by side: every receipt photo and every bank SMS.

Answers "why isn't this confirmed?" and "where did this payment go?" from
the data instead of guesses:

- each photo that reached Relay, what both readers read and why it waits;
- each incoming bank SMS and the receipt it confirmed, if any;
- SMS with no receipt (photo never uploaded, or its amount misread) and
  SMS the parser could not read.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from relay.infrastructure.config import Settings
from relay.infrastructure.database import Database
from relay.infrastructure.datetime_utils import aware
from relay.infrastructure.event_models import RawEventRow
from relay.infrastructure.models import DeviceRow
from relay.infrastructure.processing_models import ReceiptCandidateRow
from relay.infrastructure.sms_verification_models import SmsVerificationRow


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01")) if value else None
    except (InvalidOperation, ValueError):
        return None


def _show(value: Decimal | None) -> str:
    return f"{value:,.2f}" if value is not None else "-"


async def reconcile(settings: Settings, day: date) -> list[str]:
    from relay.domain.sms_verification import SmsDirection, parse_bank_sms
    from relay.infrastructure.cli import _text_amount
    from relay.infrastructure.email_settings import recipient
    from relay.infrastructure.text_recheck import VERSION, recipient_check

    zone = ZoneInfo(settings.receipt_timezone)
    start = datetime.combine(day, time(), zone)
    end = start + timedelta(days=1)

    def clock(moment: datetime | None) -> str:
        return aware(moment).astimezone(zone).strftime("%H:%M") if moment else "--:--"

    database = Database(settings)
    try:
        async with database.session() as session:
            owner = await recipient(session)
            devices = await session.execute(select(DeviceRow.id, DeviceRow.name))
            names: dict[UUID, str] = {row[0]: row[1] for row in devices.all()}
            photos = (
                await session.execute(
                    select(RawEventRow, ReceiptCandidateRow)
                    .outerjoin(
                        ReceiptCandidateRow,
                        ReceiptCandidateRow.raw_event_id == RawEventRow.id,
                    )
                    .where(
                        RawEventRow.collector == "images",
                        RawEventRow.occurred_at >= start,
                        RawEventRow.occurred_at < end,
                    )
                    .order_by(RawEventRow.occurred_at)
                )
            ).all()
            texts = (
                await session.scalars(
                    select(RawEventRow)
                    .where(
                        RawEventRow.collector == "sms",
                        RawEventRow.occurred_at >= start,
                        RawEventRow.occurred_at < end,
                    )
                    .order_by(RawEventRow.occurred_at)
                )
            ).all()
            links = (
                await session.scalars(
                    select(SmsVerificationRow).where(
                        SmsVerificationRow.sms_occurred_at >= start - timedelta(days=1),
                        SmsVerificationRow.sms_occurred_at < end + timedelta(days=1),
                    )
                )
            ).all()
    finally:
        await database.close()

    by_sms = {
        link.sms_event_id: link.candidate_id for link in links if link.sms_event_id
    }
    short: dict[UUID, str] = {}
    lines = [f"== {day.isoformat()} (Cairo) =="]

    # Photos -----------------------------------------------------------------
    receipts: list[tuple[str, Decimal | None, Decimal | None, str]] = []
    lines.append(f"\nPHOTOS that reached Relay: {len(photos)}")
    lines.append(
        "  time  id       phone        status        model     text      recipient  why"
    )
    for event, row in photos:
        device = str(names.get(event.device_id, "?"))[:12]
        if row is None:
            lines.append(
                f"  {clock(event.occurred_at)} {'-':8} {device:12} not processed yet"
            )
            continue
        key = str(row.id)[:8]
        short[row.id] = key
        resolution = (
            row.resolution_result if isinstance(row.resolution_result, dict) else {}
        )
        fields = resolution.get("fields")
        field = fields.get("amount") if isinstance(fields, dict) else None
        model = _money(field.get("value")) if isinstance(field, dict) else None
        machine = row.machine_result if isinstance(row.machine_result, dict) else {}
        read = machine.get(VERSION)
        text = _money(_text_amount(machine))
        verdict = "-"
        if isinstance(read, dict) and owner is not None:
            found = read.get("found")
            brand = bool(found.get("brand")) if isinstance(found, dict) else False
            verdict = recipient_check(
                str(read.get("text") or ""), owner[0], owner[1], brand
            )
        status = row.review_status
        if row.hidden_reason:
            status = "HIDDEN"
        elif "auto_confirmed" in resolution:
            status = "CONFIRMED"
        why = ""
        if row.hidden_reason:
            why = str(row.hidden_reason)
        elif not isinstance(read, dict):
            why = "text not read yet"
        elif resolution.get("possible_repeat"):
            why = "possible repeat"
        elif resolution.get("recipient_mismatch"):
            why = f"recipient {resolution['recipient_mismatch'].get('kind')}"
        elif resolution.get("rejected_reason"):
            why = str(resolution["rejected_reason"])
        lines.append(
            f"  {clock(event.occurred_at)} {key} {device:12} {status:13} "
            f"{_show(model):>9} {_show(text):>9} {verdict:10} {why}"
        )
        if not row.hidden_reason and status != "REJECTED":
            receipts.append((key, model, text, status))

    # Bank SMS ---------------------------------------------------------------
    unread: list[str] = []
    free: list[tuple[str, Decimal]] = []
    incoming = 0
    lines.append("\nBANK SMS (incoming)")
    lines.append("  time  amount       confirmed receipt")
    for event in texts:
        body = event.payload.get("body") if isinstance(event.payload, dict) else None
        parsed = parse_bank_sms(body) if isinstance(body, str) else None
        if parsed is None:
            if isinstance(body, str) and any(ch.isdigit() for ch in body):
                unread.append(
                    f"  {clock(event.occurred_at)} {' '.join(body.split())[:140]}"
                )
            continue
        if parsed.direction != SmsDirection.IN:
            continue
        incoming += 1
        amount = parsed.amount.quantize(Decimal("0.01"))
        owner_id: UUID | None = by_sms.get(event.id)
        matched = short.get(owner_id, str(owner_id)[:8]) if owner_id else "NONE"
        lines.append(f"  {clock(event.occurred_at)} {_show(amount):>11}  {matched}")
        if owner_id is None:
            free.append((clock(event.occurred_at), amount))

    # What is left ----------------------------------------------------------
    lines.append(f"\nSUMMARY: {len(photos)} photos, {incoming} incoming SMS")
    lines.append(f"SMS with no receipt ({len(free)}):")
    for at, amount in free:
        near = [
            f"{key} (model {_show(model)}, text {_show(text)})"
            for key, model, text, status in receipts
            if status != "CONFIRMED" and amount in (model, text)
        ]
        hint = (
            "same amount waiting: " + ", ".join(near)
            if near
            else "no photo with this amount"
        )
        lines.append(f"  {at} {_show(amount):>11}  {hint}")
    lines.append(f"SMS the parser could not read ({len(unread)}):")
    lines.extend(unread)
    return lines
