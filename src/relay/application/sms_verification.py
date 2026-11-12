"""Verify receipts from one phone against bank SMS collected on another."""

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal, InvalidOperation
from uuid import UUID

from relay.application.errors import Conflict, NotFound
from relay.application.sms_verification_ports import (
    SmsEvent,
    SmsVerificationUnitOfWork,
    StoredVerification,
)
from relay.domain.agent import JsonValue
from relay.domain.sms_verification import (
    MAX_SMS_SOURCES,
    WAITING_HOURS,
    MatchMethod,
    MatchWindow,
    ReceiptFacts,
    SmsDirection,
    SmsFacts,
    VerificationStatus,
    compatible,
    decide,
    parse_bank_sms,
)

logger = logging.getLogger(__name__)
SEARCH_HOURS = WAITING_HOURS
# A receipt screenshot usually lands in the folder shortly after the transfer.
# Within FOLDER_NEAR the SMS time is trusted as the receipt time; up to
# FOLDER_BEFORE the SMS still identifies the payment, but the operator must
# enter the receipt's date and time and they must agree with the SMS.
FOLDER_NEAR = timedelta(hours=2)
FOLDER_BEFORE = timedelta(hours=12)
FOLDER_AFTER = timedelta(minutes=10)


def _facts(events: list[SmsEvent]) -> list[SmsFacts]:
    facts = []
    for event in events:
        parsed = parse_bank_sms(event.body)
        if parsed is not None:
            facts.append(
                SmsFacts(
                    event.event_id,
                    event.device_id,
                    parsed.amount,
                    parsed.currency,
                    event.occurred_at,
                    parsed.direction,
                    parsed.stated_local,
                )
            )
    return facts


def _sms_json(sms: SmsFacts, *, in_window: bool | None = None) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {
        "event_id": str(sms.event_id),
        "device_id": str(sms.device_id),
        "amount": f"{sms.amount:.2f}",
        "currency": sms.currency,
        "occurred_at": sms.occurred_at.astimezone(UTC).isoformat(),
        "direction": str(sms.direction),
        "stated_time": sms.stated_local.isoformat() if sms.stated_local else None,
    }
    if in_window is not None:
        value["in_window"] = in_window
    return value


