"""Run the isolated Google Document AI leg of the Relay OCR bake-off."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

from relay.application.processing_ports import OcrProvider
from tools.ocr_bakeoff.adapters import GoogleDocumentAiProvider
from tools.ocr_bakeoff.benchmark import (
    _sha256,
    calculate_metrics,
    run_cloud_provider,
)
from tools.ocr_bakeoff.dataset import load_dataset
from tools.ocr_bakeoff.models import BakeoffSample, ProviderSampleResult

_TESSERACT_ARABIC_ACCURACY = 0.533
_TESSERACT_MIXED_ACCURACY = 0.684
_MATERIAL_REGRESSION_MARGIN = 0.05
_GOOGLE_LIST_PRICE_PER_PAGE_USD = 0.0015


def _accuracy(metric: dict[str, Any] | None) -> str:
    if metric is None or metric.get("accuracy") is None:
        return "not_measured"
    return f"{float(metric['accuracy']) * 100:.1f}%"


def _credential_mode() -> str | None:
    if os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN"):
        return "oauth_access_token"
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return "application_default_credentials_file"
    try:
        if importlib.util.find_spec("google.auth") is not None:
            return "application_default_credentials"
    except ModuleNotFoundError:
        pass
    return None


def _valid_cached_ids(output: Path, samples: list[BakeoffSample]) -> frozenset[str]:
    cache_dir = output / "provider-samples" / "google_document_ai"
    valid: set[str] = set()
    for sample in samples:
        cache = cache_dir / f"{sample.sample_id}.json"
        if not cache.is_file():
            continue
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if payload.get("source_sha256") == _sha256(
            sample.source_path
        ) and not payload.get("error"):
            valid.add(sample.sample_id)
    return frozenset(valid)


def _dataset_payload(samples: list[BakeoffSample]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for sample in samples:
        source_hash = _sha256(sample.source_path)
        if sample.expected_sha256 and source_hash != sample.expected_sha256:
            raise ValueError(f"Frozen source hash mismatch for {sample.sample_id}")
        entries.append(
            {
                "sample_id": sample.sample_id,
                "source_sha256": source_hash,
                "expected_receipt": sample.expected_receipt,
                "language_category": sample.language_category,
                "scorable_fields": sorted(
                    set(sample.expected).difference(sample.uncertain_fields)
                ),
                "uncertain_fields": sorted(sample.uncertain_fields),
                "tags": sorted(sample.tags),
                "duplicate_group": sample.duplicate_group,
            }
        )
    return {
        "sample_count": len(samples),
        "positive_receipt_count": sum(sample.expected_receipt for sample in samples),
        "synthetic_negative_count": sum(
            not sample.expected_receipt for sample in samples
        ),
        "real_negative_count": 0,
        "original_bytes_only": True,
        "samples": entries,
    }


def _write_separated_evidence(
    output: Path, results: list[ProviderSampleResult]
) -> None:
    raw_dir = output / "raw-google-evidence"
    extraction_dir = output / "relay-extraction-results"
    raw_dir.mkdir(parents=True, exist_ok=True)
    extraction_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        raw_payload = {
            "sample_id": result.sample_id,
            "source_sha256": result.source_sha256,
            "provider_id": result.provider_id,
            "provider_version": result.provider_version,
            "duration_ms": result.duration_ms,
            "raw_text": result.raw_text,
            "lines": result.evidence,
            "line_count": result.line_count,
            "token_count": result.token_count,
            "confidence_count": result.confidence_count,
            "geometry_count": result.geometry_count,
            "error": result.error,
        }
        extraction_payload = {
            "sample_id": result.sample_id,
            "source_sha256": result.source_sha256,
            "selected_fields": result.selected_fields,
            "conflicts": result.conflicts,
            "receipt_classified": result.receipt_classified,
            "transaction_promoted": result.transaction_promoted,
            "promotion_explanation": result.promotion_explanation,
            "error": result.error,
        }
        (raw_dir / f"{result.sample_id}.json").write_text(
            json.dumps(raw_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (extraction_dir / f"{result.sample_id}.json").write_text(
            json.dumps(extraction_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _metric_rows(metrics: dict[str, Any], *, raw: bool) -> list[str]:
    source = metrics["raw_ocr_coverage"] if raw else metrics
    labels = (
        ("Amount", "amount"),
        ("Currency", "currency"),
        ("Amount + currency pair", "amount_currency_pair"),
        ("Reference", "reference"),
        ("Date", "date"),
        ("Time", "time"),
        ("Exact handle", "handle"),
        ("Exact recipient identifier", "recipient_identifier"),
    )
    rows = ["| Metric | Exact / scorable | Accuracy |", "| --- | ---: | ---: |"]
    if raw:
        completeness = metrics["receipt_text_ocr_completeness"]
        rows.append(
            "| Reviewed-field OCR completeness | "
            f"{completeness['exact']} / {completeness['scorable']} | "
            f"{_accuracy(completeness)} |"
        )
    for label, key in labels:
        value = source[key]
        rows.append(
            f"| {label} | {value['exact']} / {value['scorable']} | {_accuracy(value)} |"
        )
    value = source["participant_handle_usefulness"]
    rows.append(
        f"| Participant/handle usefulness | {value['exact']} / "
        f"{value['scorable']} | {_accuracy(value)} |"
    )
    categories = source["category_accuracy"]
    for label, key in (
        ("Arabic reviewed fields", "arabic"),
        ("English reviewed fields", "english"),
        ("Mixed-language reviewed fields", "mixed"),
        ("Photographed-screen reviewed fields", "photographed_screen"),
        ("Compressed/cropped reviewed fields", "compressed_or_cropped"),
    ):
        value = categories[key]
        rows.append(
            f"| {label} | {value['exact']} / {value['scorable']} | {_accuracy(value)} |"
        )
    return rows


def _status_block(
    status: str,
    metrics: dict[str, Any] | None,
    *,
    complete: bool,
    gate: bool,
) -> dict[str, str]:
    value = metrics if complete else None
    categories = value["category_accuracy"] if value is not None else {}
    return {
        "GOOGLE_BAKEOFF_STATUS": status,
        "GOOGLE_REFERENCE_ACCURACY": _accuracy(
            value["reference"] if value is not None else None
        ),
        "GOOGLE_AMOUNT_ACCURACY": _accuracy(
            value["amount"] if value is not None else None
        ),
        "GOOGLE_CURRENCY_ACCURACY": _accuracy(
            value["currency"] if value is not None else None
        ),
        "GOOGLE_AMOUNT_CURRENCY_ACCURACY": _accuracy(
            value["amount_currency_pair"] if value is not None else None
        ),
        "GOOGLE_DATE_ACCURACY": _accuracy(value["date"] if value is not None else None),
        "GOOGLE_TIME_ACCURACY": _accuracy(value["time"] if value is not None else None),
        "GOOGLE_ARABIC_ACCURACY": _accuracy(categories.get("arabic")),
        "GOOGLE_ENGLISH_ACCURACY": _accuracy(categories.get("english")),
        "GOOGLE_MIXED_ACCURACY": _accuracy(categories.get("mixed")),
        "GOOGLE_PHOTO_ACCURACY": _accuracy(categories.get("photographed_screen")),
        "GOOGLE_NEGATIVE_FALSE_RECEIPTS": (
            str(value["negative_false_receipt_classifications"])
            if value is not None
            else "not_measured"
        ),
        "GOOGLE_NEGATIVE_FALSE_TRANSACTION_PROMOTIONS": (
            str(value["negative_false_transaction_promotions"])
            if value is not None
            else "not_measured"
        ),
        "GOOGLE_AVERAGE_LATENCY_MS": (
            f"{value['average_latency_ms']:.0f}"
            if value is not None
            else "not_measured"
        ),
        "GOOGLE_MAX_LATENCY_MS": (
            f"{value['max_latency_ms']:.0f}" if value is not None else "not_measured"
        ),
        "GOOGLE_MEETS_95_PERCENT_GATE": "yes" if gate else "no",
        "NEXT_PROVIDER_REQUIRED": "yes" if complete and not gate else "no",
        "PRODUCTION_INTEGRATION_RECOMMENDED": "yes" if gate else "no",
        "PRODUCTION_SWITCH_IMPLEMENTED": "no",
        "P06_STARTED": "no",
    }


def _write_report(
    output: Path,
    *,
    dataset: dict[str, Any],
    provider_status: str,
    credential_mode: str | None,
    metrics: dict[str, Any] | None,
    results: list[ProviderSampleResult],
    api_calls: int,
    cache_hits: int,
) -> None:
    complete = bool(results) and all(result.error is None for result in results)
    arabic = metrics["category_accuracy"]["arabic"]["accuracy"] if metrics else None
    mixed = metrics["category_accuracy"]["mixed"]["accuracy"] if metrics else None
    gate_checks = {
        "reference_at_least_95_percent": bool(
            complete and metrics and (metrics["reference"]["accuracy"] or 0.0) >= 0.95
        ),
        "amount_currency_at_least_95_percent": bool(
            complete
            and metrics
            and (metrics["amount_currency_pair"]["accuracy"] or 0.0) >= 0.95
        ),
        "zero_negative_transaction_promotions": bool(
            complete
            and metrics
            and metrics["negative_false_transaction_promotions"] == 0
        ),
        "no_material_arabic_or_mixed_regression": bool(
            complete
            and arabic is not None
            and mixed is not None
            and arabic + _MATERIAL_REGRESSION_MARGIN >= _TESSERACT_ARABIC_ACCURACY
            and mixed + _MATERIAL_REGRESSION_MARGIN >= _TESSERACT_MIXED_ACCURACY
        ),
    }
    gate = all(gate_checks.values())
    if "BILLING_DISABLED" in provider_status:
        status = "BLOCKED_BILLING_DISABLED"
    elif not credential_mode:
        status = "BLOCKED_MISSING_GOOGLE_CONFIGURATION"
    elif not complete:
        status = "INCOMPLETE_PROVIDER_ERRORS"
    elif gate:
        status = "COMPLETE_PASS"
    else:
        status = "COMPLETE_FAIL"
    status_values = _status_block(status, metrics, complete=complete, gate=gate)
    successful_calls = sum(result.error is None for result in results) - cache_hits
    estimated_cost = max(0, successful_calls) * _GOOGLE_LIST_PRICE_PER_PAGE_USD
    recommendation = (
        "Guarded OcrProvider integration proposal; do not switch automatically."
        if gate
        else (
            "Run Azure Document Intelligence next; do not integrate Google."
            if complete
            else "Complete the real Google run before making a provider decision."
        )
    )
    payload: dict[str, Any] = {
        "dataset": dataset,
        "provider_status": provider_status,
        "credential_mode": credential_mode or "not_configured",
        "metrics": metrics,
        "measurement_method": {
            "raw_completeness": (
                "Exact coverage of reviewed fields, not a full transcript"
            ),
            "raw_numeric_fields": "Contiguous exact numeric tokens; no substrings",
            "raw_currency_and_pair": (
                "Expected currency on the same OCR line as the exact transfer amount; "
                "fee and total lines cannot supply it"
            ),
            "latency": (
                "OCR HTTP request/response time; excludes OAuth refresh "
                "and local extraction"
            ),
            "p95": "Nearest-rank percentile over 23 successful OCR request durations",
        },
        "run_consumption": {
            "api_calls_made": api_calls,
            "cache_hits": cache_hits,
            "pages_submitted": api_calls,
            "pages_processed": len(results)
            - sum(result.error is not None for result in results),
            "successful_new_pages": max(0, successful_calls),
            "list_price_estimate_usd": round(estimated_cost, 4),
            "measured_billed_cost_usd": None,
            "cost_note": (
                "List-price estimate only; the first 1,000 monthly Enterprise OCR "
                "pages may be free and billing-account usage was not inspected."
            ),
        },
        "gate": {
            "checks": gate_checks,
            "material_regression_margin": _MATERIAL_REGRESSION_MARGIN,
            "baseline_tesseract_arabic_accuracy": _TESSERACT_ARABIC_ACCURACY,
            "baseline_tesseract_mixed_accuracy": _TESSERACT_MIXED_ACCURACY,
            "eligible": gate,
        },
        "confidence_geometry_layout": (
            metrics.get("confidence_geometry_layout") if metrics else None
        ),
        "operational_requirements": [
            "Google Cloud project with Document AI API enabled",
            "Enterprise Document OCR processor in an approved region",
            (
                "Least-privilege caller identity and Application Default Credentials "
                "or short-lived token"
            ),
            (
                "Network egress, regional data-handling review, quota monitoring, "
                "and billing controls"
            ),
            (
                "Secret rotation without embedding credentials in Relay "
                "configuration or artifacts"
            ),
        ],
        "decision": {
            "production_integration_recommended": gate,
            "next_provider_required": complete and not gate,
            "recommendation": recommendation,
            "production_switch_implemented": False,
            "p06_started": False,
        },
        "status": status_values,
    }
    lines = [
        "# Relay Google Document AI OCR Bake-Off",
        "",
        "This is a benchmark-only Google run over the frozen 23-image Relay set.",
        "Production OCR behavior, duplicate/review semantics, stored pilot results,",
        "and P06 were not changed.",
        "",
        "## Provider status",
        "",
        f"- {provider_status}",
        f"- Credential mode: {credential_mode or 'not configured'}",
        (
            "- Original image bytes only; request base64 is transport encoding, "
            "not enhancement."
        ),
        "- Raw provider evidence and Relay extraction results are stored separately.",
        (
            "- Raw numeric coverage requires exact tokens. Currency and pair coverage "
            "require the exact transfer amount and currency on the same OCR line."
        ),
        "- Fee/total currency does not fill a missing transfer currency.",
        "- Missing or uncertain ground truth is excluded from accuracy denominators.",
        (
            "- Latency measures the OCR HTTP request/response and excludes OAuth "
            "refresh and local extraction; p95 uses nearest rank."
        ),
        "",
    ]
    if metrics:
        negative_count = metrics["negative_sample_count"]
        false_receipts = metrics["negative_false_receipt_classifications"]
        false_promotions = metrics["negative_false_transaction_promotions"]
        confidence = metrics["confidence_geometry_layout"]["confidence_values"]
        geometries = metrics["confidence_geometry_layout"]["geometries"]
        average_latency = metrics["average_latency_ms"]
        p95_latency = metrics["p95_latency_ms"]
        max_latency = metrics["max_latency_ms"]
        lines.extend(
            [
                "## A. Raw provider OCR coverage",
                "",
                *_metric_rows(metrics, raw=True),
                "",
                "## B. Final Relay structured extraction accuracy",
                "",
                *_metric_rows(metrics, raw=False),
                "",
                "## Safety and operations",
                "",
                (
                    f"- Synthetic negative false receipts: {false_receipts} / "
                    f"{negative_count}"
                ),
                (
                    "- Synthetic negative false transaction promotions: "
                    f"{false_promotions} / {negative_count}"
                ),
                (
                    "- Average / p95 / max latency: "
                    f"{average_latency:.0f} / {p95_latency:.0f} / "
                    f"{max_latency:.0f} ms"
                ),
                (
                    "- API calls / pages submitted / cache hits: "
                    f"{api_calls} / {api_calls} / {cache_hits}"
                ),
                (
                    "- List-price estimate for successful new pages: "
                    f"USD {estimated_cost:.4f}; billed cost was not inspected."
                ),
                f"- Confidence values exposed: {confidence}",
                f"- Bounding geometries exposed: {geometries}",
                "",
            ]
        )
    else:
        lines.extend(
            [
                f"- Benchmark API attempts: {api_calls}",
                "- Successfully processed pages: 0",
                "- Raw OCR coverage and Relay accuracy: not measured",
                "",
            ]
        )
    lines.extend(
        [
            "The six negatives are synthetic; no real non-InstaPay negative exists in",
            (
                "the frozen set, so this benchmark cannot independently establish "
                "real-world"
            ),
            "negative safety.",
            "",
            "## Operational requirements",
            "",
            (
                "- Enabled Enterprise Document OCR processor, Document AI API, "
                "and billing."
            ),
            "- Caller permission `documentai.processors.processOnline` and secure ADC.",
            (
                "- Regional API endpoint, network egress, quota monitoring, "
                "and evidence storage."
            ),
            (
                "- The existing `OcrProvider` boundary carries line/token "
                "confidence and geometry."
            ),
            (
                "- The actual processor version was not exposed by the "
                "saved response mapping."
            ),
            "",
            "## Decision",
            "",
            recommendation,
            "",
            "```text",
            *(f"{key}={value}" for key, value in status_values.items()),
            "```",
        ]
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "google-document-ai-results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "google-document-ai-summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (output / "google-document-ai-status.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in status_values.items()) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--positive-manifest", type=Path, required=True)
    parser.add_argument("--pilot-manifest", type=Path, required=True)
    parser.add_argument("--negative-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    args = parser.parse_args()
    samples = load_dataset(
        args.positive_manifest.resolve(),
        args.pilot_manifest.resolve(),
        args.negative_manifest.resolve(),
    )
    if len(samples) != 23:
        raise ValueError(f"Expected the fixed 23-sample dataset, got {len(samples)}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    dataset = _dataset_payload(samples)
    (output / "frozen-dataset.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    processor = os.environ.get("GOOGLE_DOCUMENT_AI_PROCESSOR_NAME")
    credential_mode = _credential_mode()
    if not processor or not credential_mode:
        missing = []
        if not processor:
            missing.append("processor resource name")
        if not credential_mode:
            missing.append("credential source")
        _write_report(
            output,
            dataset=dataset,
            provider_status="blocked: missing " + " and ".join(missing),
            credential_mode=credential_mode,
            metrics=None,
            results=[],
            api_calls=0,
            cache_hits=0,
        )
        return
    cached_before = _valid_cached_ids(output, samples)
    provider: OcrProvider = GoogleDocumentAiProvider(processor)
    results = asyncio.run(
        run_cloud_provider(
            provider,
            samples,
            output,
            args.timeout_seconds,
        )
    )
    _write_separated_evidence(output, results)
    api_calls = len(samples) - len(cached_before)
    errors = sum(result.error is not None for result in results)
    metrics = calculate_metrics(samples, results) if errors == 0 else None
    _write_report(
        output,
        dataset=dataset,
        provider_status=(
            "complete"
            if errors == 0
            else f"incomplete: {errors} provider request errors"
        ),
        credential_mode=credential_mode,
        metrics=metrics,
        results=results,
        api_calls=api_calls,
        cache_hits=len(cached_before),
    )


if __name__ == "__main__":
    main()
