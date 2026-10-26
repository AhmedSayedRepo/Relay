"""Conservative field evidence extraction and merging for the offline spike."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tools.ocr_spike.models import Box

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_CURRENCY = r"(?:E[GC]P|ج\.?\s*م\.?|جنيه(?:\s+مصري)?)"
_NUMBER = r"[0-9٠-٩۰-۹][0-9٠-٩۰-۹,]*(?:\.[0-9٠-٩۰-۹]{1,2})?"
_AMOUNT_PAIR = re.compile(
    rf"^\s*(?:(?P<currency_before>{_CURRENCY})\s+)?(?P<number>{_NUMBER})"
    rf"(?:\s+(?P<currency_after>{_CURRENCY}))?\s*$",
    re.IGNORECASE,
)
_REFERENCE = re.compile(r"(?<!\d)([1-9][0-9]{11})(?!\d)")
_PHONE = re.compile(r"(?<!\d)(0(?:10|11|12|15)[0-9]{8})(?!\d)")
_DATE_TIME = re.compile(
    r"(?<!\w)([0-3]?\d\s+[A-Za-z]{3}\s+20\d{2})\s+"
    r"((?:0?[1-9]|1[0-2]):[0-5]\d\s+(?:AM|PM))(?!\w)",
    re.IGNORECASE,
)
_HANDLE = re.compile(r"(?<![\w.])([\w.+-]{3,}@instapay)(?![\w.])", re.IGNORECASE)
_STATUS = re.compile(
    r"(?:transfer\s+successful|تمت?\s+العملية\s+بنجاح|تم\s+التحويل\s+بنجاح)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class EvidenceToken:
    text: str
    confidence: float
    box: Box


@dataclass(frozen=True, slots=True)
class CropEvidence:
    source_pass: str
    original_text: str
    confidence: float
    crop_geometry: Box
    tokens: tuple[EvidenceToken, ...]
    source_region: str = "independent_visual_band"
    structural_evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FieldCandidate:
    field: str
    value: str
    normalized: str
    original_ocr_text: str
    confidence: float
    source_pass: str
    crop_geometry: Box
    token_geometry: tuple[Box, ...]
    supporting_structural_evidence: tuple[str, ...]
    conflicts: tuple[str, ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "field": self.field,
            "value": self.value,
            "normalized": self.normalized,
            "original_ocr_text": self.original_ocr_text,
            "confidence": self.confidence,
            "source_pass": self.source_pass,
            "crop_geometry": self.crop_geometry.as_list(),
            "token_geometry": [box.as_list() for box in self.token_geometry],
            "supporting_structural_evidence": list(self.supporting_structural_evidence),
            "conflicts": list(self.conflicts),
        }


@dataclass(frozen=True, slots=True)
class MergedField:
    selected: FieldCandidate | None
    candidates: tuple[FieldCandidate, ...]
    conflict: bool
    reinforcement_count: int

    def as_json(self) -> dict[str, object]:
        return {
            "selected": self.selected.as_json() if self.selected else None,
            "candidates": [candidate.as_json() for candidate in self.candidates],
            "conflict": self.conflict,
            "reinforcement_count": self.reinforcement_count,
        }


def _candidate(
    evidence: CropEvidence,
    field: str,
    value: str,
    normalized: str,
    confidence: float,
    supporting: tuple[str, ...],
) -> FieldCandidate:
    return FieldCandidate(
        field,
        value,
        normalized,
        evidence.original_text,
        confidence,
        evidence.source_pass,
        evidence.crop_geometry,
        tuple(token.box for token in evidence.tokens),
        tuple(dict.fromkeys((*supporting, *evidence.structural_evidence))),
    )


def extract_candidates(evidence: CropEvidence) -> tuple[FieldCandidate, ...]:
    comparable = evidence.original_text.translate(_ARABIC_DIGITS).strip()
    candidates: list[FieldCandidate] = []
    amount_match = _AMOUNT_PAIR.fullmatch(comparable)
    if amount_match is not None:
        currency_before = amount_match.group("currency_before")
        currency_after = amount_match.group("currency_after")
        number = amount_match.group("number")
        if (currency_before is None) != (currency_after is None):
            digits = re.sub(r"[^0-9]", "", number)
            token_confidence = min(
                (token.confidence for token in evidence.tokens),
                default=evidence.confidence,
            )
            amount_token_confidence = max(
                (
                    token.confidence
                    for token in evidence.tokens
                    if digits
                    and digits
                    in re.sub(r"[^0-9]", "", token.text.translate(_ARABIC_DIGITS))
                ),
                default=evidence.confidence,
            )
            currency_text = currency_before or currency_after or ""
            latin_currency_confusion = bool(
                re.fullmatch(r"ECP", currency_text, re.IGNORECASE)
            )
            currency_token_confidence = max(
                (
                    token.confidence
                    for token in evidence.tokens
                    if re.fullmatch(r"E[GC]P", token.text, re.IGNORECASE)
                ),
                default=evidence.confidence,
            )
            guarded_confusion = bool(
                latin_currency_confusion
                and amount_token_confidence >= 0.85
                and currency_token_confidence >= 0.40
                and {
                    "upper_receipt_region",
                    "above_handle_anchor",
                    "transfer_amount_label",
                }.intersection(evidence.structural_evidence)
            )
            if 1 <= len(digits) <= 9 and (
                token_confidence >= 0.70 or guarded_confusion
            ):
                normalized_amount = number.replace(",", "")
                normalized_currency = (
                    "EGP"
                    if re.search(r"E[GC]P|ج|جنيه", currency_text, re.IGNORECASE)
                    else currency_text.upper()
                )
                currency_support = (
                    (
                        "ocr_currency_confusion_normalized",
                        "high_confidence_amount_with_position_guard",
                    )
                    if latin_currency_confusion
                    else ()
                )
                candidates.extend(
                    (
                        _candidate(
                            evidence,
                            "amount",
                            number,
                            normalized_amount,
                            token_confidence,
                            ("explicit_adjacent_currency", *currency_support),
                        ),
                        _candidate(
                            evidence,
                            "currency",
                            currency_text,
                            normalized_currency,
                            token_confidence,
                            ("explicit_adjacent_amount", *currency_support),
                        ),
                    )
                )

    reference_matches = tuple(_REFERENCE.finditer(comparable))
    phone_values = {match.group(1) for match in _PHONE.finditer(comparable)}
    for match in reference_matches:
        value = match.group(1)
        token_confidence = max(
            (
                token.confidence
                for token in evidence.tokens
                if value in token.text.translate(_ARABIC_DIGITS)
            ),
            default=evidence.confidence,
        )
        if value not in phone_values and token_confidence >= 0.85:
            candidates.append(
                _candidate(
                    evidence,
                    "reference",
                    value,
                    value,
                    token_confidence,
                    ("twelve_digit_non_phone_span",),
                )
            )

    date_time_matches = tuple(_DATE_TIME.finditer(comparable))
    if len(date_time_matches) == 1:
        match = date_time_matches[0]
        candidates.extend(
            (
                _candidate(
                    evidence,
                    "date",
                    match.group(1),
                    match.group(1).title(),
                    evidence.confidence,
                    ("rigid_datetime_span",),
                ),
                _candidate(
                    evidence,
                    "time",
                    match.group(2),
                    match.group(2).upper(),
                    evidence.confidence,
                    ("rigid_datetime_span",),
                ),
            )
        )
    for match in _HANDLE.finditer(comparable):
        value = match.group(1)
        candidates.append(
            _candidate(
                evidence,
                "handle",
                value,
                value.casefold(),
                evidence.confidence,
                ("instapay_handle_syntax",),
            )
        )
    for value in phone_values:
        candidates.append(
            _candidate(
                evidence,
                "recipient_identifier",
                value,
                value,
                evidence.confidence,
                ("egyptian_mobile_syntax",),
            )
        )
    status_match = _STATUS.search(comparable)
    if status_match:
        candidates.append(
            _candidate(
                evidence,
                "status",
                status_match.group(0),
                "SUCCESS",
                evidence.confidence,
                ("explicit_status_text",),
            )
        )
    return tuple(candidates)


class EvidenceMerger:
    """Select only unanimous normalized values and retain every conflict."""

    def merge(self, candidates: tuple[FieldCandidate, ...]) -> dict[str, MergedField]:
        grouped: dict[str, list[FieldCandidate]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate.field, []).append(candidate)
        result: dict[str, MergedField] = {}
        for field, field_candidates in grouped.items():
            by_value: dict[str, list[FieldCandidate]] = {}
            for candidate in field_candidates:
                by_value.setdefault(candidate.normalized, []).append(candidate)
            selectable = field_candidates
            if field in {"amount", "currency"}:
                required = {
                    "upper_receipt_region",
                    "above_handle_anchor",
                    "transfer_amount_label",
                }
                selectable = [
                    candidate
                    for candidate in field_candidates
                    if required.intersection(candidate.supporting_structural_evidence)
                ]
            selectable_by_value: dict[str, list[FieldCandidate]] = {}
            for candidate in selectable:
                selectable_by_value.setdefault(candidate.normalized, []).append(
                    candidate
                )
            conflict = len(selectable_by_value) > 1
            selected = None
            reinforcement = 0
            if len(selectable_by_value) == 1:
                values = next(iter(selectable_by_value.values()))
                selected = max(values, key=lambda item: item.confidence)
                reinforcement = len({item.source_pass for item in values})
            result[field] = MergedField(
                selected,
                tuple(field_candidates),
                conflict,
                reinforcement,
            )
        return result


def has_sufficient_receipt_evidence(
    candidates: tuple[FieldCandidate, ...],
    *,
    brand_visible: bool,
) -> bool:
    """Offline-only conservative evidence check; it is not production classification."""
    fields = {candidate.field for candidate in candidates}
    transaction_sequence = {"reference", "date", "time"}.issubset(fields)
    instapay_identity = brand_visible or "handle" in fields
    independent_support = bool(
        {"recipient_identifier", "status"}.intersection(fields)
        or {"amount", "currency"}.issubset(fields)
    )
    return transaction_sequence and instapay_identity and independent_support
