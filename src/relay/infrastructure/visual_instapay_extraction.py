"""Canonical ROI recognition after visual admission; unchanged local OCR modes."""

import hashlib
import re
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from relay.application.evidence_ports import ReceiptEvidence
from relay.application.evidence_sources import (
    correlate_receipt_sources,
    normalize_field,
)
from relay.application.processing_errors import ProcessingError
from relay.domain.agent import JsonValue
from relay.domain.evidence import EvidenceKind, EvidenceSource, FieldCandidate
from relay.domain.processing import (
    OcrDocument,
    OcrLine,
    OcrToken,
    ProcessingFailure,
    ReceiptType,
    normalize_ocr_text,
)
from relay.infrastructure.instapay_adapter import (
    _THRESHOLDS,
    InstaPayReceiptAdapter,
    _field_matches,
    _matching_confidence,
)
from relay.infrastructure.visual_registration import register_receipt


def roi_matches(field: str, text: str) -> list[str]:
    """Numeric parsing is allowed only within the separately traced amount ROI."""
    translated = text.translate(
        str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    )
    if field == "amount":
        return re.findall(
            r"(?<![\w.])[0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?(?![\w.])", translated
        )
    if field == "currency":
        return re.findall(
            r"\b(?:EGP|USD|EUR)\b|ج\.?\s*م\.?|جنيه مصري", translated, re.I
        )
    if field == "recipient":
        return [text.strip()] if text.strip() else []
    return _field_matches(field, translated)


