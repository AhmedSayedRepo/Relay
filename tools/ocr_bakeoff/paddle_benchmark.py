"""One frozen, self-hosted PaddleOCR CPU benchmark and hard decision."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import re
import socket
import time
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from importlib import import_module
from importlib.metadata import distributions
from pathlib import Path
from typing import Any

from relay.application.processing_ports import OcrProvider
from relay.domain.processing import OcrDocument, PreprocessedImage
from tools.ocr_bakeoff.benchmark import (
    _document_result,
    _mime_and_size,
    _sha256,
    calculate_metrics,
)
from tools.ocr_bakeoff.dataset import load_dataset
from tools.ocr_bakeoff.google_benchmark import _dataset_payload, _metric_rows
from tools.ocr_bakeoff.models import ProviderSampleResult
from tools.ocr_bakeoff.paddle_provider import PaddleOcrProvider

CRITICAL_FIELDS = ("reference", "amount", "currency", "date", "time")
TRUST_CONFIDENCE = 0.95
_LOCAL_SOCKET_CONNECT = socket.socket.connect


def decide(metrics: dict[str, Any]) -> tuple[str, bool]:
    reference = metrics["reference"]["accuracy"]
    pair = metrics["amount_currency_pair"]["accuracy"]
    if reference is None or pair is None:
        raise ValueError("Required gate fields have no reviewed denominator")
    safe = metrics["negative_false_transaction_promotions"] == 0
    gate = reference >= 0.95 and pair >= 0.95 and safe
    if gate:
        return "INTEGRATE", True
    if reference >= 0.90 and pair >= 0.90 and safe:
        return "REVIEW_FIRST", False
    return "REJECT_PROVIDER", False


def evaluate_trust(
    document: OcrDocument, result: ProviderSampleResult
) -> dict[str, Any]:
    """Evidence eligibility is not validated authority to auto-link duplicates."""
    fields: dict[str, Any] = {}
    for name in CRITICAL_FIELDS:
        candidate = result.selected_fields.get(name)
        reasons = []
        if candidate is None:
            reasons.append("missing_or_unselected")
        else:
            source = str(candidate.get("source_pass", ""))
            source_index = source.rsplit("_", 1)[-1]
            line = (
                document.lines[int(source_index)]
                if source_index.isdigit() and int(source_index) < len(document.lines)
                else None
            )
            confidence = float(candidate.get("confidence", 0))
            if not math.isfinite(confidence) or confidence < TRUST_CONFIDENCE:
                reasons.append("insufficient_native_span_confidence")
            if not candidate.get("normalized") or not candidate.get(
                "supporting_structural_evidence"
            ):
                reasons.append("missing_structural_validation")
            normalized = str(candidate.get("normalized", ""))
            valid = True
            try:
                if name == "reference":
                    valid = re.fullmatch(r"[1-9][0-9]{11}", normalized) is not None
                elif name == "amount":
                    amount = Decimal(normalized)
                    valid = amount.is_finite() and amount > 0
                elif name == "currency":
                    valid = normalized == "EGP" and bool(
                        re.search(
                            r"\bEGP\b|ج\.?\s*م\.?|جنيه",
                            str(candidate.get("original_ocr_text", "")),
                            re.IGNORECASE,
                        )
                    )
                elif name == "date":
                    datetime.strptime(normalized, "%d %b %Y")
                elif name == "time":
                    datetime.strptime(normalized, "%I:%M %p")
            except (ValueError, InvalidOperation):
                valid = False
            if not valid:
                reasons.append("invalid_field_structure")
            if (
                line is None
                or not line.bounding_box
                or not candidate.get("crop_geometry")
            ):
                reasons.append("untraceable_evidence")
            elif line.text != candidate.get("original_ocr_text"):
                reasons.append("source_text_mismatch")
            if name in result.conflicts or candidate.get("conflicts"):
                reasons.append("conflicting_candidate")
        fields[name] = {"evidence_eligible": not reasons, "reasons": reasons}
    all_eligible = (
        all(value["evidence_eligible"] for value in fields.values())
        and result.receipt_classified
        and not result.conflicts
    )
    return {
        "fields": fields,
        "all_critical_evidence_eligible": all_eligible,
        "receipt_classified": result.receipt_classified,
        "unresolved_conflicts": result.conflicts,
        "automatic_trust_validated": False,
        "review_status": "NEEDS_REVIEW",
        "transaction_promoted": False,
        "duplicate_link_eligible": False,
        "reason": (
            "Evidence eligibility is provisional; operator confirmation is required "
            "until automatic critical-field/reference trust is independently validated."
        ),
    }


def _percentage(metric: dict[str, Any]) -> str:
    value = metric.get("accuracy")
    return "not_measured" if value is None else f"{100 * value:.1f}%"


def _network_denied(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("Network disabled during local receipt inference")


def _local_connect(connection: socket.socket, address: Any) -> Any:
    # Windows asyncio's internal socketpair needs a numeric loopback connection.
    if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
        return _LOCAL_SOCKET_CONNECT(connection, address)
    return _network_denied()


def run(args: argparse.Namespace) -> None:
    output: Path = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    samples = load_dataset(
        args.positive_manifest.resolve(),
        args.pilot_manifest.resolve(),
        args.negative_manifest.resolve(),
    )
    frozen = json.loads(args.frozen_dataset.read_text(encoding="utf-8"))
    if len(samples) != 23 or _dataset_payload(samples) != frozen:
        raise ValueError("Source bytes or reviewed metadata differ from frozen dataset")
    (output / "frozen-dataset.json").write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Load local artifacts only. Disallow even model/font downloads during inference.
    psutil = import_module("psutil")
    process = psutil.Process()
    for directory in (
        "raw-paddle-evidence",
        "relay-extraction-results",
        "trust-results",
    ):
        (output / directory).mkdir(exist_ok=True)
    if list((output / "raw-paddle-evidence").glob("*.json")):
        raise ValueError("Refusing to overwrite an existing measured run")
    startup_started = time.perf_counter()
    socket.socket.connect = _local_connect  # type: ignore[assignment,method-assign]
    socket.create_connection = _network_denied
    implementation = PaddleOcrProvider(args.model_root.resolve())
    provider: OcrProvider = implementation
    startup_ms = round((time.perf_counter() - startup_started) * 1000)
    print(f"Local CPU provider ready; startup {startup_ms} ms", flush=True)
    results: list[ProviderSampleResult] = []
    trusts: list[dict[str, Any]] = []
    for sample in samples:
        mime, width, height = _mime_and_size(sample.source_path)
        raw = sample.source_path.read_bytes()
        document = asyncio.run(
            provider.extract(
                PreprocessedImage(raw, mime, width, height, ()), ("eng", "ara"), 120.0
            )
        )
        result = _document_result(sample, document, _sha256(sample.source_path))
        trust = {"sample_id": sample.sample_id, **evaluate_trust(document, result)}
        raw_payload = {
            "sample_id": sample.sample_id,
            "source_sha256": result.source_sha256,
            "provider_id": provider.provider_id,
            "provider_version": provider.provider_version,
            "raw_native": implementation.last_raw,
            "mapped_lines": result.evidence,
            "confidence_semantics": (
                "Native per-recognition-span score; independent word confidence "
                "is not exposed and is left null."
            ),
        }
        for directory, payload in (
            ("raw-paddle-evidence", raw_payload),
            ("relay-extraction-results", result.as_json()),
            ("trust-results", trust),
        ):
            (output / directory / f"{sample.sample_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        results.append(result)
        trusts.append(trust)
        print(f"Completed {len(results)}/{len(samples)} CPU image", flush=True)
    metrics = calculate_metrics(samples, results)
    metrics["estimated_cost_per_1_000_receipt_images"] = "USD 0 OCR API fee"
    metrics["operational_complexity"] = "Local CPU runtime and two pinned model bundles"
    decision, gate = decide(metrics)
    trusted_results = [replace(item, transaction_promoted=False) for item in results]
    trusted_metrics = calculate_metrics(samples, trusted_results)
    memory = process.memory_info()
    peak_rss = getattr(memory, "peak_wset", memory.rss)
    model_manifest = json.loads(
        (output / "model-manifest.json").read_text(encoding="utf-8")
    )
    for artifact in model_manifest["files"]:
        path = args.model_root / artifact["model"] / artifact["filename"]
        if _sha256(path) != artifact["sha256"]:
            raise ValueError("Model artifact hash mismatch")
    categories = metrics["category_accuracy"]
    status = {
        "PADDLE_IMPLEMENTATION_STATUS": "COMPLETE_CPU_BENCHMARK",
        "REFERENCE_ACCURACY": _percentage(metrics["reference"]),
        "AMOUNT_ACCURACY": _percentage(metrics["amount"]),
        "CURRENCY_ACCURACY": _percentage(metrics["currency"]),
        "AMOUNT_CURRENCY_ACCURACY": _percentage(metrics["amount_currency_pair"]),
        "DATE_ACCURACY": _percentage(metrics["date"]),
        "TIME_ACCURACY": _percentage(metrics["time"]),
        "ARABIC_ACCURACY": _percentage(categories["arabic"]),
        "ENGLISH_ACCURACY": _percentage(categories["english"]),
        "MIXED_ACCURACY": _percentage(categories["mixed"]),
        "PHOTO_ACCURACY": _percentage(categories["photographed_screen"]),
        "NEGATIVE_FALSE_PROMOTIONS": str(
            metrics["negative_false_transaction_promotions"]
        ),
        "CPU_AVG_LATENCY_MS": f"{metrics['average_latency_ms']:.0f}",
        "CPU_MAX_LATENCY_MS": str(metrics["max_latency_ms"]),
        "MEMORY_USAGE": f"{peak_rss / 1024**2:.1f}_MiB_peak_process_working_set",
        "GPU_REQUIRED": "no",
        "DECISION": decision,
        "MEETS_95_PERCENT_GATE": "yes" if gate else "no",
        "NEXT_ACTION": (
            "STOP_no_tuning_recommend_one_open_source_candidate"
            if decision == "REJECT_PROVIDER"
            else "Prepare_guarded_local_integration_without_automatic_provider_switch"
        ),
        "TESSERACT_TUNING_STOPPED": "yes",
        "PAID_CLOUD_REQUIRED": "no",
        "P06_STARTED": "no",
    }
    payload = {
        "status": status,
        "metrics": metrics,
        "trust_stage_metrics": trusted_metrics,
        "trust_eligible_count": sum(
            trust["all_critical_evidence_eligible"] for trust in trusts
        ),
        "automatic_reference_trust_validated": False,
        "production_switch_implemented": False,
        "startup_ms": startup_ms,
        "peak_process_working_set_bytes": peak_rss,
        "model_size_bytes": model_manifest["total_bytes"],
        "performance_method": (
            "CPU decode, native detection/recognition and native JSON export; first "
            "image included; startup and downloads excluded from image latency. "
            "Evidence mapping and Relay extraction are outside this timer. "
            "Windows process-lifetime peak working set includes startup."
        ),
        "cpu": platform.processor(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "model_manifest": model_manifest,
        "dependencies": sorted(
            f"{dist.metadata['Name']}=={dist.version}" for dist in distributions()
        ),
        "samples": [result.as_json() for result in results],
    }
    (output / "paddle-results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    block = "\n".join(f"{key}={value}" for key, value in status.items())
    (output / "paddle-status.txt").write_text(block + "\n", encoding="utf-8")
    lines = [
        "# Bounded PaddleOCR CPU benchmark",
        "",
        "One fixed model configuration; 23 frozen original images; no cloud OCR.",
        "Final Relay extraction drives the decision, not raw text alone.",
        "",
        "## Raw OCR coverage",
        "",
        *_metric_rows(metrics, raw=True),
        "",
        "## Final conservative Relay extraction",
        "",
        *_metric_rows(metrics, raw=False),
        "",
        "## Safety and CPU performance",
        "",
        "- Baseline negative false promotions: "
        f"{status['NEGATIVE_FALSE_PROMOTIONS']}/6",
        "- Trust-stage negative promotions: 0/6; all records await operator review.",
        "- Automatic duplicate linking is disabled; no reference trust is validated.",
        "- Recognition scores are span-level, not calibrated field probabilities.",
        f"- Startup: {startup_ms} ms (downloads excluded).",
        f"- Peak process working set: {peak_rss / 1024**2:.1f} MiB.",
        f"- Model bundle: {model_manifest['total_bytes'] / 1024**2:.1f} MiB.",
        "- No real non-receipt negative exists in the frozen set.",
        "",
        "```text",
        block,
        "```",
    ]
    (output / "paddle-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(block, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in (
        "positive-manifest",
        "pilot-manifest",
        "negative-manifest",
        "frozen-dataset",
        "model-root",
        "output",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
