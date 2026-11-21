"""Cashier shifts: open until someone closes them; closing opens the next.

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shifts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.Uuid(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["closed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("day", "number", name="uq_shifts_day_number"),
    )
    op.create_index("ix_shifts_opened", "shifts", ["opened_at"])


def downgrade() -> None:
    op.drop_index("ix_shifts_opened", table_name="shifts")
    op.drop_table("shifts")
