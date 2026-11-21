"""SQLAlchemy read models and audited operator actions."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import Text, and_, case, delete, func, or_, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.orm import aliased

from relay.domain.agent import JsonValue
from relay.domain.devices import DeviceStatus
from relay.domain.operations import DateWindow, DuplicateGroupQuery, TransactionQuery
from relay.domain.processing import JobStatus, ProcessingFailure, ProcessingJob
from relay.domain.review_trust import initial_resolution, reviewed_value
from relay.infrastructure.agent_models import ConfigurationRow
from relay.infrastructure.binary_models import BinaryObjectRow
from relay.infrastructure.datetime_utils import aware
from relay.infrastructure.event_models import RawEventRow
from relay.infrastructure.models import DeviceRow
from relay.infrastructure.operation_models import OperatorAuditRow
from relay.infrastructure.phone_health import summary as health_summary
from relay.infrastructure.processing_models import (
    OcrResultRow,
    ProcessingJobRow,
    ReceiptCandidateRow,
    ReceiptReviewAuditRow,
)
from relay.infrastructure.processing_repository import to_job
from relay.infrastructure.sms_verification_models import SmsVerificationRow
from relay.infrastructure.text_recheck import VERSION as TEXT_READING

RETRYABLE_FAILURES = {
    ProcessingFailure.OCR_TIMEOUT.value,
    ProcessingFailure.OCR_FAILED.value,
    ProcessingFailure.INTERNAL_ERROR.value,
}


def _iso(value: datetime | None) -> str | None:
    return aware(value).isoformat() if value is not None else None


def _money(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _field_value(resolution: Any, name: str) -> str | None:
    """The current value of a receipt field: reviewed, or the reader's suggestion."""
    fields = resolution.get("fields") if isinstance(resolution, dict) else None
    item = fields.get(name) if isinstance(fields, dict) else None
    value = item.get("value") if isinstance(item, dict) else None
    return value if isinstance(value, str) and value else None