class SmsVerificationService:
    def __init__(
        self,
        uow: SmsVerificationUnitOfWork,
        *,
        zone: tzinfo,
        window: MatchWindow,
        lookback_days: int,
    ) -> None:
        self.uow = uow
        self.repo = uow.sms_verification
        self.zone = zone
        self.window = window
        self.lookback = timedelta(days=lookback_days)

    async def bank_sms(
        self,
        since: datetime,
        until: datetime,
        *,
        device_id: UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, JsonValue]:
        """Every bank SMS in the window: matched to a receipt, unused, or
        without a readable amount. Bodies stay out (admin-only data)."""
        rows: list[JsonValue] = []
        counts = {"MATCHED": 0, "UNUSED": 0, "NO_AMOUNT": 0}
        unused: dict[str, Decimal] = {}
        for sms in await self.repo.bank_sms(since, until, device_id):
            parsed = parse_bank_sms(sms.body)
            state = "UNUSED"
            if parsed is None:
                state = "NO_AMOUNT"
            elif sms.candidate_id is not None:
                state = "MATCHED"
            counts[state] += 1
            if parsed is not None and state == "UNUSED":
                unused[parsed.currency] = (
                    unused.get(parsed.currency, Decimal(0)) + parsed.amount
                )
            if status and state != status:
                continue
            rows.append(
                {
                    "event_id": str(sms.event_id),
                    "device_id": str(sms.device_id),
                    "device_name": sms.device_name,
                    "sender": sms.sender,
                    "occurred_at": sms.occurred_at.astimezone(UTC).isoformat(),
                    "amount": f"{parsed.amount:.2f}" if parsed else None,
                    "currency": parsed.currency if parsed else None,
                    "direction": str(parsed.direction) if parsed else None,
                    "status": state,
                    "candidate_id": (
                        str(sms.candidate_id) if sms.candidate_id else None
                    ),
                }
            )
        return {
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "limit": limit,
            "offset": offset,
            "counts": {key: value for key, value in counts.items()},
            "unused_totals": [
                {"currency": currency, "amount": f"{amount:.2f}"}
                for currency, amount in sorted(unused.items())
            ],
        }

    async def sources(self, device_id: UUID) -> tuple[list[UUID], SmsDirection]:
        if not await self.repo.existing_devices({device_id}):
            raise NotFound("Device not found.")
        return (
            await self.repo.sms_sources(device_id),
            await self.repo.sms_direction(device_id),
        )

    async def set_sources(
        self,
        device_id: UUID,
        sources: list[UUID],
        actor_id: UUID,
        direction: SmsDirection = SmsDirection.IN,
    ) -> tuple[list[UUID], SmsDirection]:
        unique = list(dict.fromkeys(sources))
        if not await self.repo.existing_devices({device_id}):
            raise NotFound("Device not found.")
        if device_id in unique:
            raise Conflict("A device cannot verify its own receipts.")
        if len(unique) > MAX_SMS_SOURCES:
            raise Conflict("Too many SMS phones for one device.")
        if await self.repo.existing_devices(set(unique)) != set(unique):
            raise NotFound("SMS device not found.")
        await self.repo.set_sms_sources(
            device_id, unique, direction, actor_id, datetime.now(UTC)
        )
        await self.uow.commit()
        return unique, direction

    async def run(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        since = now - self.lookback
        manual = set()
        for candidate_id, row in (await self.repo.manual_rows()).items():
            receipt = await self._receipt(candidate_id)
            if (
                receipt is None
                or row.sms is None
                or row.sms.amount != receipt.amount
                or row.sms.currency != receipt.currency
                or row.sms.device_id not in receipt.sms_devices
                or row.sms.direction != receipt.direction
            ):
                # The receipt was corrected or unlinked after the operator's pick.
                await self.repo.delete(candidate_id)
            else:
                manual.add(candidate_id)
        receipts = [
            receipt
            for receipt in await self.repo.eligible_receipts(since=since)
            if receipt.candidate_id not in manual
        ]
        await self.repo.delete_auto_since(since)
        taken = frozenset(await self.repo.held_events())
        devices = {device for receipt in receipts for device in receipt.sms_devices}
        events = (
            await self.repo.sms_events(devices, since - timedelta(days=1), now)
            if devices
            else []
        )
        decisions = decide(
            receipts,
            _facts(events),
            zone=self.zone,
            window=self.window,
            now=now,
            taken=taken,
        )
        await self.repo.add_decisions(decisions, now)
        await self.uow.commit()
        counts = Counter(str(decision.status) for decision in decisions)
        summary = {
            status.value.lower(): counts.get(status.value, 0)
            for status in VerificationStatus
        }
        logger.info("sms_verification.run", extra=summary)
        return summary

    async def matches_reading(
        self,
        device_id: UUID,
        candidate_id: UUID,
        values: dict[str, str],
        exclude: frozenset[UUID] = frozenset(),
    ) -> UUID | None:
        """The one free bank SMS that fits an unreviewed reading's amount,
        currency, date and time, or None when there is none or several."""
        sources = frozenset(await self.repo.sms_sources(device_id))
        if not sources:
            return None
        try:
            amount = Decimal(values["amount"]).quantize(Decimal("0.01"))
            local = datetime.fromisoformat(f"{values['date']}T{values['time']}")
        except (KeyError, InvalidOperation, ValueError):
            return None
        if local.tzinfo is not None or amount <= 0:
            return None
        receipt = ReceiptFacts(
            candidate_id,
            amount,
            values.get("currency", "").upper(),
            local,
            datetime.now(UTC),
            sources,
            await self.repo.sms_direction(device_id),
        )
        held = await self.repo.held_events(except_candidate=candidate_id)
        centre = local.replace(tzinfo=self.zone).astimezone(UTC)
        span = timedelta(hours=SEARCH_HOURS)
        events = await self.repo.sms_events(set(sources), centre - span, centre + span)
        found = [
            sms
            for sms in _facts(events)
            if sms.event_id not in held
            and sms.event_id not in exclude
            and compatible(receipt, sms, self.zone, self.window)
        ]
        return found[0].event_id if len(found) == 1 else None

    async def match_by_folder_time(
        self,
        device_id: UUID,
        candidate_id: UUID,
        amount: str,
        currency: str,
        folder_at: datetime,
        exclude: frozenset[UUID] = frozenset(),
    ) -> tuple[UUID, datetime, bool] | None:
        """For a receipt whose own date and time cannot be read: the one free
        bank SMS with this amount from up to 12 hours before the image reached
        the folder, its local transfer time, and whether it is within 2 hours
        (near). None when there is none or several."""
        sources = frozenset(await self.repo.sms_sources(device_id))
        try:
            value = Decimal(amount).quantize(Decimal("0.01"))
        except InvalidOperation:
            return None
        if not sources or value <= 0 or folder_at.tzinfo is None:
            return None
        direction = await self.repo.sms_direction(device_id)
        held = await self.repo.held_events(except_candidate=candidate_id)
        start, end = folder_at - FOLDER_BEFORE, folder_at + FOLDER_AFTER
        events = await self.repo.sms_events(
            set(sources), start - timedelta(days=1), end + timedelta(days=1)
        )
        found = []
        for sms in _facts(events):
            moment = (
                sms.stated_local.replace(tzinfo=self.zone)
                if sms.stated_local is not None
                else sms.occurred_at
            )
            if (
                sms.event_id not in held
                and sms.event_id not in exclude
                and sms.device_id in sources
                and sms.direction == direction
                and sms.amount == value
                and sms.currency == currency.upper()
                and start <= moment <= end
            ):
                found.append((sms.event_id, moment.astimezone(self.zone)))
        if len(found) != 1:
            return None
        event_id, moment = found[0]
        near = moment >= folder_at - FOLDER_NEAR
        return event_id, moment.replace(tzinfo=None, second=0, microsecond=0), near

    async def _receipt(self, candidate_id: UUID) -> ReceiptFacts | None:
        found = await self.repo.eligible_receipts(candidate_id=candidate_id)
        return found[0] if found else None

    async def _options(self, receipt: ReceiptFacts) -> list[tuple[SmsFacts, bool]]:
        held = await self.repo.held_events(except_candidate=receipt.candidate_id)
        centre = receipt.local_time.replace(tzinfo=self.zone).astimezone(UTC)
        span = timedelta(hours=SEARCH_HOURS)
        events = await self.repo.sms_events(
            set(receipt.sms_devices), centre - span, centre + span
        )
        options = []
        for sms in _facts(events):
            if sms.event_id in held:
                continue
            if (
                sms.amount != receipt.amount
                or sms.currency != receipt.currency
                or sms.direction != receipt.direction
            ):
                continue
            options.append((sms, compatible(receipt, sms, self.zone, self.window)))
        options.sort(key=lambda item: (not item[1], item[0].occurred_at))
        return options

    async def detail(self, candidate_id: UUID) -> dict[str, JsonValue]:
        device_id = await self.repo.candidate_device(candidate_id)
        if device_id is None:
            raise NotFound("Receipt not found.")
        stored = await self.repo.get(candidate_id)
        receipt = await self._receipt(candidate_id)
        if stored is not None:
            state = str(stored.status)
        elif not await self.repo.sms_sources(device_id):
            state = "NOT_SET_UP"
        elif receipt is None:
            state = "NOT_READY"
        else:
            state = "NOT_CHECKED"
        options: list[JsonValue] = []
        if receipt is not None and state != VerificationStatus.VERIFIED:
            options = [
                _sms_json(sms, in_window=in_window)
                for sms, in_window in await self._options(receipt)
            ]
        return {
            "candidate_id": str(candidate_id),
            "status": state,
            "method": str(stored.method) if stored else None,
            "sms": _sms_json(stored.sms) if stored and stored.sms else None,
            "option_count": stored.option_count if stored else 0,
            "options": options,
        }

    async def confirm(
        self, candidate_id: UUID, event_id: UUID, actor_id: UUID
    ) -> dict[str, JsonValue]:
        if await self.repo.candidate_device(candidate_id) is None:
            raise NotFound("Receipt not found.")
        receipt = await self._receipt(candidate_id)
        if receipt is None:
            raise Conflict(
                "Review amount, currency, date and time and link an SMS phone first."
            )
        chosen = next(
            (
                sms
                for sms, _ in await self._options(receipt)
                if sms.event_id == event_id
            ),
            None,
        )
        if chosen is None:
            raise Conflict("That SMS cannot verify this receipt.")
        await self.repo.save(
            StoredVerification(
                candidate_id,
                VerificationStatus.VERIFIED,
                MatchMethod.MANUAL,
                chosen,
                1,
                actor_id,
                datetime.now(UTC),
            )
        )
        await self.uow.commit()
        return await self.detail(candidate_id)

    async def clear(self, candidate_id: UUID) -> dict[str, JsonValue]:
        if await self.repo.candidate_device(candidate_id) is None:
            raise NotFound("Receipt not found.")
        await self.repo.delete(candidate_id)
        await self.uow.commit()
        return await self.detail(candidate_id)
