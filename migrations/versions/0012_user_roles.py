"""User roles (admin, reviewer, viewer) and hidden deleted users.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Everyone who could sign in before keeps full access.
    op.add_column(
        "users",
        sa.Column(
            "role", sa.String(16), nullable=False, server_default=sa.text("'ADMIN'")
        ),
    )
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "role")
