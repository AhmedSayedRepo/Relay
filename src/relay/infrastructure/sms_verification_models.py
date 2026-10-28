from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from relay.infrastructure.database import Base


class SmsSourceLinkRow(Base):
    """Receipts collected on one device are verified against another's SMS."""

    __tablename__ = "sms_verification_links"
    __table_args__ = (
        CheckConstraint(
            "receipt_device_id <> sms_device_id", name="sms_link_distinct_devices"
        ),
        Index("ix_sms_links_sms_device", "sms_device_id"),
    )

    receipt_device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    sms_device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SmsVerificationRow(Base):
    """Latest verification decision; SMS facts are snapshotted to outlive purges."""

    __tablename__ = "receipt_sms_verifications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('VERIFIED','AMBIGUOUS','WAITING','UNMATCHED')",
            name="sms_verification_status",
        ),
        CheckConstraint("method IN ('AUTO','MANUAL')", name="sms_verification_method"),
        CheckConstraint(
            "(status = 'VERIFIED') = (sms_event_id IS NOT NULL)",
            name="sms_verification_evidence",
        ),
        Index("ix_sms_verifications_status", "status"),
    )

    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("receipt_candidates.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    # No FK: the raw SMS expires, but one SMS may still verify only one receipt.
    sms_event_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    sms_device_id: Mapped[UUID | None] = mapped_column(Uuid)
    sms_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sms_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    sms_currency: Mapped[str | None] = mapped_column(String(3))
    option_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decided_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
