"""Cross-device receipt verification against received-transfer bank SMS.

A receipt is collected on one phone and the bank SMS for the incoming
transfer arrives on another. InstaPay credit SMS share only the amount and the
date/time with the receipt, so matching uses exactly those: same amount and
currency, and an SMS time inside a short window around the receipt minute.
Each SMS can verify at most one receipt, so one real payment cannot vouch for
several edited receipts. Anything unclear is left for the operator.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

WAITING_HOURS = 24
MAX_SMS_SOURCES = 10


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    AMBIGUOUS = "AMBIGUOUS"
    WAITING = "WAITING"
    UNMATCHED = "UNMATCHED"


class MatchMethod(StrEnum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"


@dataclass(frozen=True, slots=True)
class MatchWindow:
    """SMS may arrive slightly before (clock skew) or after the receipt minute."""

    before: timedelta = timedelta(minutes=2)
    after: timedelta = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class ReceiptFacts:
    candidate_id: UUID
    amount: Decimal
    currency: str
    local_time: datetime  # naive wall-clock minute shown on the receipt
    created_at: datetime
    sms_devices: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class SmsFacts:
    event_id: UUID
    device_id: UUID
    amount: Decimal
    currency: str
    occurred_at: datetime  # timezone-aware


@dataclass(frozen=True, slots=True)
class Decision:
    candidate_id: UUID
    status: VerificationStatus
    sms: SmsFacts | None
    option_count: int


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")
_NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?"
_CURRENCY = r"(?:EGP|E\.G\.P|LE|L\.E|جنيه|جنية|ج\.م|جم)"
_AMOUNTS = (
    re.compile(rf"{_CURRENCY}\.?\s*{_NUMBER}", re.I),
    re.compile(rf"{_NUMBER}\s*{_CURRENCY}", re.I),
)
_BALANCE = re.compile(r"(?:balance|bal\.?|رصيد|الرصيد)[^\d]{0,24}$", re.I)
_CREDIT = re.compile(
    r"received|credited|deposit|added|incoming|استلام|استلمت|إيداع|ايداع|إضافة|اضافة|"
    r"وارد|تحويل وارد",
    re.I,
)
_DEBIT = re.compile(r"debited|withdraw|purchase|sent to|خصم|سحب|شراء|مدين", re.I)


def parse_credit_sms(body: str) -> tuple[Decimal, str] | None:
    """Read the credited amount of an incoming-transfer SMS, or decline.

    Provisional until real bank samples are added as fixtures. It is strict on
    purpose: a debit, a missing credit word, or more than one candidate amount
    (balances are ignored) yields None, which can never verify a receipt.
    """
    if not body or len(body) > 1000:
        return None
    text = body.translate(_DIGITS)
    if not _CREDIT.search(text) or _DEBIT.search(text):
        return None
    found: set[Decimal] = set()
    for pattern in _AMOUNTS:
        for match in pattern.finditer(text):
            if _BALANCE.search(text[: match.start()]):
                continue
            whole, cents = match.group(1), match.group(2) or "0"
            try:
                value = Decimal(f"{whole.replace(',', '')}.{cents}")
            except InvalidOperation:
                continue
            if Decimal("0") < value < Decimal("10000000"):
                found.add(value.quantize(Decimal("0.01")))
    if len(found) != 1:
        return None
    return found.pop(), "EGP"


def window_bounds(
    local_minute: datetime, zone: tzinfo, window: MatchWindow
) -> tuple[datetime, datetime]:
    start = local_minute.replace(second=0, microsecond=0, tzinfo=zone).astimezone(UTC)
    return start - window.before, start + timedelta(minutes=1) + window.after


def compatible(
    receipt: ReceiptFacts, sms: SmsFacts, zone: tzinfo, window: MatchWindow
) -> bool:
    if sms.device_id not in receipt.sms_devices:
        return False
    if sms.amount != receipt.amount or sms.currency != receipt.currency:
        return False
    start, end = window_bounds(receipt.local_time, zone, window)
    return start <= sms.occurred_at.astimezone(UTC) < end


def decide(
    receipts: Sequence[ReceiptFacts],
    messages: Iterable[SmsFacts],
    *,
    zone: tzinfo,
    window: MatchWindow,
    now: datetime,
    taken: frozenset[UUID] = frozenset(),
) -> list[Decision]:
    """Assign SMS one-to-one; verify only when both sides are unique."""
    free = [sms for sms in messages if sms.event_id not in taken]
    options = {
        receipt.candidate_id: [
            sms for sms in free if compatible(receipt, sms, zone, window)
        ]
        for receipt in receipts
    }
    claims: dict[UUID, int] = {}
    for matches in options.values():
        for sms in matches:
            claims[sms.event_id] = claims.get(sms.event_id, 0) + 1
    decisions = []
    for receipt in receipts:
        matches = options[receipt.candidate_id]
        if len(matches) == 1 and claims[matches[0].event_id] == 1:
            decisions.append(
                Decision(
                    receipt.candidate_id, VerificationStatus.VERIFIED, matches[0], 1
                )
            )
        elif matches:
            decisions.append(
                Decision(
                    receipt.candidate_id,
                    VerificationStatus.AMBIGUOUS,
                    None,
                    len(matches),
                )
            )
        else:
            waiting = now - receipt.created_at < timedelta(hours=WAITING_HOURS)
            decisions.append(
                Decision(
                    receipt.candidate_id,
                    (
                        VerificationStatus.WAITING
                        if waiting
                        else VerificationStatus.UNMATCHED
                    ),
                    None,
                    0,
                )
            )
    return decisions
