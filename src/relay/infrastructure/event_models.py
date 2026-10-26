from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from relay.domain.agent import JsonValue
from relay.infrastructure.database import Base


class RawEventRow(Base):
    __tablename__ = "raw_events"
    __table_args__ = (
        UniqueConstraint(
            "installation_id", "event_id", name="uq_raw_events_installation_event"
        ),
        CheckConstraint("collector IN ('sms','images')", name="raw_event_collector"),
        CheckConstraint("schema_version = 1", name="raw_event_schema"),
        CheckConstraint("config_version > 0", name="raw_event_config_version"),
        CheckConstraint(
            "ingestion_status = 'accepted'", name="raw_event_ingestion_status"
        ),
        CheckConstraint(
            "(collector = 'sms' AND object_id IS NULL) OR "
            "(collector = 'images' AND object_id IS NOT NULL)",
            name="raw_event_object_link",
        ),
        Index("ix_raw_events_device_occurred", "device_id", "occurred_at"),
        Index("ix_raw_events_collector_occurred", "collector", "occurred_at"),
        Index("ix_raw_events_delete_after", "delete_after"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    event_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"), nullable=False)
    installation_id: Mapped[UUID] = mapped_column(
        ForeignKey("device_installations.id"), nullable=False
    )
    collector: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payload: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    metadata_json: Mapped[dict[str, JsonValue]] = mapped_column(
        "metadata", JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ingestion_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="accepted"
    )
    delete_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    object_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("binary_objects.id", ondelete="CASCADE")
    )
