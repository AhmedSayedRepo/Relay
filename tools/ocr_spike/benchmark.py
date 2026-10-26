"""Offline benchmark runner for independent receipt text-band discovery."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from relay.infrastructure.image_processing import (
    PillowImagePreprocessor,
)
from relay.infrastructure.tesseract_ocr import (
    TesseractOcrProvider,
)
from tools.ocr_spike.discovery import (
    DiscoveryBounds,
    discover_text_bands_with_diagnostics,
    estimate_working_region,
)
from tools.ocr_spike.evidence import (
    CropEvidence,
    EvidenceMerger,
    EvidenceToken,
    extract_candidates,
    has_sufficient_receipt_evidence,
)
from tools.ocr_spike.models import Anchor, Box, SampleManifest, SampleResult

_HANDLE = re.compile(r"[\w.+-]{3,}@instapay", re.IGNORECASE)
_REFERENCE = re.compile(r"(?<!\d)[1-9]\d{11}(?!\d)")
_DATE = re.compile(r"\b[0-3]?\d\s+[A-Za-z]{3}\s+20\d{2}\b")
_BRAND = re.compile(r"\binstapay\b|انستاباي", re.IGNORECASE)
_FOOTER = re.compile(r"powered\s+by", re.IGNORECASE)
_PARTICIPANT = re.compile(
    r"\b(?:to|from|recipient|sender)\b|(?:إلى|من|المستلم|المرسل)", re.IGNORECASE
)
_TRANSFER_AMOUNT_LABEL = re.compile(r"transfer\s+amount|مبلغ\s+التحويل", re.IGNORECASE)


def _box(value: tuple[int, int, int, int] | None) -> Box:
    return Box(*(value or (0, 0, 0, 0)))


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


def _line_evidence(
    line: Any,
    source_pass: str,
    anchors: tuple[Anchor, ...],
    image_height: int,
) -> CropEvidence:
    box = _box(line.bounding_box)
    return CropEvidence(
        source_pass,
        str(line.text),
        float(line.confidence or 0.0),
        box,
        tuple(
            EvidenceToken(
                str(token.text),
                float(token.confidence or 0.0),
                _box(token.bounding_box),
            )
            for token in line.tokens
        ),
        "primary_full_page" if source_pass == "primary_psm6" else "visual_band",
        _structural_support(box, str(line.text), anchors, image_height),
    )


def _anchors(lines: tuple[Any, ...]) -> tuple[Anchor, ...]:
    matchers = (
        ("handle", _HANDLE),
        ("reference", _REFERENCE),
        ("date", _DATE),
        ("brand", _BRAND),
        ("footer", _FOOTER),
        ("participant", _PARTICIPANT),
    )
    anchors: list[Anchor] = []
    for line in lines:
        if line.bounding_box is None:
            continue
        for kind, matcher in matchers:
            if matcher.search(line.text):
                anchors.append(
                    Anchor(
                        kind,
                        Box(*line.bounding_box),
                        float(line.confidence or 0.0),
                        line.text,
                    )
                )
    return tuple(anchors)


def _scaled_box(
    value: tuple[int, int, int, int] | None,
    scale: int,
) -> tuple[int, int, int, int]:
    left, top, width, height = value or (0, 0, 0, 0)
    return (
        round(left / scale),
        round(top / scale),
        round(width / scale),
        round(height / scale),
    )


def _translated_evidence(
    line: Any,
    crop_box: Box,
    anchors: tuple[Anchor, ...],
    image_height: int,
    source_pass: str = "visual_band_psm7",
) -> CropEvidence:
    def translated(value: tuple[int, int, int, int] | None) -> Box:
        local = _box(value)
        return Box(
            crop_box.left + local.left,
            crop_box.top + local.top,
            local.width,
            local.height,
        )

    return CropEvidence(
        source_pass,
        str(line.text),
        float(line.confidence or 0.0),
        crop_box,
        tuple(
            EvidenceToken(
                str(token.text),
                float(token.confidence or 0.0),
                translated(token.bounding_box),
            )
            for token in line.tokens
        ),
        "independent_visual_band",
        _structural_support(crop_box, str(line.text), anchors, image_height),
    )


def _translated_lines(
    values: tuple[Any, ...],
    crop_box: Box,
    anchors: tuple[Anchor, ...],
    image_height: int,
    scale: int,
    source_pass: str,
) -> list[CropEvidence]:
    evidence: list[CropEvidence] = []
    for line in values:
        adjusted_tokens = tuple(
            type(token)(
                token.text,
                token.confidence,
                _scaled_box(token.bounding_box, scale),
            )
            for token in line.tokens
        )
        adjusted = type(line)(
            line.text,
            line.confidence,
            _scaled_box(line.bounding_box, scale),
            adjusted_tokens,
        )
        evidence.append(
            _translated_evidence(
                adjusted,
                crop_box,
                anchors,
                image_height,
                source_pass,
            )
        )
    return evidence


def _target_hit(box: Box, target: Box) -> bool:
    overlap = box.intersection_area(target)
    return bool(overlap) and (
        box.coverage_of(target) >= 0.20 or target.coverage_of(box) >= 0.55
    )


def _diagnostic(
    image: Image.Image,
    sample: SampleManifest,
    bands: tuple[Any, ...],
    output_path: Path,
) -> None:
    canvas = image.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    for target in sample.targets:
        box = target.box
        draw.rectangle(
            (box.left, box.top, box.right, box.bottom), outline=(30, 80, 230), width=4
        )
        draw.text((box.left, max(0, box.top - 16)), target.field, fill=(30, 80, 230))
    for index, band in enumerate(bands):
        box = band.box
        hit = any(_target_hit(box, target.box) for target in sample.targets)
        color = (20, 170, 70) if hit else (230, 130, 20)
        draw.rectangle(
            (box.left, box.top, box.right, box.bottom), outline=color, width=3
        )
        draw.text((box.left, box.top), str(index + 1), fill=color)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=False, compress_level=6)


def _ocr_languages(language: str) -> tuple[str, ...]:
    normalized = language.casefold()
    if normalized in {"en", "eng", "english"}:
        return ("eng",)
    if normalized in {"ar", "ara", "arabic"}:
        return ("ara", "eng")
    return ("eng", "ara")


def _targeted_ocr_languages(
    language: str, anchors: tuple[Anchor, ...]
) -> tuple[str, ...]:
    primary = _ocr_languages(language)
    if primary == ("eng",) and any(
        anchor.kind in {"brand", "handle"} for anchor in anchors
    ):
        return ("eng", "ara")
    return primary


def _needs_english_currency_retry(evidence: list[CropEvidence]) -> bool:
    if not evidence:
        return False
    has_digits = any(re.search(r"[0-9٠-٩۰-۹]", item.original_text) for item in evidence)
    fields = {
        candidate.field for item in evidence for candidate in extract_candidates(item)
    }
    structural = {value for item in evidence for value in item.structural_evidence}
    return bool(
        has_digits
        and not {"amount", "currency"}.issubset(fields)
        and {
            "upper_receipt_region",
            "above_handle_anchor",
            "transfer_amount_label",
        }.intersection(structural)
    )


def _promotion_decision(merged: dict[str, Any], combined_text: str) -> dict[str, Any]:
    selected_candidates = tuple(
        value.selected for value in merged.values() if value.selected is not None
    )
    selected_fields = tuple(
        sorted(candidate.field for candidate in selected_candidates)
    )
    brand_visible = bool(_BRAND.search(combined_text))
    receipt_classified = has_sufficient_receipt_evidence(
        selected_candidates,
        brand_visible=brand_visible,
    )
    transaction_core_present = bool(
        {"amount", "currency"}.issubset(selected_fields)
        or {"reference", "status"}.intersection(selected_fields)
    )
    transaction_promoted = receipt_classified and transaction_core_present
    promoted_fields = selected_fields if transaction_promoted else ()
    suppressed_fields = () if transaction_promoted else selected_fields
    if transaction_promoted:
        explanation = (
            "Receipt evidence gate passed and a transaction core field was selected."
        )
    elif selected_fields:
        explanation = (
            "Extracted candidate fields were retained but the conservative receipt "
            "evidence gate suppressed transaction promotion."
        )
    else:
        explanation = "No field candidate was selected for promotion."
    return {
        "receipt_classified": receipt_classified,
        "transaction_candidate_promoted": transaction_promoted,
        "selected_candidate_fields": list(selected_fields),
        "promoted_fields": list(promoted_fields),
        "suppressed_candidate_fields": list(suppressed_fields),
        "brand_visible": brand_visible,
        "explanation": explanation,
    }


def _primary_lines(
    provider: TesseractOcrProvider,
    preprocessed: Any,
    languages: tuple[str, ...],
    timeout_seconds: float,
) -> tuple[Any, ...]:
    """Run only the full-page pass consumed by this isolated benchmark.

    The production provider also performs parser-oriented secondary OCR. The
    region-discovery spike ignores those secondary lines and performs its own
    bounded crops, so invoking them here duplicates work without evidence.
    """

    deadline = time.monotonic() + timeout_seconds
    with tempfile.TemporaryDirectory(prefix="relay-ocr-spike-primary-") as directory:
        work = Path(directory)
        source = work / "input.png"
        source.write_bytes(preprocessed.data)
        return provider._run_tesseract(
            source,
            work / "result",
            languages,
            6,
            deadline,
        )


def run_sample(
    manifest: SampleManifest,
    base_dir: Path,
    output_dir: Path,
    provider: TesseractOcrProvider,
    bounds: DiscoveryBounds,
) -> SampleResult:
    started = time.monotonic()
    source = Path(manifest.source_filename)
    if not source.is_absolute():
        source = (base_dir / source).resolve()
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    preprocessed = PillowImagePreprocessor().preprocess(raw)
    preprocess_complete = time.monotonic()
    languages = _ocr_languages(manifest.language)
    primary_lines = _primary_lines(
        provider, preprocessed, languages, bounds.timeout_seconds
    )
    primary_complete = time.monotonic()
    with Image.open(io.BytesIO(preprocessed.data)) as opened:
        raster = opened.convert("L")

    anchors = _anchors(primary_lines)
    targeted_languages = _targeted_ocr_languages(manifest.language, anchors)
    working_region = estimate_working_region(raster, anchors)
    discovery = discover_text_bands_with_diagnostics(
        raster, anchors, bounds, working_region
    )
    discovery_complete = time.monotonic()
    bands = discovery.selected_bands
    primary_evidence = [
        _line_evidence(line, "primary_psm6", anchors, raster.height)
        for line in primary_lines
    ]
    targeted: list[CropEvidence] = []
    errors: list[str] = []
    targeted_started = time.monotonic()
    deadline = targeted_started + bounds.timeout_seconds

    with tempfile.TemporaryDirectory(prefix="relay-ocr-spike-") as temporary:
        work = Path(temporary)
        for index, band in enumerate(bands):
            if time.monotonic() >= deadline:
                errors.append("targeted_ocr_timeout")
                break
            box = band.box
            crop = ImageOps.autocontrast(
                raster.crop((box.left, box.top, box.right, box.bottom)), cutoff=1
            )
            scale = 1
            if crop.height < 140:
                scale = 3 if crop.height < 36 else 2
                crop = crop.resize(
                    (crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS
                )
            crop_path = work / f"band-{index}.png"
            crop.save(crop_path, format="PNG", optimize=False, compress_level=6)
            try:
                lines = provider._run_tesseract(
                    crop_path,
                    work / f"band-result-{index}",
                    targeted_languages,
                    7,
                    deadline,
                )
            except Exception as error:
                errors.append(f"band_{index}:{type(error).__name__}")
                continue
            band_evidence = _translated_lines(
                lines,
                box,
                anchors,
                raster.height,
                scale,
                "visual_band_psm7",
            )
            if (
                languages == ("eng",)
                and targeted_languages != languages
                and _needs_english_currency_retry(band_evidence)
                and time.monotonic() < deadline
            ):
                try:
                    english_lines = provider._run_tesseract(
                        crop_path,
                        work / f"band-result-english-{index}",
                        languages,
                        7,
                        deadline,
                    )
                except Exception as error:
                    errors.append(f"band_{index}_english_retry:{type(error).__name__}")
                else:
                    band_evidence.extend(
                        _translated_lines(
                            english_lines,
                            box,
                            anchors,
                            raster.height,
                            scale,
                            "visual_band_psm7_english_currency_retry",
                        )
                    )
                    for proposal in discovery.proposals:
                        if proposal.selected and proposal.box == box:
                            proposal.grouping_information["ocr_retry"] = (
                                "english_currency_recovery"
                            )
            targeted.extend(band_evidence)
    targeted_complete = time.monotonic()

    all_candidates = tuple(
        candidate
        for evidence in (*primary_evidence, *targeted)
        for candidate in extract_candidates(evidence)
    )
    merged = EvidenceMerger().merge(all_candidates)
    target_discovery = {
        target.field: any(_target_hit(band.box, target.box) for band in bands)
        for target in manifest.targets
    }
    irrelevant = sum(
        1
        for band in bands
        if manifest.targets
        and not any(_target_hit(band.box, target.box) for target in manifest.targets)
    )
    useful_targets_removed = [
        target.field
        for target in manifest.targets
        if working_region.applied and working_region.box.coverage_of(target.box) < 0.80
    ]
    amount_target = next(
        (target for target in manifest.targets if target.field == "amount"), None
    )
    amount_failure_analysis: dict[str, Any] = {"applicable": False}
    if manifest.expected_amount is not None and amount_target is not None:
        generated = [
            proposal
            for proposal in discovery.proposals
            if _target_hit(proposal.box, amount_target.box)
        ]
        selected_targeted = [
            item
            for item in targeted
            if _target_hit(item.crop_geometry, amount_target.box)
        ]
        expected_digits = re.sub(r"\D", "", manifest.expected_amount)
        amount_digits_produced = any(
            expected_digits and expected_digits in re.sub(r"\D", "", item.original_text)
            for item in selected_targeted
        )
        target_candidates = [
            candidate
            for item in selected_targeted
            for candidate in extract_candidates(item)
        ]
        currency_produced = any(
            candidate.field == "currency"
            and manifest.expected_currency is not None
            and candidate.normalized == manifest.expected_currency.upper()
            for candidate in target_candidates
        )
        normalized_amount = manifest.expected_amount.replace(",", "")
        amount_confidence_passed = any(
            candidate.field == "amount" and candidate.normalized == normalized_amount
            for candidate in target_candidates
        )
        currency_confidence_passed = any(
            candidate.field == "currency"
            and manifest.expected_currency is not None
            and candidate.normalized == manifest.expected_currency.upper()
            for candidate in target_candidates
        )
        selected_amount = merged.get("amount")
        selected_currency = merged.get("currency")
        promotion_conflict = bool(
            (selected_amount and selected_amount.conflict)
            or (selected_currency and selected_currency.conflict)
        )
        promotion_passed = bool(
            selected_amount
            and selected_amount.selected
            and selected_amount.selected.normalized == normalized_amount
            and selected_currency
            and selected_currency.selected
            and manifest.expected_currency is not None
            and selected_currency.selected.normalized
            == manifest.expected_currency.upper()
        )
        generated_target = bool(generated)
        selected_target = any(proposal.selected for proposal in generated)
        working_region_miss = bool(
            working_region.applied
            and working_region.box.coverage_of(amount_target.box) < 0.80
        )
        rejection_reasons = sorted(
            {
                proposal.rejection_reason
                for proposal in generated
                if proposal.rejection_reason is not None
            }
        )
        if not generated_target:
            stage_failure = "DISCOVERY_MISS"
            root_cause = (
                "WORKING_REGION_MISS"
                if working_region_miss
                else "REGION_GENERATION_MISS"
            )
        elif not selected_target:
            stage_failure = "SELECTION_MISS"
            if any(
                reason in {"crop-count limit", "pixel-budget limit"}
                for reason in rejection_reasons
            ):
                root_cause = "BUDGET_SUPPRESSION"
            elif rejection_reasons and set(rejection_reasons) == {"merged"}:
                root_cause = "MERGE_SUPPRESSION"
            else:
                root_cause = "RANKING_SELECTION_MISS"
        elif not amount_digits_produced:
            stage_failure = "OCR_AMOUNT_FAILURE"
            root_cause = "OTHER"
        elif not currency_produced:
            stage_failure = "OCR_CURRENCY_FAILURE"
            root_cause = "OTHER"
        elif not (amount_confidence_passed and currency_confidence_passed):
            stage_failure = "CONFIDENCE_GATE_FAILURE"
            root_cause = "OTHER"
        elif promotion_conflict or not promotion_passed:
            stage_failure = "PROMOTION_CONFLICT"
            root_cause = "OTHER"
        else:
            stage_failure = "PASS"
            root_cause = "OTHER"
        amount_failure_analysis = {
            "applicable": True,
            "target_region_generated": generated_target,
            "selected": selected_target,
            "targeted_ocr_produced_amount_digits": amount_digits_produced,
            "targeted_ocr_produced_supported_currency": currency_produced,
            "confidence_gate_passed": (
                amount_confidence_passed and currency_confidence_passed
            ),
            "candidate_promotion_passed": promotion_passed,
            "promotion_conflict": promotion_conflict,
            "stage_failure": stage_failure,
            "root_cause": root_cause,
            "proposal_ids": [proposal.proposal_id for proposal in generated],
            "rejection_reasons": rejection_reasons,
        }
    primary_text = "\n".join(line.text for line in primary_lines)
    combined_text = "\n".join(
        [primary_text, *(item.original_text for item in targeted)]
    )
    classification_evidence: list[str] = []
    if _BRAND.search(combined_text):
        classification_evidence.append("instapay_brand")
    for field in (
        "handle",
        "reference",
        "date",
        "time",
        "amount",
        "currency",
        "status",
    ):
        if field in merged and merged[field].selected is not None:
            classification_evidence.append(field)
    promotion_decision = _promotion_decision(merged, combined_text)
    receipt_classified = bool(promotion_decision["receipt_classified"])
    transaction_promoted = bool(promotion_decision["transaction_candidate_promoted"])
    promoted_fields = set(promotion_decision["promoted_fields"])
    false_positives: list[str] = []
    if not manifest.expected_receipt:
        if receipt_classified:
            false_positives.append("false_receipt_classification")
        if transaction_promoted:
            false_positives.append("false_transaction_candidate_promotion")
        if transaction_promoted and {"amount", "currency"}.intersection(
            promoted_fields
        ):
            false_positives.append("false_amount_currency_promotion")
        if transaction_promoted and "reference" in promoted_fields:
            false_positives.append("false_reference_serial_promotion")
    if (
        manifest.expected_amount is None
        and manifest.must_not_promote_transfer_amount
        and "amount" in merged
        and merged["amount"].selected is not None
    ):
        false_positives.append("transfer_amount_from_non_transfer_row")

    evidence_complete = time.monotonic()

    _diagnostic(
        raster,
        manifest,
        bands,
        output_dir / "diagnostics" / f"{manifest.sample_id}.png",
    )
    completed = time.monotonic()
    return SampleResult(
        manifest.sample_id,
        digest,
        raster.size,
        primary_text,
        [
            {
                "text": item.original_text,
                "confidence": item.confidence,
                "geometry": item.crop_geometry.as_list(),
                "tokens": [
                    {
                        "text": token.text,
                        "confidence": token.confidence,
                        "geometry": token.box.as_list(),
                    }
                    for token in item.tokens
                ],
                "source_pass": item.source_pass,
            }
            for item in primary_evidence
        ],
        [
            {
                "kind": anchor.kind,
                "text": anchor.text,
                "confidence": anchor.confidence,
                "geometry": anchor.box.as_list(),
            }
            for anchor in anchors
        ],
        [
            {
                "geometry": band.box.as_list(),
                "score": band.score,
                "density": band.density,
                "provenance": band.provenance,
                "anchor_support": list(band.anchor_support),
            }
            for band in bands
        ],
        [
            {
                "text": item.original_text,
                "confidence": item.confidence,
                "crop_geometry": item.crop_geometry.as_list(),
                "tokens": [
                    {
                        "text": token.text,
                        "confidence": token.confidence,
                        "geometry": token.box.as_list(),
                    }
                    for token in item.tokens
                ],
                "source_pass": item.source_pass,
            }
            for item in targeted
        ],
        {field: value.as_json() for field, value in sorted(merged.items())},
        target_discovery,
        irrelevant,
        classification_evidence,
        false_positives,
        len(bands),
        sum(band.box.area for band in bands),
        round((completed - started) * 1000),
        list(manifest.uncertain_expected_fields),
        errors,
        {
            "geometry": working_region.box.as_list(),
            "confidence": working_region.confidence,
            "provenance": working_region.provenance,
            "applied": working_region.applied,
        },
        {
            "useful_targets_removed": useful_targets_removed,
            "removed_useful_receipt_content": bool(useful_targets_removed),
            "removed_only_bezel_or_background_noise": bool(
                working_region.applied and not useful_targets_removed
            ),
            "caused_target_region_loss": bool(useful_targets_removed),
        },
        [proposal.as_json() for proposal in discovery.proposals],
        amount_failure_analysis,
        promotion_decision,
        {
            "preprocess": round((preprocess_complete - started) * 1000),
            "primary_ocr": round((primary_complete - preprocess_complete) * 1000),
            "discovery": round((discovery_complete - primary_complete) * 1000),
            "targeted_ocr": round((targeted_complete - targeted_started) * 1000),
            "evidence_and_decision": round(
                (evidence_complete - targeted_complete) * 1000
            ),
            "diagnostic": round((completed - evidence_complete) * 1000),
            "total": round((completed - started) * 1000),
        },
    )


def _metrics(
    results: list[SampleResult], manifests: list[SampleManifest]
) -> dict[str, Any]:
    targets = [
        value for result in results for value in result.target_discovery.values()
    ]
    total_crops = sum(result.crop_count for result in results)
    irrelevant = sum(result.irrelevant_crop_count for result in results)
    by_id = {result.sample_id: result for result in results}
    amount_targets = [
        result.target_discovery["amount"]
        for result in results
        if "amount" in result.target_discovery
    ]
    stage_failures = [
        result.amount_failure_analysis.get("stage_failure")
        for result in results
        if result.amount_failure_analysis.get("applicable")
    ]
    negative_ids = {
        manifest.sample_id for manifest in manifests if not manifest.expected_receipt
    }
    negative_results = [
        result for result in results if result.sample_id in negative_ids
    ]
    false_receipt_classifications = sum(
        bool(result.promotion_decision.get("receipt_classified"))
        for result in negative_results
    )
    false_transaction_promotions = sum(
        bool(result.promotion_decision.get("transaction_candidate_promoted"))
        for result in negative_results
    )
    false_amount_currency_promotions = sum(
        bool(
            {"amount", "currency"}.intersection(
                result.promotion_decision.get("promoted_fields", [])
            )
        )
        for result in negative_results
    )
    false_reference_serial_promotions = sum(
        "reference" in result.promotion_decision.get("promoted_fields", [])
        for result in negative_results
    )

    expected_by_field = {
        "amount": lambda item: item.expected_amount,
        "currency": lambda item: item.expected_currency,
        "reference": lambda item: item.expected_reference,
        "date": lambda item: item.expected_date,
        "time": lambda item: item.expected_time,
    }

    def normalized_expected(field: str, value: str) -> str:
        if field == "amount":
            return value.replace(",", "")
        if field == "currency":
            return value.upper()
        return value

    def recovery(field: str) -> dict[str, int]:
        expected_count = 0
        recovered_count = 0
        exact_count = 0
        for manifest in manifests:
            expected = expected_by_field[field](manifest)
            if expected is None or field in manifest.uncertain_expected_fields:
                continue
            expected_count += 1
            selected = (
                by_id[manifest.sample_id].merged_fields.get(field, {}).get("selected")
            )
            if selected is not None:
                recovered_count += 1
                if selected["normalized"] == normalized_expected(field, expected):
                    exact_count += 1
        return {
            "exact": exact_count,
            "recovered_any": recovered_count,
            "expected": expected_count,
        }

    return {
        "target_band_discovery": {
            "found": sum(targets),
            "annotated": len(targets),
        },
        "amount_region_discovery": {
            "found": sum(amount_targets),
            "annotated": len(amount_targets),
        },
        "irrelevant_crop_rate": irrelevant / total_crops if total_crops else 0.0,
        "amount_recovery": recovery("amount"),
        "currency_recovery": recovery("currency"),
        "reference_recovery": recovery("reference"),
        "date_recovery": recovery("date"),
        "time_recovery": recovery("time"),
        "negative_sample_count": sum(
            not manifest.expected_receipt for manifest in manifests
        ),
        "false_receipt_classifications": false_receipt_classifications,
        "false_transaction_promotions": false_transaction_promotions,
        "false_amount_currency_promotions": false_amount_currency_promotions,
        "false_reference_serial_promotions": false_reference_serial_promotions,
        "negative_false_positive_rate": (
            false_transaction_promotions / len(negative_results)
            if negative_results
            else None
        ),
        "false_positive_count": false_transaction_promotions,
        "false_positive_event_count": sum(
            len(result.false_positives) for result in negative_results
        ),
        "negative_sample_explanations": [
            {
                "sample_id": result.sample_id,
                "false_positive_events": result.false_positives,
                **result.promotion_decision,
            }
            for result in negative_results
        ],
        "conflict_count": sum(
            1
            for result in results
            for value in result.merged_fields.values()
            if value.get("conflict")
        ),
        "working_regions_applied": sum(
            bool(result.working_region.get("applied")) for result in results
        ),
        "generation_misses": stage_failures.count("DISCOVERY_MISS"),
        "selection_misses": stage_failures.count("SELECTION_MISS"),
        "ocr_amount_failures": stage_failures.count("OCR_AMOUNT_FAILURE"),
        "ocr_currency_failures": stage_failures.count("OCR_CURRENCY_FAILURE"),
        "confidence_gate_failures": stage_failures.count("CONFIDENCE_GATE_FAILURE"),
        "promotion_conflicts": stage_failures.count("PROMOTION_CONFLICT"),
        "region_proposal_count": sum(
            len(result.region_proposals) for result in results
        ),
        "average_secondary_crops": (total_crops / len(results) if results else 0.0),
        "max_secondary_crops": max(
            (result.crop_count for result in results), default=0
        ),
        "average_processing_ms": (
            sum(result.duration_ms for result in results) / len(results)
            if results
            else 0.0
        ),
        "max_processing_ms": max((result.duration_ms for result in results), default=0),
        "average_stage_ms": {
            stage: (
                sum(result.timings_ms.get(stage, 0) for result in results)
                / len(results)
                if results
                else 0.0
            )
            for stage in (
                "preprocess",
                "primary_ocr",
                "discovery",
                "targeted_ocr",
                "evidence_and_decision",
                "diagnostic",
            )
        },
    }


def _write_summary(
    output: Path,
    manifests: list[SampleManifest],
    results: list[SampleResult],
    metrics: dict[str, Any],
) -> None:
    lines = [
        "# Offline OCR region-discovery spike",
        "",
        "The spike is isolated from production. It uses independent grayscale and",
        "autocontrast projection and text-density bands, optional primary-OCR",
        "anchor scoring, and bounded eng+ara PSM 7 crops.",
        "Primary and targeted evidence sources remain separate.",
        "",
        "## Evaluated samples",
        "",
    ]
    by_id = {result.sample_id: result for result in results}
    for manifest in manifests:
        result = by_id[manifest.sample_id]
        recovered = {
            field: evidence["selected"]["normalized"]
            for field, evidence in result.merged_fields.items()
            if evidence["selected"] is not None
        }
        lines.append(
            f"- {manifest.sample_id}: crops={result.crop_count}, "
            f"targets={result.target_discovery}, "
            f"recovered={recovered}, uncertainties="
            f"{result.ground_truth_uncertainties or 'none'}, "
            f"promotion={result.promotion_decision}, "
            f"errors={result.errors or 'none'}"
        )
    if metrics["negative_sample_count"]:
        decision_lines = (
            "B — promising but needs another offline iteration. Synthetic generic",
            "negatives now measure this spike's false transaction-promotion behavior,",
            "but they do not establish production readiness or replace real-world",
            "negative coverage. Production integration is not recommended.",
        )
        limitation_lines = (
            "The negative set is fully synthetic and intentionally small. Real",
            "Non-receipt captures and broader layout/language coverage remain "
            "required.",
        )
    else:
        decision_lines = (
            "B — promising but needs another offline iteration. The available samples",
            "provide useful known cases, but the requested negative set is incomplete",
            "because no generic non-receipt negative was staged. Production",
            "integration is not recommended from this evidence alone.",
        )
        limitation_lines = (
            "A true generic non-receipt negative remains required. References marked",
            "visually uncertain require manual verification before final scoring.",
        )
    lines.extend(
        (
            "",
            "## Metrics",
            "",
            f"- Target-region discovery: {metrics['target_band_discovery']}",
            f"- Amount-region discovery: {metrics['amount_region_discovery']}",
            f"- Irrelevant crop rate: {metrics['irrelevant_crop_rate']:.3f}",
            f"- Amount recovery: {metrics['amount_recovery']}",
            f"- Currency recovery: {metrics['currency_recovery']}",
            f"- Reference recovery: {metrics['reference_recovery']}",
            f"- Date recovery: {metrics['date_recovery']}",
            f"- Negative samples: {metrics['negative_sample_count']}",
            "- False receipt classifications: "
            f"{metrics['false_receipt_classifications']}",
            "- False transaction promotions: "
            f"{metrics['false_transaction_promotions']}",
            "- False amount/currency promotions: "
            f"{metrics['false_amount_currency_promotions']}",
            "- False reference/serial promotions: "
            f"{metrics['false_reference_serial_promotions']}",
            "- Negative false-positive rate: "
            f"{metrics['negative_false_positive_rate']}",
            f"- Generation misses: {metrics['generation_misses']}",
            f"- Selection misses: {metrics['selection_misses']}",
            f"- OCR amount failures: {metrics['ocr_amount_failures']}",
            f"- OCR currency failures: {metrics['ocr_currency_failures']}",
            f"- Confidence failures: {metrics['confidence_gate_failures']}",
            f"- Promotion conflicts: {metrics['promotion_conflicts']}",
            f"- Average crops: {metrics['average_secondary_crops']:.2f}",
            f"- Max crops: {metrics['max_secondary_crops']}",
            f"- Average runtime ms: {metrics['average_processing_ms']:.0f}",
            f"- Max runtime ms: {metrics['max_processing_ms']}",
            "",
            "## Decision",
            "",
            *decision_lines,
            "",
            "## Limitations",
            "",
            *limitation_lines,
        )
    )
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tesseract", type=Path, required=True)
    parser.add_argument("--tessdata", type=Path, required=True)
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifests = [SampleManifest.from_json(item) for item in payload["samples"]]
    args.output.mkdir(parents=True, exist_ok=True)
    provider = TesseractOcrProvider(args.tesseract.resolve(), args.tessdata.resolve())
    bounds = DiscoveryBounds()
    results = [
        run_sample(sample, manifest_path.parent, args.output, provider, bounds)
        for sample in manifests
    ]
    metrics = _metrics(results, manifests)
    report = {
        "spike_version": "offline-region-discovery-v4",
        "production_enabled": False,
        "production_code_changed": False,
        "bounds": asdict(bounds),
        "sample_count": len(results),
        "samples": [result.as_json() for result in results],
        "metrics": metrics,
        "architecture_decision": "B",
        "more_samples_required": True,
        "negative_sample_missing": metrics["negative_sample_count"] == 0,
        "production_integration_recommended": False,
    }
    (args.output / "benchmark-results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_summary(args.output, manifests, results, metrics)


if __name__ == "__main__":
    main()
