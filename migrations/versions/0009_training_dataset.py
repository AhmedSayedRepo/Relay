"""Add per-device training consent and self-contained reviewed training examples.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "training_capture_consents",
        sa.Column(
            "device_id",
            sa.Uuid(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "training_examples",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column(
            "device_id",
            sa.Uuid(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("transaction_group_id", sa.String(64), nullable=False),
        sa.Column("split", sa.String(8), nullable=False),
        sa.Column("image_key", sa.String(128), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("labels", json_type, nullable=False),
        sa.Column("label_version", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_id", name="uq_training_examples_candidate"),
        sa.CheckConstraint(
            "split IN ('TRAIN','HOLDOUT')", name="training_example_split"
        ),
    )
    op.create_index("ix_training_examples_device", "training_examples", ["device_id"])
    op.create_index(
        "ix_training_examples_group", "training_examples", ["transaction_group_id"]
    )
    op.create_index(
        "ix_training_examples_created", "training_examples", ["created_at", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_training_examples_created", table_name="training_examples")
    op.drop_index("ix_training_examples_group", table_name="training_examples")
    op.drop_index("ix_training_examples_device", table_name="training_examples")
    op.drop_table("training_examples")
    op.drop_table("training_capture_consents")
