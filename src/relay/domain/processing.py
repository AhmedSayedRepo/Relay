"""Provider-neutral processing, OCR, receipt, confidence, and review concepts."""

import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from relay.domain.agent import JsonValue

DEFAULT_PROCESSOR_VERSION = "receipt_image:v1"
DEFAULT_PARSER_TYPE = "instapay_receipt"
DEFAULT_PARSER_VERSION = "v1"
DEFAULT_OCR_PROVIDER = "tesseract"
DEFAULT_OCR_LANGUAGES = ("eng", "ara")
MAX_OCR_TEXT_BYTES = 64 * 1024
MAX_OCR_BLOCKS = 5_000


def normalize_ocr_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    lines = [" ".join(line.split())[:512] for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


class JobStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class AttemptStatus(StrEnum):
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ReceiptType(StrEnum):
    INSTAPAY_TRANSFER_RECEIPT = "INSTAPAY_TRANSFER_RECEIPT"
    UNKNOWN_RECEIPT = "UNKNOWN_RECEIPT"


class FieldStatus(StrEnum):
    CONFIDENT = "CONFIDENT"
    UNCERTAIN = "UNCERTAIN"
    MISSING = "MISSING"
    INVALID = "INVALID"


class ReviewStatus(StrEnum):
    AUTO_ACCEPTED = "AUTO_ACCEPTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"


class OcrLineOrigin(StrEnum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"


class ProcessingFailure(StrEnum):
    UNSUPPORTED_IMAGE = "UNSUPPORTED_IMAGE"
    IMAGE_DECODE_FAILED = "IMAGE_DECODE_FAILED"
    OCR_TIMEOUT = "OCR_TIMEOUT"
    OCR_FAILED = "OCR_FAILED"
    OCR_EMPTY = "OCR_EMPTY"
    OCR_LANGUAGE_UNAVAILABLE = "OCR_LANGUAGE_UNAVAILABLE"
    UNKNOWN_RECEIPT = "UNKNOWN_RECEIPT"
    PARSER_FAILED = "PARSER_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class PreprocessedImage:
    data: bytes
    content_type: str
    width: int
    height: int
    transformations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OcrToken:
    text: str
    confidence: float | None
    bounding_box: tuple[int, int, int, int] | None = None

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "bounding_box": list(self.bounding_box) if self.bounding_box else None,
        }


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    confidence: float | None
    bounding_box: tuple[int, int, int, int] | None = None
    tokens: tuple[OcrToken, ...] = ()
    origin: OcrLineOrigin = OcrLineOrigin.PRIMARY
    ocr_mode: str = "full_page_psm6"
    source_region: tuple[int, int, int, int] | None = None
    source_line_index: int | None = None
    parser_eligible: bool = True

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "bounding_box": list(self.bounding_box) if self.bounding_box else None,
            "tokens": [token.as_json() for token in self.tokens],
            "origin": self.origin.value,
            "ocr_mode": self.ocr_mode,
            "source_region": list(self.source_region) if self.source_region else None,
            "source_line_index": self.source_line_index,
            "parser_eligible": self.parser_eligible,
        }


@dataclass(frozen=True, slots=True)
class OcrDocument:
    provider_id: str
    provider_version: str
    requested_languages: tuple[str, ...]
    available_languages: tuple[str, ...]
    normalized_text: str
    lines: tuple[OcrLine, ...]
    mean_confidence: float | None
    duration_ms: int
    secondary_lines: tuple[OcrLine, ...] = ()

    @property
    def evidence_lines(self) -> tuple[OcrLine, ...]:
        return (*self.lines, *self.secondary_lines)

    @property
    def parser_lines(self) -> tuple[OcrLine, ...]:
        by_source: dict[int, list[OcrLine]] = {}
        for line in self.secondary_lines:
            if not line.parser_eligible or line.source_line_index is None:
                continue
            by_source.setdefault(line.source_line_index, []).append(line)
        result: list[OcrLine] = []
        for index, primary in enumerate(self.lines):
            result.extend(by_source.get(index, ()))
            result.append(primary)
        return tuple(result)

    def parser_view(self) -> "OcrDocument":
        return replace(self, lines=self.parser_lines, secondary_lines=())


@dataclass(frozen=True, slots=True)
class ExtractionField:
    field: str
    value: str | None
    original_text: str | None
    confidence: float
    status: FieldStatus
    evidence: tuple[str, ...]

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "field": self.field,
            "value": self.value,
            "original_text": self.original_text,
            "confidence": self.confidence,
            "status": self.status.value,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class ParsedReceipt:
    receipt_type: ReceiptType
    parser_version: str
    classification_evidence: tuple[str, ...]
    fields: tuple[ExtractionField, ...]
    review_status: ReviewStatus
    amount: Decimal | None
    currency: str | None
    transaction_occurred_at: datetime | None
    timezone_state: str
    reference_original: str | None
    reference_normalized: str | None
    sender_name: str | None
    recipient_name: str | None
    recipient_identifier: str | None
    transfer_status: str | None
    evidence_result: dict[str, JsonValue] | None = None

    def machine_result(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "receipt_type": self.receipt_type.value,
            "parser_version": self.parser_version,
            "classification_evidence": list(self.classification_evidence),
            "fields": [field.as_json() for field in self.fields],
            "review_status": self.review_status.value,
            "source_trust": "UNVERIFIED_UPLOAD",
        }
        if self.evidence_result is not None:
            result["evidence_first"] = self.evidence_result
        return result


@dataclass(slots=True)
class ProcessingJob:
    id: UUID
    object_id: UUID
    raw_event_id: UUID
    device_id: UUID
    processor_version: str
    parser_type: str
    parser_version: str
    ocr_provider: str
    requested_languages: tuple[str, ...]
    status: JobStatus
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: ProcessingFailure | None = None


@dataclass(slots=True)
class ReceiptCandidate:
    id: UUID
    job_id: UUID
    object_id: UUID
    raw_event_id: UUID
    device_id: UUID
    receipt_type: ReceiptType
    parser_version: str
    review_status: ReviewStatus
    duplicate_of_candidate_id: UUID | None
    duplicate_reason: str | None
    amount: Decimal | None
    currency: str | None
    transaction_occurred_at: datetime | None
    timezone_state: str
    reference_original: str | None
    reference_normalized: str | None
    sender_name: str | None
    recipient_name: str | None
    recipient_identifier: str | None
    transfer_status: str | None
    machine_result: dict[str, JsonValue]
    reviewed_result: dict[str, JsonValue] | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    resolution_result: dict[str, JsonValue] | None = None
