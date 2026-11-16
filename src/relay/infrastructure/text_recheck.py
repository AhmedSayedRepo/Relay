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
_HANDLE = re.compile(
    r"([a-z][a-z0-9._-]{1,40}(?:\s[a-z0-9._-]{1,20})?)\s*@\s*instapay", re.I
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
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct")
_MONTHS += ("nov", "dec")


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


def _amounts(text: str) -> set[str]:
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
        amounts = _amounts(text)
        if len(amounts) == 1 and _amounts(other) == amounts:
            values["amount"], values["currency"] = next(iter(amounts)), "EGP"
    # A receipt shows a reference or an amount, plus wording that says what
    # it is. A payment request looks like a receipt but moves no money.
    request = bool(_REQUEST.search(text))
    receipt = (found["reference"] or found["amount"]) and sum(found.values()) >= 3
    return {
        "receipt": receipt and not request,
        "payment_request": request,
        "found": found,
        "values": values,
        "sender": sender(text),
        "text": text[:MAX_TEXT],
    }


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


def recipient_matches(text: str, name: str, phone: str | None, instapay: bool) -> bool:
    """The receipt was sent to the owner: the name shows, and on InstaPay
    receipts the phone number too (one misread digit is tolerated)."""
    if not name or name.casefold() not in text.casefold():
        return False
    if not instapay or not phone:
        return True
    return any(
        len(found) == len(phone)
        and sum(a != b for a, b in zip(found, phone, strict=True)) <= 1
        for found in _phones(text)
    )


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