def _safe_configuration(value: dict[str, Any]) -> dict[str, JsonValue]:
    collectors = value.get("collectors")
    if not isinstance(collectors, dict):
        collectors = {}
    sms = collectors.get("sms")
    images = collectors.get("images")
    sms_value = sms if isinstance(sms, dict) else {}
    image_value = images if isinstance(images, dict) else {}
    sources = image_value.get("sources")
    source_ids: list[JsonValue] = (
        [
            str(item.get("id"))
            for item in sources
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        if isinstance(sources, list)
        else []
    )
    sms_rules = sms_value.get("rules")
    image_rules = image_value.get("rules")
    return {
        "sms_enabled": sms_value.get("enabled") is True,
        "sms_rule_count": len(sms_rules) if isinstance(sms_rules, list) else 0,
        "images_enabled": image_value.get("enabled") is True,
        "image_rule_count": len(image_rules) if isinstance(image_rules, list) else 0,
        "image_source_ids": source_ids,
    }


def _similar_to(value: ReceiptCandidateRow) -> str | None:
    """The receipt this one may repeat, to open side by side."""
    if value.duplicate_of_candidate_id is not None:
        return str(value.duplicate_of_candidate_id)
    resolution = value.resolution_result
    resolution = resolution if isinstance(resolution, dict) else {}
    for key in ("old_receipt", "possible_repeat"):
        tag = resolution.get(key)
        other = tag.get("with") if isinstance(tag, dict) else None
        if other:
            return str(other)
    if value.hidden_reason == "same_receipt" and resolution.get("copy_of"):
        return str(resolution["copy_of"])
    return None


def _with_unchecked(counts: dict[str, int], unchecked: int) -> dict[str, int]:
    if unchecked:
        counts["WAITING"] = counts.get("WAITING", 0) + unchecked
    return counts


def _status_reason(value: ReceiptCandidateRow, sms: Any) -> str | None:
    """Why a receipt waits for review or was rejected, as a short code the
    portal translates. None for accepted receipts."""
    resolution = value.resolution_result
    resolution = resolution if isinstance(resolution, dict) else {}
    machine = value.machine_result if isinstance(value.machine_result, dict) else {}
    read = machine.get(TEXT_READING)
    read = read if isinstance(read, dict) else {}
    if value.hidden_reason == "same_receipt":
        return "copy"
    if value.hidden_reason is not None:
        # not_a_receipt, shop_invoice, no_transfer: hidden by a reader.
        return "hidden_" + value.hidden_reason
    if value.review_status == "REJECTED":
        if resolution.get("rejected_reason") == "payment_request":
            return "payment_request"
        if resolution.get("rejected_reason") == "not_completed":
            return "transfer_failed"
        if isinstance(value.reviewed_result, dict):
            return "rejected_by_reviewer"
        if read.get("failed"):
            return "transfer_failed"
        return "not_recognized"
    if value.review_status != "NEEDS_REVIEW":
        return None
    if isinstance(resolution.get("old_receipt"), dict):
        return "old_receipt"
    repeat = resolution.get("possible_repeat")
    repeat_kind = repeat.get("kind") if isinstance(repeat, dict) else None
    if value.duplicate_of_candidate_id is not None or repeat_kind == "reference":
        return "duplicate"
    mismatch = resolution.get("recipient_mismatch")
    if isinstance(mismatch, dict):
        return (
            "recipient_unclear"
            if mismatch.get("kind") == "unclear"
            else "other_recipient"
        )
    if repeat is not None:
        return "repeated_amount"
    if not read:
        return "reading"
    if sms != "VERIFIED":
        return "no_sms"
    return "missing_fields"


def _read_parties(machine: Any) -> dict[str, JsonValue]:
    """Sender and recipient as read (unconfirmed): classic PaddleOCR first,
    each part it could not read taken from Tesseract."""
    from relay.infrastructure.ppocr_reader import latest
    from relay.infrastructure.text_recheck import parties, sender

    read = machine.get(TEXT_READING) if isinstance(machine, dict) else None
    read = read if isinstance(read, dict) else {}
    listed = read.get("parties")
    found: dict[str, Any] = dict(listed) if isinstance(listed, dict) else {}
    handle = read.get("sender")
    paddle = latest(machine) if isinstance(machine, dict) else None
    if paddle is not None:
        text = str(paddle.get("text") or "")
        found |= parties(text)
        handle = sender(text) or handle
    # The InstaPay address names the sender best; a printed name equal to
    # the recipient's was misread from the "to" part.
    name = found.get("sender_name")
    if name and name == found.get("recipient_name"):
        name = None
    who = handle or name
    recipient = " · ".join(
        str(found[key])
        for key in ("recipient_name", "recipient_phone")
        if found.get(key)
    )
    return {
        "suggested_sender": str(who) if who else None,
        "suggested_recipient": recipient or None,
    }


class SqlOperationsRepository:
    def __init__(self, session: Any) -> None:
        self.db = session

    @staticmethod
    def _child_count() -> Any:
        child = aliased(ReceiptCandidateRow)
        return (
            select(func.count(child.id))
            .where(
                child.duplicate_of_candidate_id == ReceiptCandidateRow.id,
                child.resolution_result.is_not(None),
                ReceiptCandidateRow.resolution_result.is_not(None),
            )
            .correlate(ReceiptCandidateRow)
            .scalar_subquery()
        )

    def _transaction_select(self) -> Any:
        return (
            select(
                ReceiptCandidateRow,
                ProcessingJobRow.status.label("processing_status"),
                ProcessingJobRow.failure_code.label("processing_failure"),
                RawEventRow.received_at,
                RawEventRow.occurred_at.label("folder_at"),
                RawEventRow.collector,
                BinaryObjectRow.source_type,
                BinaryObjectRow.source_id,
                DeviceRow.name.label("device_name"),
                DeviceRow.external_device_id,
                OcrResultRow.provider,
                OcrResultRow.provider_version,
                OcrResultRow.processor_version,
                OcrResultRow.mean_confidence,
                self._child_count().label("child_count"),
                SmsVerificationRow.status.label("sms_verification"),
            )
            .join(ProcessingJobRow, ProcessingJobRow.id == ReceiptCandidateRow.job_id)
            .join(RawEventRow, RawEventRow.id == ReceiptCandidateRow.raw_event_id)
            .join(BinaryObjectRow, BinaryObjectRow.id == ReceiptCandidateRow.object_id)
            .join(DeviceRow, DeviceRow.id == ReceiptCandidateRow.device_id)
            .join(OcrResultRow, OcrResultRow.job_id == ReceiptCandidateRow.job_id)
            .outerjoin(
                SmsVerificationRow,
                SmsVerificationRow.candidate_id == ReceiptCandidateRow.id,
            )
        )

    @staticmethod
    def _receipt_range(
        window: DateWindow, copies: bool = False, set_aside: bool = False
    ) -> tuple[Any, ...]:
        # Images that turned out not to be receipts are never counted or listed,
        # except on the Rejected page (set_aside). The Copies list shows hidden
        # copies and old receipts shown again.
        visible: Any
        if set_aside:
            visible = or_(
                ReceiptCandidateRow.review_status == "REJECTED",
                ReceiptCandidateRow.hidden_reason.is_not(None),
            )
        elif copies:
            visible = or_(
                ReceiptCandidateRow.hidden_reason == "same_receipt",
                and_(
                    ReceiptCandidateRow.hidden_reason.is_(None),
                    ReceiptCandidateRow.resolution_result["old_receipt"]
                    .as_string()
                    .is_not(None),
                ),
            )
        else:
            visible = ReceiptCandidateRow.hidden_reason.is_(None)
        if window.folder_time:
            folder = select(RawEventRow.id).where(
                RawEventRow.occurred_at >= window.start,
                RawEventRow.occurred_at < window.end_exclusive,
            )
            return (visible, ReceiptCandidateRow.raw_event_id.in_(folder))
        return (
            visible,
            ReceiptCandidateRow.created_at >= window.start,
            ReceiptCandidateRow.created_at < window.end_exclusive,
        )

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _apply_transaction_filters(
        self, statement: Any, query: TransactionQuery
    ) -> Any:
        statement = statement.where(
            *self._receipt_range(
                query.window,
                query.duplicate_status == "COPY",
                query.review_status == "SET_ASIDE",
            )
        )
        if query.search:
            escaped = self._escape_like(query.search.casefold())
            pattern = f"%{escaped}%"
            normalized = "".join(c for c in query.search.upper() if c.isalnum())
            conditions = [
                func.lower(ReceiptCandidateRow.reference_original).like(
                    pattern, escape="\\"
                ),
                func.lower(ReceiptCandidateRow.sender_name).like(pattern, escape="\\"),
                func.lower(ReceiptCandidateRow.recipient_name).like(
                    pattern, escape="\\"
                ),
            ]
            if normalized:
                conditions.append(
                    ReceiptCandidateRow.reference_normalized.like(
                        f"%{self._escape_like(normalized)}%", escape="\\"
                    )
                )
            statement = statement.where(or_(*conditions))
        if query.review_status and query.review_status != "SET_ASIDE":
            if query.review_status == "NEEDS_REVIEW":
                statement = statement.where(
                    or_(
                        ReceiptCandidateRow.review_status == "NEEDS_REVIEW",
                        and_(
                            ReceiptCandidateRow.review_status == "AUTO_ACCEPTED",
                            ReceiptCandidateRow.resolution_result.is_(None),
                            ReceiptCandidateRow.receipt_type
                            == "INSTAPAY_TRANSFER_RECEIPT",
                        ),
                    )
                )
            else:
                statement = statement.where(
                    ReceiptCandidateRow.review_status == query.review_status
                )
        if query.receipt_type:
            statement = statement.where(
                ReceiptCandidateRow.receipt_type == query.receipt_type
            )
        if query.currency:
            statement = statement.where(ReceiptCandidateRow.currency == query.currency)
        if query.device_id:
            statement = statement.where(
                ReceiptCandidateRow.device_id == query.device_id
            )
        if query.source_type:
            statement = statement.where(
                BinaryObjectRow.source_type == query.source_type
            )
        if query.processing_status:
            statement = statement.where(
                ProcessingJobRow.status == query.processing_status
            )
        child_count = self._child_count()
        if query.duplicate_status == "DUPLICATE":
            statement = statement.where(
                ReceiptCandidateRow.duplicate_of_candidate_id.is_not(None),
                ReceiptCandidateRow.resolution_result.is_not(None),
            )
        elif query.duplicate_status == "CANONICAL":
            statement = statement.where(
                ReceiptCandidateRow.duplicate_of_candidate_id.is_(None),
                child_count > 0,
            )
        elif query.duplicate_status == "GROUPED":
            statement = statement.where(
                or_(
                    ReceiptCandidateRow.duplicate_of_candidate_id.is_not(None),
                    child_count > 0,
                )
            )
            statement = statement.where(
                ReceiptCandidateRow.resolution_result.is_not(None)
            )
        elif query.duplicate_status == "UNIQUE":
            statement = statement.where(
                ReceiptCandidateRow.duplicate_of_candidate_id.is_(None),
                child_count == 0,
            )
        return statement

    @staticmethod
    def _transaction_row(row: Any) -> dict[str, JsonValue]:
        value: ReceiptCandidateRow = row[0]
        resolution = value.resolution_result
        amount = reviewed_value(resolution, "amount")
        currency = reviewed_value(resolution, "currency")
        reference = reviewed_value(resolution, "reference_number")
        date_value = reviewed_value(resolution, "date")
        time_value = reviewed_value(resolution, "time")
        child_count = int(row.child_count or 0)
        trusted_duplicate = reference is not None
        group_id = (
            value.duplicate_of_candidate_id
            if trusted_duplicate and value.duplicate_of_candidate_id is not None
            else (value.id if trusted_duplicate and child_count else None)
        )
        return {
            "id": str(value.id),
            "job_id": str(value.job_id),
            "object_id": str(value.object_id),
            "raw_event_id": str(value.raw_event_id),
            "device_id": str(value.device_id),
            "device_name": row.device_name,
            "external_device_id": row.external_device_id,
            "receipt_type": value.receipt_type,
            "review_status": value.review_status,
            "processing_status": row.processing_status,
            "processing_failure": row.processing_failure,
            "sms_verification": row.sms_verification,
            "amount": amount,
            "currency": currency,
            "transaction_occurred_at": (
                value.transaction_occurred_at.isoformat()
                if value.transaction_occurred_at and date_value and time_value
                else None
            ),
            "timezone_state": value.timezone_state,
            "reference_original": reference,
            "reference_normalized": reference,
            "sender_name": reviewed_value(resolution, "sender")
            or reviewed_value(resolution, "handle"),
            "recipient_name": reviewed_value(resolution, "recipient"),
            **_read_parties(value.machine_result),
            "suggested_amount": _field_value(resolution, "amount"),
            "status_reason": _status_reason(value, row.sms_verification),
            "similar_to": _similar_to(value),
            "recipient_identifier": reviewed_value(resolution, "recipient_identifier"),
            "transfer_status": reviewed_value(resolution, "transfer_status"),
            "authoritative_status": (
                resolution.get("state")
                if isinstance(resolution, dict)
                else "UNVERIFIED_HISTORICAL"
            ),
            "resolution_result": cast(
                dict[str, JsonValue],
                resolution
                or initial_resolution(
                    cast(dict[str, JsonValue], value.machine_result),
                    supported_receipt=value.receipt_type == "INSTAPAY_TRANSFER_RECEIPT",
                ),
            ),
            "duplicate_group_id": str(group_id) if group_id else None,
            "duplicate_of_candidate_id": (
                str(value.duplicate_of_candidate_id)
                if trusted_duplicate and value.duplicate_of_candidate_id
                else None
            ),
            "duplicate_reason": value.duplicate_reason,
            "is_canonical": child_count > 0 and value.duplicate_of_candidate_id is None,
            "duplicate_group_size": child_count + 1 if group_id else 1,
            "source": row.collector,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "received_at": _iso(row.received_at),
            "folder_at": _iso(row.folder_at),
            "created_at": _iso(value.created_at),
            "updated_at": _iso(value.updated_at),
            "ocr_provider": row.provider,
            "ocr_provider_version": row.provider_version,
            "processor_version": row.processor_version,
            "parser_version": value.parser_version,
            "ocr_mean_confidence": (
                float(row.mean_confidence) if row.mean_confidence is not None else None
            ),
            "image_url": f"/api/v1/objects/{value.object_id}/content",
        }

    @staticmethod
    def _final() -> tuple[Any, ...]:
        """Confirmed receipts, each payment once (duplicates excluded)."""
        return (
            ReceiptCandidateRow.review_status == "AUTO_ACCEPTED",
            ReceiptCandidateRow.resolution_result.is_not(None),
            ReceiptCandidateRow.duplicate_of_candidate_id.is_(None),
        )

    async def money_totals(self, window: DateWindow) -> list[JsonValue]:
        rows = await self.db.execute(
            select(
                ReceiptCandidateRow.currency,
                func.sum(ReceiptCandidateRow.amount),
                func.count(),
            )
            .where(
                *self._receipt_range(window),
                *self._final(),
                ReceiptCandidateRow.amount.is_not(None),
                ReceiptCandidateRow.currency.is_not(None),
            )
            .group_by(ReceiptCandidateRow.currency)
            .order_by(ReceiptCandidateRow.currency)
        )
        return [
            {
                "currency": currency,
                "amount": _money(amount),
                "transaction_count": int(count),
            }
            for currency, amount, count in rows
        ]

    async def _bank_sms_received(self, window: DateWindow) -> dict[str, JsonValue]:
        """Money the bank says arrived: incoming bank SMS in the range (when
        they reached the phone), summed per currency."""
        from relay.domain.sms_verification import SmsDirection, parse_bank_sms

        rows = await self.db.execute(
            select(RawEventRow.id, RawEventRow.payload).where(
                RawEventRow.collector == "sms",
                RawEventRow.occurred_at >= window.start,
                RawEventRow.occurred_at < window.end_exclusive,
            )
        )
        used = set(
            await self.db.scalars(
                select(SmsVerificationRow.sms_event_id).where(
                    SmsVerificationRow.sms_event_id.is_not(None)
                )
            )
        )
        totals: dict[str, tuple[int, Decimal]] = {}
        unused: dict[str, tuple[int, Decimal]] = {}
        for event_id, payload in rows:
            body = payload.get("body") if isinstance(payload, dict) else None
            parsed = parse_bank_sms(body) if isinstance(body, str) else None
            if parsed is None or parsed.direction != SmsDirection.IN:
                continue
            count, amount = totals.get(parsed.currency, (0, Decimal(0)))
            totals[parsed.currency] = (count + 1, amount + parsed.amount)
            if event_id not in used:
                # No receipt claims it: a receipt photo may be missing.
                count, amount = unused.get(parsed.currency, (0, Decimal(0)))
                unused[parsed.currency] = (count + 1, amount + parsed.amount)
        return {
            "bank_sms_unused_count": sum(count for count, _ in unused.values()),
            "bank_sms_unused_totals": [
                {
                    "currency": currency,
                    "amount": f"{amount:.2f}",
                    "transaction_count": count,
                }
                for currency, (count, amount) in sorted(unused.items())
            ],
            "bank_sms_received_count": sum(count for count, _ in totals.values()),
            "bank_sms_received_totals": [
                {
                    "currency": currency,
                    "amount": f"{amount:.2f}",
                    "transaction_count": count,
                }
                for currency, (count, amount) in sorted(totals.items())
            ],
        }

    async def dashboard(self, window: DateWindow) -> dict[str, JsonValue]:
        receipt_range = self._receipt_range(window)
        final_count = await self.db.scalar(
            select(func.count())
            .select_from(ReceiptCandidateRow)
            .where(
                *receipt_range,
                ReceiptCandidateRow.review_status == "AUTO_ACCEPTED",
                ReceiptCandidateRow.resolution_result.is_not(None),
                # A duplicate is the same payment again: count it once.
                ReceiptCandidateRow.duplicate_of_candidate_id.is_(None),
            )
        )
        reviewed_count = await self.db.scalar(
            select(func.count())
            .select_from(ReceiptCandidateRow)
            .where(
                *receipt_range,
                # JSON columns store Python None as JSON null, not SQL NULL.
                func.coalesce(
                    sql_cast(ReceiptCandidateRow.reviewed_result, Text), "null"
                )
                != "null",
            )
        )
        rejected = await self.db.scalar(
            select(func.count())
            .select_from(ReceiptCandidateRow)
            .where(*receipt_range, ReceiptCandidateRow.review_status == "REJECTED")
        )
        needs_review = await self.db.scalar(
            select(func.count())
            .select_from(ReceiptCandidateRow)
            .where(
                *receipt_range,
                or_(
                    ReceiptCandidateRow.review_status == "NEEDS_REVIEW",
                    and_(
                        ReceiptCandidateRow.review_status == "AUTO_ACCEPTED",
                        ReceiptCandidateRow.resolution_result.is_(None),
                        ReceiptCandidateRow.receipt_type == "INSTAPAY_TRANSFER_RECEIPT",
                    ),
                ),
            )
        )
        totals = await self.db.execute(
            select(
                ReceiptCandidateRow.currency,
                func.sum(ReceiptCandidateRow.amount),
                func.count(),
            )
            .where(
                *receipt_range,
                ReceiptCandidateRow.review_status == "AUTO_ACCEPTED",
                ReceiptCandidateRow.resolution_result.is_not(None),
                ReceiptCandidateRow.duplicate_of_candidate_id.is_(None),
                ReceiptCandidateRow.amount.is_not(None),
                ReceiptCandidateRow.currency.is_not(None),
            )
            .group_by(ReceiptCandidateRow.currency)
            .order_by(ReceiptCandidateRow.currency)
        )
        child_count = self._child_count()
        group_count = await self.db.scalar(
            select(func.count())
            .select_from(ReceiptCandidateRow)
            .where(
                *receipt_range,
                ReceiptCandidateRow.duplicate_of_candidate_id.is_(None),
                child_count > 0,
            )
        )
        duplicate_count = await self.db.scalar(
            select(func.count())
            .select_from(ReceiptCandidateRow)
            .where(
                *receipt_range,
                ReceiptCandidateRow.duplicate_of_candidate_id.is_not(None),
                ReceiptCandidateRow.resolution_result.is_not(None),
            )
        )
        job_counts = await self.db.execute(
            select(ProcessingJobRow.status, func.count())
            .where(
                ProcessingJobRow.created_at >= window.start,
                ProcessingJobRow.created_at < window.end_exclusive,
            )
            .group_by(ProcessingJobRow.status)
        )
        # Same clock as every other number here: when the image reached the
        # phone's folder (or the SMS reached the phone), not when it was
        # uploaded. Photos that turned out not to be receipts are counted
        # apart so the image total matches the receipts listed by phone.
        hidden_image = (
            select(ReceiptCandidateRow.id)
            .where(
                ReceiptCandidateRow.raw_event_id == RawEventRow.id,
                ReceiptCandidateRow.hidden_reason.is_not(None),
            )
            .exists()
        )
        event_counts = await self.db.execute(
            select(
                RawEventRow.collector,
                func.count(),
                func.sum(case((hidden_image, 1), else_=0)),
            )
            .where(
                RawEventRow.occurred_at >= window.start,
                RawEventRow.occurred_at < window.end_exclusive,
            )
            .group_by(RawEventRow.collector)
        )
        final = and_(*self._final())
        # When each phone last sent a photo (any range), to spot a quiet phone.
        last_photo = (
            select(func.max(RawEventRow.received_at))
            .where(
                RawEventRow.device_id == DeviceRow.id,
                RawEventRow.collector == "images",
            )
            .correlate(DeviceRow)
            .scalar_subquery()
        )
        device_counts = await self.db.execute(
            select(
                DeviceRow.id,
                DeviceRow.name,
                DeviceRow.last_seen_at,
                last_photo,
                func.count(ReceiptCandidateRow.id),
                func.sum(case((final, 1), else_=0)),
                func.sum(case((final, ReceiptCandidateRow.amount), else_=0)),
                func.sum(case((SmsVerificationRow.status == "VERIFIED", 1), else_=0)),
            )
            .select_from(DeviceRow)
            # Every active receipts phone is listed, with zeros when it sent
            # nothing in this range, so a quiet phone never just disappears.
            .outerjoin(
                ReceiptCandidateRow,
                and_(ReceiptCandidateRow.device_id == DeviceRow.id, *receipt_range),
            )
            .outerjoin(
                SmsVerificationRow,
                SmsVerificationRow.candidate_id == ReceiptCandidateRow.id,
            )
            .where(
                or_(
                    ReceiptCandidateRow.id.is_not(None),
                    and_(
                        DeviceRow.status == DeviceStatus.ACTIVE,
                        select(RawEventRow.id)
                        .where(
                            RawEventRow.device_id == DeviceRow.id,
                            RawEventRow.collector == "images",
                        )
                        .exists(),
                    ),
                )
            )
            .group_by(DeviceRow.id, DeviceRow.name, DeviceRow.last_seen_at)
            .order_by(func.count(ReceiptCandidateRow.id).desc(), DeviceRow.name)
            .limit(20)
        )
        device_rows = device_counts.all()
        # Each phone's health report (why it went quiet), read separately:
        # a JSON column cannot be grouped on.
        healths = dict(
            (
                await self.db.execute(
                    select(DeviceRow.id, DeviceRow.health).where(
                        DeviceRow.id.in_([row[0] for row in device_rows])
                    )
                )
            ).all()
        )
        # Money still waiting for review, by the amount read so far (counted
        # once per payment: possible copies of another receipt are left out).
        waiting: dict[str, list[Decimal]] = {}
        review_rows = await self.db.execute(
            select(
                ReceiptCandidateRow.amount,
                ReceiptCandidateRow.currency,
                ReceiptCandidateRow.resolution_result,
            ).where(
                *receipt_range,
                ReceiptCandidateRow.review_status == "NEEDS_REVIEW",
                ReceiptCandidateRow.duplicate_of_candidate_id.is_(None),
            )
        )
        for amount, currency, resolution in review_rows:
            text = _field_value(resolution, "amount")
            try:
                value = Decimal(text) if text else amount
            except ArithmeticError:
                value = amount
            if value is None:
                continue
            code = (_field_value(resolution, "currency") or currency or "EGP").upper()
            waiting.setdefault(code[:3], []).append(Decimal(str(value)))
        auto_count = await self.db.scalar(
            select(func.count())
            .select_from(ReceiptCandidateRow)
            .where(
                *receipt_range,
                *self._final(),
                sql_cast(ReceiptCandidateRow.resolution_result, Text).like(
                    '%"auto_confirmed"%'
                ),
            )
        )
        sms_counts = await self.db.execute(
            select(SmsVerificationRow.status, func.count())
            .join(
                ReceiptCandidateRow,
                ReceiptCandidateRow.id == SmsVerificationRow.candidate_id,
            )
            .where(*receipt_range)
            .group_by(SmsVerificationRow.status)
        )
        # Receipts in Review that were never checked against the SMS yet
        # (no verification row) still wait for one: count them as waiting.
        unchecked = await self.db.scalar(
            select(func.count())
            .select_from(ReceiptCandidateRow)
            .where(
                *receipt_range,
                ReceiptCandidateRow.review_status == "NEEDS_REVIEW",
                ~select(SmsVerificationRow.candidate_id)
                .where(SmsVerificationRow.candidate_id == ReceiptCandidateRow.id)
                .exists(),
            )
        )
        jobs = {str(status): int(count) for status, count in job_counts}
        events: dict[str, int] = {}
        hidden_images = 0
        for source, count, hidden in event_counts:
            events[str(source)] = int(count) - int(hidden or 0)
            if source == "images":
                hidden_images = int(hidden or 0)
        return {
            "date_from": window.start.date().isoformat(),
            "date_to": (window.end_exclusive.date() - timedelta(days=1)).isoformat(),
            "final_transaction_count": int(final_count or 0),
            "reviewed_count": int(reviewed_count or 0),
            "needs_review_count": int(needs_review or 0),
            "rejected_count": int(rejected or 0),
            "duplicate_group_count": int(group_count or 0),
            "duplicate_candidate_count": int(duplicate_count or 0),
            "processing_failure_count": jobs.get("FAILED", 0),
            "pending_processing_count": jobs.get("PENDING", 0),
            "receipt_image_activity": events.get("images", 0),
            "sms_activity": events.get("sms", 0),
            **await self._bank_sms_received(window),
            "hidden_image_count": hidden_images,
            "totals_by_currency": [
                {
                    "currency": currency,
                    "amount": _money(amount),
                    "transaction_count": int(count),
                }
                for currency, amount, count in totals
            ],
            "processing_by_status": [
                {"status": status, "count": count}
                for status, count in sorted(jobs.items())
            ],
            "activity_by_source": [
                {"source": source, "count": count}
                for source, count in sorted(events.items())
            ],
            "transactions_by_device": [
                {
                    "device_id": str(device_id),
                    "device_name": name,
                    "count": int(count),
                    "confirmed_count": int(confirmed or 0),
                    "confirmed_amount": _money(
                        Decimal(str(amount or 0)).quantize(Decimal("0.01"))
                    ),
                    "sms_verified_count": int(sms_verified or 0),
                    "last_seen_at": _iso(seen),
                    "last_photo_at": _iso(photo),
                    "health": cast(JsonValue, health_summary(healths.get(device_id))),
                }
                for (
                    device_id,
                    name,
                    seen,
                    photo,
                    count,
                    confirmed,
                    amount,
                    sms_verified,
                ) in device_rows
            ],
            "auto_confirmed_count": int(auto_count or 0),
            "manual_confirmed_count": max(
                0, int(final_count or 0) - int(auto_count or 0)
            ),
            "sms_by_status": cast(
                dict[str, JsonValue],
                _with_unchecked(
                    {str(status): int(count) for status, count in sms_counts},
                    int(unchecked or 0),
                ),
            ),
            "review_totals_by_currency": [
                {
                    "currency": code,
                    "amount": _money(sum(values, Decimal(0)).quantize(Decimal("0.01"))),
                    "transaction_count": len(values),
                }
                for code, values in sorted(waiting.items())
            ],
        }

    async def transactions(
        self, query: TransactionQuery
    ) -> tuple[list[dict[str, JsonValue]], int]:
        statement = self._apply_transaction_filters(self._transaction_select(), query)
        count = await self.db.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        sort_columns = {
            "created_at": ReceiptCandidateRow.created_at,
            "folder_at": RawEventRow.occurred_at,
            "transaction_occurred_at": ReceiptCandidateRow.transaction_occurred_at,
            "amount": ReceiptCandidateRow.amount,
            "reference": ReceiptCandidateRow.reference_normalized,
            "review_status": ReceiptCandidateRow.review_status,
        }
        sort = sort_columns.get(query.sort_by, ReceiptCandidateRow.created_at)
        ordering = sort.desc() if query.sort_direction == "desc" else sort.asc()
        rows = await self.db.execute(
            statement.order_by(ordering, ReceiptCandidateRow.id)
            .limit(query.limit)
            .offset(query.offset)
        )
        return [self._transaction_row(row) for row in rows], int(count or 0)

    async def _transaction_by_id(
        self, candidate_id: UUID
    ) -> dict[str, JsonValue] | None:
        row = (
            await self.db.execute(
                self._transaction_select().where(ReceiptCandidateRow.id == candidate_id)
            )
        ).one_or_none()
        return self._transaction_row(row) if row is not None else None

    async def transaction(self, candidate_id: UUID) -> dict[str, JsonValue] | None:
        result = await self._transaction_by_id(candidate_id)
        if result is None:
            return None
        candidate = await self.db.get(ReceiptCandidateRow, candidate_id)
        if candidate is None:
            return None
        ocr = await self.db.scalar(
            select(OcrResultRow).where(OcrResultRow.job_id == candidate.job_id)
        )
        if ocr is None:
            return None
        reviews = await self.db.scalars(
            select(ReceiptReviewAuditRow)
            .where(ReceiptReviewAuditRow.candidate_id == candidate_id)
            .order_by(ReceiptReviewAuditRow.created_at)
        )
        resolutions = await self.db.scalars(
            select(OperatorAuditRow)
            .where(
                OperatorAuditRow.action == "DUPLICATE_RESOLUTION",
                OperatorAuditRow.candidate_id == candidate_id,
            )
            .order_by(OperatorAuditRow.created_at)
        )
        result.update(
            {
                "machine_result": cast(dict[str, JsonValue], candidate.machine_result),
                "reviewed_result": cast(
                    dict[str, JsonValue] | None, candidate.reviewed_result
                ),
                "ocr": {
                    "provider": ocr.provider,
                    "provider_version": ocr.provider_version,
                    "processor_version": ocr.processor_version,
                    "requested_languages": cast(
                        list[JsonValue], ocr.requested_languages
                    ),
                    "available_languages": cast(
                        list[JsonValue], ocr.available_languages
                    ),
                    "normalized_text": ocr.normalized_text,
                    "blocks": cast(list[JsonValue], ocr.blocks),
                    "mean_confidence": (
                        float(ocr.mean_confidence)
                        if ocr.mean_confidence is not None
                        else None
                    ),
                    "duration_ms": ocr.duration_ms,
                },
                "review_history": [
                    {
                        "reviewer_id": str(item.reviewer_id),
                        "action": item.action,
                        "before": cast(dict[str, JsonValue], item.before_result),
                        "after": cast(dict[str, JsonValue], item.after_result),
                        "created_at": _iso(item.created_at),
                    }
                    for item in reviews
                ],
                "duplicate_resolution_history": [
                    {
                        "actor_id": str(item.actor_id),
                        "context": cast(dict[str, JsonValue], item.context),
                        "created_at": _iso(item.created_at),
                    }
                    for item in resolutions
                ],
            }
        )
        return result

    async def duplicate_groups(
        self, query: DuplicateGroupQuery
    ) -> tuple[list[dict[str, JsonValue]], int]:
        child = aliased(ReceiptCandidateRow)
        statement = (
            select(
                ReceiptCandidateRow,
                DeviceRow.name.label("device_name"),
                DeviceRow.external_device_id,
                func.count(child.id).label("duplicate_count"),
                func.min(child.created_at).label("first_duplicate_at"),
                func.max(child.created_at).label("last_duplicate_at"),
            )
            .join(
                child,
                and_(
                    child.duplicate_of_candidate_id == ReceiptCandidateRow.id,
                    child.resolution_result.is_not(None),
                ),
            )
            .join(DeviceRow, DeviceRow.id == ReceiptCandidateRow.device_id)
            .where(
                ReceiptCandidateRow.created_at >= query.window.start,
                ReceiptCandidateRow.created_at < query.window.end_exclusive,
                ReceiptCandidateRow.resolution_result.is_not(None),
            )
            .group_by(ReceiptCandidateRow.id, DeviceRow.id)
        )
        if query.search:
            escaped = self._escape_like(query.search.casefold())
            statement = statement.where(
                or_(
                    func.lower(ReceiptCandidateRow.reference_original).like(
                        f"%{escaped}%", escape="\\"
                    ),
                    func.lower(ReceiptCandidateRow.reference_normalized).like(
                        f"%{escaped}%", escape="\\"
                    ),
                )
            )
        if query.device_id:
            statement = statement.where(
                ReceiptCandidateRow.device_id == query.device_id
            )
        if query.currency:
            statement = statement.where(ReceiptCandidateRow.currency == query.currency)
        if query.receipt_type:
            statement = statement.where(
                ReceiptCandidateRow.receipt_type == query.receipt_type
            )
        count = await self.db.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        sort_columns = {
            "created_at": ReceiptCandidateRow.created_at,
            "reference": ReceiptCandidateRow.reference_normalized,
            "size": func.count(child.id),
        }
        sort = sort_columns.get(query.sort_by, ReceiptCandidateRow.created_at)
        ordering = sort.desc() if query.sort_direction == "desc" else sort.asc()
        rows = await self.db.execute(
            statement.order_by(ordering, ReceiptCandidateRow.id)
            .limit(query.limit)
            .offset(query.offset)
        )
        return [
            {
                "group_id": str(row[0].id),
                "canonical_candidate_id": str(row[0].id),
                "reference_original": reviewed_value(
                    row[0].resolution_result, "reference_number"
                ),
                "reference_normalized": reviewed_value(
                    row[0].resolution_result, "reference_number"
                ),
                "amount": reviewed_value(row[0].resolution_result, "amount"),
                "currency": reviewed_value(row[0].resolution_result, "currency"),
                "receipt_type": row[0].receipt_type,
                "device_id": str(row[0].device_id),
                "device_name": row.device_name,
                "external_device_id": row.external_device_id,
                "group_size": int(row.duplicate_count) + 1,
                "duplicate_reason": "REFERENCE_NUMBER",
                "created_at": _iso(row[0].created_at),
                "first_duplicate_at": _iso(row.first_duplicate_at),
                "last_duplicate_at": _iso(row.last_duplicate_at),
            }
            for row in rows
        ], int(count or 0)

    async def duplicate_group(
        self, group_id: UUID, *, limit: int, offset: int
    ) -> dict[str, JsonValue] | None:
        canonical = await self.db.scalar(
            select(ReceiptCandidateRow).where(
                ReceiptCandidateRow.id == group_id,
                ReceiptCandidateRow.duplicate_of_candidate_id.is_(None),
                ReceiptCandidateRow.resolution_result.is_not(None),
                self._child_count() > 0,
            )
        )
        if canonical is None:
            return None
        member_filter = or_(
            ReceiptCandidateRow.id == group_id,
            and_(
                ReceiptCandidateRow.duplicate_of_candidate_id == group_id,
                ReceiptCandidateRow.resolution_result.is_not(None),
            ),
        )
        total = await self.db.scalar(
            select(func.count(ReceiptCandidateRow.id)).where(member_filter)
        )
        ids = list(
            await self.db.scalars(
                select(ReceiptCandidateRow.id)
                .where(member_filter)
                .order_by(ReceiptCandidateRow.created_at, ReceiptCandidateRow.id)
                .limit(limit)
                .offset(offset)
            )
        )
        members = []
        for candidate_id in ids:
            item = await self.transaction(candidate_id)
            if item is not None:
                members.append(item)
        return {
            "group_id": str(group_id),
            "canonical_candidate_id": str(group_id),
            "reference_original": reviewed_value(
                canonical.resolution_result, "reference_number"
            ),
            "reference_normalized": reviewed_value(
                canonical.resolution_result, "reference_number"
            ),
            "duplicate_reason": "REFERENCE_NUMBER",
            "group_size": int(total or 0),
            "limit": limit,
            "offset": offset,
            "members": cast(list[JsonValue], members),
        }

    async def resolve_duplicate(
        self,
        group_id: UUID,
        candidate_id: UUID,
        actor_id: UUID,
        decision: str,
        note: str | None,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> dict[str, JsonValue] | None:
        candidate = await self.db.scalar(
            select(ReceiptCandidateRow)
            .where(
                ReceiptCandidateRow.id == candidate_id,
                or_(
                    ReceiptCandidateRow.id == group_id,
                    ReceiptCandidateRow.duplicate_of_candidate_id == group_id,
                ),
            )
            .with_for_update()
        )
        canonical = await self.db.scalar(
            select(ReceiptCandidateRow.id).where(
                ReceiptCandidateRow.id == group_id,
                ReceiptCandidateRow.duplicate_of_candidate_id.is_(None),
                self._child_count() > 0,
            )
        )
        if candidate is None or canonical is None:
            return None
        previous = await self.db.scalar(
            select(OperatorAuditRow.context)
            .where(
                OperatorAuditRow.action == "DUPLICATE_RESOLUTION",
                OperatorAuditRow.candidate_id == candidate_id,
            )
            .order_by(OperatorAuditRow.created_at.desc())
            .limit(1)
        )
        context: dict[str, JsonValue] = {
            "group_id": str(group_id),
            "canonical_candidate_id": str(group_id),
            "decision": decision,
            "previous_decision": (
                previous.get("decision") if isinstance(previous, dict) else None
            ),
            "note": note,
            "machine_duplicate_reason": candidate.duplicate_reason
            or "REFERENCE_NUMBER",
            "deterministic_link_preserved": True,
        }
        self.db.add(
            OperatorAuditRow(
                id=uuid4(),
                actor_id=actor_id,
                action="DUPLICATE_RESOLUTION",
                candidate_id=candidate_id,
                job_id=None,
                context=context,
                created_at=now,
                expires_at=expires_at,
            )
        )
        await self.db.flush()
        return {
            "candidate_id": str(candidate_id),
            "group_id": str(group_id),
            "decision": decision,
            "machine_duplicate_reason": context["machine_duplicate_reason"],
            "deterministic_link_preserved": True,
            "actor_id": str(actor_id),
            "created_at": now.isoformat(),
        }

    async def retry_processing(
        self,
        job_id: UUID,
        actor_id: UUID,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> ProcessingJob | None:
        row = await self.db.scalar(
            select(ProcessingJobRow)
            .where(ProcessingJobRow.id == job_id)
            .with_for_update()
        )
        if (
            row is None
            or row.status != JobStatus.FAILED.value
            or row.failure_code not in RETRYABLE_FAILURES
            or row.attempt_count >= 5
        ):
            return None
        previous_failure = row.failure_code
        previous_max = row.max_attempts
        row.max_attempts = max(row.max_attempts, row.attempt_count + 1)
        row.status = JobStatus.PENDING.value
        row.next_attempt_at = now
        row.completed_at = None
        row.failure_code = None
        row.updated_at = now
        self.db.add(
            OperatorAuditRow(
                id=uuid4(),
                actor_id=actor_id,
                action="PROCESSING_RETRY",
                candidate_id=None,
                job_id=job_id,
                context={
                    "previous_status": JobStatus.FAILED.value,
                    "previous_failure": previous_failure,
                    "attempt_count": row.attempt_count,
                    "previous_max_attempts": previous_max,
                    "new_max_attempts": row.max_attempts,
                },
                created_at=now,
                expires_at=expires_at,
            )
        )
        await self.db.flush()
        return to_job(row)

    async def devices(
        self, *, limit: int, offset: int
    ) -> tuple[list[dict[str, JsonValue]], int]:
        latest = (
            select(
                ConfigurationRow.device_id,
                func.max(ConfigurationRow.version).label("version"),
            )
            .group_by(ConfigurationRow.device_id)
            .subquery()
        )
        receipt_count = (
            select(func.count(ReceiptCandidateRow.id))
            .where(ReceiptCandidateRow.device_id == DeviceRow.id)
            .correlate(DeviceRow)
            .scalar_subquery()
        )
        event_count = (
            select(func.count(RawEventRow.id))
            .where(RawEventRow.device_id == DeviceRow.id)
            .correlate(DeviceRow)
            .scalar_subquery()
        )
        duplicate_count = (
            select(func.count(ReceiptCandidateRow.id))
            .where(
                ReceiptCandidateRow.device_id == DeviceRow.id,
                ReceiptCandidateRow.duplicate_of_candidate_id.is_not(None),
            )
            .correlate(DeviceRow)
            .scalar_subquery()
        )
        review_count = (
            select(func.count(ReceiptCandidateRow.id))
            .where(
                ReceiptCandidateRow.device_id == DeviceRow.id,
                ReceiptCandidateRow.review_status == "NEEDS_REVIEW",
            )
            .correlate(DeviceRow)
            .scalar_subquery()
        )
        statement = (
            select(
                DeviceRow,
                ConfigurationRow.version,
                ConfigurationRow.schema_version,
                ConfigurationRow.settings,
                receipt_count.label("receipt_count"),
                event_count.label("event_count"),
                duplicate_count.label("duplicate_count"),
                review_count.label("review_count"),
            )
            .join(latest, latest.c.device_id == DeviceRow.id)
            .join(
                ConfigurationRow,
                (ConfigurationRow.device_id == latest.c.device_id)
                & (ConfigurationRow.version == latest.c.version),
            )
            .where(DeviceRow.deleted_at.is_(None))
            .order_by(DeviceRow.created_at, DeviceRow.id)
        )
        total = await self.db.scalar(
            select(func.count())
            .select_from(DeviceRow)
            .where(DeviceRow.deleted_at.is_(None))
        )
        rows = await self.db.execute(statement.limit(limit).offset(offset))
        items: list[dict[str, JsonValue]] = []
        for row in rows:
            device: DeviceRow = row[0]
            items.append(
                {
                    "id": str(device.id),
                    "external_device_id": device.external_device_id,
                    "name": device.name,
                    "platform": device.platform,
                    "app_version": device.app_version,
                    "status": str(device.status),
                    "last_seen_at": _iso(device.last_seen_at),
                    "health": cast(JsonValue, health_summary(device.health)),
                    "configuration_version": int(row.version),
                    "configuration_schema_version": int(row.schema_version),
                    "configuration": _safe_configuration(row.settings),
                    "transaction_count": int(row.receipt_count),
                    "event_count": int(row.event_count),
                    "duplicate_count": int(row.duplicate_count),
                    "needs_review_count": int(row.review_count),
                }
            )
        return items, int(total or 0)

    async def add_export_audit(
        self,
        actor_id: UUID,
        context: dict[str, JsonValue],
        *,
        now: datetime,
        expires_at: datetime,
    ) -> None:
        self.db.add(
            OperatorAuditRow(
                id=uuid4(),
                actor_id=actor_id,
                action="EXPORT",
                candidate_id=None,
                job_id=None,
                context=context,
                created_at=now,
                expires_at=expires_at,
            )
        )
        await self.db.flush()

    async def purge_expired_audit(self, before: datetime) -> int:
        result = await self.db.execute(
            delete(OperatorAuditRow).where(OperatorAuditRow.expires_at <= before)
        )
        return int(result.rowcount or 0)
