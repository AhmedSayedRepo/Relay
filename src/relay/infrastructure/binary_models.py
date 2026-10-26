from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from relay.infrastructure.database import Base


class BinaryObjectRow(Base):
    __tablename__ = "binary_objects"
    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "content_hash",
            name="uq_binary_objects_installation_hash",
        ),
        CheckConstraint(
            "content_type IN ('image/jpeg','image/png','image/webp')",
            name="binary_object_content_type",
        ),
        CheckConstraint(
            "byte_size > 0 AND byte_size <= 8388608", name="binary_object_byte_size"
        ),
        CheckConstraint(
            "width > 0 AND width <= 12000 AND height > 0 AND height <= 12000",
            name="binary_object_dimensions",
        ),
        CheckConstraint("width * height <= 40000000", name="binary_object_pixels"),
        CheckConstraint(
            "storage_backend = 'filesystem'", name="binary_object_storage_backend"
        ),
        CheckConstraint("status = 'available'", name="binary_object_status"),
        Index("ix_binary_objects_device_created", "device_id", "created_at"),
        Index("ix_binary_objects_delete_after", "delete_after"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"), nullable=False)
    installation_id: Mapped[UUID] = mapped_column(
        ForeignKey("device_installations.id"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="available")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finalized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    delete_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class UploadSessionRow(Base):
    __tablename__ = "binary_upload_sessions"
    __table_args__ = (
        UniqueConstraint(
            "installation_id", "upload_id", name="uq_binary_uploads_installation_upload"
        ),
        CheckConstraint(
            "status IN ('active','finalized')", name="binary_upload_status"
        ),
        CheckConstraint(
            "byte_size > 0 AND byte_size <= 8388608", name="binary_upload_byte_size"
        ),
        CheckConstraint(
            "uploaded_bytes >= 0 AND uploaded_bytes <= byte_size",
            name="binary_upload_progress",
        ),
        Index("ix_binary_uploads_device_status", "device_id", "status"),
        Index("ix_binary_uploads_expires_at", "expires_at"),
        Index("ix_binary_uploads_device_created", "device_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    upload_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    event_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"), nullable=False)
    installation_id: Mapped[UUID] = mapped_column(
        ForeignKey("device_installations.id"), nullable=False
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    media_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uploaded_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    object_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("binary_objects.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
