"""Warnings that need someone now, so a silent phone is noticed the same
evening and not the next day:

- a receipt phone has not contacted Relay for over an hour in working hours
  (app closed by battery saving, phone off or offline);
- an incoming bank SMS is over an hour old and no receipt matches it (the
  photo never reached Relay, or it is waiting in Review);
- an old receipt shown again: its reference was already on a receipt from an
  earlier day, or its printed date is days before it reached the phone.

Shown on the dashboard and emailed, each warning once, by ``check-alerts``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.devices import DeviceStatus
from relay.infrastructure.config import Settings
from relay.infrastructure.event_models import RawEventRow
from relay.infrastructure.models import DeviceRow
from relay.infrastructure.phone_health import describe as health_words
from relay.infrastructure.phone_health import summary
from relay.infrastructure.processing_models import ReceiptCandidateRow
from relay.infrastructure.sms_verification_models import SmsVerificationRow

WORK_STARTS, WORK_ENDS = 9, 23  # receipt hours, local time
QUIET = timedelta(hours=1)  # how long before something counts as missing
SENT_KEY = "alerts_sent"


async def current_alerts(
    session: AsyncSession, settings: Settings, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Open warnings, oldest first. Each has a stable ``key`` (for emailing
    once), a ``kind``, and what the portal shows."""
    from relay.domain.sms_verification import SmsDirection, parse_bank_sms

    moment = now or datetime.now(UTC)
    zone = ZoneInfo(settings.receipt_timezone)
    local = moment.astimezone(zone)
    alerts: list[dict[str, Any]] = []

    if WORK_STARTS <= local.hour < WORK_ENDS:
        phones = await session.execute(
            select(
                DeviceRow.id, DeviceRow.name, DeviceRow.last_seen_at, DeviceRow.health
            ).where(
                DeviceRow.status == DeviceStatus.ACTIVE,
                select(RawEventRow.id)
                .where(
                    RawEventRow.device_id == DeviceRow.id,
                    RawEventRow.collector == "images",
                )
                .exists(),
            )
        )
        for device_id, name, seen, health in phones.all():
            if seen is not None and moment - _aware(seen) < QUIET:
                continue
            alerts.append(
                {
                    "key": f"silent:{device_id}:{local.date().isoformat()}",
                    "kind": "silent_phone",
                    "device": str(name),
                    "since": _aware(seen).isoformat() if seen else None,
                    "health": summary(health),
                }
            )

    # Today only (local time): the list starts empty every morning; older
    # unmatched SMS are in the daily report.
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    matched = select(SmsVerificationRow.sms_event_id).where(
        SmsVerificationRow.sms_event_id.is_not(None)
    )
    texts = await session.execute(
        select(RawEventRow.id, RawEventRow.occurred_at, RawEventRow.payload)
        .where(
            RawEventRow.collector == "sms",
            RawEventRow.occurred_at >= midnight,
            RawEventRow.occurred_at < moment - QUIET,
            RawEventRow.id.not_in(matched),
        )
        .order_by(RawEventRow.occurred_at)
    )
    for event_id, occurred, payload in texts.all():
        body = payload.get("body") if isinstance(payload, dict) else None
        parsed = parse_bank_sms(body) if isinstance(body, str) else None
        if parsed is None or parsed.direction != SmsDirection.IN:
            continue
        alerts.append(
            {
                "key": f"sms:{event_id}",
                "kind": "sms_without_receipt",
                "amount": f"{parsed.amount:.2f}",
                "currency": parsed.currency,
                "since": _aware(occurred).isoformat(),
            }
        )

    old = await session.execute(
        select(
            ReceiptCandidateRow.id,
            ReceiptCandidateRow.created_at,
            ReceiptCandidateRow.resolution_result,
            DeviceRow.name,
        )
        .join(DeviceRow, DeviceRow.id == ReceiptCandidateRow.device_id)
        .where(
            ReceiptCandidateRow.review_status == "NEEDS_REVIEW",
            ReceiptCandidateRow.hidden_reason.is_(None),
            ReceiptCandidateRow.created_at >= midnight.astimezone(UTC),
            ReceiptCandidateRow.resolution_result["old_receipt"]
            .as_string()
            .is_not(None),
        )
        .order_by(ReceiptCandidateRow.created_at)
    )
    for candidate_id, created, resolution, device in old.all():
        alerts.append(_old_receipt(candidate_id, created, resolution, device))

    from relay.infrastructure.set_aside import rejected_with_sms

    for item in await rejected_with_sms(session, local.date(), zone):
        alerts.append(
            {
                "key": f"rejected:{item['id']}",
                "kind": "rejected_with_sms",
                "id": item["id"],
                "amount": item["sms"]["amount"],
                "currency": item["sms"]["currency"],
                "since": item["sms"]["at"],
                "arrived": item["arrived"],
            }
        )
    return alerts


