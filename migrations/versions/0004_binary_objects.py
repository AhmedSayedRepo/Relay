"""Binary objects, resumable uploads, and image raw-event linkage.

Revision ID: 0004
Revises: 0003 (unchanged)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.operations import BatchOperations

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "binary_objects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column(
            "installation_id",
            sa.Uuid(),
            sa.ForeignKey("device_installations.id"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("storage_backend", sa.String(32), nullable=False),
        sa.Column("storage_key", sa.String(160), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "installation_id",
            "content_hash",
            name="uq_binary_objects_installation_hash",
        ),
        sa.CheckConstraint(
            "content_type IN ('image/jpeg','image/png','image/webp')",
            name="binary_object_content_type",
        ),
        sa.CheckConstraint(
            "byte_size > 0 AND byte_size <= 8388608", name="binary_object_byte_size"
        ),
        sa.CheckConstraint(
            "width > 0 AND width <= 12000 AND height > 0 AND height <= 12000",
            name="binary_object_dimensions",
        ),
        sa.CheckConstraint("width * height <= 40000000", name="binary_object_pixels"),
        sa.CheckConstraint(
            "storage_backend = 'filesystem'", name="binary_object_storage_backend"
        ),
        sa.CheckConstraint("status = 'available'", name="binary_object_status"),
    )
    op.create_index(
        "ix_binary_objects_device_created",
        "binary_objects",
        ["device_id", "created_at"],
    )
    op.create_index(
        "ix_binary_objects_delete_after", "binary_objects", ["delete_after"]
    )

    op.create_table(
        "binary_upload_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("upload_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column(
            "installation_id",
            sa.Uuid(),
            sa.ForeignKey("device_installations.id"),
            nullable=False,
        ),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("media_at", sa.DateTime(timezone=True)),
        sa.Column("uploaded_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "object_id",
            sa.Uuid(),
            sa.ForeignKey("binary_objects.id", ondelete="CASCADE"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "installation_id", "upload_id", name="uq_binary_uploads_installation_upload"
        ),
        sa.CheckConstraint(
            "status IN ('active','finalized')", name="binary_upload_status"
        ),
        sa.CheckConstraint(
            "byte_size > 0 AND byte_size <= 8388608", name="binary_upload_byte_size"
        ),
        sa.CheckConstraint(
            "uploaded_bytes >= 0 AND uploaded_bytes <= byte_size",
            name="binary_upload_progress",
        ),
    )
    op.create_index(
        "ix_binary_uploads_device_status",
        "binary_upload_sessions",
        ["device_id", "status"],
    )
    op.create_index(
        "ix_binary_uploads_expires_at", "binary_upload_sessions", ["expires_at"]
    )
    op.create_index(
        "ix_binary_uploads_device_created",
        "binary_upload_sessions",
        ["device_id", "created_at"],
    )

    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("raw_events", recreate="always") as batch:
            _upgrade_raw_events(batch)
    else:
        with op.batch_alter_table("raw_events") as batch:
            _upgrade_raw_events(batch)


def _upgrade_raw_events(batch: BatchOperations) -> None:
    batch.add_column(sa.Column("object_id", sa.Uuid(), nullable=True))
    batch.create_foreign_key(
        batch.f("fk_raw_events_object_id_binary_objects"),
        "binary_objects",
        ["object_id"],
        ["id"],
        ondelete="CASCADE",
    )
    batch.drop_constraint(batch.f("ck_raw_events_raw_event_collector"), type_="check")
    batch.create_check_constraint(
        "raw_event_collector", "collector IN ('sms','images')"
    )
    batch.create_check_constraint(
        "raw_event_object_link",
        "(collector = 'sms' AND object_id IS NULL) OR "
        "(collector = 'images' AND object_id IS NOT NULL)",
    )


def downgrade() -> None:
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("raw_events", recreate="always") as batch:
            _downgrade_raw_events(batch)
    else:
        with op.batch_alter_table("raw_events") as batch:
            _downgrade_raw_events(batch)
    op.drop_table("binary_upload_sessions")
    op.drop_table("binary_objects")


def _downgrade_raw_events(batch: BatchOperations) -> None:
    batch.drop_constraint(batch.f("ck_raw_events_raw_event_object_link"), type_="check")
    batch.drop_constraint(batch.f("ck_raw_events_raw_event_collector"), type_="check")
    batch.drop_constraint(
        batch.f("fk_raw_events_object_id_binary_objects"), type_="foreignkey"
    )
    batch.drop_column("object_id")
    batch.create_check_constraint("raw_event_collector", "collector = 'sms'")
