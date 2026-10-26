from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from relay.domain.agent import JsonValue
from relay.presentation.schemas import InputModel


class ProcessingRequest(InputModel):
    processor_version: str = Field(
        default="receipt_image:v1", pattern=r"^[a-z][a-z0-9_.-]{0,31}:v[1-9][0-9]{0,5}$"
    )
    parser_type: str = Field(
        default="instapay_receipt", pattern=r"^[a-z][a-z0-9_.-]{0,31}$"
    )
    parser_version: str = Field(default="v1", pattern=r"^v[1-9][0-9]{0,5}$")
    requested_languages: list[str] = Field(
        default_factory=lambda: ["eng", "ara"], min_length=1, max_length=4
    )


class ProcessingJobResponse(BaseModel):
    id: UUID
    object_id: UUID
    raw_event_id: UUID
    device_id: UUID
    processor_version: str
    parser_type: str
    parser_version: str
    ocr_provider: str
    requested_languages: list[str]
    status: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failure_code: str | None


class ProcessingJobDetail(ProcessingJobResponse):
    attempts: list[dict[str, JsonValue]]


class ReceiptCandidateResponse(BaseModel):
    id: UUID
    job_id: UUID
    object_id: UUID
    raw_event_id: UUID
    device_id: UUID
    receipt_type: str
    parser_version: str
    review_status: str
    authoritative_status: str
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
    resolution_result: dict[str, JsonValue] | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class ReceiptCandidateDetail(ReceiptCandidateResponse):
    ocr: dict[str, JsonValue]
    audit: list[dict[str, JsonValue]]
    reviewed_fields: list[dict[str, JsonValue]] = Field(default_factory=list)


class CorrectionInput(InputModel):
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    transaction_occurred_at: datetime | None = None
    timezone_state: Literal["KNOWN", "UNKNOWN"] | None = None
    reference_number: str | None = Field(default=None, min_length=1, max_length=128)
    sender_name: str | None = Field(default=None, min_length=1, max_length=160)
    recipient_name: str | None = Field(default=None, min_length=1, max_length=160)
    recipient_identifier: str | None = Field(default=None, min_length=1, max_length=128)
    transfer_status: Literal["SUCCESS", "FAILED", "PENDING"] | None = None


class ReviewRequest(InputModel):
    action: Literal["ACCEPT", "CORRECT", "REJECT"]
    corrections: CorrectionInput | None = None


class ExceptionRequest(InputModel):
    command_id: UUID
    expected_updated_at: str = Field(min_length=10, max_length=40)
    field: (
        Literal[
            "reference_number",
            "amount",
            "currency",
            "date",
            "time",
            "classification",
            "layout",
            "sender",
            "handle",
            "recipient",
            "recipient_identifier",
            "transfer_status",
        ]
        | None
    ) = None
    action: Literal["CONFIRM", "CORRECT", "UNAVAILABLE", "REJECT"]
    value: str | None = Field(default=None, max_length=128)
    reason: str | None = Field(default=None, max_length=500)


class ProcessingList(BaseModel):
    items: list[ProcessingJobResponse]
    limit: int
    offset: int
    has_more: bool


class ReceiptList(BaseModel):
    items: list[ReceiptCandidateResponse]