def extract_registered(
    adapter: InstaPayReceiptAdapter,
    data: bytes,
    event_id: str,
    timestamp: str,
    languages: tuple[str, ...],
    timeout: float,
    structured_sources: tuple[tuple[FieldCandidate, ...], ...],
) -> ReceiptEvidence:
    provider = adapter.provider
    if not languages or any(
        lang not in provider.available_languages for lang in languages
    ):
        raise ProcessingError(ProcessingFailure.OCR_LANGUAGE_UNAVAILABLE)
    started = time.monotonic()
    deadline = started + min(timeout, 20.0)
    registration = register_receipt(data)
    registration_ms = (time.monotonic() - started) * 1000
    profile = registration.profile
    working = registration.working
    raw: list[JsonValue] = []
    rejected: list[JsonValue] = []
    all_lines: list[OcrLine] = []
    candidates: list[FieldCandidate] = []
    status_lines: tuple[OcrLine, ...] = ()
    ocr_ms = 0.0
    source = EvidenceSource(
        EvidenceKind.RECEIPT,
        event_id,
        adapter.adapter_id,
        "v2",
        timestamp,
        hashlib.sha256(data).hexdigest(),
        profile.profile if profile else None,
        "visual_canonical_v1",
    )
    if profile and working:
        boxes = dict(profile.rois)
        with tempfile.TemporaryDirectory(prefix="relay-visual-roi-") as directory:
            work = Path(directory)
            index = 0

            def recognize(field: str, psm: int) -> tuple[OcrLine, ...]:
                nonlocal ocr_ms, index
                assert working is not None
                x, y, width, height = boxes[field]
                path = work / f"region-{index}.png"
                working.raster.crop((x, y, x + width, y + height)).save(path, "PNG")
                output = work / f"ocr-{index}"
                index += 1
                before = time.monotonic()
                lines = provider._run_tesseract(path, output, languages, psm, deadline)
                ocr_ms += (time.monotonic() - before) * 1000
                translated = tuple(
                    replace(
                        line,
                        bounding_box=(
                            (
                                line.bounding_box[0] + x,
                                line.bounding_box[1] + y,
                                line.bounding_box[2],
                                line.bounding_box[3],
                            )
                            if line.bounding_box
                            else None
                        ),
                        tokens=tuple(
                            OcrToken(
                                token.text,
                                token.confidence,
                                (
                                    (
                                        token.bounding_box[0] + x,
                                        token.bounding_box[1] + y,
                                        token.bounding_box[2],
                                        token.bounding_box[3],
                                    )
                                    if token.bounding_box
                                    else None
                                ),
                            )
                            for token in line.tokens
                        ),
                        ocr_mode=f"targeted_{field}_psm{psm}",
                        parser_eligible=False,
                    )
                    for line in lines
                )
                all_lines.extend(translated)
                raw.append(
                    {
                        "purpose": field,
                        "region": list(boxes[field]),
                        "psm": psm,
                        "coordinate_space": "canonical_profile",
                        "variant": profile.variant,
                        "original_polygon": [
                            list(p) for p in working.polygon(boxes[field])
                        ],
                        "lines": [line.as_json() for line in translated],
                    }
                )
                return translated

            def capture(
                field: str,
                lines: tuple[OcrLine, ...],
                consistency: set[str] | None = None,
            ) -> None:
                assert working is not None
                observations = [
                    (line, value, normalize_field(field, value))
                    for line in lines
                    for value in roi_matches(field, line.text)
                ]
                values = {
                    normalized
                    for _, _, normalized in observations
                    if normalized is not None
                }
                if not observations:
                    rejected.append(
                        {
                            "field": field,
                            "reason": "no_supported_explicit_value",
                            "region": list(boxes[field]),
                        }
                    )
                for line, value, normalized in observations:
                    if normalized is None or (
                        field == "amount"
                        and len(value.replace(",", "")) >= 10
                        and "." not in value
                    ):
                        rejected.append(
                            {
                                "field": field,
                                "raw_value": value,
                                "original_text": line.text,
                                "reason": "parser_validation",
                                "region": list(boxes[field]),
                            }
                        )
                        continue
                    confidence = _matching_confidence(line, value)
                    sufficient = (
                        confidence is not None
                        and confidence >= _THRESHOLDS.get(field, 0.90)
                    )
                    strong = (
                        sufficient and len(values) == 1 and consistency == {normalized}
                    )
                    candidates.append(
                        FieldCandidate(
                            field,
                            value,
                            normalized,
                            line.text,
                            source,
                            (
                                "visual_registered_canonical_roi",
                                f"field_region:{field}",
                                profile.variant,
                            ),
                            sufficient,
                            strong,
                            confidence,
                            line.bounding_box,
                            (
                                working.polygon(line.bounding_box)
                                if line.bounding_box
                                else ()
                            ),
                        )
                    )

            # Receipt observations used for unique source association are still
            # located in canonical regions. No anchor transcript is recognized.
            associated: tuple[FieldCandidate, ...] = ()
            for field, names in (
                ("reference_number", ("reference_number",)),
                ("handle", ("handle",)),
                ("amount", ("amount", "currency")),
                ("date", ("date", "time")),
                ("recipient_identifier", ("recipient_identifier",)),
                ("recipient", ("recipient",)),
            ):
                if all(
                    name in {item.field for item in associated if item.usable}
                    for name in names
                ):
                    raw.append(
                        {
                            "purpose": field,
                            "recognition_skipped": "correlated_explicit_source",
                        }
                    )
                    continue
                first = recognize(field, 7)
                second = recognize(field, 6)
                for name in names:
                    consistent = {
                        value
                        for line in second
                        for raw_value in roi_matches(name, line.text)
                        if (value := normalize_field(name, raw_value)) is not None
                    }
                    capture(name, first, consistent)
                    capture(name, second)
                associated = correlate_receipt_sources(
                    tuple(candidates), structured_sources
                )
            candidates.extend(associated)
            status_lines = recognize(
                "status", 6
            )  # retained explicit text, never interpreted as proof of settlement
    text = normalize_ocr_text("\n".join(line.text for line in all_lines))
    confidences = [line.confidence for line in all_lines if line.confidence is not None]
    document = OcrDocument(
        provider.provider_id,
        provider.provider_version,
        languages,
        provider.available_languages,
        text,
        tuple(all_lines),
        sum(confidences) / len(confidences) if confidences else None,
        round(ocr_ms),
    )
    admitted = bool(profile and working)
    failed = admitted and any(
        re.search(r"transfer\s+failed|فشل\s*التحويل", line.text, re.I)
        for line in status_lines
    )
    metadata: dict[str, JsonValue] = {
        "adapter": adapter.adapter_id,
        "adapter_version": "v2",
        "extraction_version": "visual_canonical_v1",
        "layout_profile": profile.profile if profile else None,
        "layout_variant": profile.variant if profile else None,
        "registration": registration.as_json(),
        "raw_region_evidence": raw,
        "rejected_field_observations": rejected,
        "recognition_provider": provider.provider_id,
        "recognition_version": provider.provider_version,
        "recognition_skipped": None if admitted else "visual_registration_not_admitted",
        "geometry_coordinate_space": "canonical_profile",
        "transformations": list(working.transformations) if working else [],
        "original_size": list(working.original_size) if working else None,
        "working_size": list(working.raster.size) if working else None,
        "original_mapping_available": (
            working.original_mapping_available if working else False
        ),
        "trust_policy": "semantic_context_unique_format_dual_mode_agreement_v1",
        "timings_ms": {
            "registration": round(registration_ms, 2),
            "targeted_ocr": round(ocr_ms, 2),
            "adapter_layout": round((time.monotonic() - started) * 1000 - ocr_ms, 2),
        },
    }
    possible = admitted or registration.reason == "ambiguous_layout"
    return ReceiptEvidence(
        document,
        tuple(candidates),
        (
            ReceiptType.INSTAPAY_TRANSFER_RECEIPT
            if possible
            else ReceiptType.UNKNOWN_RECEIPT
        ),
        admitted,
        admitted,
        possible,
        failed,
        metadata,
    )
