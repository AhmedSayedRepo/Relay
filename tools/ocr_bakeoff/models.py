"""Data contracts for the benchmark-only OCR engine bake-off."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class BakeoffSample:
    sample_id: str
    source_path: Path
    expected_receipt: bool
    language_category: str
    expected: dict[str, str]
    uncertain_fields: frozenset[str]
    duplicate_group: str | None
    tags: frozenset[str]
    must_not_promote_transfer_amount: bool
    expected_sha256: str | None = None


@dataclass(slots=True)
class ProviderSampleResult:
    sample_id: str
    source_sha256: str
    provider_id: str
    provider_version: str
    duration_ms: int
    raw_text: str
    selected_fields: dict[str, dict[str, Any]]
    conflicts: list[str]
    receipt_classified: bool
    transaction_promoted: bool
    promotion_explanation: str
    line_count: int
    token_count: int
    confidence_count: int
    geometry_count: int
    error: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "source_sha256": self.source_sha256,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "duration_ms": self.duration_ms,
            "raw_text": self.raw_text,
            "selected_fields": self.selected_fields,
            "conflicts": self.conflicts,
            "receipt_classified": self.receipt_classified,
            "transaction_promoted": self.transaction_promoted,
            "promotion_explanation": self.promotion_explanation,
            "line_count": self.line_count,
            "token_count": self.token_count,
            "confidence_count": self.confidence_count,
            "geometry_count": self.geometry_count,
            "error": self.error,
            "evidence": self.evidence,
        }
