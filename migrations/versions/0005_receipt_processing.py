"""Durable OCR, receipt extraction, duplicate linkage, and review audit.

Revision ID: 0005
Revises: 0004 (unchanged)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "object_id",
            sa.Uuid(),
            sa.ForeignKey("binary_objects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "raw_event_id",
            sa.Uuid(),
            sa.ForeignKey("raw_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Uuid(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("processor_version", sa.String(64), nullable=False),
        sa.Column("parser_type", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("ocr_provider", sa.String(64), nullable=False),
        sa.Column("requested_languages", json_type, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "object_id",
            "processor_version",
            "parser_type",
            "parser_version",
            name="uq_processing_jobs_version_tuple",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','PROCESSING','SUCCEEDED','NEEDS_REVIEW',"
            "'FAILED','SKIPPED')",
            name="processing_job_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts AND "
            "max_attempts >= 1 AND max_attempts <= 5",
            name="processing_job_attempt_bounds",
        ),
    )
    op.create_index(
        "ix_processing_jobs_claim",
        "processing_jobs",
        ["status", "next_attempt_at", "created_at"],
    )
    op.create_index("ix_processing_jobs_object", "processing_jobs", ["object_id"])
    op.create_index("ix_processing_jobs_expires", "processing_jobs", ["expires_at"])

    op.create_table(
        "processing_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_version", sa.String(64)),
        sa.Column("failure_code", sa.String(40)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "job_id", "attempt_number", name="uq_processing_attempt_number"
        ),
        sa.CheckConstraint(
            "status IN ('PROCESSING','SUCCEEDED','FAILED')",
            name="processing_attempt_status",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1 AND attempt_number <= 5",
            name="processing_attempt_number",
        ),
    )
    op.create_index(
        "ix_processing_attempts_job",
        "processing_attempts",
        ["job_id", "attempt_number"],
    )
    op.create_index(
        "ix_processing_attempts_expires", "processing_attempts", ["expires_at"]
    )

    op.create_table(
        "ocr_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "object_id",
            sa.Uuid(),
            sa.ForeignKey("binary_objects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_version", sa.String(64), nullable=False),
        sa.Column("processor_version", sa.String(64), nullable=False),
        sa.Column("requested_languages", json_type, nullable=False),
        sa.Column("available_languages", json_type, nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("blocks", json_type, nullable=False),
        sa.Column("mean_confidence", sa.Numeric(5, 4)),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", name="uq_ocr_results_job"),
        sa.CheckConstraint(
            "length(normalized_text) <= 65536", name="ocr_result_text_length"
        ),
        sa.CheckConstraint(
            "mean_confidence IS NULL OR "
            "(mean_confidence >= 0 AND mean_confidence <= 1)",
            name="ocr_result_confidence",
        ),
    )
    op.create_index("ix_ocr_results_object", "ocr_results", ["object_id"])
    op.create_index("ix_ocr_results_expires", "ocr_results", ["expires_at"])

    op.create_table(
        "receipt_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "object_id",
            sa.Uuid(),
            sa.ForeignKey("binary_objects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "raw_event_id",
            sa.Uuid(),
            sa.ForeignKey("raw_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Uuid(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("receipt_type", sa.String(40), nullable=False),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("review_status", sa.String(20), nullable=False),
        sa.Column(
            "duplicate_of_candidate_id",
            sa.Uuid(),
            sa.ForeignKey("receipt_candidates.id", ondelete="SET NULL"),
        ),
        sa.Column("duplicate_reason", sa.String(32)),
        sa.Column("amount", sa.Numeric(18, 2)),
        sa.Column("currency", sa.String(3)),
        sa.Column("transaction_occurred_at", sa.DateTime(timezone=False)),
        sa.Column("timezone_state", sa.String(16), nullable=False),
        sa.Column("reference_original", sa.String(128)),
        sa.Column("reference_normalized", sa.String(64)),
        sa.Column("sender_name", sa.String(160)),
        sa.Column("recipient_name", sa.String(160)),
        sa.Column("recipient_identifier", sa.String(128)),
        sa.Column("transfer_status", sa.String(24)),
        sa.Column("machine_result", json_type, nullable=False),
        sa.Column("reviewed_result", json_type),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", name="uq_receipt_candidates_job"),
        sa.CheckConstraint(
            "receipt_type IN ('INSTAPAY_TRANSFER_RECEIPT','UNKNOWN_RECEIPT')",
            name="receipt_candidate_type",
        ),
        sa.CheckConstraint(
            "review_status IN ('AUTO_ACCEPTED','NEEDS_REVIEW','REJECTED')",
            name="receipt_candidate_review_status",
        ),
        sa.CheckConstraint(
            "duplicate_reason IS NULL OR duplicate_reason = 'REFERENCE_NUMBER'",
            name="receipt_candidate_duplicate_reason",
        ),
        sa.CheckConstraint(
            "amount IS NULL OR amount > 0", name="receipt_candidate_amount"
        ),
        sa.CheckConstraint(
            "currency IS NULL OR length(currency) = 3",
            name="receipt_candidate_currency",
        ),
        sa.CheckConstraint(
            "timezone_state IN ('KNOWN','UNKNOWN')",
            name="receipt_candidate_timezone_state",
        ),
        sa.CheckConstraint(
            "transfer_status IS NULL OR "
            "transfer_status IN ('SUCCESS','FAILED','PENDING')",
            name="receipt_candidate_transfer_status",
        ),
        sa.CheckConstraint(
            "reference_normalized IS NULL OR "
            "(length(reference_normalized) >= 8 AND "
            "length(reference_normalized) <= 40)",
            name="receipt_candidate_reference_length",
        ),
    )
    op.create_index(
        "ix_receipt_candidates_review",
        "receipt_candidates",
        ["review_status", "created_at"],
    )
    op.create_index(
        "ix_receipt_candidates_device",
        "receipt_candidates",
        ["device_id", "created_at"],
    )
    op.create_index(
        "ix_receipt_candidates_reference",
        "receipt_candidates",
        ["device_id", "reference_normalized"],
    )
    op.create_index(
        "ix_receipt_candidates_expires", "receipt_candidates", ["expires_at"]
    )
    condition = sa.text(
        "reference_normalized IS NOT NULL AND duplicate_of_candidate_id IS NULL"
    )
    op.create_index(
        "uq_receipt_canonical_reference",
        "receipt_candidates",
        ["device_id", "receipt_type", "reference_normalized"],
        unique=True,
        postgresql_where=condition,
        sqlite_where=condition,
    )

    op.create_table(
        "receipt_review_audit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.Uuid(),
            sa.ForeignKey("receipt_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("before_result", json_type, nullable=False),
        sa.Column("after_result", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('ACCEPT','CORRECT','REJECT')", name="receipt_review_action"
        ),
    )
    op.create_index(
        "ix_receipt_review_candidate",
        "receipt_review_audit",
        ["candidate_id", "created_at"],
    )
    op.create_index("ix_receipt_review_expires", "receipt_review_audit", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_receipt_review_expires", table_name="receipt_review_audit")
    op.drop_index("ix_receipt_review_candidate", table_name="receipt_review_audit")
    op.drop_table("receipt_review_audit")
    op.drop_index("uq_receipt_canonical_reference", table_name="receipt_candidates")
    op.drop_index("ix_receipt_candidates_expires", table_name="receipt_candidates")
    op.drop_index("ix_receipt_candidates_reference", table_name="receipt_candidates")
    op.drop_index("ix_receipt_candidates_device", table_name="receipt_candidates")
    op.drop_index("ix_receipt_candidates_review", table_name="receipt_candidates")
    op.drop_table("receipt_candidates")
    op.drop_index("ix_ocr_results_expires", table_name="ocr_results")
    op.drop_index("ix_ocr_results_object", table_name="ocr_results")
    op.drop_table("ocr_results")
    op.drop_index("ix_processing_attempts_expires", table_name="processing_attempts")
    op.drop_index("ix_processing_attempts_job", table_name="processing_attempts")
    op.drop_table("processing_attempts")
    op.drop_index("ix_processing_jobs_expires", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_object", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_claim", table_name="processing_jobs")
    op.drop_table("processing_jobs")
