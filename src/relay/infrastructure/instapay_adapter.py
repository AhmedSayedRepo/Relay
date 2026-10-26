"""Versioned InstaPay field localization, using the existing local recognizer.

No full-page transcript, sample identifiers, inferred values, OCR repair or
recognizer configuration search. Recognition modes 6/7 are existing capabilities.
Two modes are consistency checks, NOT independent financial evidence sources.
"""

import asyncio
import re

from relay.application.evidence_ports import ReceiptEvidence
from relay.domain.evidence import FieldCandidate
from relay.domain.processing import (
    OcrLine,
)
from relay.infrastructure.receipt_geometry import (
    WorkingReceipt,
)
from relay.infrastructure.tesseract_ocr import TesseractOcrProvider

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_REFERENCE = re.compile(r"reference(?:\s+number)?|الرقم\s*المرجعي|رقم\s*مرجعي", re.I)
_DATE = re.compile(r"\bdate\b|التاريخ", re.I)
_AMOUNT_LABEL = re.compile(r"transfer\s+amount|مبلغ\s*التحويل", re.I)
_EXPANDED = re.compile(
    r"service\s+fee|رسوم\s*الخدمة|hide\s+details|إخفاء\s*التفاصيل", re.I
)
_HANDLE = re.compile(r"[A-Za-z0-9._+\-]+@instapay", re.I)
_NUMBER = r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?"
_PAIR = re.compile(
    rf"(?P<amount>{_NUMBER})\s*(?P<currency>EGP|USD|EUR|ج\.?\s*م\.?)\b", re.I
)
_DATE_VALUE = re.compile(
    r"\b(?:[0-9]{1,2}\s+[A-Za-z]{3}\s+[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{2}/[0-9]{2}/[0-9]{4})\b"
)
_TIME_VALUE = re.compile(r"\b[0-9]{1,2}:[0-9]{2}(?:\s*[AP]M)?\b", re.I)
_THRESHOLDS = {
    "reference_number": 0.98,
    "amount": 0.90,
    "currency": 0.95,
    "date": 0.90,
    "time": 0.90,
    "handle": 0.90,
    "recipient_identifier": 0.95,
}
SUPPORTED_PROFILES = ("instapay_compact_bilingual_v1", "instapay_expanded_bilingual_v1")


def select_layout(lines: tuple[OcrLine, ...]) -> str | None:
    """Require participant, reference and date anchors, plus a layout discriminator."""
    text = "\n".join(line.text for line in lines)
    if not (_HANDLE.search(text) and _REFERENCE.search(text) and _DATE.search(text)):
        return None
    if _EXPANDED.search(text):
        return SUPPORTED_PROFILES[1]
    if _AMOUNT_LABEL.search(text):
        return SUPPORTED_PROFILES[0]
    return None


def field_region(
    image: WorkingReceipt, label: OcrLine, field: str
) -> tuple[int, int, int, int]:
    """Label-relative value column/row, never a field's benchmark coordinates."""
    width, height = image.raster.size
    assert label.bounding_box is not None
    x, y, label_width, label_height = label.bounding_box
    if field == "amount":
        top = max(0, y - round(height * 0.09))
        return round(width * 0.18), top, round(width * 0.64), max(1, y - top)
    left_column = x + label_width / 2 > width / 2
    left = round(width * (0.04 if left_column else 0.48))
    right = round(
        width
        * (0.70 if field == "date" and left_column else 0.57 if left_column else 0.97)
    )
    padding = max(round(height * 0.006), label_height // 3)
    top = max(0, y - padding)
    bottom = min(height, y + label_height + padding)
    return left, top, max(1, right - left), max(1, bottom - top)


def _field_matches(field: str, text: str) -> list[str]:
    text = text.translate(_DIGITS)
    if field in {"amount", "currency"}:
        return [match.group(field) for match in _PAIR.finditer(text)]
    if field == "reference_number":
        return re.findall(r"(?<![0-9])[0-9]{12}(?![0-9])", text)
    if field == "date":
        return _DATE_VALUE.findall(text)
    if field == "time":
        return _TIME_VALUE.findall(text)
    if field == "handle":
        return _HANDLE.findall(text)
    if field == "recipient_identifier":
        return re.findall(r"(?<![0-9])01[0125][0-9]{8}(?![0-9])", text)
    return []


def _matching_confidence(line: OcrLine, value: str) -> float | None:
    # Use the matched token's score, not a whole-line mean that includes labels.
    parts = value.split()
    scores = [
        token.confidence
        for token in line.tokens
        if token.text.translate(_DIGITS) in parts and token.confidence is not None
    ]
    return min(scores) if scores else line.confidence


class InstaPayReceiptAdapter:
    adapter_id = "instapay_receipt"
    adapter_version = "v2"

    def __init__(self, provider: TesseractOcrProvider) -> None:
        self.provider = provider

    async def extract(
        self,
        data: bytes,
        *,
        source_event_id: str,
        timestamp: str,
        requested_languages: tuple[str, ...],
        timeout_seconds: float,
        structured_sources: tuple[tuple[FieldCandidate, ...], ...] = (),
    ) -> ReceiptEvidence:
        return await asyncio.to_thread(
            self._extract,
            data,
            source_event_id,
            timestamp,
            requested_languages,
            timeout_seconds,
            structured_sources,
        )

    def _extract(
        self,
        data: bytes,
        source_event_id: str,
        timestamp: str,
        languages: tuple[str, ...],
        timeout: float,
        structured_sources: tuple[tuple[FieldCandidate, ...], ...] = (),
    ) -> ReceiptEvidence:
        # The legacy anchor helpers above remain characterization utilities only.
        # Production admission no longer calls OCR or select_layout.
        from relay.infrastructure.visual_instapay_extraction import extract_registered

        return extract_registered(
            self,
            data,
            source_event_id,
            timestamp,
            languages,
            timeout,
            structured_sources,
        )
