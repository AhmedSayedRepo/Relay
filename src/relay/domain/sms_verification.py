"""Cross-device receipt verification against InstaPay bank SMS.

A receipt is collected on one phone and the bank SMS for the same transfer
arrives on another. Bank SMS share only the amount and the date/time with the
receipt (the SMS reference is a different bank identifier), so matching uses
exactly those: same amount and currency, same transfer direction, and the time
the SMS states (or, failing that, when it arrived) close to the receipt minute.
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
# A time printed in the SMS is the transfer minute itself; allow one minute of
# rounding either way. Arrival time falls back to MatchWindow.
STATED_TOLERANCE = timedelta(minutes=1)


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    AMBIGUOUS = "AMBIGUOUS"
    WAITING = "WAITING"
    UNMATCHED = "UNMATCHED"


class MatchMethod(StrEnum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"


class SmsDirection(StrEnum):
    """Which side of the transfer the SMS phone is on."""

    IN = "IN"  # money received (credit SMS)
    OUT = "OUT"  # money sent (debit SMS)


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
    direction: SmsDirection = SmsDirection.IN


@dataclass(frozen=True, slots=True)
class SmsFacts:
    event_id: UUID
    device_id: UUID
    amount: Decimal
    currency: str
    occurred_at: datetime  # timezone-aware arrival time
    direction: SmsDirection = SmsDirection.IN
    stated_local: datetime | None = None  # naive transfer time printed in the SMS


@dataclass(frozen=True, slots=True)
class ParsedSms:
    amount: Decimal
    currency: str
    direction: SmsDirection
    stated_local: datetime | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    candidate_id: UUID
    status: VerificationStatus
    sms: SmsFacts | None
    option_count: int


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬۰۱۲۳۴۵۶۷۸۹", "0123456789.,0123456789")
_NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?"
_CURRENCY = r"(?:EGP|E\.G\.P|LE|L\.E|جنيه|جنية|ج\.م|جم)"

# CIB-style IPN SMS, e.g. "Kindly note your account ********8413 was charged
# with IPN Outward with amount EGP 650.00 on 25-10-2026 at 23:00 with reference
# 1b52f3a6". The Inward (credit) wording is assumed by symmetry until a real
# sample confirms it.
_IPN = re.compile(
    r"was\s+(charged|credited)\s+with\s+IPN\s+(Outward|Inward)\s+with\s+amount\s+"
    rf"(EGP)\s*{_NUMBER}\s+on\s+(\d{{1,2}})-(\d{{1,2}})-(\d{{4}})\s+at\s+"
    r"(\d{1,2}):(\d{2})",
    re.I,
)

_AMOUNTS = (
    re.compile(rf"{_CURRENCY}\.?\s*{_NUMBER}", re.I),
    re.compile(rf"{_NUMBER}\s*{_CURRENCY}", re.I),
)
_BALANCE = re.compile(r"(?:balance|bal\.?|رصيد|الرصيد)[^\d]{0,24}$", re.I)
_IN_WORDS = re.compile(
    r"received|credited|deposit|added|incoming|inward|"
    r"استلام|استلمت|إيداع|ايداع|إضافة|اضافة|وارد",
    re.I,
)
_OUT_WORDS = re.compile(
    r"debited|charged|outward|sent to|خصم|صادر|مدين|إرسال|ارسال", re.I
)
# Not InstaPay transfers: never let a card purchase or cash withdrawal match.
_EXCLUDED = re.compile(r"purchase|withdraw|\bATM\b|\bPOS\b|شراء|سحب|نقاط البيع", re.I)
_INSTAPAY = re.compile(
    r"\bIPN\b|insta\s?pay|انستا\s?باي|إنستا\s?باي|انستاباى|تحويل لحظي|الدفع اللحظي",
    re.I,
)
_DATE = re.compile(r"(?<!\d)(\d{1,2})[-/](\d{1,2})[-/](\d{4})(?!\d)")
_TIME = re.compile(
    r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])\s*(AM|PM|ص|م(?![\u0621-\u064A]))?", re.I
)


def _decimal(whole: str, cents: str | None) -> Decimal | None:
    try:
        value = Decimal(f"{whole.replace(',', '')}.{cents or '0'}")
    except InvalidOperation:
        return None
    if not Decimal("0") < value < Decimal("10000000"):
        return None
    return value.quantize(Decimal("0.01"))


def _parse_ipn(text: str) -> ParsedSms | None:
    matches = list(_IPN.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    verb, flow = match.group(1).lower(), match.group(2).lower()
    if (verb == "charged") != (flow == "outward"):
        return None  # contradictory wording; never guess
    amount = _decimal(match.group(4), match.group(5))
    day, month, year, hour, minute = (int(match.group(i)) for i in range(6, 11))
    try:
        stated = datetime(year, month, day, hour, minute)
    except ValueError:
        return None
    if amount is None:
        return None
    direction = SmsDirection.OUT if flow == "outward" else SmsDirection.IN
    return ParsedSms(amount, match.group(3).upper(), direction, stated)


def _stated_time(text: str) -> datetime | None:
    dates, times = _DATE.findall(text), _TIME.findall(text)
    if len(dates) != 1 or len(times) != 1:
        return None
    day, month, year = (int(part) for part in dates[0])
    hour, minute, meridiem = int(times[0][0]), int(times[0][1]), times[0][2]
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        pm = meridiem.upper() == "PM" or meridiem == "م"
        hour = hour % 12 + (12 if pm else 0)
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def _parse_generic(text: str) -> ParsedSms | None:
    """English or Arabic wording with one clear direction and one amount."""
    if _EXCLUDED.search(text):
        return None
    money_in, money_out = bool(_IN_WORDS.search(text)), bool(_OUT_WORDS.search(text))
    if money_in == money_out:
        return None  # neither or both: unclear direction
    if money_out and not _INSTAPAY.search(text):
        return None  # a debit must say it is an InstaPay/IPN transfer
    found: set[Decimal] = set()
    for pattern in _AMOUNTS:
        for match in pattern.finditer(text):
            if _BALANCE.search(text[: match.start()]):
                continue
            value = _decimal(match.group(1), match.group(2))
            if value is not None:
                found.add(value)
    if len(found) != 1:
        return None
    direction = SmsDirection.OUT if money_out else SmsDirection.IN
    return ParsedSms(found.pop(), "EGP", direction, _stated_time(text))


def parse_bank_sms(body: str) -> ParsedSms | None:
    """Read amount, direction and stated time from an InstaPay bank SMS.

    Strict on purpose: unknown wording, contradictory direction, or more than
    one candidate amount (balances are ignored) yields None, which can never
    verify a receipt.
    """
    if not body or len(body) > 1000:
        return None
    text = body.translate(_DIGITS)
    return _parse_ipn(text) or _parse_generic(text)


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
    if sms.direction != receipt.direction:
        return False
    if sms.amount != receipt.amount or sms.currency != receipt.currency:
        return False
    minute = receipt.local_time.replace(second=0, microsecond=0)
    if sms.stated_local is not None:
        return abs(sms.stated_local - minute) <= STATED_TOLERANCE
    start, end = window_bounds(minute, zone, window)
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