def _old_receipt(
    candidate_id: Any, created: datetime, resolution: Any, device: Any
) -> dict[str, Any]:
    from relay.domain.review_categories import suggestion

    resolution = resolution if isinstance(resolution, dict) else {}
    fields = resolution.get("fields")
    fields = fields if isinstance(fields, dict) else {}
    tag = resolution.get("old_receipt")
    tag = tag if isinstance(tag, dict) else {}
    return {
        "key": f"old:{candidate_id}",
        "kind": "old_receipt",
        "id": str(candidate_id),
        "device": str(device),
        "amount": suggestion(fields.get("amount")),
        "currency": suggestion(fields.get("currency")) or "EGP",
        "reference": suggestion(fields.get("reference_number")),
        "since": _aware(created).isoformat(),
        "reason": tag.get("kind"),
        "paid_on": tag.get("paid_on"),
        "first_seen": tag.get("first_seen"),
    }


def _money(alert: dict[str, Any]) -> str:
    try:
        return f"{float(alert['amount']):,.2f} {alert['currency']}"
    except (TypeError, ValueError):
        return "amount not read"


def _old_why(alert: dict[str, Any], zone: ZoneInfo) -> str:
    if alert.get("reason") == "reference":
        first = alert.get("first_seen")
        day = (
            datetime.fromisoformat(first).astimezone(zone).strftime("%Y-%m-%d")
            if first
            else "an earlier day"
        )
        return f"same reference as a receipt from {day}"
    return f"paid on {alert.get('paid_on')}"


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def describe(alert: dict[str, Any], zone: ZoneInfo) -> str:
    """One English line for the email."""
    since = alert.get("since")
    at = (
        datetime.fromisoformat(since).astimezone(zone).strftime("%H:%M")
        if since
        else "-"
    )
    if alert["kind"] == "silent_phone":
        why = health_words(alert.get("health"))
        return (
            f"{alert['device']}: no contact since {at} ({why}). "
            "Open Relay Receipts, tap Sync now, and set its battery to Unrestricted."
        )
    if alert["kind"] == "rejected_with_sms":
        return (
            f"A rejected receipt shows {_money(alert)}, and the bank SMS for that "
            f"amount at {at} is still unused. Check the Rejected page: if the "
            "receipt is real, bring it back to Review."
        )
    if alert["kind"] == "old_receipt":
        return (
            f"Old receipt shown again on {alert['device']} at {at}: {_money(alert)}, "
            f"{_old_why(alert, zone)}. It was not confirmed; check it in Review."
        )
    return (
        f"Bank SMS {alert['amount']} {alert['currency']} at {at}: no receipt matches "
        "it. Check that the photo reached the cashier phone and Relay."
    )


async def check_alerts(settings: Settings, now: datetime | None = None) -> int:
    """Email warnings not emailed before; returns how many were sent."""
    from relay.infrastructure.database import Database
    from relay.infrastructure.email_settings import AppSettingRow, mail_config
    from relay.infrastructure.mailer import SmtpMailer

    moment = now or datetime.now(UTC)
    zone = ZoneInfo(settings.receipt_timezone)
    database = Database(settings)
    try:
        async with database.session() as session:
            alerts = await current_alerts(session, settings, moment)
            row = await session.get(AppSettingRow, SENT_KEY)
            sent: dict[str, str] = {}
            if row is not None:
                try:
                    loaded = json.loads(row.value)
                    sent = loaded if isinstance(loaded, dict) else {}
                except ValueError:
                    sent = {}
            fresh = [alert for alert in alerts if alert["key"] not in sent]
            config = await mail_config(session, settings)
            if not fresh or config is None or not config.report_email:
                return 0
            lines = [describe(alert, zone) for alert in fresh]
            text = "Relay needs attention:\n\n" + "\n".join(
                f"- {line}" for line in lines
            )
            html = render_email(fresh, zone, moment)
            await SmtpMailer(config).send_report(
                config.report_email,
                f"Relay: {len(fresh)} warning(s)",
                text,
                html,
                None,
            )
            # Remember for two days; older keys are dropped.
            keep = moment - timedelta(days=2)
            sent = {
                key: at
                for key, at in sent.items()
                if datetime.fromisoformat(at) >= keep
            } | {alert["key"]: moment.isoformat() for alert in fresh}
            value = json.dumps(sent)
            if row is None:
                session.add(AppSettingRow(key=SENT_KEY, value=value, updated_at=moment))
            else:
                row.value, row.updated_at = value, moment
            await session.commit()
            return len(fresh)
    finally:
        await database.close()


