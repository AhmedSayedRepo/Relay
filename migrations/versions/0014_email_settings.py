"""Email sender settings entered in the portal.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sender_name", sa.String(120), nullable=False),
        sa.Column("sender_email", sa.String(254), nullable=False),
        sa.Column("smtp_host", sa.String(253), nullable=False),
        sa.Column("smtp_port", sa.Integer(), nullable=False),
        sa.Column("smtp_security", sa.String(16), nullable=False),
        sa.Column("password_encrypted", sa.Text()),
        sa.Column("report_email", sa.String(254)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid()),
    )


def downgrade() -> None:
    op.drop_table("email_settings")
