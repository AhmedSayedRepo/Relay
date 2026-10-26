"""Apply one conservative Relay extraction policy to provider evidence."""

from __future__ import annotations

import re
from typing import Any

from relay.domain.processing import OcrDocument, OcrLine
from tools.ocr_spike.evidence import (
    CropEvidence,
    EvidenceMerger,
    EvidenceToken,
    extract_candidates,
    has_sufficient_receipt_evidence,
)
from tools.ocr_spike.models import Anchor, Box

_HANDLE = re.compile(r"[\w.+-]{3,}@instapay", re.IGNORECASE)
_REFERENCE = re.compile(r"(?<!\d)[1-9]\d{11}(?!\d)")
_DATE = re.compile(r"\b[0-3]?\d\s+[A-Za-z]{3}\s+20\d{2}\b")
_BRAND = re.compile(r"\binstapay\b|انستاباي", re.IGNORECASE)
_TRANSFER_AMOUNT_LABEL = re.compile(r"transfer\s+amount|مبلغ\s+التحويل", re.IGNORECASE)


def _box(value: tuple[int, int, int, int] | None, width: int, height: int) -> Box:
    return Box(*(value or (0, 0, width, height)))


def _anchors(lines: tuple[OcrLine, ...], width: int, height: int) -> tuple[Anchor, ...]:
    matchers = (
        ("handle", _HANDLE),
        ("reference", _REFERENCE),
        ("date", _DATE),
        ("brand", _BRAND),
    )
    result: list[Anchor] = []
    for line in lines:
        for kind, matcher in matchers:
            if matcher.search(line.text):
                result.append(
                    Anchor(
                        kind,
                        _box(line.bounding_box, width, height),
                        float(line.confidence or 0.0),
                        line.text,
                    )
                )
    return tuple(result)


def _structural_support(
    box: Box,
    text: str,
    anchors: tuple[Anchor, ...],
    image_height: int,
) -> tuple[str, ...]:
    support: list[str] = []
    if _TRANSFER_AMOUNT_LABEL.search(text):
        support.append("transfer_amount_label")
    handle_tops = [anchor.box.top for anchor in anchors if anchor.kind == "handle"]
    if handle_tops and box.bottom < min(handle_tops):
        support.append("above_handle_anchor")
    detail_tops = [
        anchor.box.top for anchor in anchors if anchor.kind in {"reference", "date"}
    ]
    center = box.top + box.height / 2
    if detail_tops and box.bottom < min(detail_tops) and center <= image_height * 0.40:
        support.append("upper_receipt_region")
    return tuple(support)


def evaluate_document(
    document: OcrDocument,
    *,
    image_width: int,
    image_height: int,
) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, Any]]:
    lines = document.evidence_lines
    anchors = _anchors(lines, image_width, image_height)
    evidence: list[CropEvidence] = []
    for index, line in enumerate(lines):
        box = _box(line.bounding_box, image_width, image_height)
        tokens = tuple(
            EvidenceToken(
                token.text,
                float(token.confidence or line.confidence or 0.0),
                _box(token.bounding_box, image_width, image_height),
            )
            for token in line.tokens
        )
        evidence.append(
            CropEvidence(
                f"{document.provider_id}_line_{index}",
                line.text,
                float(line.confidence or 0.0),
                box,
                tokens,
                "provider_document_line",
                _structural_support(box, line.text, anchors, image_height),
            )
        )
    candidates = tuple(
        candidate
        for item in evidence
        for candidate in extract_candidates(item)
        if "ocr_currency_confusion_normalized"
        not in candidate.supporting_structural_evidence
    )
    merged = EvidenceMerger().merge(candidates)
    selected = {
        field: value.selected.as_json()
        for field, value in merged.items()
        if value.selected is not None
    }
    conflicts = sorted(field for field, value in merged.items() if value.conflict)
    selected_candidates = tuple(
        value.selected for value in merged.values() if value.selected is not None
    )
    receipt_classified = has_sufficient_receipt_evidence(
        selected_candidates,
        brand_visible=bool(_BRAND.search(document.normalized_text)),
    )
    selected_fields = set(selected)
    transaction_promoted = receipt_classified and bool(
        {"amount", "currency"}.issubset(selected_fields)
        or {"reference", "status"}.intersection(selected_fields)
    )
    if conflicts:
        transaction_promoted = False
        explanation = (
            "Conflicting provider values require review; nothing was promoted."
        )
    elif transaction_promoted:
        explanation = "Conservative receipt evidence gate passed."
    elif selected:
        explanation = (
            "Candidate evidence was retained but transaction promotion was suppressed."
        )
    else:
        explanation = "No field candidate was selected."
    return (
        selected,
        conflicts,
        {
            "receipt_classified": receipt_classified,
            "transaction_promoted": transaction_promoted,
            "explanation": explanation,
        },
    )
