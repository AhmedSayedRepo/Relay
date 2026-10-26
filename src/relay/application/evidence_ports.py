"""Generic structured-evidence and receipt-adapter boundaries."""

from dataclasses import dataclass
from typing import Protocol

from relay.domain.agent import JsonValue
from relay.domain.evidence import FieldCandidate
from relay.domain.processing import OcrDocument, ReceiptType

EVIDENCE_PROCESSOR_VERSION = "receipt_evidence:v1"
EVIDENCE_PARSER_TYPE = "receipt_adapter"
EVIDENCE_PARSER_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class ReceiptEvidence:
    document: OcrDocument
    candidates: tuple[FieldCandidate, ...]
    receipt_type: ReceiptType
    classification_resolved: bool
    supported_layout: bool
    receipt_possible: bool
    explicit_transfer_failure: bool
    metadata: dict[str, JsonValue]


class EvidenceExtractor(Protocol):
    async def extract(
        self,
        data: bytes,
        *,
        source_event_id: str,
        timestamp: str,
        requested_languages: tuple[str, ...],
        timeout_seconds: float,
        structured_sources: tuple[tuple[FieldCandidate, ...], ...] = (),
    ) -> ReceiptEvidence: ...


class ReceiptAdapter(EvidenceExtractor, Protocol):
    @property
    def adapter_id(self) -> str: ...

    @property
    def adapter_version(self) -> str: ...
