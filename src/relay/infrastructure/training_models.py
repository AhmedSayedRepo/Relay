from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from relay.infrastructure.database import Base
from relay.infrastructure.processing_models import JSON_TYPE


class TrainingConsentRow(Base):
    """Per-device opt-in for copying reviewed receipts into the dataset."""

    __tablename__ = "training_capture_consents"

    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class TrainingExampleRow(Base):
    """Self-contained reviewed example; survives receipt/image retention purges."""

    __tablename__ = "training_examples"
    __table_args__ = (
        UniqueConstraint("candidate_id", name="uq_training_examples_candidate"),
        CheckConstraint(
            "split IN ('TRAIN','HOLDOUT')", name="training_example_split"
        ),
        Index("ix_training_examples_device", "device_id"),
        Index("ix_training_examples_group", "transaction_group_id"),
        Index("ix_training_examples_created", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    # No foreign key: the source candidate may later expire.
    candidate_id: Mapped[UUID] = mapped_column(nullable=False)
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    transaction_group_id: Mapped[str] = mapped_column(String(64), nullable=False)
    split: Mapped[str] = mapped_column(String(8), nullable=False)
    image_key: Mapped[str] = mapped_column(String(128), nullable=False)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    label_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
