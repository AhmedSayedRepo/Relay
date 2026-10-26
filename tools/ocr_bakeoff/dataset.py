"""Load the preserved Relay ground truth without changing source images."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.ocr_bakeoff.models import BakeoffSample

_FIELD_NAMES = {
    "amount": "amount",
    "currency": "currency",
    "reference": "reference",
    "date": "date",
    "time": "time",
    "sender_handle": "handle",
    "recipient_identifier": "recipient_identifier",
}


def _language_category(value: dict[str, Any]) -> str:
    language = str(value.get("language", "en")).lower()
    notes = " ".join(str(note).lower() for note in value.get("notes", []))
    if language in {"en", "eng", "english"}:
        return "english"
    if "mixed arabic/english" in notes:
        return "mixed"
    if "arabic" in notes:
        return "arabic"
    return "mixed"


def _tags(value: dict[str, Any]) -> frozenset[str]:
    notes = " ".join(str(note).lower() for note in value.get("notes", []))
    result: set[str] = set()
    if "photograph" in notes or "phone frame" in notes:
        result.add("photographed_screen")
    if any(term in notes for term in ("compressed", "cropped", "overlay", "noisy")):
        result.add("compressed_or_cropped")
    return frozenset(result)


def _expected(value: dict[str, Any]) -> dict[str, str]:
    source = value.get("expected", {})
    result: dict[str, str] = {}
    for source_name, field_name in _FIELD_NAMES.items():
        item = source.get(source_name)
        if item is not None:
            result[field_name] = str(item)
    return result


def _uncertain(value: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        _FIELD_NAMES.get(str(item), str(item))
        for item in value.get("uncertain_expected_fields", [])
    )


def _read_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    samples = payload.get("samples")
    if not isinstance(samples, list):
        raise ValueError(f"Manifest has no samples list: {path}")
    return [dict(item) for item in samples]


def load_dataset(
    positive_manifest: Path,
    pilot_manifest: Path,
    negative_manifest: Path,
) -> list[BakeoffSample]:
    result: list[BakeoffSample] = []
    for manifest, kind in (
        (positive_manifest, "positive"),
        (pilot_manifest, "pilot"),
        (negative_manifest, "negative"),
    ):
        for value in _read_manifest(manifest):
            filename = value.get("source_filename", value.get("filename"))
            if not isinstance(filename, str):
                raise ValueError(f"Missing source filename in {manifest}")
            source = Path(filename)
            if not source.is_absolute():
                source = (manifest.parent / source).resolve()
            integrity = value.get("source_integrity", {})
            expected_receipt = bool(value.get("expected_receipt", kind != "negative"))
            tags = set(_tags(value))
            tags.add(kind)
            non_transfer = value.get("visible_non_transfer_values", {})
            result.append(
                BakeoffSample(
                    sample_id=str(value["sample_id"]),
                    source_path=source,
                    expected_receipt=expected_receipt,
                    language_category=(
                        _language_category(value) if expected_receipt else "negative"
                    ),
                    expected=_expected(value),
                    uncertain_fields=_uncertain(value),
                    duplicate_group=(
                        str(value.get("expected", {}).get("duplicate_group"))
                        if value.get("expected", {}).get("duplicate_group") is not None
                        else None
                    ),
                    tags=frozenset(tags),
                    must_not_promote_transfer_amount=bool(
                        non_transfer.get("must_not_promote_to_transfer_amount", False)
                    ),
                    expected_sha256=(
                        str(integrity["sha256"]).lower()
                        if integrity.get("sha256")
                        else None
                    ),
                )
            )
    identifiers = [sample.sample_id for sample in result]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate sample identifiers across bake-off manifests")
    missing = [
        str(sample.source_path) for sample in result if not sample.source_path.is_file()
    ]
    if missing:
        raise FileNotFoundError("Missing benchmark images: " + ", ".join(missing))
    return result
