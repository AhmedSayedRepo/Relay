"""Explicit existing SMS evidence and conservative association, not duplicates."""

import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

from relay.domain.evidence import EvidenceKind, EvidenceSource, FieldCandidate

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_LABELS = {
    "reference_number": r"Reference(?: number)?|الرقم المرجعي|رقم المرجع",
    "amount": r"Transfer amount|Amount transferred|مبلغ التحويل",
    "currency": r"Currency|العملة",
    "date": r"Date|التاريخ",
    "time": r"Time|الوقت",
    "handle": r"Sender handle|حساب المرسل",
    "recipient_identifier": r"Recipient identifier|رقم المستلم",
}


def normalize_field(field: str, value: str) -> str | None:
    """Supported explicit values only; no digit repair or timezone inference."""
    text = unicodedata.normalize("NFKC", value).translate(_DIGITS).strip()
    text = re.sub(r"[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", text)
    if field == "reference_number":
        return text if re.fullmatch(r"[1-9][0-9]{11}", text) else None
    if field == "amount":
        if re.fullmatch(r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?", text) is None:
            return None
        try:
            amount = Decimal(text.replace(",", ""))
        except InvalidOperation:
            return None
        if not amount.is_finite() or not Decimal("0") < amount <= Decimal(
            "9999999999999999.99"
        ):
            return None
        return format(amount.normalize(), "f")
    if field == "currency":
        if text.upper() in {"EGP", "USD", "EUR"}:
            return text.upper()
        # Bare "pound" does not identify an ISO currency; receipt type, country
        # assumptions and sender identity must not silently turn it into EGP.
        if re.fullmatch(r"ج\.?\s*م\.?|جنيه مصري", text):
            return "EGP"
        return None
    if field in {"date", "time"}:
        formats = (
            ("%d %b %Y", "%Y-%m-%d", "%d/%m/%Y")
            if field == "date"
            else ("%I:%M %p", "%H:%M")
        )
        for pattern in formats:
            try:
                parsed = datetime.strptime(text.upper(), pattern)
                return parsed.strftime("%Y-%m-%d" if field == "date" else "%H:%M")
            except ValueError:
                continue
        return None
    if field == "handle":
        return (
            text.casefold()
            if re.fullmatch(r"[\w.+-]{3,}@instapay", text, re.I)
            else None
        )
    if field == "recipient_identifier":
        return text if re.fullmatch(r"0(?:10|11|12|15)[0-9]{8}", text) else None
    return (
        text
        if 0 < len(text) <= 160 and not any(ord(char) < 32 for char in text)
        else None
    )


def parse_explicit_sms(
    event_id: str, sender: str, body: str, timestamp: str, content_sha256: str
) -> tuple[FieldCandidate, ...]:
    """Only a supported labeled body; sender/timestamp supply provenance, not fields.

    No realistic SMS corpus is provided with the frozen receipt set. This format
    is an explicit integration contract, not a claim all bank SMS are supported.
    """
    if not event_id or not sender or len(body.encode("utf-8")) > 4096:
        return ()
    source = EvidenceSource(
        EvidenceKind.SMS,
        event_id,
        "explicit_sms_labels",
        "v2",
        timestamp,
        content_sha256,
    )
    candidates = []
    for field, label in _LABELS.items():
        pattern = re.compile(rf"^(?:{label})\s*[:：]\s*(.+?)\s*$", re.I | re.M)
        for match in pattern.finditer(body):
            raw = match.group(1)
            normalized = normalize_field(field, raw)
            if normalized is not None:
                candidates.append(
                    FieldCandidate(
                        field,
                        raw,
                        normalized,
                        match.group(0),
                        source,
                        ("explicit_labeled_semantic_field", "admitted_sms_event"),
                        True,
                        True,
                    )
                )
    return tuple(candidates)


def correlate_receipt_sources(
    image_candidates: tuple[FieldCandidate, ...],
    sms_groups: tuple[tuple[FieldCandidate, ...], ...],
) -> tuple[FieldCandidate, ...]:
    """Associate one unique source group; never return a duplicate identity."""
    image_values: dict[str, set[str]] = {}
    for candidate in image_candidates:
        if candidate.structural_evidence:
            image_values.setdefault(candidate.field, set()).add(
                candidate.normalized_value
            )

    def agrees(values: dict[str, set[str]], field: str) -> bool:
        image = image_values.get(field, set())
        source = values.get(field, set())
        return len(image) == len(source) == 1 and image == source

    matches: list[tuple[FieldCandidate, ...]] = []
    seen_hashes: set[str] = set()
    for group in sms_groups:
        if not group or group[0].source.content_sha256 in seen_hashes:
            continue
        values: dict[str, set[str]] = {}
        for candidate in group:
            if candidate.usable:
                values.setdefault(candidate.field, set()).add(
                    candidate.normalized_value
                )
        reference_match = agrees(values, "reference_number")
        party_match = agrees(values, "handle") or agrees(values, "recipient_identifier")
        pair_match = agrees(values, "amount") and agrees(values, "currency")
        # Local date/time must be explicitly in both sources. SMS capture time
        # and receipt wall time cannot be compared by inventing a timezone.
        time_match = agrees(values, "date") and agrees(values, "time")
        if (reference_match and (party_match or pair_match)) or (
            pair_match and party_match and time_match
        ):
            matches.append(group)
            seen_hashes.add(group[0].source.content_sha256)
    return matches[0] if len(matches) == 1 else ()
