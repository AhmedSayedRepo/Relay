"""Who confirmed, rejected or sent back each receipt, and when.

One list built from what Relay already keeps: the review audit (people, and
copies Relay rejected), the field decisions of a person who confirmed a
receipt, Relay's own confirmation and rejection marks, and "not a copy"
decisions. Nothing new is stored except the "automatic" mark on copies.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from relay.domain.processing import ReviewStatus
from relay.infrastructure.models import UserRow
from relay.infrastructure.processing_models import (
    ReceiptCandidateRow,
    ReceiptReviewAuditRow,
    ReceiptReviewedFieldRow,
)

ACTIONS = ("confirmed", "rejected", "rejected_copy", "sent_back", "not_a_copy")
AUTOMATIC = "automatic"
# Field decisions Relay writes itself (a matching bank SMS).
_AUTO_REASONS = ("auto-confirmed:",)


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return _when(datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


def _amount(row: ReceiptCandidateRow) -> tuple[str | None, str | None]:
    if row.amount is not None:
        return f"{row.amount:.2f}", row.currency
    fields = (row.resolution_result or {}).get("fields")
    if isinstance(fields, dict):
        amount = fields.get("amount")
        currency = fields.get("currency")
        value = amount.get("value") if isinstance(amount, dict) else None
        code = currency.get("value") if isinstance(currency, dict) else None
        return (str(value) if value else None), (str(code) if code else None)
    return None, None


def _sender(row: ReceiptCandidateRow) -> str | None:
    if row.sender_name:
        return row.sender_name
    parties = (row.resolution_result or {}).get("parties")
    if isinstance(parties, dict) and isinstance(parties.get("sender"), str):
        return str(parties["sender"])
    return None


async def _load(
    session: AsyncSession,
    candidates: dict[UUID, ReceiptCandidateRow],
    ids: set[UUID],
) -> None:
    missing = ids - set(candidates)
    if missing:
        rows = (
            await session.execute(
                select(ReceiptCandidateRow).where(ReceiptCandidateRow.id.in_(missing))
            )
        ).scalars()
        candidates.update({row.id: row for row in rows})


def _rule(auto: dict[str, Any]) -> str:
    if auto.get("no_reference"):
        return "sms_no_reference"
    return "sms_same_amount"


async def trail(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    *,
    actor: str | None = None,
    action: str | None = None,
) -> list[dict[str, Any]]:
    """Every decision in [start, end), newest first. ``actor`` is a user's
    email or "automatic"; ``action`` one of ACTIONS."""
    users = {
        row.id: row.email
        for row in (await session.execute(select(UserRow.id, UserRow.email))).all()
    }
    events: list[dict[str, Any]] = []
    audited: set[UUID] = set()

    # People's decisions, and copies Relay rejected itself.
    audit_rows = (
        await session.execute(
            select(ReceiptReviewAuditRow).where(
                ReceiptReviewAuditRow.created_at >= start,
                ReceiptReviewAuditRow.created_at < end,
            )
        )
    ).scalars()
    for audit in audit_rows:
        after = audit.after_result or {}
        kind = {
            "ACCEPT": "confirmed",
            "CORRECT": "confirmed",
            "REOPEN": "sent_back",
        }.get(audit.action)
        if kind is None:
            kind = "rejected_copy" if after.get("reason") == "copy" else "rejected"
        automatic = bool(after.get("automatic"))
        events.append(
            {
                "candidate_id": audit.candidate_id,
                "at": _when(audit.created_at),
                "action": kind,
                "actor_id": None if automatic else audit.reviewer_id,
                "rule": "copy_of_confirmed" if automatic else None,
                "reason": after.get("reason") or after.get("rejected_reason"),
                "source": "audit",
            }
        )

    # Receipts touched in the window: Relay's own marks, people's confirmations.
    rows = (
        await session.execute(
            select(ReceiptCandidateRow).where(ReceiptCandidateRow.updated_at >= start)
        )
    ).scalars()
    candidates = {row.id: row for row in rows}
    rejected = [
        row.id
        for row in candidates.values()
        if row.review_status == ReviewStatus.REJECTED.value
    ]
    if rejected:
        audited.update(
            (
                await session.execute(
                    select(ReceiptReviewAuditRow.candidate_id).where(
                        ReceiptReviewAuditRow.action == "REJECT",
                        ReceiptReviewAuditRow.candidate_id.in_(rejected),
                    )
                )
            ).scalars()
        )
    for row in candidates.values():
        resolution = row.resolution_result or {}
        if row.review_status == ReviewStatus.REJECTED.value and row.id not in audited:
            events.append(
                {
                    "candidate_id": row.id,
                    "at": _when(row.updated_at),
                    "action": (
                        "rejected_copy"
                        if resolution.get("rejected_reason") == "copy"
                        else "rejected"
                    ),
                    "actor_id": None,
                    "rule": "reader",
                    "reason": row.hidden_reason or resolution.get("rejected_reason"),
                }
            )
        cleared = resolution.get("repeat_cleared")
        if isinstance(cleared, dict):
            try:
                who: UUID | None = UUID(str(cleared.get("user")))
            except ValueError:
                who = None
            events.append(
                {
                    "candidate_id": row.id,
                    "at": _when(cleared.get("at")),
                    "action": "not_a_copy",
                    "actor_id": who,
                    "rule": None,
                    "reason": None,
                }
            )

    # Confirmations: the field decisions written when a receipt is confirmed,
    # by Relay (a matching bank SMS) or by a person; one entry per receipt.
    decided = (
        await session.execute(
            select(
                ReceiptReviewedFieldRow.candidate_id,
                ReceiptReviewedFieldRow.reviewer_id,
                ReceiptReviewedFieldRow.reason,
                ReceiptReviewedFieldRow.created_at,
            ).where(
                ReceiptReviewedFieldRow.created_at >= start,
                ReceiptReviewedFieldRow.created_at < end,
            )
        )
    ).all()
    latest: dict[tuple[UUID, UUID | None], datetime] = {}
    for candidate_id, reviewer_id, reason, created_at in decided:
        automatic = bool(reason) and str(reason).startswith(_AUTO_REASONS)
        key = (candidate_id, None if automatic else reviewer_id)
        moment = _when(created_at)
        if moment is not None and (key not in latest or moment > latest[key]):
            latest[key] = moment
    await _load(session, candidates, {candidate_id for candidate_id, _ in latest})
    for (candidate_id, reviewer_id), moment in latest.items():
        receipt = candidates.get(candidate_id)
        if receipt is None:
            continue
        if (
            reviewer_id is not None
            and receipt.review_status != ReviewStatus.AUTO_ACCEPTED.value
        ):
            continue  # a person is still working on it
        auto = (receipt.resolution_result or {}).get("auto_confirmed")
        events.append(
            {
                "candidate_id": candidate_id,
                "at": moment,
                "action": "confirmed",
                "actor_id": reviewer_id,
                "rule": (
                    None
                    if reviewer_id is not None
                    else _rule(auto if isinstance(auto, dict) else {})
                ),
                "reason": None,
            }
        )

    await _load(session, candidates, {e["candidate_id"] for e in events})
    # A confirmation also leaves an ACCEPT in the review audit, written by the
    # same account; the field decisions say who really decided it.
    by_fields: dict[UUID, list[datetime]] = {}
    for event in events:
        if event["action"] == "confirmed" and event.get("source") != "audit":
            by_fields.setdefault(event["candidate_id"], []).append(event["at"])
    result: list[dict[str, Any]] = []
    seen: set[tuple[UUID, str, str]] = set()
    for event in events:
        if event["action"] == "confirmed" and event.get("source") == "audit":
            moments = by_fields.get(event["candidate_id"], [])
            if event["at"] is not None and any(
                abs((event["at"] - moment).total_seconds()) < 120 for moment in moments
            ):
                continue
        at = event["at"]
        if at is None or not start <= at < end:
            continue
        if action and event["action"] != action:
            continue
        actor_id = event["actor_id"]
        if actor == AUTOMATIC and actor_id is not None:
            continue
        if actor and actor != AUTOMATIC and users.get(actor_id) != actor:
            continue
        mark = (event["candidate_id"], event["action"], at.isoformat()[:19])
        if mark in seen:
            continue
        seen.add(mark)
        found = candidates.get(event["candidate_id"])
        amount, currency = _amount(found) if found is not None else (None, None)
        result.append(
            {
                "candidate_id": str(event["candidate_id"]),
                "at": at.isoformat(),
                "action": event["action"],
                "actor": users.get(actor_id) if actor_id is not None else None,
                "automatic": actor_id is None,
                "rule": event["rule"],
                "reason": event["reason"],
                "amount": amount,
                "currency": currency,
                "sender": _sender(found) if found is not None else None,
            }
        )
    result.sort(key=lambda item: item["at"], reverse=True)
    return result


HEADERS = ("Time", "Action", "By", "Rule / reason", "Amount", "Sender", "Receipt ID")
LABELS = {
    "confirmed": "Confirmed",
    "rejected": "Rejected",
    "rejected_copy": "Rejected as a copy",
    "sent_back": "Sent back to Review",
    "not_a_copy": "Marked not a copy",
    "sms_same_amount": "Bank SMS with the same amount",
    "sms_no_reference": "Bank SMS at the same time (no reference)",
    "copy_of_confirmed": "Copy of a confirmed receipt",
    "reader": "Relay reader",
}


def workbook(items: Iterable[dict[str, Any]], zone: Any) -> bytes:
    """The trail as an Excel sheet (local times)."""
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Audit trail"
    sheet.append(HEADERS)
    for item in items:
        at = datetime.fromisoformat(item["at"]).astimezone(zone)
        why = LABELS.get(item["rule"] or "", item["rule"] or "")
        if item["reason"]:
            why = f"{why} · {item['reason']}" if why else str(item["reason"])
        sheet.append(
            (
                at.strftime("%Y-%m-%d %H:%M"),
                LABELS.get(item["action"], item["action"]),
                "Automatic" if item["automatic"] else (item["actor"] or ""),
                why,
                (
                    (f"{item['amount']} {item['currency'] or ''}".strip())
                    if item["amount"]
                    else ""
                ),
                item["sender"] or "",
                item["candidate_id"],
            )
        )
    for column, width in zip("ABCDEFG", (18, 22, 28, 44, 16, 28, 38), strict=True):
        sheet.column_dimensions[column].width = width
    output = BytesIO()
    book.save(output)
    return output.getvalue()
