"""Run and report the isolated Relay OCR provider bake-off."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from PIL import Image

from relay.domain.processing import OcrDocument, PreprocessedImage
from tools.ocr_bakeoff.adapters import (
    AzureDocumentIntelligenceProvider,
    GoogleDocumentAiProvider,
    ProviderHttpError,
)
from tools.ocr_bakeoff.dataset import load_dataset
from tools.ocr_bakeoff.models import BakeoffSample, ProviderSampleResult
from tools.ocr_bakeoff.policy import evaluate_document

_FIELDS = (
    "amount",
    "currency",
    "reference",
    "date",
    "time",
    "handle",
    "recipient_identifier",
)
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


@dataclass(frozen=True, slots=True)
class ProviderDescription:
    cost_per_1_000: str
    operational_complexity: str


_DESCRIPTIONS = {
    "tesseract": ProviderDescription(
        "USD 0 API fee; host CPU/operations excluded",
        "High: local binary/language packs plus Relay crop discovery and tuning",
    ),
    "google_document_ai": ProviderDescription(
        "USD 1.50 list price after the first 1,000 monthly pages",
        "Medium: Google project, OCR processor, IAM, region, quotas, and egress",
    ),
    "azure_document_intelligence": ProviderDescription(
        "Region/offer-dependent; verify in Azure calculator before pilot",
        "Medium: Azure resource/key, regional endpoint, polling, quotas, and egress",
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized(field: str, value: str) -> str:
    value = unicodedata.normalize("NFKC", value).translate(_ARABIC_DIGITS).strip()
    if field in {"amount", "reference", "recipient_identifier"}:
        return re.sub(r"[^0-9.]", "", value)
    if field == "currency":
        return (
            "EGP" if re.search(r"EGP|ج|جنيه", value, re.IGNORECASE) else value.upper()
        )
    return " ".join(value.casefold().split())


def _text_has(field: str, expected: str, text: str) -> bool:
    comparable = unicodedata.normalize("NFKC", text).translate(_ARABIC_DIGITS)
    if field == "amount":
        comparable = re.sub(r"(?<=\d),(?=\d)", "", comparable)
        return _normalized(field, expected) in re.findall(
            r"(?<![0-9.])[0-9]+(?:\.[0-9]+)?(?![0-9.])", comparable
        )
    if field in {"reference", "recipient_identifier"}:
        return _normalized(field, expected) in re.findall(
            r"(?<!\d)\d+(?!\d)", comparable
        )
    if field == "currency":
        if _normalized(field, expected) == "EGP":
            return bool(
                re.search(
                    r"\bEGP\b|ج\.?\s*م\.?|جنيه", comparable, re.IGNORECASE
                )
            )
        return bool(
            re.search(rf"\b{re.escape(expected)}\b", comparable, re.IGNORECASE)
        )
    compact_text = re.sub(r"\s+", " ", comparable.casefold())
    return _normalized(field, expected) in compact_text


def _raw_pair_has(sample: BakeoffSample, result: ProviderSampleResult) -> bool:
    amount = sample.expected.get("amount")
    currency = sample.expected.get("currency")
    if amount is None or currency is None:
        return False
    line_texts = [
        str(line["text"]) for line in result.evidence if line.get("text")
    ] or result.raw_text.splitlines()
    return any(
        _text_has("amount", amount, text) and _text_has("currency", currency, text)
        for text in line_texts
    )


def _raw_field_has(
    sample: BakeoffSample, result: ProviderSampleResult, field: str, expected: str
) -> bool:
    if (
        field == "currency"
        and "amount" in sample.expected
        and "amount" not in sample.uncertain_fields
    ):
        return _raw_pair_has(sample, result)
    return _text_has(field, expected, result.raw_text)


def _selected_exact(result: ProviderSampleResult, field: str, expected: str) -> bool:
    selected = result.selected_fields.get(field)
    if selected is None:
        return False
    value = str(selected.get("normalized", selected.get("value", "")))
    return _normalized(field, value) == _normalized(field, expected)


def _ratio(exact: int, scorable: int) -> dict[str, Any]:
    return {
        "exact": exact,
        "scorable": scorable,
        "accuracy": exact / scorable if scorable else None,
    }


def _field_metric(
    samples: Iterable[BakeoffSample],
    by_id: dict[str, ProviderSampleResult],
    field: str,
) -> dict[str, Any]:
    exact = 0
    scorable = 0
    for sample in samples:
        expected = sample.expected.get(field)
        if expected is None or field in sample.uncertain_fields:
            continue
        scorable += 1
        exact += _selected_exact(by_id[sample.sample_id], field, expected)
    return _ratio(exact, scorable)


def _group_accuracy(
    samples: Iterable[BakeoffSample],
    by_id: dict[str, ProviderSampleResult],
) -> dict[str, Any]:
    exact = 0
    scorable = 0
    for sample in samples:
        for field, expected in sample.expected.items():
            if field in sample.uncertain_fields:
                continue
            scorable += 1
            exact += _selected_exact(by_id[sample.sample_id], field, expected)
    return _ratio(exact, scorable)


def _raw_field_metric(
    samples: Iterable[BakeoffSample],
    by_id: dict[str, ProviderSampleResult],
    field: str,
) -> dict[str, Any]:
    exact = 0
    scorable = 0
    for sample in samples:
        expected = sample.expected.get(field)
        if expected is None or field in sample.uncertain_fields:
            continue
        scorable += 1
        exact += _raw_field_has(sample, by_id[sample.sample_id], field, expected)
    return _ratio(exact, scorable)


def _raw_group_accuracy(
    samples: Iterable[BakeoffSample],
    by_id: dict[str, ProviderSampleResult],
) -> dict[str, Any]:
    exact = 0
    scorable = 0
    for sample in samples:
        for field, expected in sample.expected.items():
            if field in sample.uncertain_fields:
                continue
            scorable += 1
            exact += _raw_field_has(sample, by_id[sample.sample_id], field, expected)
    return _ratio(exact, scorable)


def _percentile_95(values: list[int]) -> float:
    if not values:
        raise ValueError("Cannot calculate latency percentile without values")
    ordered = sorted(values)
    index = max(0, (95 * len(ordered) + 99) // 100 - 1)
    return float(ordered[index])


def calculate_metrics(
    samples: list[BakeoffSample],
    results: list[ProviderSampleResult],
) -> dict[str, Any]:
    by_id = {result.sample_id: result for result in results}
    successful = [result for result in results if result.error is None]
    positives = [sample for sample in samples if sample.expected_receipt]
    negatives = [sample for sample in samples if not sample.expected_receipt]
    completeness_exact = 0
    completeness_scorable = 0
    for sample in positives:
        result = by_id[sample.sample_id]
        for field, expected in sample.expected.items():
            if field in sample.uncertain_fields:
                continue
            completeness_scorable += 1
            completeness_exact += _raw_field_has(sample, result, field, expected)
    pair_exact = 0
    pair_scorable = 0
    raw_pair_exact = 0
    for sample in positives:
        amount = sample.expected.get("amount")
        currency = sample.expected.get("currency")
        if amount is None or currency is None:
            continue
        if {"amount", "currency"}.intersection(sample.uncertain_fields):
            continue
        pair_scorable += 1
        result = by_id[sample.sample_id]
        raw_pair_exact += _raw_pair_has(sample, result)
        pair_exact += _selected_exact(result, "amount", amount) and _selected_exact(
            result, "currency", currency
        )
    participant_exact = 0
    raw_participant_exact = 0
    participant_scorable = 0
    for sample in positives:
        fields = [
            field
            for field in ("handle", "recipient_identifier")
            if field in sample.expected and field not in sample.uncertain_fields
        ]
        if not fields:
            continue
        participant_scorable += 1
        participant_exact += any(
            _selected_exact(by_id[sample.sample_id], field, sample.expected[field])
            for field in fields
        )
        raw_participant_exact += any(
            _raw_field_has(
                sample, by_id[sample.sample_id], field, sample.expected[field]
            )
            for field in fields
        )
    duplicate_samples = [
        sample
        for sample in positives
        if sample.duplicate_group is not None
        and "reference" in sample.expected
        and "reference" not in sample.uncertain_fields
    ]
    categories = {
        category: _group_accuracy(
            [sample for sample in positives if sample.language_category == category],
            by_id,
        )
        for category in ("arabic", "english", "mixed")
    }
    categories["photographed_screen"] = _group_accuracy(
        [sample for sample in positives if "photographed_screen" in sample.tags],
        by_id,
    )
    categories["compressed_or_cropped"] = _group_accuracy(
        [sample for sample in positives if "compressed_or_cropped" in sample.tags],
        by_id,
    )
    false_receipts = sum(
        by_id[sample.sample_id].receipt_classified for sample in negatives
    )
    false_promotions = sum(
        by_id[sample.sample_id].transaction_promoted for sample in negatives
    )
    description = _DESCRIPTIONS.get(
        results[0].provider_id,
        ProviderDescription(
            "Sum of invoked providers; workload-dependent",
            "High: confidence fallback, conflict routing, and two provider operations",
        ),
    )
    raw_categories = {
        category: _raw_group_accuracy(
            [sample for sample in positives if sample.language_category == category],
            by_id,
        )
        for category in ("arabic", "english", "mixed")
    }
    raw_categories["photographed_screen"] = _raw_group_accuracy(
        [sample for sample in positives if "photographed_screen" in sample.tags],
        by_id,
    )
    raw_categories["compressed_or_cropped"] = _raw_group_accuracy(
        [sample for sample in positives if "compressed_or_cropped" in sample.tags],
        by_id,
    )
    latencies = [result.duration_ms for result in successful]
    return {
        "sample_count": len(results),
        "successful_sample_count": len(successful),
        "errors": [
            {"sample_id": result.sample_id, "error": result.error}
            for result in results
            if result.error is not None
        ],
        "receipt_text_ocr_completeness": _ratio(
            completeness_exact, completeness_scorable
        ),
        "raw_ocr_coverage": {
            "amount": _raw_field_metric(positives, by_id, "amount"),
            "currency": _raw_field_metric(positives, by_id, "currency"),
            "amount_currency_pair": _ratio(raw_pair_exact, pair_scorable),
            "reference": _raw_field_metric(positives, by_id, "reference"),
            "date": _raw_field_metric(positives, by_id, "date"),
            "time": _raw_field_metric(positives, by_id, "time"),
            "handle": _raw_field_metric(positives, by_id, "handle"),
            "recipient_identifier": _raw_field_metric(
                positives, by_id, "recipient_identifier"
            ),
            "participant_handle_usefulness": _ratio(
                raw_participant_exact, participant_scorable
            ),
            "category_accuracy": raw_categories,
        },
        "amount": _field_metric(positives, by_id, "amount"),
        "currency": _field_metric(positives, by_id, "currency"),
        "amount_currency_pair": _ratio(pair_exact, pair_scorable),
        "reference": _field_metric(positives, by_id, "reference"),
        "date": _field_metric(positives, by_id, "date"),
        "time": _field_metric(positives, by_id, "time"),
        "handle": _field_metric(positives, by_id, "handle"),
        "recipient_identifier": _field_metric(
            positives, by_id, "recipient_identifier"
        ),
        "participant_handle_usefulness": _ratio(
            participant_exact, participant_scorable
        ),
        "duplicate_reference_consistency": _ratio(
            sum(
                _selected_exact(
                    by_id[sample.sample_id], "reference", sample.expected["reference"]
                )
                for sample in duplicate_samples
            ),
            len(duplicate_samples),
        ),
        "category_accuracy": categories,
        "negative_sample_count": len(negatives),
        "negative_false_receipt_classifications": false_receipts,
        "negative_false_transaction_promotions": false_promotions,
        "average_latency_ms": mean(latencies),
        "p95_latency_ms": _percentile_95(latencies),
        "max_latency_ms": max(latencies),
        "estimated_cost_per_1_000_receipt_images": description.cost_per_1_000,
        "operational_complexity": description.operational_complexity,
        "confidence_geometry_layout": {
            "confidence_values": sum(result.confidence_count for result in successful),
            "geometries": sum(result.geometry_count for result in successful),
            "tokens": sum(result.token_count for result in successful),
            "assessment": (
                "Line/token confidence and bounding geometry exposed"
                if sum(result.confidence_count for result in successful)
                and sum(result.geometry_count for result in successful)
                else "Incomplete confidence or geometry exposure"
            ),
        },
    }


def _tesseract_result(
    raw: dict[str, Any], sample: BakeoffSample
) -> ProviderSampleResult:
    selected = {
        field: dict(value["selected"])
        for field, value in raw.get("merged_fields", {}).items()
        if value.get("selected") is not None
    }
    conflicts = sorted(
        field
        for field, value in raw.get("merged_fields", {}).items()
        if value.get("conflict")
    )
    lines = [*raw.get("primary_evidence", []), *raw.get("targeted_ocr", [])]
    decision = raw.get("promotion_decision", {})
    return ProviderSampleResult(
        sample.sample_id,
        str(raw["source_sha256"]),
        "tesseract",
        "current-relay-pipeline-iteration4",
        int(raw["duration_ms"]),
        "\n".join(
            [str(raw.get("primary_text", ""))]
            + [str(item.get("text", "")) for item in raw.get("targeted_ocr", [])]
        ),
        selected,
        conflicts,
        bool(decision.get("receipt_classified")),
        bool(decision.get("transaction_candidate_promoted")),
        str(decision.get("explanation", "")),
        len(lines),
        sum(len(item.get("tokens", [])) for item in lines),
        sum(item.get("confidence") is not None for item in lines for _ in (None,))
        + sum(
            token.get("confidence") is not None
            for item in lines
            for token in item.get("tokens", [])
        ),
        sum(
            item.get("geometry") is not None or item.get("crop_geometry") is not None
            for item in lines
        ),
        evidence=lines,
    )


def load_tesseract_results(
    paths: list[Path], samples: list[BakeoffSample]
) -> list[ProviderSampleResult]:
    raw_by_id: dict[str, dict[str, Any]] = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_by_id.update({str(item["sample_id"]): item for item in payload["samples"]})
    expected_ids = {sample.sample_id for sample in samples}
    if set(raw_by_id) != expected_ids:
        raise ValueError("Tesseract result set does not match the bake-off dataset")
    results = [
        _tesseract_result(raw_by_id[sample.sample_id], sample) for sample in samples
    ]
    for sample, result in zip(samples, results, strict=True):
        actual = _sha256(sample.source_path)
        if actual != result.source_sha256:
            raise ValueError(f"Tesseract source hash mismatch for {sample.sample_id}")
        if sample.expected_sha256 and actual != sample.expected_sha256:
            raise ValueError(f"Manifest source hash mismatch for {sample.sample_id}")
    return results


def _mime_and_size(path: Path) -> tuple[str, int, int]:
    with Image.open(path) as image:
        format_name = str(image.format or "").upper()
        width, height = image.size
    mime = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
        "BMP": "image/bmp",
        "TIFF": "image/tiff",
    }.get(format_name)
    if mime is None:
        raise ValueError(f"Unsupported provider input format {format_name}: {path}")
    return mime, width, height


def _document_result(
    sample: BakeoffSample, document: OcrDocument, source_sha256: str
) -> ProviderSampleResult:
    mime, width, height = _mime_and_size(sample.source_path)
    del mime
    selected, conflicts, promotion = evaluate_document(
        document, image_width=width, image_height=height
    )
    lines = document.evidence_lines
    evidence = [line.as_json() for line in lines]
    return ProviderSampleResult(
        sample.sample_id,
        source_sha256,
        document.provider_id,
        document.provider_version,
        document.duration_ms,
        document.normalized_text,
        selected,
        conflicts,
        bool(promotion["receipt_classified"]),
        bool(promotion["transaction_promoted"]),
        str(promotion["explanation"]),
        len(lines),
        sum(len(line.tokens) for line in lines),
        sum(line.confidence is not None for line in lines)
        + sum(token.confidence is not None for line in lines for token in line.tokens),
        sum(line.bounding_box is not None for line in lines)
        + sum(
            token.bounding_box is not None for line in lines for token in line.tokens
        ),
        evidence=evidence,
    )


async def run_cloud_provider(
    provider: Any,
    samples: list[BakeoffSample],
    output: Path,
    timeout_seconds: float,
) -> list[ProviderSampleResult]:
    provider_dir = output / "provider-samples" / str(provider.provider_id)
    provider_dir.mkdir(parents=True, exist_ok=True)
    results: list[ProviderSampleResult] = []
    for sample in samples:
        source_hash = _sha256(sample.source_path)
        cache = provider_dir / f"{sample.sample_id}.json"
        if cache.is_file():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("source_sha256") == source_hash and not cached.get("error"):
                results.append(ProviderSampleResult(**cached))
                continue
        raw = sample.source_path.read_bytes()
        mime, width, height = _mime_and_size(sample.source_path)
        image = PreprocessedImage(raw, mime, width, height, ())
        try:
            document = await provider.extract(image, ("eng", "ara"), timeout_seconds)
            result = _document_result(sample, document, source_hash)
        except Exception as exc:  # benchmark must preserve per-sample provider failure
            result = ProviderSampleResult(
                sample.sample_id,
                source_hash,
                str(provider.provider_id),
                str(provider.provider_version),
                0,
                "",
                {},
                [],
                False,
                False,
                "Provider request failed; nothing was promoted.",
                0,
                0,
                0,
                0,
                error=(
                    str(exc)
                    if isinstance(exc, ProviderHttpError)
                    else f"{type(exc).__name__}: provider request failed"
                ),
            )
        cache.write_text(
            json.dumps(result.as_json(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        results.append(result)
    return results


def _accuracy(metric: dict[str, Any]) -> str:
    value = metric.get("accuracy")
    return "not_measured" if value is None else f"{value * 100:.1f}%"


def _hybrid(
    samples: list[BakeoffSample],
    provider_results: dict[str, list[ProviderSampleResult]],
) -> dict[str, Any]:
    if len(provider_results) != 3 or any(
        len(results) != len(samples) or any(item.error for item in results)
        for results in provider_results.values()
    ):
        return {"status": "not_measured", "reason": "all three providers required"}
    candidates: list[dict[str, Any]] = []
    for primary_id, fallback_id in (
        ("tesseract", "google_document_ai"),
        ("tesseract", "azure_document_intelligence"),
        ("google_document_ai", "azure_document_intelligence"),
        ("google_document_ai", "tesseract"),
        ("azure_document_intelligence", "google_document_ai"),
        ("azure_document_intelligence", "tesseract"),
    ):
        primary = {item.sample_id: item for item in provider_results[primary_id]}
        fallback = {item.sample_id: item for item in provider_results[fallback_id]}
        chosen: list[ProviderSampleResult] = []
        fallback_count = 0
        conflict_count = 0
        for sample in samples:
            first = primary[sample.sample_id]
            second = fallback[sample.sample_id]
            core = [
                value
                for field, value in first.selected_fields.items()
                if field in {"amount", "currency", "reference"}
            ]
            insufficient = (
                bool(first.conflicts)
                or not core
                or any(float(value.get("confidence", 0.0)) < 0.85 for value in core)
            )
            if not insufficient:
                chosen.append(first)
                continue
            fallback_count += 1
            overlaps = set(first.selected_fields).intersection(second.selected_fields)
            conflicting = any(
                _normalized(
                    field,
                    str(first.selected_fields[field].get("normalized", "")),
                )
                != _normalized(
                    field,
                    str(second.selected_fields[field].get("normalized", "")),
                )
                for field in overlaps
            )
            if conflicting:
                conflict_count += 1
                chosen.append(
                    ProviderSampleResult(
                        sample.sample_id,
                        first.source_sha256,
                        f"{primary_id}->{fallback_id}",
                        "conflict-needs-review",
                        first.duration_ms + second.duration_ms,
                        first.raw_text + "\n" + second.raw_text,
                        {},
                        sorted(overlaps),
                        False,
                        False,
                        "Provider conflict routed to NEEDS_REVIEW; values were "
                        "not merged.",
                        first.line_count + second.line_count,
                        first.token_count + second.token_count,
                        first.confidence_count + second.confidence_count,
                        first.geometry_count + second.geometry_count,
                    )
                )
            else:
                chosen.append(second)
        metrics = calculate_metrics(samples, chosen)
        candidates.append(
            {
                "strategy": f"{primary_id}->{fallback_id}",
                "fallback_count": fallback_count,
                "conflict_needs_review_count": conflict_count,
                "metrics": metrics,
            }
        )
    best = max(
        candidates,
        key=lambda item: (
            item["metrics"]["amount_currency_pair"]["accuracy"] or 0.0,
            item["metrics"]["reference"]["accuracy"] or 0.0,
            -item["metrics"]["negative_false_transaction_promotions"],
            -item["conflict_needs_review_count"],
        ),
    )
    return {"status": "measured", "best": best, "candidates": candidates}


def _status_value(metrics: dict[str, Any] | None, key: str) -> str:
    return "not_measured" if metrics is None else _accuracy(metrics[key])


def _write_report(payload: dict[str, Any], output: Path) -> None:
    metrics = payload["metrics"]
    statuses = payload["provider_status"]
    all_complete = all(statuses.get(name) == "complete" for name in _DESCRIPTIONS)
    tesseract_arabic = (
        metrics.get("tesseract", {})
        .get("category_accuracy", {})
        .get("arabic", {})
        .get("accuracy")
    )
    cost_acceptable = {
        "tesseract": True,
        "google_document_ai": True,
        "azure_document_intelligence": False,
    }
    eligible = []
    gate_evidence: dict[str, dict[str, bool]] = {}
    for name, value in metrics.items():
        arabic = value["category_accuracy"]["arabic"]["accuracy"]
        checks = {
            "reference_at_least_95_percent": (
                (value["reference"]["accuracy"] or 0.0) >= 0.95
            ),
            "amount_currency_at_least_95_percent": (
                (value["amount_currency_pair"]["accuracy"] or 0.0) >= 0.95
            ),
            "zero_negative_transaction_promotions": (
                value["negative_false_transaction_promotions"] == 0
            ),
            "no_serious_arabic_regression": bool(
                arabic is not None
                and (tesseract_arabic is None or arabic + 0.05 >= tesseract_arabic)
            ),
            "latency_operationally_acceptable": value["average_latency_ms"] <= 10_000,
            "cost_operationally_acceptable": cost_acceptable.get(name, False),
        }
        gate_evidence[name] = checks
        if all(checks.values()):
            eligible.append(name)
    best_single = eligible[0] if eligible else "none"
    hybrid = payload["hybrid"]
    best_hybrid = (
        hybrid["best"]["strategy"]
        if hybrid.get("status") == "measured"
        else "not_measured"
    )
    recommendation = (
        f"guarded_pilot_{best_single}"
        if best_single != "none"
        else (
            "B_hybrid_provider_strategy"
            if hybrid.get("status") == "measured"
            else "measurement_incomplete_no_production_change"
        )
    )
    negative_values = ";".join(
        f"{name}="
        + (
            str(metrics[name]["negative_false_transaction_promotions"])
            if name in metrics
            else "not_measured"
        )
        for name in _DESCRIPTIONS
    )
    status = {
        "OCR_BAKEOFF_STATUS": (
            "COMPLETE" if all_complete else "PARTIAL_MISSING_CLOUD_CREDENTIALS"
        ),
        "TESSERACT_REFERENCE_ACCURACY": _status_value(
            metrics.get("tesseract"), "reference"
        ),
        "TESSERACT_AMOUNT_CURRENCY_ACCURACY": _status_value(
            metrics.get("tesseract"), "amount_currency_pair"
        ),
        "GOOGLE_REFERENCE_ACCURACY": _status_value(
            metrics.get("google_document_ai"), "reference"
        ),
        "GOOGLE_AMOUNT_CURRENCY_ACCURACY": _status_value(
            metrics.get("google_document_ai"), "amount_currency_pair"
        ),
        "AZURE_REFERENCE_ACCURACY": _status_value(
            metrics.get("azure_document_intelligence"), "reference"
        ),
        "AZURE_AMOUNT_CURRENCY_ACCURACY": _status_value(
            metrics.get("azure_document_intelligence"), "amount_currency_pair"
        ),
        "BEST_SINGLE_PROVIDER": best_single if all_complete else "not_measured",
        "BEST_HYBRID_STRATEGY": best_hybrid,
        "NEGATIVE_FALSE_PROMOTIONS": negative_values,
        "RECOMMENDED_PRODUCTION_OCR_STRATEGY": recommendation,
        "MEETS_95_PERCENT_GATE": "yes" if eligible else "no",
        "PRODUCTION_SWITCH_IMPLEMENTED": "no",
        "P06_STARTED": "no",
    }
    payload["decision"] = {
        "eligible_single_providers": eligible,
        "provider_gate_evidence": gate_evidence,
        "latency_acceptance_threshold_ms": 10_000,
        "serious_arabic_regression_margin": 0.05,
        "azure_cost_gate_note": (
            "Unresolved because the official price page requires a region/offer quote."
        ),
        "recommended_strategy": recommendation,
        "status": status,
    }
    columns = ["Metric", "Tesseract", "Google Document AI", "Azure DI Layout"]
    names = ["tesseract", "google_document_ai", "azure_document_intelligence"]

    def metric_cell(name: str, key: str) -> str:
        value = metrics.get(name)
        return "not measured" if value is None else _accuracy(value[key])

    table = ["| " + " | ".join(columns) + " |", "| --- | ---: | ---: | ---: |"]
    for label, key in (
        ("Reviewed-field text completeness", "receipt_text_ocr_completeness"),
        ("Exact amount", "amount"),
        ("Exact currency", "currency"),
        ("Exact amount+currency", "amount_currency_pair"),
        ("Exact reference", "reference"),
        ("Exact date", "date"),
        ("Exact time", "time"),
        ("Participant/handle usefulness", "participant_handle_usefulness"),
        ("Duplicate-group reference consistency", "duplicate_reference_consistency"),
    ):
        table.append(
            "| "
            + " | ".join([label, *(metric_cell(name, key) for name in names)])
            + " |"
        )
    for label, category in (
        ("Arabic reviewed-field accuracy", "arabic"),
        ("English reviewed-field accuracy", "english"),
        ("Mixed reviewed-field accuracy", "mixed"),
        ("Photographed-screen accuracy", "photographed_screen"),
        ("Compressed/cropped accuracy", "compressed_or_cropped"),
    ):
        cells = []
        for name in names:
            value = metrics.get(name)
            cells.append(
                "not measured"
                if value is None
                else _accuracy(value["category_accuracy"][category])
            )
        table.append("| " + " | ".join([label, *cells]) + " |")
    for label, key in (
        (
            "Negative false receipt classifications",
            "negative_false_receipt_classifications",
        ),
        (
            "Negative false transaction promotions",
            "negative_false_transaction_promotions",
        ),
        ("Average latency", "average_latency_ms"),
        ("Max latency", "max_latency_ms"),
        ("Estimated cost / 1,000", "estimated_cost_per_1_000_receipt_images"),
        ("Operational complexity", "operational_complexity"),
    ):
        cells = []
        for name in names:
            value = metrics.get(name)
            if value is None:
                if key == "estimated_cost_per_1_000_receipt_images":
                    cells.append(_DESCRIPTIONS[name].cost_per_1_000)
                elif key == "operational_complexity":
                    cells.append(_DESCRIPTIONS[name].operational_complexity)
                else:
                    cells.append("not measured")
            elif key.endswith("latency_ms"):
                cells.append(f"{value[key]:.0f} ms")
            else:
                cells.append(str(value[key]))
        table.append("| " + " | ".join([label, *cells]) + " |")
    quality_cells = []
    for name in names:
        value = metrics.get(name)
        if value is None:
            quality_cells.append("not measured")
            continue
        quality = value["confidence_geometry_layout"]
        quality_cells.append(
            f"{quality['assessment']} ({quality['confidence_values']} confidence, "
            f"{quality['geometries']} geometry values)"
        )
    table.append(
        "| " + " | ".join(["Confidence/geometry/layout quality", *quality_cells]) + " |"
    )
    lines = [
        "# Relay OCR Engine Bake-Off",
        "",
        "This is a benchmark-only comparison. No production provider, duplicate logic,",
        "review behavior, authenticity inference, or P06 work was changed.",
        "",
        *table,
        "",
        "## Methodology",
        "",
        "- The same unmodified image bytes are supplied to every provider; only each",
        "  provider's required request encoding differs.",
        "- Exact metrics exclude missing and explicitly uncertain ground truth.",
        "- Text completeness means raw-OCR coverage of reviewed field values because",
        "  the dataset does not contain a full character-level transcript.",
        "- All provider evidence is evaluated by the same conservative extraction and",
        "  transaction-promotion policy. Conflicts are retained and never auto-merged.",
        "- The Tesseract column imports the frozen current-pipeline evidence from the",
        "  completed 23-image Iteration 4 run; it is not another architecture",
        "  iteration.",
        "",
        "## Provider availability",
        "",
        *(f"- {name}: {statuses.get(name, 'not requested')}" for name in names),
        "",
        "No real non-InstaPay negatives were found; the negative benchmark is the six",
        "supplied synthetic samples and cannot establish production readiness alone.",
        "",
        "The OcrProvider boundary can represent all three providers. Google and Azure",
        "need benchmark-only REST adapters for their response schemas and",
        "authentication;",
        "no production adapter was installed or selected.",
        "",
        "## Hybrid strategy",
        "",
        (
            f"Best measured flow: {best_hybrid}. Conflicting values route to "
            "NEEDS_REVIEW and are never merged."
            if hybrid.get("status") == "measured"
            else "Not measured because both cloud provider result sets are required."
        ),
        "",
        "## Decision",
        "",
        f"Recommended strategy: {recommendation}.",
        "",
        "```text",
        *(f"{key}={value}" for key, value in status.items()),
        "```",
    ]
    output.mkdir(parents=True, exist_ok=True)
    (output / "ocr-bakeoff-results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "ocr-bakeoff-summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (output / "ocr-bakeoff-status.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in status.items()) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--positive-manifest", type=Path, required=True)
    parser.add_argument("--pilot-manifest", type=Path, required=True)
    parser.add_argument("--negative-manifest", type=Path, required=True)
    parser.add_argument(
        "--tesseract-results", type=Path, action="append", required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--providers", default="tesseract,google,azure")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    args = parser.parse_args()
    samples = load_dataset(
        args.positive_manifest.resolve(),
        args.pilot_manifest.resolve(),
        args.negative_manifest.resolve(),
    )
    if len(samples) != 23:
        raise ValueError(f"Expected the fixed 23-sample dataset, got {len(samples)}")
    requested = {item.strip() for item in args.providers.split(",") if item.strip()}
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    provider_results: dict[str, list[ProviderSampleResult]] = {}
    provider_status: dict[str, str] = {}
    if "tesseract" in requested:
        provider_results["tesseract"] = load_tesseract_results(
            [path.resolve() for path in args.tesseract_results], samples
        )
        provider_status["tesseract"] = "complete"
    if "google" in requested:
        processor = os.environ.get("GOOGLE_DOCUMENT_AI_PROCESSOR_NAME")
        credentials_available = bool(
            os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN")
            or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        )
        if not processor or not credentials_available:
            provider_status["google_document_ai"] = (
                "unavailable: credentials/processor missing"
            )
        else:
            results = asyncio.run(
                run_cloud_provider(
                    GoogleDocumentAiProvider(processor),
                    samples,
                    output,
                    args.timeout_seconds,
                )
            )
            provider_results["google_document_ai"] = results
            provider_status["google_document_ai"] = (
                "complete"
                if all(item.error is None for item in results)
                else "incomplete: provider errors"
            )
    if "azure" in requested:
        endpoint = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
        key = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_KEY")
        if not endpoint or not key:
            provider_status["azure_document_intelligence"] = (
                "unavailable: credentials/endpoint missing"
            )
        else:
            results = asyncio.run(
                run_cloud_provider(
                    AzureDocumentIntelligenceProvider(endpoint, key),
                    samples,
                    output,
                    args.timeout_seconds,
                )
            )
            provider_results["azure_document_intelligence"] = results
            provider_status["azure_document_intelligence"] = (
                "complete"
                if all(item.error is None for item in results)
                else "incomplete: provider errors"
            )
    metrics = {
        provider: calculate_metrics(samples, results)
        for provider, results in provider_results.items()
        if all(item.error is None for item in results)
    }
    payload: dict[str, Any] = {
        "dataset": {
            "sample_count": len(samples),
            "positive_receipt_count": sum(
                sample.expected_receipt for sample in samples
            ),
            "negative_count": sum(not sample.expected_receipt for sample in samples),
            "real_negative_count": 0,
            "samples": [
                {
                    "sample_id": sample.sample_id,
                    "source_sha256": _sha256(sample.source_path),
                    "expected_receipt": sample.expected_receipt,
                    "language_category": sample.language_category,
                    "scorable_fields": sorted(
                        set(sample.expected).difference(sample.uncertain_fields)
                    ),
                    "uncertain_fields": sorted(sample.uncertain_fields),
                    "tags": sorted(sample.tags),
                    "duplicate_group": sample.duplicate_group,
                }
                for sample in samples
            ],
        },
        "provider_status": provider_status,
        "metrics": metrics,
        "hybrid": _hybrid(samples, provider_results),
        "production_switch_implemented": False,
        "p06_started": False,
    }
    for provider, results in provider_results.items():
        (output / f"{provider}-results.json").write_text(
            json.dumps(
                {
                    "provider": provider,
                    "samples": [result.as_json() for result in results],
                    "metrics": metrics.get(provider),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    _write_report(payload, output)


if __name__ == "__main__":
    main()
