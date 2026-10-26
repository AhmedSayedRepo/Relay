"""Generic idempotent raw events with retention metadata.

Revision ID: 0003
Revises: 0002 (unchanged)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "raw_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column(
            "installation_id",
            sa.Uuid(),
            sa.ForeignKey("device_installations.id"),
            nullable=False,
        ),
        sa.Column("collector", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("metadata", json_type, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("ingestion_status", sa.String(16), nullable=False),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "installation_id",
            "event_id",
            name="uq_raw_events_installation_event",
        ),
        sa.CheckConstraint("collector = 'sms'", name="raw_event_collector"),
        sa.CheckConstraint("schema_version = 1", name="raw_event_schema"),
        sa.CheckConstraint("config_version > 0", name="raw_event_config_version"),
        sa.CheckConstraint(
            "ingestion_status = 'accepted'", name="raw_event_ingestion_status"
        ),
    )
    op.create_index(
        "ix_raw_events_device_occurred", "raw_events", ["device_id", "occurred_at"]
    )
    op.create_index(
        "ix_raw_events_collector_occurred",
        "raw_events",
        ["collector", "occurred_at"],
    )
    op.create_index("ix_raw_events_delete_after", "raw_events", ["delete_after"])


def downgrade() -> None:
    op.drop_table("raw_events")
