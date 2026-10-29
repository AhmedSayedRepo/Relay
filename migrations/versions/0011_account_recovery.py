"""Account recovery: one-time recovery codes and email reset tokens.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _user_column() -> sa.Column[sa.Uuid]:
    return sa.Column(
        "user_id",
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "user_recovery_codes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user_column(),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("code_hash", name="uq_user_recovery_codes_code_hash"),
    )
    op.create_index("ix_user_recovery_codes_user", "user_recovery_codes", ["user_id"])
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user_column(),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
    )
    op.create_index(
        "ix_password_reset_tokens_user", "password_reset_tokens", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_password_reset_tokens_user", "password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_index("ix_user_recovery_codes_user", "user_recovery_codes")
    op.drop_table("user_recovery_codes")
