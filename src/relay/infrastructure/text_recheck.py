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
_REFERENCE = re.compile(r"(?<!\d)(\d{12})(?!\d)")
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


def amounts(text: str) -> set[str]:
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
