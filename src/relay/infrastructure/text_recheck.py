"""Plain-text look at every receipt image.

WhatsApp shrinks and recompresses receipt photos, so the layout matcher often
fails on real receipts. Here the whole image is enlarged, sharpened and read
as plain text, to tell receipts from payment requests and other photos, and
to read the sender and recipient for the bank SMS check.
"""

import io
import re
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps

from relay.application.processing_errors import ProcessingError
from relay.infrastructure.image_processing import PillowImagePreprocessor

VERSION = "text_recheck_v2"
TARGET_WIDTH = 1600
MAX_SCALE = 3.0
MAX_SIDE = 4000

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_BRAND = re.compile(r"instapay|[اإ]نستا\s*با[يى]|\bIPN\b", re.I)
_SUCCESS = re.compile(
    r"تم\s*التحويل|تمت\s*العملي[ةه]|معامل[ةه]\s*ناجح[ةه]|"
    r"transfer(red)?\s+success|successful\s+trans",
    re.I,
)
_LABEL = re.compile(
    r"المرجع|الرقم\s*المرجعي|مبلغ\s*التحويل|المبلغ\s*المحول|reference|amount", re.I
)
_AMOUNT = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d{1,7})(\.\d{1,2})?\s*(?:EGP|جنيه|ج\.م)"
    r"|(?:EGP)\s*(\d{1,3}(?:,\d{3})+|\d{1,7})(\.\d{1,2})?(?![\d.,])",
    re.I,
)
# InstaPay prints a 12-digit reference; bank apps print e.g. "FT26323HQ8RM".
_REFERENCE = re.compile(r"(?<![\dA-Z])(\d{12}|FT[0-9A-Z]{8,14})(?![\dA-Z])")
_REQUEST = re.compile(r"طلب\s*دفع|payment\s*request|request(?:ed)?\s*money|تحصيل", re.I)
# OCR often splits an address ("salma 1594 @instapay"); join up to two parts.
_HANDLE = re.compile(r"([a-z][a-z0-9._-]{1,40}(?:\s\d{1,8})?)\s*@\s*instapay", re.I)
_FAILED = re.compile(
    r"فشل(?:ت)?\s*(?:ال)?(?:معامل[ةه]|عملي[ةه]|تحويل)|"
    r"(?:transaction|transfer)\s+failed|unsuccessful",
    re.I,
)
_ACCOUNT = re.compile(r"ACCOUNT\s*X+\s*(\d{4})", re.I)
_PHONE = re.compile(r"(?<!\d)(01\d{9})(?!\d)")
MAX_TEXT = 6000
_DATE = re.compile(
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+20\d\d",
    re.I,
)


_STAMP = re.compile(
    r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+"
    r"(20\d\d)\s+(\d{1,2}):(\d{2})\s*([AP]M)\b",
    re.I,
)
_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def _stamps(text: str) -> set[tuple[str, str]]:
    """Printed "14 Nov 2026 08:38 PM" stamps as (ISO date, HH:MM:00)."""
    found: set[tuple[str, str]] = set()
    for day, month, year, hour, minute, half in _STAMP.findall(text):
        number, clock = int(day), int(hour)
        if not (1 <= number <= 31 and 1 <= clock <= 12 and int(minute) <= 59):
            continue
        clock = clock % 12 + (12 if half.upper() == "PM" else 0)
        found.add(
            (
                f"{year}-{_MONTHS.index(month.lower()) + 1:02d}-{number:02d}",
                f"{clock:02d}:{minute}:00",
            )
        )
    return found


def enlarge(data: bytes) -> Image.Image:
    """Grayscale, enlarged and sharpened copy for reading small grey text."""
    prepared = PillowImagePreprocessor().preprocess(data)
    image = Image.open(io.BytesIO(prepared.data)).convert("L")
    scale = min(MAX_SCALE, max(1.0, TARGET_WIDTH / max(1, image.width)))
    scale = min(scale, MAX_SIDE / max(image.width, image.height))
    if scale > 1.05:
        size = (round(image.width * scale), round(image.height * scale))
        image = image.resize(size, Image.Resampling.LANCZOS)
    image = ImageOps.autocontrast(image, cutoff=1)
    return image.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=2))


