"""Provider-neutral evidence, resolution and transaction data trust.

Resolved data is not payment verification or receipt authenticity. Neither OCR
scores nor correlation identifiers are canonical duplicate keys.
"""

from dataclasses import dataclass
from enum import StrEnum

from relay.domain.agent import JsonValue

CRITICAL_FIELDS = ("reference_number", "amount", "currency", "date", "time")


class EvidenceKind(StrEnum):
    RECEIPT = "receipt"
    SMS = "sms"
    OPERATOR = "operator"


class ProductState(StrEnum):
    AUTO_FINALIZED = "AUTO_FINALIZED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNKNOWN = "REJECTED_OR_UNKNOWN_RECEIPT"
    PROCESSING_FAILED = "PROCESSING_FAILED"
    TRANSFER_FAILED = "TRANSFER_FAILED"
    REVIEW_FINALIZED = "REVIEW_FINALIZED"


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    source: EvidenceKind
    source_event_id: str
    adapter: str
    adapter_version: str
    timestamp: str
    content_sha256: str
    layout_profile: str | None = None
    extraction_version: str = "v1"

    @property
    def independence_key(self) -> tuple[str, str]:
        # Multiple crops, recognition modes or forwarded copies of identical
        # bytes are NOT independent source evidence.
        return self.source.value, self.content_sha256

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "source": self.source.value,
            "source_event_id": self.source_event_id,
            "adapter": self.adapter,
            "adapter_version": self.adapter_version,
            "timestamp": self.timestamp,
            "content_sha256": self.content_sha256,
            "layout_profile": self.layout_profile,
            "extraction_version": self.extraction_version,
        }


@dataclass(frozen=True, slots=True)
class FieldCandidate:
    field: str
    value: str
    normalized_value: str
    original_text: str
    source: EvidenceSource
    structural_evidence: tuple[str, ...]
    usable: bool
    validated_single_source: bool = False
    confidence: float | None = None
    geometry: tuple[int, int, int, int] | None = None
    original_polygon: tuple[tuple[float, float], ...] = ()

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "field": self.field,
            "value": self.value,
            "normalized_value": self.normalized_value,
            "original_text": self.original_text,
            **self.source.as_json(),
            "confidence": self.confidence,
            "usable": self.usable,
            "validated_single_source": self.validated_single_source,
            "structural_evidence": list(self.structural_evidence),
            "geometry": list(self.geometry) if self.geometry else None,
            "geometry_coordinate_space": "normalized_receipt",
            "original_polygon": [list(point) for point in self.original_polygon],
        }


@dataclass(frozen=True, slots=True)
class FieldResolution:
    field: str
    value: str | None
    state: str
    reason: str
    candidates: tuple[FieldCandidate, ...]

    @property
    def resolved(self) -> bool:
        return self.state == "RESOLVED"

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "field": self.field,
            "value": self.value,
            "state": self.state,
            "reason": self.reason,
            "candidates": [candidate.as_json() for candidate in self.candidates],
        }


@dataclass(frozen=True, slots=True)
class TransactionCandidate:
    state: ProductState
    fields: tuple[FieldResolution, ...]
    blocking_fields: tuple[str, ...]
    classification_resolved: bool
    supported_layout: bool

    def field(self, name: str) -> FieldResolution | None:
        return next((field for field in self.fields if field.field == name), None)

    @property
    def trusted_reference(self) -> str | None:
        reference = self.field("reference_number")
        return reference.value if reference and reference.resolved else None

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "state": self.state.value,
            "classification_resolved": self.classification_resolved,
            "supported_layout": self.supported_layout,
            "blocking_fields": list(self.blocking_fields),
            "fields": {field.field: field.as_json() for field in self.fields},
            "trusted_reference": self.trusted_reference,
            "payment_verification": "NOT_PERFORMED",
            "authenticity": "NOT_ASSESSED",
        }


class ResolutionPolicy:
    """Exact agreement and validated semantic context, never a score winner."""

    def resolve_field(
        self, field: str, candidates: tuple[FieldCandidate, ...]
    ) -> FieldResolution:
        observed = tuple(
            candidate for candidate in candidates if candidate.field == field
        )
        viable = tuple(
            candidate
            for candidate in observed
            if candidate.usable
            and candidate.structural_evidence
            and candidate.normalized_value
            and candidate.source.source_event_id
            and candidate.source.content_sha256
        )
        # A structurally located but weak-confidence competing value still blocks;
        # low confidence must not silently suppress a genuine disagreement.
        values = {
            candidate.normalized_value
            for candidate in observed
            if candidate.structural_evidence
        }
        if len(values) > 1:
            return FieldResolution(
                field, None, "UNRESOLVED", "conflicting_evidence", observed
            )
        if not viable:
            return FieldResolution(
                field, None, "UNRESOLVED", "missing_or_unusable_evidence", observed
            )
        value = viable[0].normalized_value
        # Repeated recognition or another photograph of a receipt is not an
        # independent financial source. Require distinct admitted source kinds.
        independent = {candidate.source.source for candidate in viable}
        if len(independent) >= 2:
            reason = "independent_exact_agreement"
        elif any(candidate.validated_single_source for candidate in viable):
            reason = "validated_semantic_source"
        else:
            return FieldResolution(
                field, value, "UNRESOLVED", "insufficient_validated_support", observed
            )
        return FieldResolution(field, value, "RESOLVED", reason, observed)

    def resolve(
        self,
        candidates: tuple[FieldCandidate, ...],
        *,
        classification_resolved: bool,
        supported_layout: bool,
        receipt_possible: bool = True,
        explicit_transfer_failure: bool = False,
    ) -> TransactionCandidate:
        names = tuple(
            dict.fromkeys((*CRITICAL_FIELDS, *(item.field for item in candidates)))
        )
        fields = tuple(self.resolve_field(name, candidates) for name in names)
        blocking = tuple(
            field.field
            for field in fields
            if field.field in CRITICAL_FIELDS and not field.resolved
        )
        if not classification_resolved:
            blocking = ("classification", *blocking)
        if not supported_layout:
            blocking = ("layout", *blocking)
        if not receipt_possible:
            state = ProductState.UNKNOWN
        elif explicit_transfer_failure and classification_resolved:
            state = ProductState.TRANSFER_FAILED
        else:
            state = (
                ProductState.NEEDS_REVIEW if blocking else ProductState.AUTO_FINALIZED
            )
        return TransactionCandidate(
            state, fields, blocking, classification_resolved, supported_layout
        )
