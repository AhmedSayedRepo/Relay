"""Add Sprint 8 field-review events and an independent trust projection.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.add_column(
        "receipt_candidates", sa.Column("resolution_result", json_type, nullable=True)
    )
    op.create_table(
        "receipt_reviewed_fields",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.Uuid(),
            sa.ForeignKey("receipt_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("command_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column(
            "reviewer_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("field", sa.String(40), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("machine_candidates", json_type, nullable=False),
        sa.Column("reviewed_value", sa.String(160)),
        sa.Column("reason", sa.String(500)),
        sa.Column("provenance", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('CONFIRM','CORRECT','UNAVAILABLE')",
            name="receipt_reviewed_field_action",
        ),
    )
    op.create_index(
        "ix_receipt_reviewed_fields_candidate",
        "receipt_reviewed_fields",
        ["candidate_id", "created_at"],
    )
    op.create_index(
        "ix_receipt_reviewed_fields_created",
        "receipt_reviewed_fields",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_receipt_reviewed_fields_created", table_name="receipt_reviewed_fields"
    )
    op.drop_index(
        "ix_receipt_reviewed_fields_candidate", table_name="receipt_reviewed_fields"
    )
    op.drop_table("receipt_reviewed_fields")
    op.drop_column("receipt_candidates", "resolution_result")