def transfer_from_fee(found: set[str]) -> str | None:
    """The transfer amount when the receipt shows InstaPay's fee and total:
    fee = 0.1% of the amount, at least 0.50 and at most 20.00 EGP, and
    total = amount + fee. The pair checks itself, so a stray number cannot
    pass. Returns the largest such amount, or None."""
    values = sorted(Decimal(item) for item in found)
    best: Decimal | None = None
    for total in values:
        for fee in values:
            if fee <= 0 or fee >= total:
                continue
            amount = total - fee
            if amount != amount.to_integral_value():
                continue
            expected = min(max(amount / 1000, Decimal("0.50")), Decimal("20.00"))
            if abs(expected.quantize(Decimal("0.01")) - fee) <= Decimal("0.01"):
                best = amount if best is None or amount > best else best
    return f"{best:.2f}" if best is not None else None


_TOTAL = re.compile(r"[اإ]جمال[يى]|total", re.I)


def transfer_from_total(text: str) -> str | None:
    """When OCR loses the fee: a labelled total with cents is the amount plus
    its 0.1% fee, so the whole-pound amount that fits the fee rule is the
    transfer. Only with a "total" label and a single amount with cents."""
    if not _TOTAL.search(text):
        return None
    with_cents = [Decimal(item) for item in amounts(text) if not item.endswith(".00")]
    if len(with_cents) != 1:
        return None
    total = with_cents[0]
    for amount in range(int(total), max(int(total) - 21, 0), -1):
        found = transfer_from_fee({f"{total:.2f}", f"{total - amount:.2f}"})
        if found is not None:
            return found
    return None


_COLON_CENTS = re.compile(r"(?<![\d:])(\d{1,3}):(\d{2})(?=\s*(?:EGP|جنيه))", re.I)


# The big amount at the top of the receipt sits on the line right above its
# label. Its orange "EGP" often reads as junk ("550 cap", "مجع 400"), so the
# label finds it instead.
_HERO_LABEL = re.compile(r"مبل[غع].{0,25}(?:محول|تحويل)|transfer\s*amount", re.I)
_HERO_LINE = re.compile(
    r"^\W*(?:[^\d\s]{1,5}\s+)?(\d{1,3}(?:,\d{3})+|\d{1,7})(\.\d{2})?"
    r"\s*[^\d\s]{0,5}\W*$"
)


def hero_amount(text: str) -> str | None:
    """The amount printed big above "المبلغ المحول" / "Transfer Amount", or
    right below "تم التحويل بنجاح" (bank apps print it there)."""
    lines = [line.strip() for line in text.translate(_DIGITS).splitlines()]
    seen = set()
    for index, line in enumerate(lines):
        if _HERO_LABEL.search(line) and index:
            near = lines[index - 1]
        elif _SUCCESS.search(line) and index + 1 < len(lines):
            near = lines[index + 1]
        else:
            continue
        match = _HERO_LINE.match(near)
        whole = match.group(1).replace(",", "") if match else "0"
        if match and len(whole) > 1:
            seen.add(f"{float(whole + (match.group(2) or '')):.2f}")
    return next(iter(seen)) if len(seen) == 1 else None


def amounts(text: str) -> set[str]:
    # OCR sometimes reads the decimal point of a small amount as ":" ("0:62 EGP").
    text = _COLON_CENTS.sub(r"\1.\2", text)
    found = set()
    for match in _AMOUNT.finditer(text):
        whole = (match.group(1) or match.group(3)).replace(",", "")
        cents = match.group(2) or match.group(4) or ""
        found.add(f"{float(whole + cents):.2f}")
    return found


