"""End-of-day summary email for the admin, with the day's Excel export."""

from datetime import UTC, date, datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from relay.application.operations import OperationsService, date_window
from relay.domain.operations import TransactionQuery
from relay.domain.users import Role
from relay.infrastructure.config import Settings
from relay.infrastructure.database import Database
from relay.infrastructure.excel_export import OpenpyxlWorkbookExporter
from relay.infrastructure.mailer import SmtpMailer
from relay.infrastructure.repositories import SqlUnitOfWork
from relay.infrastructure.sms_verification_repository import (
    build_sms_verification_service,
)

SEND_HOUR = 23  # cron runs hourly at :59; only the 23:59 run sends


def _money(items: Any) -> str:
    rows = items if isinstance(items, list) else []
    parts = [
        f"{float(item.get('amount') or 0):,.2f} {item.get('currency')}"
        for item in rows
        if isinstance(item, dict)
    ]
    return ", ".join(parts) or "0.00 EGP"


def _number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def render(day: date, summary: dict[str, Any], sms: dict[str, Any]) -> tuple[str, str]:
    """Plain-text and HTML bodies for one day's report."""
    sms_status = summary.get("sms_by_status") or {}
    bank = sms.get("counts") or {}
    facts = [
        ("Confirmed money", _money(summary.get("totals_by_currency"))),
        ("Confirmed receipts", _number(summary.get("final_transaction_count"))),
        ("Auto-confirmed", _number(summary.get("auto_confirmed_count"))),
        ("Confirmed by a reviewer", _number(summary.get("manual_confirmed_count"))),
        ("Waiting for review", _number(summary.get("needs_review_count"))),
        ("Duplicate groups", _number(summary.get("duplicate_group_count"))),
    ]
    matching = [
        ("Receipts matched to a bank SMS", _number(sms_status.get("VERIFIED"))),
        ("Receipts waiting for an SMS", _number(sms_status.get("WAITING"))),
        ("Receipts with no matching SMS", _number(sms_status.get("UNMATCHED"))),
        ("Receipts with several possible SMS", _number(sms_status.get("AMBIGUOUS"))),
        ("Bank SMS used for a receipt", _number(bank.get("MATCHED"))),
        ("Bank SMS not used yet", _number(bank.get("UNUSED"))),
        ("Unused SMS money", _money(sms.get("unused_totals"))),
        ("SMS without an amount", _number(bank.get("NO_AMOUNT"))),
    ]
    activity = [
        ("Receipt images received", _number(summary.get("receipt_image_activity"))),
        ("Bank SMS received", _number(summary.get("sms_activity"))),
        ("Images still processing", _number(summary.get("pending_processing_count"))),
        ("Images that failed", _number(summary.get("processing_failure_count"))),
    ]
    phones = [
        item
        for item in summary.get("transactions_by_device") or []
        if isinstance(item, dict)
    ]
    title = f"Relay daily report: {day.isoformat()}"

    lines = [title, ""]
    for heading, rows in (
        ("Summary", facts),
        ("Bank SMS matching", matching),
        ("Activity", activity),
    ):
        lines += [heading] + [f"  {label}: {value}" for label, value in rows] + [""]
    lines.append("Per phone (received / confirmed / SMS matched / confirmed money)")
    for item in phones:
        lines.append(
            f"  {item.get('device_name')}: {_number(item.get('count'))} / "
            f"{_number(item.get('confirmed_count'))} / "
            f"{_number(item.get('sms_verified_count'))} / "
            f"{float(item.get('confirmed_amount') or 0):,.2f} EGP"
        )
    if not phones:
        lines.append("  No receipts today.")
    lines += ["", "The full list is in the attached Excel file."]

    def table(heading: str, rows: list[tuple[str, Any]]) -> str:
        body = "".join(
            f'<tr><td style="padding:6px 12px;color:#4b5563">{escape(label)}</td>'
            f'<td style="padding:6px 12px;text-align:right;font-weight:600">'
            f"{escape(str(value))}</td></tr>"
            for label, value in rows
        )
        return (
            f'<h2 style="font-size:16px;margin:24px 0 8px">{escape(heading)}</h2>'
            '<table style="border-collapse:collapse;width:100%;max-width:560px;'
            'background:#fff;border:1px solid #e5e7eb">' + body + "</table>"
        )

    head = "".join(
        f'<th style="padding:6px 12px;text-align:left;background:#f3f4f6">{name}</th>'
        for name in ("Phone", "Received", "Confirmed", "SMS matched", "Money")
    )
    phone_rows = (
        "".join(
            "<tr>"
            + "".join(
                f'<td style="padding:6px 12px;border-top:1px solid #e5e7eb">'
                f"{escape(str(value))}</td>"
                for value in (
                    item.get("device_name"),
                    _number(item.get("count")),
                    _number(item.get("confirmed_count")),
                    _number(item.get("sms_verified_count")),
                    f"{float(item.get('confirmed_amount') or 0):,.2f} EGP",
                )
            )
            + "</tr>"
            for item in phones
        )
        or '<tr><td colspan="5" style="padding:6px 12px">No receipts today.</td></tr>'
    )
    html = (
        '<!doctype html><html><body style="margin:0;padding:24px;'
        "background:#f9fafb;font-family:Segoe UI,Arial,sans-serif;color:#111827\">"
        f'<h1 style="font-size:20px;margin:0 0 4px">{escape(title)}</h1>'
        '<p style="margin:0;color:#6b7280">From 12:00 AM to 11:59 PM, '
        "by when receipts reached each phone's folder.</p>"
        + table("Summary", facts)
        + table("Bank SMS matching", matching)
        + '<h2 style="font-size:16px;margin:24px 0 8px">Per phone</h2>'
        '<table style="border-collapse:collapse;width:100%;max-width:560px;'
        f'background:#fff;border:1px solid #e5e7eb"><tr>{head}</tr>{phone_rows}'
        "</table>"
        + table("Activity", activity)
        + '<p style="margin-top:24px;color:#6b7280">The full list of receipts is '
        "in the attached Excel file.</p></body></html>"
    )
    return "\n".join(lines), html


