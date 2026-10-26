"""SQLAlchemy models for durable OCR, receipt processing, and review audit."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from relay.infrastructure.database import Base

JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class ProcessingJobRow(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        UniqueConstraint(
            "object_id",
            "processor_version",
            "parser_type",
            "parser_version",
            name="uq_processing_jobs_version_tuple",
        ),
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','SUCCEEDED','NEEDS_REVIEW',"
            "'FAILED','SKIPPED')",
            name="processing_job_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts AND "
            "max_attempts >= 1 AND max_attempts <= 5",
            name="processing_job_attempt_bounds",
        ),
        Index("ix_processing_jobs_claim", "status", "next_attempt_at", "created_at"),
        Index("ix_processing_jobs_object", "object_id"),
        Index("ix_processing_jobs_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    object_id: Mapped[UUID] = mapped_column(
        ForeignKey("binary_objects.id", ondelete="CASCADE"), nullable=False
    )
    raw_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("raw_events.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    processor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_type: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    ocr_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_languages: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ProcessingAttemptRow(Base):
    __tablename__ = "processing_attempts"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "attempt_number", name="uq_processing_attempt_number"
        ),
        CheckConstraint(
            "status IN ('PROCESSING','SUCCEEDED','FAILED')",
            name="processing_attempt_status",
        ),
        CheckConstraint(
            "attempt_number >= 1 AND attempt_number <= 5",
            name="processing_attempt_number",
        ),
        Index("ix_processing_attempts_job", "job_id", "attempt_number"),
        Index("ix_processing_attempts_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_version: Mapped[str | None] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class OcrResultRow(Base):
    __tablename__ = "ocr_results"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_ocr_results_job"),
        CheckConstraint(
            "length(normalized_text) <= 65536", name="ocr_result_text_length"
        ),
        CheckConstraint(
            "mean_confidence IS NULL OR "
            "(mean_confidence >= 0 AND mean_confidence <= 1)",
            name="ocr_result_confidence",
        ),
        Index("ix_ocr_results_object", "object_id"),
        Index("ix_ocr_results_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    object_id: Mapped[UUID] = mapped_column(
        ForeignKey("binary_objects.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(64), nullable=False)
    processor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_languages: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)
    available_languages: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, nullable=False)
    mean_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ReceiptCandidateRow(Base):
    __tablename__ = "receipt_candidates"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_receipt_candidates_job"),
        CheckConstraint(
            "receipt_type IN ('INSTAPAY_TRANSFER_RECEIPT','UNKNOWN_RECEIPT')",
            name="receipt_candidate_type",
        ),
        CheckConstraint(
            "review_status IN ('AUTO_ACCEPTED','NEEDS_REVIEW','REJECTED')",
            name="receipt_candidate_review_status",
        ),
        CheckConstraint(
            "duplicate_reason IS NULL OR duplicate_reason = 'REFERENCE_NUMBER'",
            name="receipt_candidate_duplicate_reason",
        ),
        CheckConstraint(
            "amount IS NULL OR amount > 0", name="receipt_candidate_amount"
        ),
        CheckConstraint(
            "currency IS NULL OR length(currency) = 3",
            name="receipt_candidate_currency",
        ),
        CheckConstraint(
            "timezone_state IN ('KNOWN','UNKNOWN')",
            name="receipt_candidate_timezone_state",
        ),
        CheckConstraint(
            "transfer_status IS NULL OR "
            "transfer_status IN ('SUCCESS','FAILED','PENDING')",
            name="receipt_candidate_transfer_status",
        ),
        CheckConstraint(
            "reference_normalized IS NULL OR "
            "(length(reference_normalized) >= 8 AND "
            "length(reference_normalized) <= 40)",
            name="receipt_candidate_reference_length",
        ),
        Index("ix_receipt_candidates_review", "review_status", "created_at"),
        Index("ix_receipt_candidates_device", "device_id", "created_at"),
        Index("ix_receipt_candidates_reference", "device_id", "reference_normalized"),
        Index("ix_receipt_candidates_expires", "expires_at"),
        Index("ix_receipt_candidates_created", "created_at", "id"),
        Index("ix_receipt_candidates_currency_created", "currency", "created_at"),
        Index(
            "ix_receipt_candidates_duplicate",
            "duplicate_of_candidate_id",
            "created_at",
        ),
        Index(
            "uq_receipt_canonical_reference",
            "device_id",
            "receipt_type",
            "reference_normalized",
            unique=True,
            postgresql_where=text(
                "reference_normalized IS NOT NULL AND duplicate_of_candidate_id IS NULL"
            ),
            sqlite_where=text(
                "reference_normalized IS NOT NULL AND duplicate_of_candidate_id IS NULL"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    object_id: Mapped[UUID] = mapped_column(
        ForeignKey("binary_objects.id", ondelete="CASCADE"), nullable=False
    )
    raw_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("raw_events.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    receipt_type: Mapped[str] = mapped_column(String(40), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False)
    duplicate_of_candidate_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("receipt_candidates.id", ondelete="SET NULL")
    )
    duplicate_reason: Mapped[str | None] = mapped_column(String(32))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    transaction_occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False)
    )
    timezone_state: Mapped[str] = mapped_column(String(16), nullable=False)
    reference_original: Mapped[str | None] = mapped_column(String(128))
    reference_normalized: Mapped[str | None] = mapped_column(String(64))
    sender_name: Mapped[str | None] = mapped_column(String(160))
    recipient_name: Mapped[str | None] = mapped_column(String(160))
    recipient_identifier: Mapped[str | None] = mapped_column(String(128))
    transfer_status: Mapped[str | None] = mapped_column(String(24))
    machine_result: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    reviewed_result: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    resolution_result: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ReceiptReviewAuditRow(Base):
    __tablename__ = "receipt_review_audit"
    __table_args__ = (
        CheckConstraint(
            "action IN ('ACCEPT','CORRECT','REJECT')", name="receipt_review_action"
        ),
        Index("ix_receipt_review_candidate", "candidate_id", "created_at"),
        Index("ix_receipt_review_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("receipt_candidates.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    before_result: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    after_result: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ReceiptReviewedFieldRow(Base):
    """Append-only operator decision; never an extraction result."""

    __tablename__ = "receipt_reviewed_fields"
    __table_args__ = (
        CheckConstraint(
            "action IN ('CONFIRM','CORRECT','UNAVAILABLE')",
            name="receipt_reviewed_field_action",
        ),
        Index("ix_receipt_reviewed_fields_candidate", "candidate_id", "created_at"),
        Index("ix_receipt_reviewed_fields_created", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("receipt_candidates.id", ondelete="CASCADE"), nullable=False
    )
    command_id: Mapped[UUID] = mapped_column(nullable=False, unique=True)
    reviewer_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    machine_candidates: Mapped[list[Any]] = mapped_column(JSON_TYPE, nullable=False)
    reviewed_value: Mapped[str | None] = mapped_column(String(160))
    reason: Mapped[str | None] = mapped_column(String(500))
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ReceiptEvidenceRow(Base):
    __tablename__ = "receipt_evidence"
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("receipt_candidates.id", ondelete="CASCADE"), primary_key=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ReceiptExceptionCommandRow(Base):
    __tablename__ = "receipt_exception_commands"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("receipt_candidates.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    body_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