def signals(text: str, second: str | None = None) -> dict[str, Any]:
    """What the plain text says about the image.

    ``second`` is the same image read in another mode; a value is only
    suggested when both readings show the same one.
    """
    text = text.translate(_DIGITS)
    other = second.translate(_DIGITS) if second is not None else None
    references = set(_REFERENCE.findall(text))
    found = {
        "brand": bool(_BRAND.search(text)),
        "success": bool(_SUCCESS.search(text)),
        "label": bool(_LABEL.search(text)),
        "amount": bool(_AMOUNT.search(text)),
        "date": bool(_DATE.search(text)),
        "reference": bool(references),
    }
    values: dict[str, str] = {}
    if other is not None:
        if len(references) == 1 and set(_REFERENCE.findall(other)) == references:
            values["reference_number"] = next(iter(references))
        stamps = _stamps(text)
        if len(stamps) == 1 and _stamps(other) == stamps:
            values["date"], values["time"] = next(iter(stamps))
        seen = amounts(text)
        if len(seen) == 1 and amounts(other) == seen:
            values["amount"], values["currency"] = next(iter(seen)), "EGP"
    # Photos of a phone often cut off the big amount, but the fee and the
    # total add up exactly: total - fee is the transfer.
    derived = transfer_from_fee(amounts(text) | (amounts(other) if other else set()))
    if derived is None:
        derived = transfer_from_total(text) or (
            transfer_from_total(other) if other else None
        )
    if derived is not None:
        values["amount"], values["currency"] = derived, "EGP"
    hero = hero_amount(text + "\n" + (other or ""))
    if hero is not None:
        values["amount"], values["currency"] = hero, "EGP"
        found["amount"] = True
    # A receipt shows a reference or an amount, plus wording that says what
    # it is. A payment request looks like a receipt but moves no money.
    request = bool(_REQUEST.search(text))
    receipt = (found["reference"] or found["amount"]) and sum(found.values()) >= 3
    return {
        "receipt": receipt and not request,
        "payment_request": request,
        "failed": bool(_FAILED.search(text)),
        "found": found,
        "values": values,
        "sender": sender(text),
        "parties": parties(text) or (parties(other) if other is not None else {}),
        # Both readings: a phone or name missed by one is often in the other.
        "text": (text if other is None else text + "\n" + other)[:MAX_TEXT],
    }


# A shop's printed bill (restaurant, delivery): items, prices, cash paid.
# Each pattern is one kind of bill wording; OCR misreads included ("Qtv").
_INVOICE_MARKS = (
    re.compile(r"فاتور[ةه]|invoice", re.I),
    re.compile(r"\bit[e]?r?m\b|\blem\s*name|الصنف", re.I),
    re.compile(r"\bq[t][yv]\b|الكمي[ةه]", re.I),
    re.compile(r"\bprice\b|السعر", re.I),
    re.compile(r"\btotal\b", re.I),
    re.compile(r"الصاف[ىي]|المدفوع\s*نقد|نقد[اًا]|\bcash\b", re.I),
    re.compile(r"خدم[ةه]\s*(?:ال)?توصيل|الطيار|delivery", re.I),
    re.compile(r"[اإ]لوردي[ةه]|المستخدم\s*كاشير|cashier", re.I),
)


def shop_invoice(text: str) -> bool:
    """A shop's bill, not a money transfer: at least two kinds of bill
    wording and no sign of a transfer (see ``transfer_words``)."""
    if transfer_words(text):
        return False
    text = text.translate(_DIGITS)
    return sum(bool(mark.search(text)) for mark in _INVOICE_MARKS) >= 2


_TRANSFER_WORDS = re.compile(
    r"تحويل|حوال[ةه]|transfer|محفظ[ةه]|wallet|@\s*instapay|EGP|جنيه|ج\.م", re.I
)


def transfer_words(text: str) -> bool:
    """Any sign of a money transfer: InstaPay, transfer or success wording,
    receipt labels, or an amount in EGP. A photo of goods with a price tag
    (a meat label) has none."""
    text = text.translate(_DIGITS)
    return any(
        pattern.search(text)
        for pattern in (_BRAND, _SUCCESS, _LABEL, _REQUEST, _AMOUNT, _TRANSFER_WORDS)
    )


def single(text: str) -> dict[str, Any]:
    """``signals`` for one clean reading from a model (classic PaddleOCR):
    its values count as agreed with itself, since there is no second pass."""
    read = signals(text, text)
    read["text"] = text.translate(_DIGITS)[:MAX_TEXT]
    return read


_TO = re.compile(r"^\W*(?:[اإ]ل[ىي]|to)\b", re.I)
_END = re.compile(r"powered\s*by", re.I)
_NOT_NAMES = {"ACCOUNT", "BANK", "EGP", "IPN", "INSTAPAY", "MISR", "POWERED", "XXXX"}


