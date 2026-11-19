"""Warnings that need someone now, so a silent phone is noticed the same
evening and not the next day:

- a receipt phone has not contacted Relay for over an hour in working hours
  (app closed by battery saving, phone off or offline);
- an incoming bank SMS is over an hour old and no receipt matches it (the
  photo never reached Relay, or it is waiting in Review).

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
            select(DeviceRow.id, DeviceRow.name, DeviceRow.last_seen_at).where(
                DeviceRow.status == DeviceStatus.ACTIVE,
                select(RawEventRow.id)
                .where(
                    RawEventRow.device_id == DeviceRow.id,
                    RawEventRow.collector == "images",
                )
                .exists(),
            )
        )
        for device_id, name, seen in phones.all():
            if seen is not None and moment - _aware(seen) < QUIET:
                continue
            alerts.append(
                {
                    "key": f"silent:{device_id}:{local.date().isoformat()}",
                    "kind": "silent_phone",
                    "device": str(name),
                    "since": _aware(seen).isoformat() if seen else None,
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
    return alerts


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
        return (
            f"{alert['device']}: no contact since {at}. Open Relay Receipts, tap "
            "Sync now, and set its battery to Unrestricted."
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
            html = (
                "<p>Relay needs attention:</p><ul>"
                + "".join(f"<li>{_escape(line)}</li>" for line in lines)
                + "</ul>"
            )
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


def _escape(text: str) -> str:
    from html import escape

    return escape(text)
