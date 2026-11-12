from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from relay.application.sms_verification import SmsVerificationService
from relay.application.sms_verification_ports import (
    BankSms,
    SmsEvent,
    SmsVerificationUnitOfWork,
    StoredVerification,
)
from relay.domain.review_trust import reviewed_value
from relay.domain.sms_verification import (
    Decision,
    MatchMethod,
    MatchWindow,
    ReceiptFacts,
    SmsDirection,
    SmsFacts,
    VerificationStatus,
)
from relay.infrastructure.config import Settings
from relay.infrastructure.datetime_utils import aware
from relay.infrastructure.event_models import RawEventRow
from relay.infrastructure.models import DeviceRow
from relay.infrastructure.processing_models import ReceiptCandidateRow
from relay.infrastructure.sms_verification_models import (
    SmsSourceLinkRow,
    SmsVerificationRow,
)


def build_sms_verification_service(
    uow: SmsVerificationUnitOfWork, settings: Settings
) -> SmsVerificationService:
    return SmsVerificationService(
        uow,
        zone=ZoneInfo(settings.receipt_timezone),
        window=MatchWindow(
            before=timedelta(minutes=settings.sms_match_before_minutes),
            after=timedelta(minutes=settings.sms_match_after_minutes),
        ),
        lookback_days=settings.raw_event_retention_days,
    )


def _receipt(
    row: ReceiptCandidateRow, sources: frozenset[UUID], direction: SmsDirection
) -> ReceiptFacts | None:
    values = {
        name: reviewed_value(row.resolution_result, name)
        for name in ("amount", "currency", "date", "time")
    }
    if any(value is None for value in values.values()):
        return None
    try:
        amount = Decimal(str(values["amount"])).quantize(Decimal("0.01"))
        local = datetime.fromisoformat(f"{values['date']}T{values['time']}")
    except (InvalidOperation, ValueError):
        return None
    if local.tzinfo is not None or amount <= 0:
        return None
    return ReceiptFacts(
        row.id,
        amount,
        str(values["currency"]).upper(),
        local,
        aware(row.created_at),
        sources,
        direction,
    )


def _stored(row: SmsVerificationRow) -> StoredVerification:
    sms = None
    if (
        row.sms_event_id is not None
        and row.sms_device_id is not None
        and row.sms_occurred_at is not None
        and row.sms_amount is not None
        and row.sms_currency is not None
    ):
        sms = SmsFacts(
            row.sms_event_id,
            row.sms_device_id,
            row.sms_amount,
            row.sms_currency,
            aware(row.sms_occurred_at),
            SmsDirection(row.sms_direction or SmsDirection.IN.value),
            row.sms_stated_at,
        )
    return StoredVerification(
        row.candidate_id,
        VerificationStatus(row.status),
        MatchMethod(row.method),
        sms,
        row.option_count,
        row.decided_by,
        aware(row.updated_at),
    )


class SqlSmsVerificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def existing_devices(self, device_ids: set[UUID]) -> set[UUID]:
        if not device_ids:
            return set()
        rows = await self.session.scalars(
            select(DeviceRow.id).where(DeviceRow.id.in_(device_ids))
        )
        return set(rows)

    async def sms_sources(self, device_id: UUID) -> list[UUID]:
        rows = await self.session.scalars(
            select(SmsSourceLinkRow.sms_device_id)
            .where(SmsSourceLinkRow.receipt_device_id == device_id)
            .order_by(SmsSourceLinkRow.created_at, SmsSourceLinkRow.sms_device_id)
        )
        return list(rows)

    async def sms_direction(self, device_id: UUID) -> SmsDirection:
        value = await self.session.scalar(
            select(SmsSourceLinkRow.direction)
            .where(SmsSourceLinkRow.receipt_device_id == device_id)
            .limit(1)
        )
        return SmsDirection(value) if value else SmsDirection.IN

    async def set_sms_sources(
        self,
        device_id: UUID,
        sources: list[UUID],
        direction: SmsDirection,
        actor_id: UUID,
        now: datetime,
    ) -> None:
        await self.session.execute(
            delete(SmsSourceLinkRow).where(
                SmsSourceLinkRow.receipt_device_id == device_id
            )
        )
        for source in sources:
            self.session.add(
                SmsSourceLinkRow(
                    receipt_device_id=device_id,
                    sms_device_id=source,
                    direction=direction.value,
                    created_by=actor_id,
                    created_at=now,
                )
            )
        await self.session.flush()

    async def candidate_device(self, candidate_id: UUID) -> UUID | None:
        device_id: UUID | None = await self.session.scalar(
            select(ReceiptCandidateRow.device_id).where(
                ReceiptCandidateRow.id == candidate_id
            )
        )
        return device_id

    async def eligible_receipts(
        self, *, since: datetime | None = None, candidate_id: UUID | None = None
    ) -> list[ReceiptFacts]:
        links: dict[UUID, set[UUID]] = {}
        directions: dict[UUID, SmsDirection] = {}
        for receipt_device, sms_device, direction in (
            await self.session.execute(
                select(
                    SmsSourceLinkRow.receipt_device_id,
                    SmsSourceLinkRow.sms_device_id,
                    SmsSourceLinkRow.direction,
                )
            )
        ).tuples():
            links.setdefault(receipt_device, set()).add(sms_device)
            directions[receipt_device] = SmsDirection(direction)
        if not links:
            return []
        statement = select(ReceiptCandidateRow).where(
            ReceiptCandidateRow.device_id.in_(links),
            ReceiptCandidateRow.duplicate_of_candidate_id.is_(None),
            ReceiptCandidateRow.review_status != "REJECTED",
            ReceiptCandidateRow.resolution_result.is_not(None),
        )
        if since is not None:
            statement = statement.where(ReceiptCandidateRow.created_at >= since)
        if candidate_id is not None:
            statement = statement.where(ReceiptCandidateRow.id == candidate_id)
        rows = await self.session.scalars(
            statement.order_by(ReceiptCandidateRow.created_at, ReceiptCandidateRow.id)
        )
        facts = []
        for row in rows:
            receipt = _receipt(
                row, frozenset(links[row.device_id]), directions[row.device_id]
            )
            if receipt is not None:
                facts.append(receipt)
        return facts

    async def sms_events(
        self, device_ids: set[UUID], since: datetime, until: datetime
    ) -> list[SmsEvent]:
        if not device_ids:
            return []
        rows = await self.session.scalars(
            select(RawEventRow)
            .where(
                RawEventRow.device_id.in_(device_ids),
                RawEventRow.collector == "sms",
                RawEventRow.occurred_at >= since,
                RawEventRow.occurred_at <= until,
            )
            .order_by(RawEventRow.occurred_at, RawEventRow.id)
            .limit(5000)
        )
        events = []
        for row in rows:
            body = row.payload.get("body") if isinstance(row.payload, dict) else None
            if isinstance(body, str):
                events.append(
                    SmsEvent(row.id, row.device_id, aware(row.occurred_at), body)
                )
        return events

    async def bank_sms(
        self, since: datetime, until: datetime, device_id: UUID | None
    ) -> list[BankSms]:
        statement = (
            select(RawEventRow, DeviceRow.name, SmsVerificationRow.candidate_id)
            .join(DeviceRow, DeviceRow.id == RawEventRow.device_id)
            .outerjoin(
                SmsVerificationRow,
                (SmsVerificationRow.sms_event_id == RawEventRow.id)
                & (SmsVerificationRow.status == VerificationStatus.VERIFIED.value),
            )
            .where(
                RawEventRow.collector == "sms",
                RawEventRow.occurred_at >= since,
                RawEventRow.occurred_at < until,
            )
            .order_by(RawEventRow.occurred_at.desc(), RawEventRow.id)
            .limit(5000)
        )
        if device_id is not None:
            statement = statement.where(RawEventRow.device_id == device_id)
        found = []
        for row, name, candidate_id in await self.session.execute(statement):
            payload = row.payload if isinstance(row.payload, dict) else {}
            body, sender = payload.get("body"), payload.get("sender")
            found.append(
                BankSms(
                    row.id,
                    row.device_id,
                    name,
                    sender if isinstance(sender, str) else None,
                    body if isinstance(body, str) else "",
                    aware(row.occurred_at),
                    candidate_id,
                )
            )
        return found

    async def get(self, candidate_id: UUID) -> StoredVerification | None:
        row = await self.session.get(SmsVerificationRow, candidate_id)
        return _stored(row) if row else None

    async def manual_rows(self) -> dict[UUID, StoredVerification]:
        rows = await self.session.scalars(
            select(SmsVerificationRow).where(
                SmsVerificationRow.method == MatchMethod.MANUAL.value
            )
        )
        return {row.candidate_id: _stored(row) for row in rows}

    async def delete_auto_since(self, since: datetime) -> None:
        recent = select(ReceiptCandidateRow.id).where(
            ReceiptCandidateRow.created_at >= since
        )
        await self.session.execute(
            delete(SmsVerificationRow).where(
                SmsVerificationRow.method == MatchMethod.AUTO.value,
                SmsVerificationRow.candidate_id.in_(recent),
            )
        )
        await self.session.flush()

    async def held_events(self, *, except_candidate: UUID | None = None) -> set[UUID]:
        statement = select(SmsVerificationRow.sms_event_id).where(
            SmsVerificationRow.sms_event_id.is_not(None)
        )
        if except_candidate is not None:
            statement = statement.where(
                SmsVerificationRow.candidate_id != except_candidate
            )
        return {value for value in await self.session.scalars(statement) if value}

    def _row(
        self,
        candidate_id: UUID,
        status: VerificationStatus,
        method: MatchMethod,
        sms: SmsFacts | None,
        option_count: int,
        decided_by: UUID | None,
        now: datetime,
    ) -> SmsVerificationRow:
        return SmsVerificationRow(
            candidate_id=candidate_id,
            status=status.value,
            method=method.value,
            sms_event_id=sms.event_id if sms else None,
            sms_device_id=sms.device_id if sms else None,
            sms_occurred_at=sms.occurred_at if sms else None,
            sms_amount=sms.amount if sms else None,
            sms_currency=sms.currency if sms else None,
            sms_direction=sms.direction.value if sms else None,
            sms_stated_at=sms.stated_local if sms else None,
            option_count=option_count,
            decided_by=decided_by,
            updated_at=now,
        )

    async def add_decisions(self, decisions: list[Decision], now: datetime) -> None:
        for decision in decisions:
            self.session.add(
                self._row(
                    decision.candidate_id,
                    decision.status,
                    MatchMethod.AUTO,
                    decision.sms,
                    decision.option_count,
                    None,
                    now,
                )
            )
        await self.session.flush()

    async def save(self, verification: StoredVerification) -> None:
        await self.delete(verification.candidate_id)
        self.session.add(
            self._row(
                verification.candidate_id,
                verification.status,
                verification.method,
                verification.sms,
                verification.option_count,
                verification.decided_by,
                verification.updated_at,
            )
        )
        await self.session.flush()

    async def delete(self, candidate_id: UUID) -> None:
        await self.session.execute(
            delete(SmsVerificationRow).where(
                SmsVerificationRow.candidate_id == candidate_id
            )
        )
        await self.session.flush()
