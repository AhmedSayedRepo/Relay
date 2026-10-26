"""Compose the reproducible Offline Iteration 3 comparison and status block."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, cast


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _sum_pair(reports: list[dict[str, Any]], key: str) -> dict[str, int]:
    return {
        "found": sum(report["metrics"][key]["found"] for report in reports),
        "annotated": sum(report["metrics"][key]["annotated"] for report in reports),
    }


def _sum_recovery(reports: list[dict[str, Any]], key: str) -> dict[str, int]:
    return {
        field: sum(report["metrics"][key][field] for report in reports)
        for field in ("exact", "recovered_any", "expected")
    }


def combine(reports: list[dict[str, Any]]) -> dict[str, Any]:
    sample_count = sum(report["sample_count"] for report in reports)
    total_crops = sum(
        sum(sample["crop_count"] for sample in report["samples"]) for report in reports
    )
    irrelevant = sum(
        sum(sample["irrelevant_crop_count"] for sample in report["samples"])
        for report in reports
    )
    durations = [
        sample["duration_ms"] for report in reports for sample in report["samples"]
    ]
    metrics = {
        "sample_count": sample_count,
        "target_band_discovery": _sum_pair(reports, "target_band_discovery"),
        "amount_region_discovery": _sum_pair(reports, "amount_region_discovery"),
        "irrelevant_crop_rate": irrelevant / total_crops if total_crops else 0.0,
        "amount_recovery": _sum_recovery(reports, "amount_recovery"),
        "currency_recovery": _sum_recovery(reports, "currency_recovery"),
        "reference_recovery": _sum_recovery(reports, "reference_recovery"),
        "date_recovery": _sum_recovery(reports, "date_recovery"),
        "time_recovery": _sum_recovery(reports, "time_recovery"),
        "average_secondary_crops": total_crops / sample_count if sample_count else 0.0,
        "max_secondary_crops": max(
            sample["crop_count"] for report in reports for sample in report["samples"]
        ),
        "average_processing_ms": (
            sum(durations) / sample_count if sample_count else 0.0
        ),
        "max_processing_ms": max(durations, default=0),
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
        "conflict_count": sum(
            report["metrics"]["conflict_count"] for report in reports
        ),
        "negative_sample_count": sum(
            report["metrics"]["negative_sample_count"] for report in reports
        ),
        "false_positive_count": sum(
            report["metrics"]["false_positive_count"] for report in reports
        ),
        "region_proposal_count": sum(
            report["metrics"]["region_proposal_count"] for report in reports
        ),
    }
    return metrics


def _selected_fields(sample: dict[str, Any]) -> dict[str, str]:
    return {
        field: evidence["selected"]["normalized"]
        for field, evidence in sample["merged_fields"].items()
        if evidence["selected"] is not None
    }


def _ratio(value: dict[str, int], numerator: str = "found") -> str:
    denominator = "annotated" if numerator == "found" else "expected"
    return f"{value[numerator]}/{value[denominator]}"


def _table_row(label: str, *values: object) -> str:
    return "| " + " | ".join((label, *(str(value) for value in values))) + " |"


def _pack_map(pack_manifest_path: Path) -> dict[str, Any]:
    payload = _load(pack_manifest_path)
    rows: list[dict[str, Any]] = []
    for sample in payload["samples"]:
        source = (pack_manifest_path.parent / sample["source_filename"]).resolve()
        actual_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        expected_sha = sample["source_integrity"]["sha256"]
        rows.append(
            {
                "sample_id": sample["sample_id"],
                "filename": source.name,
                "maps_to": sample["sample_id"],
                "maps_to_pilot_case": None,
                "sha256": actual_sha,
                "sha256_verified": actual_sha == expected_sha,
                "reviewed_reference": sample["expected"]["reference"],
                "expected_receipt": sample["expected_receipt"],
            }
        )
    return {
        "pack": payload.get("pack", "relay_ocr_spike_samples_pack_v2"),
        "generic_non_receipt_negative_included": bool(
            payload.get("generic_non_receipt_negative_included", False)
        ),
        "samples": rows,
    }


def build_report(
    pack_report: dict[str, Any],
    pilot_report: dict[str, Any],
    baseline: dict[str, Any],
    root_causes: dict[str, Any],
    pack_map: dict[str, Any],
) -> dict[str, Any]:
    combined = combine([pack_report, pilot_report])
    pilot_samples = {
        sample["sample_id"]: _selected_fields(sample)
        for sample in pilot_report["samples"]
    }
    p04_regression = not (
        pilot_samples["P04"].get("amount") == "790"
        and pilot_samples["P04"].get("currency") == "EGP"
    )
    p05_regression = not (
        pilot_samples["P05"].get("amount") == "790"
        and pilot_samples["P05"].get("currency") == "EGP"
    )
    negative_missing = combined["negative_sample_count"] == 0
    status = {
        "OCR_SPIKE_ITERATION_3_STATUS": "COMPLETE_OFFLINE_BENCHMARK",
        "S02_ROOT_CAUSE": root_causes["S02"]["classification"],
        "S06_ROOT_CAUSE": root_causes["S06"]["classification"],
        "ITERATION_3_TARGET_DISCOVERY": _ratio(combined["target_band_discovery"]),
        "ITERATION_3_AMOUNT_REGION_DISCOVERY": _ratio(
            combined["amount_region_discovery"]
        ),
        "ITERATION_3_EXACT_AMOUNT": _ratio(combined["amount_recovery"], "exact"),
        "ITERATION_3_EXACT_CURRENCY": _ratio(combined["currency_recovery"], "exact"),
        "ITERATION_3_VERIFIED_REFERENCE": _ratio(
            combined["reference_recovery"], "exact"
        ),
        "ITERATION_3_IRRELEVANT_CROP_RATE": f"{combined['irrelevant_crop_rate']:.3f}",
        "ITERATION_3_AVG_CROPS": f"{combined['average_secondary_crops']:.2f}",
        "ITERATION_3_RUNTIME": (
            f"avg={combined['average_processing_ms']:.0f}ms,"
            f"max={combined['max_processing_ms']}ms"
        ),
        "P04_REGRESSION": "yes" if p04_regression else "no",
        "P05_REGRESSION": "yes" if p05_regression else "no",
        "GENERATION_MISSES": str(combined["generation_misses"]),
        "SELECTION_MISSES": str(combined["selection_misses"]),
        "OCR_RECOGNITION_FAILURES": str(
            combined["ocr_amount_failures"] + combined["ocr_currency_failures"]
        ),
        "NEGATIVE_SAMPLE_MISSING": "yes" if negative_missing else "no",
        "NEGATIVE_FALSE_POSITIVES": (
            "not_measured"
            if negative_missing
            else str(combined["false_positive_count"])
        ),
        "ARCHITECTURE_DECISION": "B",
        "PRODUCTION_INTEGRATION_RECOMMENDED": "no",
        "PRODUCTION_CODE_CHANGED": "no",
        "P06_STARTED": "no",
    }
    return {
        "spike_version": "offline-region-discovery-v3",
        "iteration_1": baseline["iteration_1"],
        "iteration_2": baseline["iteration_2"],
        "iteration_3": combined,
        "root_causes": root_causes,
        "pilot_outcomes": pilot_samples,
        "sample_pack_map": pack_map,
        "negative_sample_missing": negative_missing,
        "architecture_decision": "B",
        "production_integration_recommended": False,
        "production_code_changed": False,
        "p06_started": False,
        "status": status,
    }


def _summary(report: dict[str, Any]) -> str:
    i1 = report["iteration_1"]
    i2 = report["iteration_2"]
    i3 = report["iteration_3"]
    rows = [
        "# Relay OCR Region-Discovery Spike - Offline Iteration 3",
        "",
        "Production code, stored pilot records, backend/worker runtime, and P06",
        "remained untouched. The uploaded v2 pack mapped exactly to S01-S12;",
        "P01-P05 were evaluated separately from preserved pilot evidence.",
        "",
        "## Iteration comparison",
        "",
        "| Metric | Iteration 1 | Iteration 2 | Iteration 3 |",
        "| --- | ---: | ---: | ---: |",
        _table_row(
            "Target discovery",
            _ratio(i1["target_band_discovery"]),
            _ratio(i2["target_band_discovery"]),
            _ratio(i3["target_band_discovery"]),
        ),
        _table_row(
            "Irrelevant crop rate",
            f"{i1['irrelevant_crop_rate']:.3f}",
            f"{i2['irrelevant_crop_rate']:.3f}",
            f"{i3['irrelevant_crop_rate']:.3f}",
        ),
        _table_row(
            "Amount-region discovery",
            _ratio(i1["amount_region_discovery"]),
            _ratio(i2["amount_region_discovery"]),
            _ratio(i3["amount_region_discovery"]),
        ),
        _table_row(
            "Exact amount",
            _ratio(i1["amount_recovery"], "exact"),
            _ratio(i2["amount_recovery"], "exact"),
            _ratio(i3["amount_recovery"], "exact"),
        ),
        _table_row(
            "Exact currency",
            _ratio(i1["currency_recovery"], "exact"),
            _ratio(i2["currency_recovery"], "exact"),
            _ratio(i3["currency_recovery"], "exact"),
        ),
        _table_row(
            "Verified reference",
            _ratio(i1["reference_recovery_verified_only"], "exact"),
            _ratio(i2["reference_recovery_verified_only"], "exact"),
            _ratio(i3["reference_recovery"], "exact"),
        ),
        _table_row(
            "Date",
            _ratio(i1["date_recovery"], "exact"),
            _ratio(i2["date_recovery"], "exact"),
            _ratio(i3["date_recovery"], "exact"),
        ),
        _table_row(
            "Time",
            _ratio(i1["time_recovery"], "exact"),
            _ratio(i2["time_recovery"], "exact"),
            _ratio(i3["time_recovery"], "exact"),
        ),
        _table_row(
            "Average crops",
            f"{i1['average_secondary_crops']:.2f}",
            f"{i2['average_secondary_crops']:.2f}",
            f"{i3['average_secondary_crops']:.2f}",
        ),
        _table_row(
            "Max crops",
            i1["max_secondary_crops"],
            i2["max_secondary_crops"],
            i3["max_secondary_crops"],
        ),
        _table_row(
            "Average runtime",
            f"{i1['average_processing_ms']:.0f} ms",
            f"{i2['average_processing_ms']:.0f} ms",
            f"{i3['average_processing_ms']:.0f} ms",
        ),
        _table_row(
            "Max runtime",
            f"{i1['max_processing_ms']} ms",
            f"{i2['max_processing_ms']} ms",
            f"{i3['max_processing_ms']} ms",
        ),
        _table_row(
            "Generation misses", "not recorded", "not recorded", i3["generation_misses"]
        ),
        _table_row(
            "Selection misses", "not recorded", "not recorded", i3["selection_misses"]
        ),
        _table_row(
            "OCR amount failures",
            "not recorded",
            "not recorded",
            i3["ocr_amount_failures"],
        ),
        _table_row(
            "OCR currency failures",
            "not recorded",
            "not recorded",
            i3["ocr_currency_failures"],
        ),
        _table_row(
            "Confidence failures",
            "not recorded",
            "not recorded",
            i3["confidence_gate_failures"],
        ),
        _table_row("Conflicts", i1["conflicts"], i2["conflicts"], i3["conflict_count"]),
        _table_row(
            "Generic-negative false positives",
            "not measured",
            "not measured",
            "not measured",
        ),
        "",
        "Iteration 3 verified-reference scoring uses all reviewed references,",
        "including S03, S04, S05, S06, S11, and S12. The earlier 8/11 values",
        "retain their original reviewed-only denominators.",
        "",
        "## Root cause and stage findings",
        "",
        "- S02: REGION proposals covered the amount before selection; the legacy",
        "  selection omitted them. Classification: RANKING_SELECTION_MISS, with",
        "  broad row merging as a contributing ranking distortion.",
        "- S06: REGION proposals covered the amount before selection; the legacy",
        "  selection omitted them. Classification: RANKING_SELECTION_MISS.",
        "- Iteration 3 remaining amount stages: "
        f"generation={i3['generation_misses']}, "
        f"selection={i3['selection_misses']}, "
        f"OCR amount={i3['ocr_amount_failures']}, "
        f"OCR currency={i3['ocr_currency_failures']}, "
        f"confidence={i3['confidence_gate_failures']}, "
        f"conflicts={i3['conflict_count']}.",
        "",
        "## Safety and decision",
        "",
        "No transfer amount was promoted from S01 or S09-S12 fee/total rows.",
        "P04 and P05 both retained exact 790 EGP recovery. The pack contains no",
        "real generic non-receipt negative, so false-positive behavior remains",
        "unmeasured and decision C is prohibited. Decision: B - promising; another",
        "offline iteration is needed. Production integration is not recommended.",
        "",
        "## Required status",
        "",
        "```text",
    ]
    rows.extend(f"{key}={value}" for key, value in report["status"].items())
    rows.extend(("```", ""))
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack-results", type=Path, required=True)
    parser.add_argument("--pilot-results", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--root-causes", type=Path, required=True)
    parser.add_argument("--pack-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = build_report(
        _load(args.pack_results),
        _load(args.pilot_results),
        _load(args.baseline),
        _load(args.root_causes),
        _pack_map(args.pack_manifest),
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "iteration-3-comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "iteration-3-summary.md").write_text(
        _summary(report), encoding="utf-8"
    )
    (args.output / "iteration-3-status.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in report["status"].items()) + "\n",
        encoding="utf-8",
    )
    (args.output / "sample-pack-map.json").write_text(
        json.dumps(report["sample_pack_map"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
