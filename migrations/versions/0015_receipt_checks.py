"""Same-day bank SMS matches, hidden non-receipt images, admin settings.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.operations import BatchOperations

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _batch(table: str) -> BatchOperations:
    if op.get_context().dialect.name == "sqlite":
        return op.batch_alter_table(table, recreate="always")
    return op.batch_alter_table(table)


def upgrade() -> None:
    op.add_column(
        "receipt_candidates", sa.Column("hidden_reason", sa.String(32), nullable=True)
    )
    with _batch("receipt_sms_verifications") as batch:
        batch.drop_constraint(
            batch.f("ck_receipt_sms_verifications_sms_verification_method"),
            type_="check",
        )
        batch.create_check_constraint(
            "sms_verification_method", "method IN ('AUTO','MANUAL','DAY')"
        )
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid()),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.execute("DELETE FROM receipt_sms_verifications WHERE method = 'DAY'")
    with _batch("receipt_sms_verifications") as batch:
        batch.drop_constraint(
            batch.f("ck_receipt_sms_verifications_sms_verification_method"),
            type_="check",
        )
        batch.create_check_constraint(
            "sms_verification_method", "method IN ('AUTO','MANUAL')"
        )
    op.drop_column("receipt_candidates", "hidden_reason")
