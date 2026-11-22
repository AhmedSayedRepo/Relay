"""End-of-day summary email for the admin, with the day's Excel export."""

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from relay.application.operations import OperationsService, date_window
from relay.domain.operations import DateWindow, TransactionQuery
from relay.domain.users import Role
from relay.infrastructure.config import Settings
from relay.infrastructure.database import Database
from relay.infrastructure.email_settings import mail_config
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


def render(
    day: date,
    summary: dict[str, Any],
    sms: dict[str, Any],
    shift: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Plain-text and HTML bodies for one day's report, or one shift's."""
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
    title = (
        f"Relay shift report: {day.isoformat()} shift {shift['number']}"
        if shift
        else f"Relay daily report: {day.isoformat()}"
    )

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

    html = _html(day, summary, sms, matching, activity, phones, shift)
    return "\n".join(lines), html


def _html(
    day: date,
    summary: dict[str, Any],
    sms: dict[str, Any],
    matching: list[tuple[str, Any]],
    activity: list[tuple[str, Any]],
    phones: list[dict[str, Any]],
    shift: dict[str, Any] | None = None,
) -> str:
    """The QA-Studio-style HTML body (see email_layout)."""
    from relay.infrastructure import email_layout as layout

    bank = sms.get("counts") or {}
    waiting = _number(summary.get("needs_review_count"))
    unused = _number(bank.get("UNUSED"))
    strip = layout.kpis(
        [
            ("Confirmed money", _money(summary.get("totals_by_currency")), "ok"),
            (
                "Confirmed",
                str(_number(summary.get("final_transaction_count"))),
                "brand",
            ),
            ("Needs review", str(waiting), "warn" if waiting else "muted"),
            ("SMS not used", str(unused), "bad" if unused else "muted"),
        ]
    )
    phone_rows = [
        (
            str(item.get("device_name") or ""),
            str(_number(item.get("count"))),
            str(_number(item.get("confirmed_count"))),
            str(_number(item.get("sms_verified_count"))),
            f"{float(item.get('confirmed_amount') or 0):,.2f}",
        )
        for item in phones
    ]
    status_rows = [
        (
            "Auto-confirmed (bank SMS)",
            _number(summary.get("auto_confirmed_count")),
        ),
        (
            "Confirmed by a reviewer",
            _number(summary.get("manual_confirmed_count")),
        ),
        ("Waiting for review", waiting),
        (
            "Duplicate groups",
            _number(summary.get("duplicate_group_count")),
        ),
    ]
    sections = [
        layout.section_head("Per phone", len(phones))
        + "<div style='margin-top:14px'>"
        + layout.table(
            ("Phone", "Received", "Confirmed", "SMS matched", "Money (EGP)"),
            phone_rows,
            right=(1, 2, 3, 4),
            empty="No receipts today.",
        )
        + "</div>",
        layout.section_head("Receipts")
        + "<div style='margin-top:14px'>"
        + layout.table(
            ("Status", "Count"),
            [(label, str(count)) for label, count in status_rows],
            right=(1,),
        )
        + "</div>",
        layout.section_head("Bank SMS matching", dot=layout.ACCENT)
        + "<div style='margin-top:14px'>"
        + layout.facts([(label, str(value)) for label, value in matching])
        + "</div>",
        layout.section_head("Activity", dot=layout.INK3)
        + "<div style='margin-top:14px'>"
        + layout.facts([(label, str(value)) for label, value in activity])
        + "</div>",
    ]
    return layout.page(
        kind="Shift report" if shift else "Daily report",
        stamp=day.isoformat(),
        badge=(
            (f"SHIFT {shift['number']}", "brand")
            if shift
            else ("DAILY SUMMARY", "brand")
        ),
        title=f"{day:%A %d %B %Y}",
        subtitle=(
            f"{shift['span']}, by when receipts reached each phone"
            if shift
            else "12:00 AM to 11:59 PM, by when receipts reached each phone"
        ),
        strip=strip,
        sections=sections,
        footer="The full list is attached as Excel.",
    )


async def send_daily_report(
    settings: Settings, *, force: bool = False, now: datetime | None = None
) -> bool:
    """Send today's report at 23:xx local time (or now, with ``force``)."""
    zone = ZoneInfo(settings.receipt_timezone)
    local = (now or datetime.now(UTC)).astimezone(zone)
    if not force and local.hour != SEND_HOUR:
        return False
    day = local.date()
    window = date_window(day, day, zone=zone, folder_time=True)
    return await send_window_report(settings, day, window)


async def send_shift_report(
    settings: Settings, shift: dict[str, Any], language: str = "en"
) -> bool:
    """Send a closed shift's report: its counts and Excel list."""
    zone = ZoneInfo(settings.receipt_timezone)
    start = datetime.fromisoformat(str(shift["opened_at"]))
    end = datetime.fromisoformat(str(shift["closed_at"]))
    if end <= start:
        return False  # an empty shift: nothing to report
    span = f"{start.astimezone(zone):%I:%M %p} to {end.astimezone(zone):%I:%M %p}"
    window = DateWindow(start, end, folder_time=True)
    day = date.fromisoformat(str(shift["day"]))
    return await send_window_report(
        settings, day, window, shift | {"span": span}, language=language
    )


async def send_window_report(
    settings: Settings,
    day: date,
    window: DateWindow,
    shift: dict[str, Any] | None = None,
    language: str = "en",
) -> bool:
    """Email one period's report, in English or Arabic (the operator's
    portal language when they close a shift)."""
    database = Database(settings)
    try:
        async with database.session() as session:
            config = await mail_config(session, settings)
            if config is None or not config.report_email:
                print("Report email is not configured; nothing sent.")
                return False
            recipient: str = config.report_email
            mailer = SmtpMailer(config)
            uow = SqlUnitOfWork(session)
            service = OperationsService(
                uow,
                OpenpyxlWorkbookExporter(settings.receipt_timezone),
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
    text, html = render(day, dict(summary), dict(sms), shift)
    name = f"relay-{day.isoformat()}" + (f"-shift-{shift['number']}" if shift else "")
    attachment = (f"{name}.xlsx", workbook) if workbook else None
    subject = (
        f"Relay shift report: {day.isoformat()} shift {shift['number']}"
        if shift
        else f"Relay daily report: {day.isoformat()}"
    )
    if language == "ar":
        from relay.infrastructure import report_language as arabic

        text, html, subject = (
            arabic.arabic_text(text),
            arabic.arabic_html(html),
            arabic.translate(subject),
        )
        if attachment is not None:
            attachment = (attachment[0], arabic.arabic_workbook(attachment[1]))
    await mailer.send_report(recipient, subject, text, html, attachment)
    return True
