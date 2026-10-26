"""Durable audit records for Sprint 6 operator actions."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from relay.infrastructure.database import Base
from relay.infrastructure.processing_models import JSON_TYPE


class OperatorAuditRow(Base):
    __tablename__ = "operator_audit"
    __table_args__ = (
        CheckConstraint(
            "action IN ('DUPLICATE_RESOLUTION','PROCESSING_RETRY','EXPORT')",
            name="operator_audit_action",
        ),
        CheckConstraint(
            "(action = 'DUPLICATE_RESOLUTION' AND candidate_id IS NOT NULL "
            "AND job_id IS NULL) OR "
            "(action = 'PROCESSING_RETRY' AND job_id IS NOT NULL "
            "AND candidate_id IS NULL) OR "
            "(action = 'EXPORT' AND candidate_id IS NULL AND job_id IS NULL)",
            name="operator_audit_target",
        ),
        Index("ix_operator_audit_actor", "actor_id", "created_at"),
        Index("ix_operator_audit_candidate", "candidate_id", "created_at"),
        Index("ix_operator_audit_job", "job_id", "created_at"),
        Index("ix_operator_audit_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    # Target identifiers intentionally outlive short-retention receipt/job rows.
    candidate_id: Mapped[UUID | None] = mapped_column()
    job_id: Mapped[UUID | None] = mapped_column()
    context: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
