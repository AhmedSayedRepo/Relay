"""Data contracts for the isolated OCR region-discovery spike."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Box:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def area(self) -> int:
        return self.width * self.height

    def as_list(self) -> list[int]:
        return [self.left, self.top, self.width, self.height]

    def intersection_area(self, other: Box) -> int:
        width = max(0, min(self.right, other.right) - max(self.left, other.left))
        height = max(0, min(self.bottom, other.bottom) - max(self.top, other.top))
        return width * height

    def coverage_of(self, other: Box) -> float:
        return self.intersection_area(other) / other.area if other.area else 0.0

    def overlap_ratio(self, other: Box) -> float:
        denominator = min(self.area, other.area)
        return self.intersection_area(other) / denominator if denominator else 0.0


@dataclass(frozen=True, slots=True)
class Anchor:
    kind: str
    box: Box
    confidence: float
    text: str


@dataclass(frozen=True, slots=True)
class CandidateBand:
    box: Box
    score: float
    density: float
    provenance: str = "visual_projection"
    anchor_support: tuple[str, ...] = ()


@dataclass(slots=True)
class RegionProposal:
    proposal_id: str
    box: Box
    detector_source: str
    local_darkness_score: float
    gradient_score: float
    text_density_score: float
    grouping_information: dict[str, Any]
    anchor_influence: tuple[str, ...]
    ranking_score: float
    selected: bool = False
    rejection_reason: str | None = None
    overlap_relationships: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "geometry": self.box.as_list(),
            "detector_source": self.detector_source,
            "local_darkness_score": self.local_darkness_score,
            "gradient_score": self.gradient_score,
            "text_density_score": self.text_density_score,
            "grouping_information": self.grouping_information,
            "overlap_relationships": list(self.overlap_relationships),
            "anchor_influence": list(self.anchor_influence),
            "ranking_score": self.ranking_score,
            "selected": self.selected,
            "rejection_reason": self.rejection_reason,
        }


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    selected_bands: tuple[CandidateBand, ...]
    proposals: tuple[RegionProposal, ...]


@dataclass(frozen=True, slots=True)
class WorkingRegion:
    box: Box
    confidence: float
    provenance: str
    applied: bool


@dataclass(frozen=True, slots=True)
class BenchmarkTarget:
    field: str
    box: Box


@dataclass(frozen=True, slots=True)
class SampleManifest:
    sample_id: str
    source_filename: str
    language: str
    expected_receipt: bool
    description: str = ""
    intended_false_positive_challenge: str = ""
    expected_amount: str | None = None
    expected_currency: str | None = None
    expected_reference: str | None = None
    expected_date: str | None = None
    expected_time: str | None = None
    expected_handle: str | None = None
    expected_recipient_identifier: str | None = None
    expected_duplicate_group: str | None = None
    notes: tuple[str, ...] = ()
    masked_fields: tuple[str, ...] = ()
    uncertain_expected_fields: tuple[str, ...] = ()
    targets: tuple[BenchmarkTarget, ...] = ()
    must_not_promote_transfer_amount: bool = False

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> SampleManifest:
        expected = value.get("expected", {})
        non_transfer = value.get("visible_non_transfer_values", {})
        return cls(
            sample_id=str(value["sample_id"]),
            source_filename=str(value.get("source_filename", value.get("filename"))),
            language=str(value.get("language", "en")),
            expected_receipt=bool(value["expected_receipt"]),
            description=str(value.get("description", "")),
            intended_false_positive_challenge=str(
                value.get("intended_false_positive_challenge", "")
            ),
            expected_amount=expected.get("amount"),
            expected_currency=expected.get("currency"),
            expected_reference=expected.get("reference"),
            expected_date=expected.get("date"),
            expected_time=expected.get("time"),
            expected_handle=expected.get("sender_handle"),
            expected_recipient_identifier=expected.get("recipient_identifier"),
            expected_duplicate_group=expected.get("duplicate_group"),
            notes=tuple(str(item) for item in value.get("notes", [])),
            masked_fields=tuple(str(item) for item in value.get("masked_fields", [])),
            uncertain_expected_fields=tuple(
                str(item) for item in value.get("uncertain_expected_fields", [])
            ),
            targets=tuple(
                BenchmarkTarget(str(item["field"]), Box(*item["box"]))
                for item in value.get("targets", [])
            ),
            must_not_promote_transfer_amount=bool(
                non_transfer.get("must_not_promote_to_transfer_amount", False)
            ),
        )


@dataclass(slots=True)
class SampleResult:
    sample_id: str
    source_sha256: str
    image_size: tuple[int, int]
    primary_text: str
    primary_evidence: list[dict[str, Any]]
    anchors: list[dict[str, Any]]
    bands: list[dict[str, Any]]
    targeted_ocr: list[dict[str, Any]]
    merged_fields: dict[str, Any]
    target_discovery: dict[str, bool]
    irrelevant_crop_count: int
    classification_evidence: list[str]
    false_positives: list[str]
    crop_count: int
    cropped_pixels: int
    duration_ms: int
    ground_truth_uncertainties: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    working_region: dict[str, Any] = field(default_factory=dict)
    working_region_assessment: dict[str, Any] = field(default_factory=dict)
    region_proposals: list[dict[str, Any]] = field(default_factory=list)
    amount_failure_analysis: dict[str, Any] = field(default_factory=dict)
    promotion_decision: dict[str, Any] = field(default_factory=dict)
    timings_ms: dict[str, int] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "source_sha256": self.source_sha256,
            "image_size": list(self.image_size),
            "primary_text": self.primary_text,
            "primary_evidence": self.primary_evidence,
            "anchors": self.anchors,
            "visual_detector_bands": self.bands,
            "targeted_ocr": self.targeted_ocr,
            "merged_fields": self.merged_fields,
            "target_discovery": self.target_discovery,
            "irrelevant_crop_count": self.irrelevant_crop_count,
            "classification_evidence": self.classification_evidence,
            "false_positives": self.false_positives,
            "crop_count": self.crop_count,
            "cropped_pixels": self.cropped_pixels,
            "duration_ms": self.duration_ms,
            "ground_truth_uncertainties": self.ground_truth_uncertainties,
            "errors": self.errors,
            "working_region": self.working_region,
            "working_region_assessment": self.working_region_assessment,
            "region_proposals": self.region_proposals,
            "amount_failure_analysis": self.amount_failure_analysis,
            "promotion_decision": self.promotion_decision,
            "timings_ms": self.timings_ms,
        }
