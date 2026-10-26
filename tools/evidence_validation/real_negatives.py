"""Reviewed real-negative extension, kept outside the frozen synthetic denominator."""

import argparse
import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from relay.application.evidence_product import resolve_evidence
from relay.infrastructure.instapay_adapter import InstaPayReceiptAdapter
from relay.infrastructure.tesseract_ocr import TesseractOcrProvider


@dataclass(frozen=True)
class RealNegative:
    sample_id: str
    source: Path
    sha256: str


def load_real_negatives(manifest: Path) -> tuple[RealNegative, ...]:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    values = payload.get("samples")
    if not isinstance(values, list) or not values:
        raise ValueError("A nonempty reviewed real-negative set is required.")
    result = []
    for value in values:
        if (
            value.get("expected_receipt") is not False
            or value.get("synthetic") is not False
        ):
            raise ValueError(
                "Explicit reviewed expected_receipt=false/synthetic=false required."
            )
        identifier = value.get("sample_id")
        filename = value.get("source_filename")
        checksum = value.get("source_integrity", {}).get("sha256")
        if not all(
            isinstance(item, str) and item for item in (identifier, filename, checksum)
        ):
            raise ValueError(
                "Sample identifier, original filename and SHA-256 required."
            )
        source = Path(filename)
        if not source.is_absolute():
            source = manifest.parent / source
        source = source.resolve(strict=True)
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != checksum.lower():
            raise ValueError("Real-negative original-byte integrity mismatch.")
        result.append(RealNegative(identifier, source, checksum.lower()))
    if len({item.sample_id for item in result}) != len(result):
        raise ValueError("Real-negative identifiers must be unique.")
    return tuple(result)


async def evaluate(
    manifest: Path, output: Path, executable: Path, tessdata: Path
) -> None:
    if await asyncio.to_thread(output.exists):
        raise ValueError(
            "Preserve existing real-negative evidence; use a fresh report path."
        )
    samples = await asyncio.to_thread(load_real_negatives, manifest)
    adapter = InstaPayReceiptAdapter(TesseractOcrProvider(executable, tessdata))
    rows = []
    for sample in samples:
        data = await asyncio.to_thread(sample.source.read_bytes)
        evidence = await adapter.extract(
            data,
            source_event_id=sample.sample_id,
            timestamp="not_supplied",
            requested_languages=("eng", "ara"),
            timeout_seconds=20,
        )
        parsed = resolve_evidence(evidence)
        metadata = parsed.evidence_result or {}
        rows.append(
            {
                "sample_id": sample.sample_id,
                "sha256": sample.sha256,
                "state": metadata.get("state"),
                "false_receipt": evidence.classification_resolved,
                "false_transaction_promotion": metadata.get("state")
                == "AUTO_FINALIZED",
                "false_amount_currency_promotion": parsed.amount is not None
                and parsed.currency is not None,
                "false_reference_promotion": parsed.reference_normalized is not None,
                "evidence": metadata,
            }
        )
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(
        output.write_text,
        json.dumps(
            {
                "real_negative_count": len(rows),
                "frozen_dataset_modified": False,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Separate reviewed real-negative product validation"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--tessdata", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(evaluate(args.manifest, args.output, args.executable, args.tessdata))


if __name__ == "__main__":
    main()
