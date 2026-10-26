"""Deterministic receipt classification and InstaPay parser version 1."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from relay.domain.processing import (
    ExtractionField,
    FieldStatus,
    OcrDocument,
    OcrLine,
    OcrToken,
    ParsedReceipt,
    ReceiptType,
    ReviewStatus,
)

PARSER_TYPE = "instapay_receipt"
PARSER_VERSION = "v1"
MAX_LINE_LENGTH = 512
UNLABELED_REFERENCE_MIN_CONFIDENCE = 0.85
STRUCTURAL_VALUE_MIN_CONFIDENCE = 0.75
STRUCTURAL_SUPPORT_MIN_CONFIDENCE = 0.65
PARTICIPANT_TOKEN_MIN_CONFIDENCE = 0.85
MAX_STRUCTURAL_SPAN_NOISE_CHARS = 8

_INSTAPAY_MARKERS = ("instapay", "إنستاباي", "انستاباي")
_STRUCTURAL_INSTAPAY_MARKERS = (*_INSTAPAY_MARKERS, "انستاباى")
_RECEIPT_MARKERS = (
    "transfer",
    "transaction",
    "reference",
    "amount",
    "تحويل",
    "المعاملة",
    "المرجع",
    "المبلغ",
)
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_REFERENCE = re.compile(
    r"(?:reference\s*(?:number|no\.?)|reference(?=\s*[:#\-])|"
    r"\bref(?:\.|(?=\s*[:#\-]))|رقم\s*المرجع|المرجع)"
    r"\s*[:#\-]?\s*([A-Z0-9][A-Z0-9\- ]{3,63})",
    re.IGNORECASE,
)
_AMOUNT_AFTER = re.compile(
    r"(?:amount|المبلغ)\s*[:\-]?\s*"
    r"(?:(EGP|USD|EUR|ج\.?\s*م\.?|جنيه(?:\s+مصري)?))?\s*"
    r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*"
    r"(?:(EGP|USD|EUR|ج\.?\s*م\.?|جنيه(?:\s+مصري)?))?",
    re.IGNORECASE,
)
_AMOUNT_LABEL = re.compile(r"(?:amount|المبلغ)", re.IGNORECASE)
_REFERENCE_LABEL = re.compile(
    r"(?:reference\s*(?:number|no\.?)|reference|\bref\.?|رقم\s*المرجع|المرجع)",
    re.IGNORECASE,
)
_DATE_LABEL = re.compile(r"(?:date|التاريخ)", re.IGNORECASE)
_TIME_LABEL = re.compile(r"(?:time|الوقت)", re.IGNORECASE)
_CURRENCY_TOKEN = r"(?:EGP|USD|EUR|ج\.?\s*م\.?|جنيه(?:\s+مصري)?)"
_AMOUNT_NUMBER = r"[0-9][0-9,]*(?:\.[0-9]{1,2})?"
_AMOUNT_VALUE = re.compile(
    rf"(?:(?P<currency_before>{_CURRENCY_TOKEN})\s*)?"
    rf"(?P<number>{_AMOUNT_NUMBER})"
    rf"(?:\s*(?P<currency_after>{_CURRENCY_TOKEN}))?",
    re.IGNORECASE,
)
_DATE_VALUE = re.compile(
    r"(?:20[0-9]{2}[-/.][01]?[0-9][-/.][0-3]?[0-9]|"
    r"[0-3]?[0-9][-/.][01]?[0-9][-/.]20[0-9]{2}|"
    r"[0-3]?[0-9]\s+[A-Z]{3}\s+20[0-9]{2})",
    re.IGNORECASE,
)
_TIME_VALUE = re.compile(
    r"[0-2]?[0-9]:[0-5][0-9](?::[0-5][0-9])?\s*(?:AM|PM)?",
    re.IGNORECASE,
)
_COMBINED_DATE_TIME = re.compile(
    rf"(?P<date>{_DATE_VALUE.pattern})\s+(?P<time>{_TIME_VALUE.pattern})",
    re.IGNORECASE,
)
_INSTAPAY_HANDLE = re.compile(r"[A-Z0-9._%+\-]{1,64}@instapay\b", re.IGNORECASE)
_BIDI_CONTROLS = (
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)
_NAME = {
    "sender_name": re.compile(
        r"(?:sender|from|اسم\s*المرسل|من)\s*[:\-]\s*(.{1,120})", re.IGNORECASE
    ),
    "recipient_name": re.compile(
        r"(?:recipient|beneficiary|to|المستلم|المستفيد|إلى|الى)\s*[:\-]\s*(.{1,120})",
        re.IGNORECASE,
    ),
    "recipient_identifier": re.compile(
        r"(?:recipient\s*(?:id|account)|beneficiary\s*(?:id|account)|"
        r"حساب\s*المستلم)\s*[:\-]\s*([A-Z0-9*\- ]{3,64})",
        re.IGNORECASE,
    ),
}


@dataclass(frozen=True, slots=True)
class _AmountCandidate:
    original: str
    normalized: str
    currency_original: str | None
    currency: str | None


@dataclass(frozen=True, slots=True)
class _TransactionDetailSequence:
    amount_index: int | None
    reference_index: int
    date_time_index: int
    sender_handle_index: int | None = None
    recipient_identifier_index: int | None = None
    reference_token: OcrToken | None = None
    sender_handle_token: OcrToken | None = None
    date_text: str | None = None
    time_text: str | None = None
    structural_evidence: str | None = None


def normalize_reference(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper().translate(_ARABIC_DIGITS))


def _matching_text(value: str) -> str:
    return value.translate(_ARABIC_DIGITS).translate(
        {ord(character): " " for character in _BIDI_CONTROLS}
    )


def _clean_segment(value: str) -> str:
    return value.strip(f" \t:：#|-{_BIDI_CONTROLS}")


def _parse_amount_candidate(
    value: str, *, require_currency: bool
) -> _AmountCandidate | None:
    original_segment = _clean_segment(value)
    searchable = _clean_segment(_matching_text(original_segment))
    match = _AMOUNT_VALUE.fullmatch(searchable)
    if match is None:
        return None
    currency_group = match.group("currency_before") or match.group("currency_after")
    if require_currency and currency_group is None:
        return None
    original_number = original_segment[match.start("number") : match.end("number")]
    original_currency = (
        original_segment[match.start("currency_before") : match.end("currency_before")]
        if match.group("currency_before")
        else (
            original_segment[
                match.start("currency_after") : match.end("currency_after")
            ]
            if match.group("currency_after")
            else None
        )
    )
    try:
        amount = Decimal(match.group("number").replace(",", ""))
    except InvalidOperation:
        return None
    if not Decimal("0") < amount <= Decimal("9999999999999999.99"):
        return None
    return _AmountCandidate(
        original_number,
        format(amount.quantize(Decimal("0.01")), "f"),
        original_currency,
        _currency(currency_group),
    )


def _label_segments(value: str, match: re.Match[str]) -> tuple[tuple[str, str], ...]:
    return (
        (_clean_segment(value[match.end() :]), "label_before_value"),
        (_clean_segment(value[: match.start()]), "value_before_label"),
    )


def _pair_confidence(first: OcrLine, second: OcrLine) -> float | None:
    values = [
        confidence
        for confidence in (first.confidence, second.confidence)
        if confidence is not None
    ]
    return min(values) if values else None


def _geometrically_related(first: OcrLine, second: OcrLine) -> bool:
    if first.bounding_box is None or second.bounding_box is None:
        return False
    left_a, top_a, width_a, height_a = first.bounding_box
    left_b, top_b, width_b, height_b = second.bounding_box
    right_a, bottom_a = left_a + width_a, top_a + height_a
    right_b, bottom_b = left_b + width_b, top_b + height_b
    vertical_overlap = max(0, min(bottom_a, bottom_b) - max(top_a, top_b))
    horizontal_overlap = max(0, min(right_a, right_b) - max(left_a, left_b))
    horizontal_gap = max(0, max(left_a, left_b) - min(right_a, right_b))
    vertical_gap = max(0, max(top_a, top_b) - min(bottom_a, bottom_b))
    same_row = (
        vertical_overlap >= min(height_a, height_b) * 0.5 and horizontal_gap <= 500
    )
    same_column = (
        vertical_gap <= 48 and horizontal_overlap >= min(width_a, width_b) * 0.3
    )
    return same_row or same_column


def _vertically_near(first: OcrLine, second: OcrLine, maximum_gap: int) -> bool:
    if first.bounding_box is None or second.bounding_box is None:
        return False
    _, top_a, _, height_a = first.bounding_box
    _, top_b, _, height_b = second.bounding_box
    bottom_a, bottom_b = top_a + height_a, top_b + height_b
    return max(0, max(top_a, top_b) - min(bottom_a, bottom_b)) <= maximum_gap


def _labeled_amount_pair(
    lines: tuple[OcrLine, ...],
) -> tuple[_AmountCandidate, float | None, str] | None:
    results: list[tuple[_AmountCandidate, float | None, str]] = []
    for index, label_line in enumerate(lines):
        match = _AMOUNT_LABEL.search(_matching_text(str(label_line.text)))
        if match is None:
            continue
        for segment, relationship in _label_segments(str(label_line.text), match):
            if candidate := _parse_amount_candidate(segment, require_currency=False):
                results.append((candidate, label_line.confidence, relationship))
        for value_index, value_line in enumerate(lines):
            if value_index == index or not _geometrically_related(
                label_line, value_line
            ):
                continue
            if candidate := _parse_amount_candidate(
                str(value_line.text), require_currency=False
            ):
                results.append(
                    (
                        candidate,
                        _pair_confidence(label_line, value_line),
                        "geometry_paired_label_value",
                    )
                )
    unique = {(result[0].normalized, result[0].currency) for result in results}
    return results[0] if len(unique) == 1 else None


def _parse_reference_segment(value: str) -> tuple[str, str] | None:
    original = _clean_segment(value)
    searchable = _matching_text(original)
    if re.fullmatch(r"[A-Z0-9][A-Z0-9\- ]{3,63}", searchable, re.IGNORECASE) is None:
        return None
    normalized = normalize_reference(original)
    return (original, normalized) if 8 <= len(normalized) <= 40 else None


def _labeled_reference_pair(
    lines: tuple[OcrLine, ...],
) -> tuple[str, str, float | None, str] | None:
    results: list[tuple[str, str, float | None, str]] = []
    for index, label_line in enumerate(lines):
        match = _REFERENCE_LABEL.search(_matching_text(str(label_line.text)))
        if match is None:
            continue
        for segment, relationship in _label_segments(str(label_line.text), match):
            if candidate := _parse_reference_segment(segment):
                results.append((*candidate, label_line.confidence, relationship))
        for value_index, value_line in enumerate(lines):
            if value_index == index or not _geometrically_related(
                label_line, value_line
            ):
                continue
            if candidate := _parse_reference_segment(str(value_line.text)):
                results.append(
                    (
                        *candidate,
                        _pair_confidence(label_line, value_line),
                        "geometry_paired_label_value",
                    )
                )
    unique = {result[1] for result in results}
    return results[0] if len(unique) == 1 else None


def _labeled_temporal_value(
    lines: tuple[OcrLine, ...],
    label_pattern: re.Pattern[str],
    value_pattern: re.Pattern[str],
    field: str,
) -> tuple[str, float | None, tuple[str, ...]] | None:
    results: list[tuple[str, float | None, tuple[str, ...]]] = []
    for index, label_line in enumerate(lines):
        match = label_pattern.search(_matching_text(str(label_line.text)))
        if match is None:
            continue
        for segment, relationship in _label_segments(str(label_line.text), match):
            original = _clean_segment(segment)
            if value_pattern.fullmatch(_matching_text(original)):
                results.append(
                    (
                        original,
                        label_line.confidence,
                        (f"{field}_label", relationship),
                    )
                )
        for value_index, value_line in enumerate(lines):
            if value_index == index or not _geometrically_related(
                label_line, value_line
            ):
                continue
            original = _clean_segment(str(value_line.text))
            if value_pattern.fullmatch(_matching_text(original)):
                results.append(
                    (
                        original,
                        _pair_confidence(label_line, value_line),
                        (f"{field}_label", "geometry_paired_label_value"),
                    )
                )
    unique = {_matching_text(result[0]).casefold() for result in results}
    return results[0] if len(unique) == 1 else None


def _contains_marker(value: str, markers: tuple[str, ...]) -> bool:
    folded = _matching_text(value).casefold()
    return any(marker.casefold() in folded for marker in markers)


def _looks_like_phone(value: str) -> bool:
    normalized = normalize_reference(value)
    return normalized.isdigit() and 10 <= len(normalized) <= 15 and normalized[0] == "0"


def _looks_like_structural_amount(value: str) -> bool:
    candidate = _parse_amount_candidate(value, require_currency=False)
    if candidate is None:
        return False
    digits = re.sub(r"[^0-9]", "", _matching_text(candidate.original))
    return 1 <= len(digits) <= 9 and not _looks_like_phone(value)


def _strict_structural_transaction_detail_sequence(
    lines: tuple[OcrLine, ...],
) -> _TransactionDetailSequence | None:
    """Match only the complete unlabeled InstaPay layout observed during P02."""
    amount_indexes = [
        index
        for index, line in enumerate(lines)
        if line.bounding_box is not None
        and (line.confidence or 0.0) >= STRUCTURAL_VALUE_MIN_CONFIDENCE
        and _looks_like_structural_amount(str(line.text))
    ]
    handle_indexes = [
        index
        for index, line in enumerate(lines)
        if line.bounding_box is not None
        and (line.confidence or 0.0) >= STRUCTURAL_SUPPORT_MIN_CONFIDENCE
        and _INSTAPAY_HANDLE.search(_matching_text(str(line.text))) is not None
    ]
    brand_indexes = [
        index
        for index, line in enumerate(lines)
        if line.bounding_box is not None
        and (line.confidence or 0.0) >= STRUCTURAL_SUPPORT_MIN_CONFIDENCE
        and _INSTAPAY_HANDLE.search(_matching_text(str(line.text))) is None
        and _contains_marker(str(line.text), _STRUCTURAL_INSTAPAY_MARKERS)
    ]
    phone_indexes = [
        index
        for index, line in enumerate(lines)
        if line.bounding_box is not None
        and (line.confidence or 0.0) >= STRUCTURAL_VALUE_MIN_CONFIDENCE
        and _looks_like_phone(str(line.text))
    ]
    reference_indexes = [
        index
        for index, line in enumerate(lines)
        if line.bounding_box is not None
        and (line.confidence or 0.0) >= UNLABELED_REFERENCE_MIN_CONFIDENCE
        and re.fullmatch(r"[0-9]{12}", _clean_segment(_matching_text(str(line.text))))
        and not _looks_like_phone(str(line.text))
    ]
    if len(reference_indexes) != 1:
        return None
    reference_index = reference_indexes[0]
    date_time_index = reference_index + 1
    if date_time_index >= len(lines):
        return None
    date_time_line = lines[date_time_index]
    if (
        date_time_line.bounding_box is None
        or _COMBINED_DATE_TIME.fullmatch(
            _clean_segment(_matching_text(str(date_time_line.text)))
        )
        is None
    ):
        return None

    eligible: list[_TransactionDetailSequence] = []
    for handle_index in handle_indexes:
        nearby_brands = [
            brand_index
            for brand_index in brand_indexes
            if abs(brand_index - handle_index) == 1
            and _vertically_near(lines[handle_index], lines[brand_index], 220)
        ]
        if len(nearby_brands) != 1:
            continue
        brand_index = nearby_brands[0]
        participant_phones = [
            phone_index
            for phone_index in phone_indexes
            if max(handle_index, brand_index) < phone_index < reference_index
        ]
        earlier_amounts = [
            amount_index
            for amount_index in amount_indexes
            if amount_index < min(handle_index, brand_index)
        ]
        if len(participant_phones) != 1 or len(earlier_amounts) != 1:
            continue
        amount_index = earlier_amounts[0]
        phone_index = participant_phones[0]
        if not (
            _vertical_order(lines[amount_index], lines[handle_index])
            and _vertical_order(lines[handle_index], lines[phone_index])
            and _vertical_order(lines[brand_index], lines[phone_index])
            and _vertical_order(lines[phone_index], lines[reference_index])
            and _vertical_order(lines[reference_index], date_time_line)
            and _vertically_near(lines[reference_index], date_time_line, 120)
        ):
            continue
        eligible.append(
            _TransactionDetailSequence(
                amount_index,
                reference_index,
                date_time_index,
                handle_index,
                phone_index,
                structural_evidence="strict_structural_receipt_sequence",
            )
        )
    return eligible[0] if len(eligible) == 1 else None


def _bounded_date_time_span(line: OcrLine) -> tuple[str, str] | None:
    searchable = _matching_text(str(line.text))
    matches = list(_COMBINED_DATE_TIME.finditer(searchable))
    if len(matches) != 1:
        return None
    match = matches[0]
    prefix = _clean_segment(searchable[: match.start()])
    suffix = _clean_segment(searchable[match.end() :])
    if (
        len(prefix) > MAX_STRUCTURAL_SPAN_NOISE_CHARS
        or len(suffix) > MAX_STRUCTURAL_SPAN_NOISE_CHARS
    ):
        return None
    return match.group("date"), match.group("time")


def _qualifying_reference_tokens(
    lines: tuple[OcrLine, ...],
) -> list[tuple[int, OcrToken]]:
    candidates: list[tuple[int, OcrToken]] = []
    for line_index, line in enumerate(lines):
        for token in line.tokens:
            original = _clean_segment(str(token.text))
            comparable = _clean_segment(_matching_text(original))
            if (
                token.bounding_box is not None
                and (token.confidence or 0.0) >= UNLABELED_REFERENCE_MIN_CONFIDENCE
                and re.fullmatch(r"[0-9]{12}", comparable) is not None
                and not _looks_like_phone(original)
            ):
                candidates.append((line_index, token))
    return candidates


def _has_participant_name_block(
    lines: tuple[OcrLine, ...], handle_index: int, reference_index: int
) -> bool:
    for line in lines[handle_index + 1 : reference_index]:
        if line.bounding_box is None or len(line.tokens) < 2:
            continue
        alphabetic_characters = sum(character.isalpha() for character in line.text)
        if alphabetic_characters < 6:
            continue
        if any(
            (token.confidence or 0.0) >= PARTICIPANT_TOKEN_MIN_CONFIDENCE
            and sum(character.isalpha() for character in token.text) >= 4
            for token in line.tokens
        ):
            return True
    return False


def _compact_structural_transaction_detail_sequence(
    lines: tuple[OcrLine, ...],
) -> _TransactionDetailSequence | None:
    """Match a token-backed receipt sequence when surrounding OCR is noisy."""
    references = _qualifying_reference_tokens(lines)
    if len(references) != 1:
        return None
    reference_index, reference_token = references[0]

    date_times = [
        (index, value)
        for index, line in enumerate(lines)
        if line.bounding_box is not None
        and (value := _bounded_date_time_span(line)) is not None
    ]
    if len(date_times) != 1:
        return None
    date_time_index, (date_text, time_text) = date_times[0]
    if date_time_index != reference_index + 1:
        return None

    handles = [
        (line_index, token)
        for line_index, line in enumerate(lines)
        if line.bounding_box is not None
        for token in line.tokens
        if token.bounding_box is not None
        and (token.confidence or 0.0) >= STRUCTURAL_SUPPORT_MIN_CONFIDENCE
        and _INSTAPAY_HANDLE.fullmatch(_clean_segment(_matching_text(str(token.text))))
        is not None
    ]
    if len(handles) != 1:
        return None
    handle_index, handle_token = handles[0]
    reference_line = lines[reference_index]
    date_time_line = lines[date_time_index]
    if not (
        handle_index < reference_index
        and _vertical_order(lines[handle_index], reference_line)
        and _vertical_order(reference_line, date_time_line)
        and _vertically_near(reference_line, date_time_line, 120)
        and _has_participant_name_block(lines, handle_index, reference_index)
    ):
        return None
    return _TransactionDetailSequence(
        None,
        reference_index,
        date_time_index,
        sender_handle_index=handle_index,
        reference_token=reference_token,
        sender_handle_token=handle_token,
        date_text=date_text,
        time_text=time_text,
        structural_evidence="compact_structural_receipt_sequence",
    )


def _observed_transaction_detail_sequence(
    lines: tuple[OcrLine, ...],
) -> _TransactionDetailSequence | None:
    amount_indexes = [
        index
        for index, line in enumerate(lines)
        if _parse_amount_candidate(str(line.text), require_currency=True) is not None
        and line.bounding_box is not None
    ]
    brand_indexes = [
        index
        for index, line in enumerate(lines)
        if _contains_marker(str(line.text), _INSTAPAY_MARKERS)
        and line.bounding_box is not None
    ]
    status_indexes = [
        index
        for index, line in enumerate(lines)
        if _contains_marker(
            str(line.text),
            ("successful", "success", "completed", "تم التحويل", "ناجحة"),
        )
        and line.bounding_box is not None
    ]
    date_time_indexes = [
        index
        for index, line in enumerate(lines)
        if _COMBINED_DATE_TIME.fullmatch(_clean_segment(_matching_text(str(line.text))))
        and line.bounding_box is not None
    ]
    reference_indexes = [
        index
        for index, line in enumerate(lines)
        if line.bounding_box is not None
        and (line.confidence or 0.0) >= UNLABELED_REFERENCE_MIN_CONFIDENCE
        and re.fullmatch(r"[0-9]{12}", _clean_segment(_matching_text(str(line.text))))
        and not _looks_like_phone(str(line.text))
    ]
    eligible: list[_TransactionDetailSequence] = []
    for reference_index in reference_indexes:
        date_time_index = reference_index + 1
        if date_time_index not in date_time_indexes:
            continue
        earlier_amounts = [index for index in amount_indexes if index < reference_index]
        if not earlier_amounts:
            continue
        amount_index = earlier_amounts[-1]
        if not any(index < reference_index for index in brand_indexes):
            continue
        if not any(index < reference_index for index in status_indexes):
            continue
        amount_line = lines[amount_index]
        reference_line = lines[reference_index]
        date_time_line = lines[date_time_index]
        if not (
            _vertical_order(amount_line, reference_line)
            and _vertical_order(reference_line, date_time_line)
            and _vertically_near(reference_line, date_time_line, 120)
        ):
            continue
        eligible.append(
            _TransactionDetailSequence(amount_index, reference_index, date_time_index)
        )
    return eligible[0] if len(eligible) == 1 and len(reference_indexes) == 1 else None


def _vertical_order(first: OcrLine, second: OcrLine) -> bool:
    if first.bounding_box is None or second.bounding_box is None:
        return False
    return first.bounding_box[1] < second.bounding_box[1]


def classify_receipt(document: OcrDocument) -> tuple[ReceiptType, tuple[str, ...]]:
    folded = document.normalized_text.casefold()
    evidence: list[str] = []
    if any(marker.casefold() in folded for marker in _INSTAPAY_MARKERS):
        evidence.append("instapay_brand_marker")
    if any(marker.casefold() in folded for marker in _RECEIPT_MARKERS):
        evidence.append("receipt_field_marker")
    receipt_type = ReceiptType.UNKNOWN_RECEIPT
    if len(evidence) == 2:
        receipt_type = ReceiptType.INSTAPAY_TRANSFER_RECEIPT
    else:
        lines = tuple(
            line for line in document.lines if len(line.text) <= MAX_LINE_LENGTH
        )
        if _strict_structural_transaction_detail_sequence(lines) is not None:
            evidence.append("strict_structural_receipt_sequence")
            receipt_type = ReceiptType.INSTAPAY_TRANSFER_RECEIPT
        elif _compact_structural_transaction_detail_sequence(lines) is not None:
            evidence.append("compact_structural_receipt_sequence")
            receipt_type = ReceiptType.INSTAPAY_TRANSFER_RECEIPT
    return receipt_type, tuple(evidence)


class InstaPayReceiptParser:
    parser_type = PARSER_TYPE
    parser_version = PARSER_VERSION

    def parse(self, document: OcrDocument) -> ParsedReceipt:
        receipt_type, classification = classify_receipt(document)
        lines = tuple(
            line for line in document.lines if len(line.text) <= MAX_LINE_LENGTH
        )
        if receipt_type == ReceiptType.UNKNOWN_RECEIPT:
            return self._unknown(classification)

        sequence = _observed_transaction_detail_sequence(lines)
        if sequence is None:
            sequence = _strict_structural_transaction_detail_sequence(lines)
        if sequence is None:
            sequence = _compact_structural_transaction_detail_sequence(lines)
        reference = self._reference(lines, sequence)
        amount, currency = self._amount(lines)
        occurred, date_field, time_field = self._date_time(lines, sequence)
        names = list(
            self._named(field, pattern, lines) for field, pattern in _NAME.items()
        )
        if names[0].status == FieldStatus.MISSING and sequence is not None:
            names[0] = self._sender_handle(lines, sequence)
        if names[2].status == FieldStatus.MISSING and sequence is not None:
            names[2] = self._recipient_identifier(lines, sequence)
        transfer = self._transfer_status(lines)
        fields = (reference, amount, currency, date_field, time_field, *names, transfer)
        return ParsedReceipt(
            receipt_type=receipt_type,
            parser_version=self.parser_version,
            classification_evidence=classification,
            fields=fields,
            # OCR confidence describes legibility, not transaction authenticity.
            # Uploaded receipts remain review-required until a human accepts them.
            review_status=ReviewStatus.NEEDS_REVIEW,
            amount=Decimal(amount.value) if amount.value else None,
            currency=currency.value,
            transaction_occurred_at=occurred,
            timezone_state="UNKNOWN",
            reference_original=reference.original_text,
            reference_normalized=reference.value,
            sender_name=names[0].value,
            recipient_name=names[1].value,
            recipient_identifier=names[2].value,
            transfer_status=transfer.value,
        )

    @staticmethod
    def _confidence(
        value: float | None, *, valid: bool = True
    ) -> tuple[float, FieldStatus]:
        score = max(0.0, min(1.0, value if value is not None else 0.5))
        if not valid:
            return min(score, 0.49), FieldStatus.INVALID
        return (
            (score, FieldStatus.CONFIDENT)
            if score >= 0.75
            else (
                score,
                FieldStatus.UNCERTAIN,
            )
        )

    def _reference(
        self,
        lines: tuple[OcrLine, ...],
        sequence: _TransactionDetailSequence | None,
    ) -> ExtractionField:
        for line in lines:
            text = str(line.text)
            match = _REFERENCE.search(_matching_text(text))
            if match:
                original = text[match.start(1) : match.end(1)].strip()
                normalized = normalize_reference(original)
                valid = 8 <= len(normalized) <= 40
                score, status = self._confidence(line.confidence, valid=valid)
                return ExtractionField(
                    "reference_number",
                    normalized if valid else None,
                    original,
                    score,
                    status,
                    ("reference_label", "alphanumeric_normalization"),
                )

        paired = _labeled_reference_pair(lines)
        if paired is not None:
            original, normalized, confidence, relationship = paired
            score, status = self._confidence(confidence)
            return ExtractionField(
                "reference_number",
                normalized,
                original,
                score,
                status,
                (
                    "reference_label",
                    relationship,
                    "alphanumeric_normalization",
                ),
            )

        if sequence is not None:
            if sequence.reference_token is not None:
                token = sequence.reference_token
                original = _clean_segment(str(token.text))
                normalized = normalize_reference(original)
                score, status = self._confidence(token.confidence)
                return ExtractionField(
                    "reference_number",
                    normalized,
                    original,
                    score,
                    status,
                    (
                        "compact_structural_receipt_sequence",
                        "reference_token_span",
                        "token_level_ocr_confidence",
                        "observed_12_digit_reference",
                        "high_ocr_confidence",
                        "alphanumeric_normalization",
                    ),
                )
            line = lines[sequence.reference_index]
            original = _clean_segment(str(line.text))
            normalized = normalize_reference(original)
            score, status = self._confidence(line.confidence)
            return ExtractionField(
                "reference_number",
                normalized,
                original,
                score,
                status,
                (
                    "transaction_detail_sequence",
                    "observed_12_digit_reference",
                    "high_ocr_confidence",
                    "alphanumeric_normalization",
                ),
            )
        return _missing("reference_number")

    def _amount(
        self, lines: tuple[OcrLine, ...]
    ) -> tuple[ExtractionField, ExtractionField]:
        for line in lines:
            original_text = str(line.text)
            text = _matching_text(original_text)
            match = _AMOUNT_AFTER.search(text)
            if not match:
                continue
            original = original_text[match.start(2) : match.end(2)]
            amount_tokens = re.findall(r"[0-9][0-9,]*(?:\.[0-9]{1,2})?", text)
            ambiguous = len(amount_tokens) > 1
            try:
                value = Decimal(original.replace(",", ""))
                valid = Decimal("0") < value <= Decimal("9999999999999999.99")
                normalized = (
                    format(value.quantize(Decimal("0.01")), "f") if valid else None
                )
            except InvalidOperation:
                valid, normalized = False, None
            score, status = self._confidence(line.confidence, valid=valid)
            if ambiguous and valid:
                score, status = min(score, 0.74), FieldStatus.UNCERTAIN
            currency_value = _currency(match.group(1) or match.group(3))
            currency_score, currency_status = self._confidence(
                line.confidence, valid=currency_value is not None
            )
            if ambiguous and currency_value is not None:
                currency_score = min(currency_score, 0.74)
                currency_status = FieldStatus.UNCERTAIN
            return (
                ExtractionField(
                    "amount", normalized, original, score, status, ("amount_label",)
                ),
                ExtractionField(
                    "currency",
                    currency_value,
                    match.group(1) or match.group(3),
                    currency_score,
                    currency_status,
                    ("explicit_currency_marker",),
                ),
            )

        paired = _labeled_amount_pair(lines)
        if paired is not None:
            candidate, confidence, relationship = paired
            return self._amount_fields(
                candidate,
                confidence,
                ("amount_label", relationship),
            )

        unlabeled: list[tuple[_AmountCandidate, float | None]] = []
        for line in lines:
            unlabeled_candidate = _parse_amount_candidate(
                str(line.text), require_currency=True
            )
            if unlabeled_candidate is not None:
                unlabeled.append((unlabeled_candidate, line.confidence))
        if len(unlabeled) == 1:
            candidate, confidence = unlabeled[0]
            return self._amount_fields(
                candidate,
                confidence,
                ("explicit_currency_marker", "same_line_value_currency_pair"),
            )
        return _missing("amount"), _missing("currency")

    def _amount_fields(
        self,
        candidate: _AmountCandidate,
        confidence: float | None,
        evidence: tuple[str, ...],
    ) -> tuple[ExtractionField, ExtractionField]:
        amount_score, amount_status = self._confidence(confidence)
        currency_score, currency_status = self._confidence(
            confidence, valid=candidate.currency is not None
        )
        currency_evidence = (
            evidence
            if "explicit_currency_marker" in evidence
            else (*evidence, "explicit_currency_marker")
        )
        return (
            ExtractionField(
                "amount",
                candidate.normalized,
                candidate.original,
                amount_score,
                amount_status,
                evidence,
            ),
            ExtractionField(
                "currency",
                candidate.currency,
                candidate.currency_original,
                currency_score,
                currency_status,
                currency_evidence if candidate.currency is not None else evidence,
            ),
        )

    def _date_time(
        self,
        lines: tuple[OcrLine, ...],
        sequence: _TransactionDetailSequence | None,
    ) -> tuple[datetime | None, ExtractionField, ExtractionField]:
        date_match = _labeled_temporal_value(
            lines, _DATE_LABEL, _DATE_VALUE, "transaction_date"
        )
        time_match = _labeled_temporal_value(
            lines, _TIME_LABEL, _TIME_VALUE, "transaction_time"
        )
        if sequence is not None and (date_match is None or time_match is None):
            line = lines[sequence.date_time_index]
            combined = (
                (sequence.date_text, sequence.time_text)
                if sequence.date_text is not None and sequence.time_text is not None
                else None
            )
            if combined is None:
                match = _COMBINED_DATE_TIME.fullmatch(
                    _clean_segment(_matching_text(str(line.text)))
                )
                combined = (
                    (match.group("date"), match.group("time"))
                    if match is not None
                    else None
                )
            if combined is not None:
                evidence: tuple[str, ...] = (
                    "observed_datetime_format",
                    "transaction_detail_sequence",
                )
                if (
                    sequence.structural_evidence
                    == "compact_structural_receipt_sequence"
                ):
                    evidence = (*evidence, "bounded_datetime_span")
                if date_match is None:
                    date_match = (combined[0], line.confidence, evidence)
                if time_match is None:
                    time_match = (combined[1], line.confidence, evidence)
        parsed_date = _parse_date(date_match[0]) if date_match else None
        parsed_time = _parse_time(time_match[0]) if time_match else None
        date_field = _temporal_field(
            "transaction_date", date_match, parsed_date is not None
        )
        time_field = _temporal_field(
            "transaction_time", time_match, parsed_time is not None
        )
        occurred = (
            datetime.combine(parsed_date, parsed_time)
            if parsed_date is not None and parsed_time is not None
            else None
        )
        return occurred, date_field, time_field

    def _sender_handle(
        self,
        lines: tuple[OcrLine, ...],
        sequence: _TransactionDetailSequence,
    ) -> ExtractionField:
        if sequence.sender_handle_index is not None:
            line = lines[sequence.sender_handle_index]
            handle_token = sequence.sender_handle_token
            match = _INSTAPAY_HANDLE.search(_matching_text(str(line.text)))
            if match is not None or handle_token is not None:
                if handle_token is not None:
                    original = _clean_segment(str(handle_token.text))
                    confidence = handle_token.confidence
                else:
                    assert match is not None
                    original = str(line.text)[match.start() : match.end()]
                    confidence = line.confidence
                score, status = self._confidence(confidence)
                structural_evidence = (
                    sequence.structural_evidence or "strict_structural_receipt_sequence"
                )
                if structural_evidence == "compact_structural_receipt_sequence":
                    score, status = min(score, 0.74), FieldStatus.UNCERTAIN
                return ExtractionField(
                    "sender_name",
                    original,
                    original,
                    score,
                    status,
                    ("instapay_handle", structural_evidence),
                )
        brand_indexes = [
            index
            for index, line in enumerate(lines[: sequence.reference_index])
            if _contains_marker(str(line.text), _INSTAPAY_MARKERS)
        ]
        for index, line in enumerate(lines[: sequence.reference_index]):
            match = _INSTAPAY_HANDLE.search(_matching_text(str(line.text)))
            if (
                match is None
                or sequence.amount_index is None
                or sequence.amount_index >= index
            ):
                continue
            adjacent_brand = next(
                (
                    brand_index
                    for brand_index in brand_indexes
                    if abs(brand_index - index) == 1
                    and _vertically_near(lines[index], lines[brand_index], 220)
                ),
                None,
            )
            if adjacent_brand is None:
                continue
            original = str(line.text)[match.start() : match.end()]
            score, status = self._confidence(line.confidence)
            return ExtractionField(
                "sender_name",
                original,
                original,
                score,
                status,
                ("instapay_handle", "transaction_detail_sequence"),
            )
        return _missing("sender_name")

    def _recipient_identifier(
        self,
        lines: tuple[OcrLine, ...],
        sequence: _TransactionDetailSequence,
    ) -> ExtractionField:
        if sequence.recipient_identifier_index is None:
            return _missing("recipient_identifier")
        line = lines[sequence.recipient_identifier_index]
        original = _clean_segment(str(line.text))
        normalized = normalize_reference(original)
        score, status = self._confidence(
            line.confidence, valid=_looks_like_phone(original)
        )
        return ExtractionField(
            "recipient_identifier",
            normalized if status != FieldStatus.INVALID else None,
            original,
            score,
            status,
            (
                "phone_shaped_participant_identifier",
                "strict_structural_receipt_sequence",
                "participant_position_before_reference",
            ),
        )

    def _named(
        self, field: str, pattern: re.Pattern[str], lines: tuple[OcrLine, ...]
    ) -> ExtractionField:
        for line in lines:
            match = pattern.search(str(line.text))
            if match:
                original = " ".join(match.group(1).split())
                valid = 1 <= len(original) <= 120 and not any(
                    ord(c) < 32 for c in original
                )
                score, status = self._confidence(line.confidence, valid=valid)
                return ExtractionField(
                    field,
                    original if valid else None,
                    original,
                    score,
                    status,
                    (f"{field}_label",),
                )
        return _missing(field)

    def _transfer_status(self, lines: tuple[OcrLine, ...]) -> ExtractionField:
        markers = {
            "SUCCESS": ("successful", "success", "completed", "تم التحويل", "ناجحة"),
            "FAILED": ("failed", "declined", "فشل", "مرفوض"),
            "PENDING": ("pending", "processing", "قيد التنفيذ"),
        }
        for line in lines:
            folded = str(line.text).casefold()
            for value, needles in markers.items():
                if any(needle.casefold() in folded for needle in needles):
                    score, status = self._confidence(line.confidence)
                    return ExtractionField(
                        "transfer_status",
                        value,
                        value,
                        score,
                        status,
                        ("status_marker",),
                    )
        return _missing("transfer_status")

    def _unknown(self, evidence: tuple[str, ...]) -> ParsedReceipt:
        fields = tuple(
            _missing(name)
            for name in (
                "reference_number",
                "amount",
                "currency",
                "transaction_date",
                "transaction_time",
                "sender_name",
                "recipient_name",
                "recipient_identifier",
                "transfer_status",
            )
        )
        return ParsedReceipt(
            ReceiptType.UNKNOWN_RECEIPT,
            self.parser_version,
            evidence,
            fields,
            ReviewStatus.REJECTED,
            None,
            None,
            None,
            "UNKNOWN",
            None,
            None,
            None,
            None,
            None,
            None,
        )


def _currency(value: str | None) -> str | None:
    if value is None:
        return None
    compact = re.sub(r"[.\s]", "", value).casefold()
    if compact in {"egp", "جم", "جنيه", "جنيهمصري"}:
        return "EGP"
    upper = compact.upper()
    return upper if upper in {"USD", "EUR"} else None


def _missing(field: str) -> ExtractionField:
    return ExtractionField(field, None, None, 0.0, FieldStatus.MISSING, ())


def _parse_date(value: str) -> date | None:
    normalized = _matching_text(value).replace(".", "/").replace("-", "/")
    for pattern in ("%Y/%m/%d", "%d/%m/%Y", "%d %b %Y"):
        try:
            return datetime.strptime(normalized, pattern).date()
        except ValueError:
            pass
    return None


def _parse_time(value: str) -> time | None:
    normalized = " ".join(_matching_text(value).upper().split())
    for pattern in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        try:
            return datetime.strptime(normalized, pattern).time()
        except ValueError:
            pass
    return None


def _temporal_field(
    name: str,
    match: tuple[str, float | None, tuple[str, ...]] | None,
    valid: bool,
) -> ExtractionField:
    if match is None:
        return _missing(name)
    score, status = InstaPayReceiptParser._confidence(match[1], valid=valid)
    return ExtractionField(
        name, match[0] if valid else None, match[0], score, status, match[2]
    )
