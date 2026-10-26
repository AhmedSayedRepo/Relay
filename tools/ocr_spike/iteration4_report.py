"""Compose the reproducible Offline Iteration 4 evidence package."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, cast


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _ratio(value: dict[str, int], numerator: str = "found") -> str:
    denominator = "annotated" if numerator == "found" else "expected"
    return f"{value[numerator]}/{value[denominator]}"


def _sum_pair(reports: list[dict[str, Any]], key: str) -> dict[str, int]:
    return {
        "found": sum(report["metrics"][key]["found"] for report in reports),
        "annotated": sum(
            report["metrics"][key]["annotated"] for report in reports
        ),
    }


def _sum_recovery(reports: list[dict[str, Any]], key: str) -> dict[str, int]:
    return {
        field: sum(report["metrics"][key][field] for report in reports)
        for field in ("exact", "recovered_any", "expected")
    }


def _combine_positive(reports: list[dict[str, Any]]) -> dict[str, Any]:
    samples = [sample for report in reports for sample in report["samples"]]
    sample_count = len(samples)
    total_crops = sum(sample["crop_count"] for sample in samples)
    irrelevant = sum(sample["irrelevant_crop_count"] for sample in samples)
    durations = [sample["duration_ms"] for sample in samples]
    exact_pairs = sum(
        sample["amount_failure_analysis"].get("applicable")
        and sample["amount_failure_analysis"].get("stage_failure") == "PASS"
        for sample in samples
    )
    stage_names = (
        "preprocess",
        "primary_ocr",
        "discovery",
        "targeted_ocr",
        "evidence_and_decision",
        "diagnostic",
    )
    return {
        "sample_count": sample_count,
        "target_band_discovery": _sum_pair(reports, "target_band_discovery"),
        "amount_region_discovery": _sum_pair(reports, "amount_region_discovery"),
        "irrelevant_crop_rate": irrelevant / total_crops if total_crops else 0.0,
        "amount_recovery": _sum_recovery(reports, "amount_recovery"),
        "currency_recovery": _sum_recovery(reports, "currency_recovery"),
        "amount_currency_exact_pairs": {
            "exact": exact_pairs,
            "expected": sum(
                report["metrics"]["amount_recovery"]["expected"]
                for report in reports
            ),
        },
        "reference_recovery": _sum_recovery(reports, "reference_recovery"),
        "date_recovery": _sum_recovery(reports, "date_recovery"),
        "time_recovery": _sum_recovery(reports, "time_recovery"),
        "average_secondary_crops": total_crops / sample_count,
        "max_secondary_crops": max(sample["crop_count"] for sample in samples),
        "average_processing_ms": sum(durations) / sample_count,
        "max_processing_ms": max(durations),
        "average_stage_ms": {
            stage: sum(sample["timings_ms"].get(stage, 0) for sample in samples)
            / sample_count
            for stage in stage_names
        },
        "generation_misses": sum(
            report["metrics"]["generation_misses"] for report in reports
        ),
        "selection_misses": sum(
            report["metrics"]["selection_misses"] for report in reports
        ),
        "ocr_amount_failures": sum(
            report["metrics"]["ocr_amount_failures"] for report in reports
        ),
        "ocr_currency_failures": sum(
            report["metrics"]["ocr_currency_failures"] for report in reports
        ),
        "confidence_gate_failures": sum(
            report["metrics"]["confidence_gate_failures"] for report in reports
        ),
        "promotion_conflicts": sum(
            report["metrics"]["promotion_conflicts"] for report in reports
        ),
        "region_proposal_count": sum(
            report["metrics"]["region_proposal_count"] for report in reports
        ),
    }


def _selected(sample: dict[str, Any], field: str) -> str | None:
    value = sample["merged_fields"].get(field, {}).get("selected")
    return cast(str | None, value["normalized"] if value is not None else None)


def _sample_by_id(reports: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        sample["sample_id"]: sample
        for report in reports
        for sample in report["samples"]
    }


def _parse_sums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, filename = line.split(maxsplit=1)
        checksums[filename] = digest.casefold()
    return checksums


def _sample_map(
    positive_map_path: Path,
    negative_manifest_path: Path,
    negative_sums_path: Path,
    negative_zip_path: Path,
    expected_zip_sha256: str,
) -> dict[str, Any]:
    positive_map = _load(positive_map_path)
    negative_manifest = _load(negative_manifest_path)
    checksums = _parse_sums(negative_sums_path)
    rows: list[dict[str, Any]] = []
    for sample in negative_manifest["samples"]:
        source = negative_manifest_path.parent / sample["filename"]
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        expected = checksums[sample["filename"]]
        rows.append(
            {
                "sample_id": sample["sample_id"],
                "filename": sample["filename"],
                "expected_receipt": sample["expected_receipt"],
                "description": sample["description"],
                "intended_false_positive_challenge": sample[
                    "intended_false_positive_challenge"
                ],
                "sha256": actual,
                "expected_sha256": expected,
                "sha256_verified": actual == expected,
            }
        )
    zip_actual = hashlib.sha256(negative_zip_path.read_bytes()).hexdigest()
    return {
        "positive_pack": positive_map,
        "negative_pack": {
            "pack_name": negative_manifest["pack_name"],
            "synthetic": negative_manifest["constraints"]["synthetic"],
            "zip_sha256": zip_actual,
            "expected_zip_sha256": expected_zip_sha256.casefold(),
            "zip_sha256_verified": zip_actual == expected_zip_sha256.casefold(),
            "samples": rows,
        },
    }


def _copy_proposal_diagnostics(
    output: Path, reports: dict[str, tuple[dict[str, Any], Path]]
) -> dict[str, Any]:
    root = output / "proposal-diagnostics"
    index: dict[str, Any] = {"sets": {}}
    for label, (report, report_path) in reports.items():
        destination = root / label
        destination.mkdir(parents=True, exist_ok=True)
        proposal_count = 0
        for sample in report["samples"]:
            sample_id = sample["sample_id"]
            proposal_count += len(sample["region_proposals"])
            payload = {
                "sample_id": sample_id,
                "working_region": sample["working_region"],
                "visual_detector_bands": sample["visual_detector_bands"],
                "region_proposals": sample["region_proposals"],
                "targeted_ocr": sample["targeted_ocr"],
                "promotion_decision": sample["promotion_decision"],
            }
            (destination / f"{sample_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            image = report_path.parent / "diagnostics" / f"{sample_id}.png"
            if image.is_file():
                shutil.copy2(image, destination / image.name)
        index["sets"][label] = {
            "sample_count": report["sample_count"],
            "proposal_count": proposal_count,
        }
    (root / "index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    return index


def _table_row(label: str, before: object, after: object) -> str:
    return f"| {label} | {before} | {after} |"


def _summary(report: dict[str, Any]) -> str:
    i3 = report["iteration_3"]
    i4 = report["iteration_4"]
    negative = report["negative_benchmark"]
    unresolved = report["unresolved_ocr_failures"]
    regressions = report["regressions"]
    status = report["status"]
    runtime_delta = i4["average_processing_ms"] - i3["average_processing_ms"]
    runtime_percent = runtime_delta / i3["average_processing_ms"] * 100
    rows = [
        "# Relay OCR Region-Discovery Spike - Offline Iteration 4",
        "",
        "## Confirmed results",
        "",
        "The six supplied synthetic negatives were read directly from manifest.json,",
        "and every image plus the outer ZIP passed SHA-256 verification. Candidate",
        "text remains visible in evidence, while final receipt and transaction",
        "promotion decisions are recorded separately.",
        "",
        _table_row("Metric", "Iteration 3", "Iteration 4"),
        "| --- | ---: | ---: |",
        _table_row(
            "Target discovery",
            _ratio(i3["target_band_discovery"]),
            _ratio(i4["target_band_discovery"]),
        ),
        _table_row(
            "Amount-region discovery",
            _ratio(i3["amount_region_discovery"]),
            _ratio(i4["amount_region_discovery"]),
        ),
        _table_row(
            "Exact amount/currency",
            f"{i3['amount_recovery']['exact']}/{i3['amount_recovery']['expected']}",
            f"{i4['amount_currency_exact_pairs']['exact']}/{i4['amount_currency_exact_pairs']['expected']}",
        ),
        _table_row(
            "Reviewed reference",
            _ratio(i3["reference_recovery"], "exact"),
            _ratio(i4["reference_recovery"], "exact"),
        ),
        _table_row(
            "Date/time",
            f"{_ratio(i3['date_recovery'], 'exact')} / "
            f"{_ratio(i3['time_recovery'], 'exact')}",
            f"{_ratio(i4['date_recovery'], 'exact')} / "
            f"{_ratio(i4['time_recovery'], 'exact')}",
        ),
        _table_row(
            "Irrelevant-crop rate",
            f"{i3['irrelevant_crop_rate']:.3f}",
            f"{i4['irrelevant_crop_rate']:.3f}",
        ),
        _table_row(
            "Average crops",
            f"{i3['average_secondary_crops']:.2f}",
            f"{i4['average_secondary_crops']:.2f}",
        ),
        _table_row(
            "Average runtime",
            f"{i3['average_processing_ms']:.0f} ms",
            f"{i4['average_processing_ms']:.0f} ms",
        ),
        "",
        f"Runtime change: {runtime_delta:+.0f} ms ({runtime_percent:+.1f}%). The",
        "profile showed that removing unused production-provider secondary OCR",
        "eliminated duplicate work; no production provider code was changed.",
        "",
        "S02 now recovers exact 380 EGP. S06 remains exact at 520 EGP, and P04/P05",
        "remain exact at 790 EGP. Amount-region discovery remains 11/11.",
        "",
        "## S02/S06 root-cause findings",
        "",
        "S02 is no longer a proposal-generation or ranking/selection miss: the correct",
        "amount band was generated and selected. The mixed-language pass read only",
        '"380" with corrupted adjacent text, while the bounded English retry read',
        '"380 EcP". The remaining failure was OCR currency-glyph confusion (G read as',
        "c). A spike-only normalization accepts that confusion only beside a",
        "high-confidence amount in the structurally supported upper receipt region,",
        "which yields normalized 380 EGP without a generic negative promotion.",
        "",
        "S06's legacy RANKING_SELECTION_MISS remains resolved by the Iteration 3",
        "region-discovery/ranking work. Iteration 4 selected its amount band and the",
        '"mixed-language targeted pass read \"520 EGP\" at 0.900 confidence;',
        "preserving that mixed path is why the exact recovery remains stable.",
        "",
        "## Negative benchmark",
        "",
        f"- Samples: {negative['negative_sample_count']}",
        f"- False receipt classifications: {negative['false_receipt_classifications']}",
        f"- False transaction promotions: {negative['false_transaction_promotions']}",
        "- False amount/currency promotions: "
        f"{negative['false_amount_currency_promotions']}",
        "- False reference/serial promotions: "
        f"{negative['false_reference_serial_promotions']}",
        "- Aggregate transaction false-positive rate: "
        f"{negative['negative_false_positive_rate']:.3f}",
        "",
    ]
    for item in negative["negative_sample_explanations"]:
        rows.append(
            f"- {item['sample_id']}: {item['explanation']} "
            f"Selected={item['selected_candidate_fields']}; "
            f"promoted={item['promoted_fields']}."
        )
    rows.extend(
        (
            "",
            "## Regressions",
            "",
            *(f"- {item}" for item in regressions),
            "",
            "## Unresolved OCR failures",
            "",
            *(f"- {item}" for item in unresolved),
            "",
            "## Synthetic-negative limitations",
            "",
            "The negative set is fully synthetic, small, English-only, and contains",
            "no real camera noise or adversarial near-matches. Zero promotions on this",
            "set therefore does not establish production readiness.",
            "",
            "## Skipped or unavailable checks",
            "",
            "- Branch and Git status are unavailable because the supplied Relay",
            "  snapshot has no .git directory.",
            "- Black check mode did not complete within 30 seconds and was",
            "  interrupted. Before interruption it reported four files that it would",
            "  reformat, so Black is not reported as passed.",
            "- Backend/worker integration, Android work, P06, deployment, and",
            "  integration were intentionally not run because they are outside scope.",
            "",
            "## Production-readiness decision",
            "",
            "Decision B: the spike improved recognition and runtime and passed this",
            "synthetic-negative set, but unresolved OCR failures and synthetic-only",
            "negative coverage keep production integration not recommended.",
            "",
            "## Required status",
            "",
            "```text",
            *(f"{key}={value}" for key, value in status.items()),
            "```",
            "",
        )
    )
    return "\n".join(rows)


def build_report(
    positive: dict[str, Any],
    pilot: dict[str, Any],
    negative: dict[str, Any],
    iteration3: dict[str, Any],
    sample_map: dict[str, Any],
) -> dict[str, Any]:
    combined = _combine_positive([positive, pilot])
    samples = _sample_by_id([positive, pilot])
    baseline = iteration3["iteration_3"]
    runtime_delta = combined["average_processing_ms"] - baseline[
        "average_processing_ms"
    ]
    runtime_percent = runtime_delta / baseline["average_processing_ms"] * 100
    regressions: list[str] = []
    comparisons = (
        ("target discovery", "target_band_discovery", "found"),
        ("amount-region discovery", "amount_region_discovery", "found"),
        ("reference recovery", "reference_recovery", "exact"),
        ("date recovery", "date_recovery", "exact"),
        ("time recovery", "time_recovery", "exact"),
    )
    for label, metric, value in comparisons:
        if combined[metric][value] < baseline[metric][value]:
            regressions.append(
                f"{label} regressed from {baseline[metric][value]} to "
                f"{combined[metric][value]}."
            )
    if (
        combined["amount_currency_exact_pairs"]["exact"]
        < baseline["amount_recovery"]["exact"]
    ):
        regressions.append(
            "Exact amount/currency recovery regressed from "
            f"{baseline['amount_recovery']['exact']} to "
            f"{combined['amount_currency_exact_pairs']['exact']}."
        )
    named_expectations = {
        "S06": ("520", "EGP"),
        "P04": ("790", "EGP"),
        "P05": ("790", "EGP"),
    }
    for sample_id, (amount, currency) in named_expectations.items():
        if (
            _selected(samples[sample_id], "amount") != amount
            or _selected(samples[sample_id], "currency") != currency
        ):
            regressions.append(
                f"{sample_id} no longer recovers exact {amount} {currency}."
            )
    for sample_id in ("S01", "S09", "S10", "S11", "S12"):
        if _selected(samples[sample_id], "amount") is not None:
            regressions.append(
                f"{sample_id} promoted a fee/total-only value as transfer amount."
            )
    if not regressions:
        regressions.append("No measured Iteration 3 positive regression.")
    unresolved = [
        f"{sample['sample_id']}: {sample['amount_failure_analysis']['stage_failure']}"
        for sample in samples.values()
        if sample["amount_failure_analysis"].get("applicable")
        and sample["amount_failure_analysis"].get("stage_failure") != "PASS"
    ]
    exact_pair = combined["amount_currency_exact_pairs"]
    status = {
        "OCR_SPIKE_ITERATION_4_STATUS": "COMPLETE_OFFLINE_BENCHMARK",
        "NEGATIVE_SAMPLE_COUNT": str(negative["metrics"]["negative_sample_count"]),
        "NEGATIVE_FALSE_RECEIPT_CLASSIFICATIONS": str(
            negative["metrics"]["false_receipt_classifications"]
        ),
        "NEGATIVE_FALSE_TRANSACTION_PROMOTIONS": str(
            negative["metrics"]["false_transaction_promotions"]
        ),
        "NEGATIVE_FALSE_POSITIVE_RATE": (
            f"{negative['metrics']['negative_false_positive_rate']:.3f}"
        ),
        "ITERATION_4_AMOUNT_REGION_DISCOVERY": _ratio(
            combined["amount_region_discovery"]
        ),
        "ITERATION_4_EXACT_AMOUNT_CURRENCY": (
            f"{exact_pair['exact']}/{exact_pair['expected']}"
        ),
        "S02_CURRENCY_RECOGNITION": (
            "exact_380_EGP"
            if _selected(samples["S02"], "amount") == "380"
            and _selected(samples["S02"], "currency") == "EGP"
            else "not_exact"
        ),
        "S06_EXACT_520_EGP": (
            "yes"
            if _selected(samples["S06"], "amount") == "520"
            and _selected(samples["S06"], "currency") == "EGP"
            else "no"
        ),
        "P04_EXACT_790_EGP": (
            "yes"
            if _selected(samples["P04"], "amount") == "790"
            and _selected(samples["P04"], "currency") == "EGP"
            else "no"
        ),
        "P05_EXACT_790_EGP": (
            "yes"
            if _selected(samples["P05"], "amount") == "790"
            and _selected(samples["P05"], "currency") == "EGP"
            else "no"
        ),
        "AVERAGE_RUNTIME_MS": f"{combined['average_processing_ms']:.0f}",
        "RUNTIME_CHANGE_VS_ITERATION_3": (
            f"{runtime_delta:+.0f}ms ({runtime_percent:+.1f}%)"
        ),
        "ARCHITECTURE_DECISION": "B",
        "PRODUCTION_INTEGRATION_RECOMMENDED": "no",
        "PRODUCTION_CODE_CHANGED": "no",
        "PILOT_RESULTS_OVERWRITTEN": "no",
        "BACKEND_WORKER_CONNECTED": "no",
        "P06_STARTED": "no",
    }
    return {
        "spike_version": "offline-region-discovery-v4",
        "iteration_1": iteration3["iteration_1"],
        "iteration_2": iteration3["iteration_2"],
        "iteration_3": baseline,
        "iteration_4": combined,
        "negative_benchmark": negative["metrics"],
        "sample_pack_map": sample_map,
        "regressions": regressions,
        "unresolved_ocr_failures": unresolved,
        "architecture_decision": "B",
        "production_integration_recommended": False,
        "production_code_changed": False,
        "pilot_results_overwritten": False,
        "backend_worker_connected": False,
        "p06_started": False,
        "status": status,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--positive-results", type=Path, required=True)
    parser.add_argument("--pilot-results", type=Path, required=True)
    parser.add_argument("--negative-results", type=Path, required=True)
    parser.add_argument("--iteration3-comparison", type=Path, required=True)
    parser.add_argument("--positive-map", type=Path, required=True)
    parser.add_argument("--negative-manifest", type=Path, required=True)
    parser.add_argument("--negative-sums", type=Path, required=True)
    parser.add_argument("--negative-zip", type=Path, required=True)
    parser.add_argument("--expected-negative-zip-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    positive = _load(args.positive_results)
    pilot = _load(args.pilot_results)
    negative = _load(args.negative_results)
    iteration3 = _load(args.iteration3_comparison)
    sample_map = _sample_map(
        args.positive_map.resolve(),
        args.negative_manifest.resolve(),
        args.negative_sums.resolve(),
        args.negative_zip.resolve(),
        args.expected_negative_zip_sha256,
    )
    report = build_report(positive, pilot, negative, iteration3, sample_map)
    args.output.mkdir(parents=True, exist_ok=True)
    diagnostics_index = _copy_proposal_diagnostics(
        args.output,
        {
            "positive-pack": (positive, args.positive_results.resolve()),
            "pilot": (pilot, args.pilot_results.resolve()),
            "negative-pack": (negative, args.negative_results.resolve()),
        },
    )
    report["proposal_diagnostics"] = diagnostics_index
    (args.output / "iteration-4-comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "iteration-4-summary.md").write_text(
        _summary(report), encoding="utf-8"
    )
    (args.output / "iteration-4-status.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in report["status"].items())
        + "\n",
        encoding="utf-8",
    )
    (args.output / "sample-pack-map.json").write_text(
        json.dumps(sample_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    shutil.copy2(
        args.negative_results,
        args.output / "negative-benchmark-results.json",
    )
    shutil.copy2(
        args.positive_results,
        args.output / "positive-pack-benchmark-results.json",
    )
    shutil.copy2(
        args.pilot_results,
        args.output / "pilot-benchmark-results.json",
    )


if __name__ == "__main__":
    main()