def _person(line: str) -> str | None:
    """A name printed in capitals: "SALMA MOHAMED GALAL" in full, or only the
    first name when the rest is masked ("YOUSSEF M***** Y******"). OCR noise
    before the name ("ke YOUSSEF ...") is skipped."""
    match = re.search(r"(?<![A-Za-z])([A-Z]{3,})(?![A-Za-z])", line)
    if match is None or match.group(1) in _NOT_NAMES:
        return None
    first = match.group(1)
    rest = re.findall(r"[A-Za-z*]{2,}", line[match.end() :])
    if any("*" in word for word in rest):
        return first
    rest = [w for w in rest if w.isupper() and w not in _NOT_NAMES]
    return " ".join([first, *rest[:4]])


def parties(text: str) -> dict[str, str]:
    """Sender name above the "to" line; recipient name and phone below it."""
    lines = text.splitlines()
    to = next((i for i, line in enumerate(lines) if _TO.match(line)), None)
    if to is None:
        # The "to" word is often lost; a masked name is always the recipient.
        masked = next(
            (i for i, line in enumerate(lines) if "*" in line and _person(line)), None
        )
        if masked is None:
            return {}
        to = masked - 1
    after: list[str] = []
    for line in lines[to + 1 :]:
        if _END.search(line):
            break
        after.append(line)
    found = {
        "sender_name": next(filter(None, map(_person, lines[:to])), None),
        "recipient_name": next(filter(None, map(_person, after)), None),
        "recipient_phone": next(
            (m.group(1) for m in map(_PHONE.search, after) if m), None
        ),
    }
    return {key: value for key, value in found.items() if value}


def sender(text: str) -> str | None:
    """Who sent the money, as printed: their InstaPay address or the last
    four digits of their account. None when neither can be read."""
    handle = _HANDLE.search(text)
    if handle:
        return "".join(handle.group(1).split()).casefold() + "@instapay"
    account = _ACCOUNT.search(text)
    return "account:" + account.group(1) if account else None


def _phones(text: str) -> set[str]:
    return set(_PHONE.findall(text.translate(_DIGITS)))


def shows_a_phone(text: str) -> bool:
    """Any mobile number printed at all (someone's, readable)."""
    return bool(_phones(text))


def recipient_check(text: str, name: str, phone: str | None, instapay: bool) -> str:
    """Was the receipt sent to the owner? "match", "different" or "unclear".

    The phone is the strongest sign (one misread digit tolerated). Without
    it, the owner's name is enough unless another phone number is printed.
    Bank receipts often show no phone, so there the name decides."""
    folded = text.casefold().replace("0", "o")
    name_found = bool(name) and name.casefold().replace("0", "o") in folded
    phones = _phones(text)
    if phone and any(
        len(found) == len(phone)
        and sum(a != b for a, b in zip(found, phone, strict=True)) <= 1
        for found in phones
    ):
        return "match"
    if not instapay or not phone:
        return "match" if name_found else "unclear"
    if phones:
        return "different"
    return "match" if name_found else "unclear"


def recipient_matches(text: str, name: str, phone: str | None, instapay: bool) -> bool:
    return recipient_check(text, name, phone, instapay) == "match"


def read_text(
    data: bytes, provider: Any, languages: tuple[str, ...], seconds: float = 25.0
) -> tuple[str, str] | None:
    """The enlarged image read twice (block mode, then sparse mode), or None
    when it cannot be read."""
    deadline = time.monotonic() + seconds
    try:
        image = enlarge(data)
    except (ProcessingError, OSError, ValueError):
        return None
    texts: list[str] = []
    with tempfile.TemporaryDirectory(prefix="relay-recheck-") as directory:
        source = Path(directory) / "page.png"
        image.save(source, "PNG")
        for psm in (6, 11):
            try:
                lines = provider._run_tesseract(
                    source, Path(directory) / f"ocr-{psm}", languages, psm, deadline
                )
            except ProcessingError:
                lines = ()
            texts.append("\n".join(line.text for line in lines))
    return (texts[0], texts[1]) if any(texts) else None


def assess(data: bytes, provider: Any, languages: tuple[str, ...]) -> dict[str, Any]:
    """Read one image as plain text and say what it is."""
    texts = read_text(data, provider, languages)
    if texts is None:
        return {
            "receipt": False,
            "payment_request": False,
            "found": {},
            "values": {},
            "sender": None,
            "text": "",
        }
    block, sparse = texts
    return signals(block + "\n" + sparse, sparse if block else None)