async def send_daily_report(
    settings: Settings, *, force: bool = False, now: datetime | None = None
) -> bool:
    """Send today's report at 23:xx local time (or now, with ``force``)."""
    zone = ZoneInfo(settings.receipt_timezone)
    local = (now or datetime.now(UTC)).astimezone(zone)
    if not force and local.hour != SEND_HOUR:
        return False
    recipient = settings.daily_report_email
    if not recipient or not settings.smtp_host:
        print("Daily report email is not configured; nothing sent.")
        return False
    day = local.date()
    window = date_window(day, day, zone=zone, folder_time=True)
    database = Database(settings)
    try:
        async with database.session() as session:
            uow = SqlUnitOfWork(session)
            service = OperationsService(
                uow,
                OpenpyxlWorkbookExporter(),
                audit_retention_days=settings.operator_audit_retention_days,
            )
            summary = await service.dashboard(window)
            sms = await build_sms_verification_service(uow, settings).bank_sms(
                window.start, window.end_exclusive, limit=1
            )
            active = await uow.users.list_active()
            admins = [user for user in active if user.role == Role.ADMIN]
            workbook: bytes | None = None
            if admins:
                workbook = await service.export(
                    TransactionQuery(window=window, limit=100),
                    admins[0].id,
                    {"date_from": day.isoformat(), "date_to": day.isoformat()},
                )
    finally:
        await database.close()
    text, html = render(day, dict(summary), dict(sms))
    attachment = (f"relay-{day.isoformat()}.xlsx", workbook) if workbook else None
    await SmtpMailer(settings).send_report(
        recipient,
        f"Relay daily report: {day.isoformat()}",
        text,
        html,
        attachment,
    )
    return True
