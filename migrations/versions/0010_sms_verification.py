"""Cross-device SMS verification: device links and per-receipt decisions.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sms_verification_links",
        sa.Column(
            "receipt_device_id",
            sa.Uuid(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "sms_device_id",
            sa.Uuid(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.CheckConstraint(
            "receipt_device_id <> sms_device_id", name="sms_link_distinct_devices"
        ),
        sa.CheckConstraint("direction IN ('IN','OUT')", name="sms_link_direction"),
    )
    op.create_index(
        "ix_sms_links_sms_device", "sms_verification_links", ["sms_device_id"]
    )
    op.create_table(
        "receipt_sms_verifications",
        sa.Column(
            "candidate_id",
            sa.Uuid(),
            sa.ForeignKey("receipt_candidates.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("sms_event_id", sa.Uuid(), nullable=True, unique=True),
        sa.Column("sms_device_id", sa.Uuid(), nullable=True),
        sa.Column("sms_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sms_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("sms_currency", sa.String(3), nullable=True),
        sa.Column("sms_direction", sa.String(3), nullable=True),
        sa.Column("sms_stated_at", sa.DateTime(), nullable=True),
        sa.Column("option_count", sa.Integer(), nullable=False),
        sa.Column(
            "decided_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('VERIFIED','AMBIGUOUS','WAITING','UNMATCHED')",
            name="sms_verification_status",
        ),
        sa.CheckConstraint(
            "method IN ('AUTO','MANUAL')", name="sms_verification_method"
        ),
        sa.CheckConstraint(
            "(status = 'VERIFIED') = (sms_event_id IS NOT NULL)",
            name="sms_verification_evidence",
        ),
    )
    op.create_index(
        "ix_sms_verifications_status", "receipt_sms_verifications", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_sms_verifications_status", table_name="receipt_sms_verifications")
    op.drop_table("receipt_sms_verifications")
    op.drop_index("ix_sms_links_sms_device", table_name="sms_verification_links")
    op.drop_table("sms_verification_links")
