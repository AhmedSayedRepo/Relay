"""Sprint 2 persistence. Historical credentials and installations are retained."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from relay.domain.agent import JsonValue
from relay.infrastructure.database import Base


class ChallengeRow(Base):
    __tablename__ = "enrollment_challenges"
    __table_args__ = (
        CheckConstraint("attempts >= 0 AND attempts <= 5", name="challenge_attempts"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"), index=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    replace_existing: Mapped[bool] = mapped_column(Boolean)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InstallationRow(Base):
    __tablename__ = "device_installations"
    __table_args__ = (
        Index(
            "uq_device_installations_active_device",
            "device_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
        CheckConstraint(
            "acknowledged_version IS NULL OR acknowledged_version > 0",
            name="ack_positive",
        ),
        CheckConstraint("reported_config_version >= 0", name="reported_nonnegative"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"), index=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_version: Mapped[int | None] = mapped_column(Integer)
    reported_config_version: Mapped[int] = mapped_column(Integer, default=0)


class CredentialRow(Base):
    __tablename__ = "device_credentials"
    __table_args__ = (
        Index(
            "uq_device_credentials_active_installation",
            "installation_id",
            unique=True,
            postgresql_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
            sqlite_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    installation_id: Mapped[UUID] = mapped_column(
        ForeignKey("device_installations.id"), index=True
    )
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"), index=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_request_id: Mapped[UUID | None] = mapped_column(Uuid)
    # Application checks successor binding; no self-FK insert-order dependency.
    successor_id: Mapped[UUID | None] = mapped_column(Uuid)


class ConfigurationRow(Base):
    __tablename__ = "device_configurations"
    __table_args__ = (
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("schema_version = 1", name="supported_schema"),
    )
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    settings: Mapped[dict[str, JsonValue]] = mapped_column(JSON)


class DeliveryRow(Base):
    __tablename__ = "configuration_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "version"],
            ["device_configurations.device_id", "device_configurations.version"],
        ),
    )
    installation_id: Mapped[UUID] = mapped_column(
        ForeignKey("device_installations.id"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"))