def render_email(alerts: list[dict[str, Any]], zone: ZoneInfo, moment: datetime) -> str:
    """The warnings email, in the same look as the daily report."""
    from relay.infrastructure import email_layout as layout

    def clock(alert: dict[str, Any]) -> str:
        since = alert.get("since")
        return (
            datetime.fromisoformat(since).astimezone(zone).strftime("%H:%M")
            if since
            else "never"
        )

    phones = [alert for alert in alerts if alert["kind"] == "silent_phone"]
    texts = [alert for alert in alerts if alert["kind"] == "sms_without_receipt"]
    old = [alert for alert in alerts if alert["kind"] == "old_receipt"]
    rejected = [alert for alert in alerts if alert["kind"] == "rejected_with_sms"]
    sections = []
    if phones:
        sections.append(
            layout.section_head(
                "Silent phones", len(phones), dot=layout.TONES["bad"][0]
            )
            + "<div style='margin-top:14px'>"
            + layout.table(
                ("Phone", "Last contact", "Why"),
                [
                    (
                        str(alert["device"]),
                        clock(alert),
                        health_words(alert.get("health")),
                    )
                    for alert in phones
                ],
            )
            + "</div>"
            + layout.note(
                "On the phone: open Relay Receipts, tap Sync now, and set its "
                "battery to Unrestricted."
            )
        )
    if texts:
        sections.append(
            layout.section_head(
                "Bank SMS with no receipt", len(texts), dot=layout.TONES["warn"][0]
            )
            + "<div style='margin-top:14px'>"
            + layout.table(
                ("Amount", "Arrived"),
                [
                    (f"{float(alert['amount']):,.2f} {alert['currency']}", clock(alert))
                    for alert in texts
                ],
                right=(0,),
            )
            + "</div>"
            + layout.note(
                "Check that the receipt photo reached the cashier phone and Relay."
            )
        )
    if old:
        sections.append(
            layout.section_head(
                "Old receipts shown again", len(old), dot=layout.TONES["bad"][0]
            )
            + "<div style='margin-top:14px'>"
            + layout.table(
                ("Amount", "Phone", "Arrived", "Why"),
                [
                    (
                        _money(alert),
                        str(alert["device"]),
                        clock(alert),
                        _old_why(alert, zone),
                    )
                    for alert in old
                ],
                right=(0,),
            )
            + "</div>"
            + layout.note(
                "Not confirmed. Open Review: if it is a copy of an old receipt, "
                "reject it."
            )
        )
    if rejected:
        sections.append(
            layout.section_head(
                "Rejected, but the bank SMS is unused",
                len(rejected),
                dot=layout.TONES["bad"][0],
            )
            + "<div style='margin-top:14px'>"
            + layout.table(
                ("Amount", "SMS at"),
                [(_money(alert), clock(alert)) for alert in rejected],
                right=(0,),
            )
            + "</div>"
            + layout.note(
                "The money arrived but its receipt was rejected. Open the Rejected "
                "page and bring the receipt back to Review if it is real."
            )
        )
    local = moment.astimezone(zone)
    return layout.page(
        kind="Warnings",
        stamp=f"{local:%Y-%m-%d %H:%M}",
        badge=("NEEDS ATTENTION", "bad"),
        title=f"{len(alerts)} warning{'s' if len(alerts) != 1 else ''}",
        subtitle="Each warning is sent once. Today's list is on the dashboard.",
        sections=sections,
    )
