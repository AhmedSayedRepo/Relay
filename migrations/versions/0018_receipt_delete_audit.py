"""Operator audit records a receipt deleted from the Rejected page.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TARGET = (
    "(action = 'DUPLICATE_RESOLUTION' AND candidate_id IS NOT NULL "
    "AND job_id IS NULL) OR "
    "(action = 'PROCESSING_RETRY' AND job_id IS NOT NULL "
    "AND candidate_id IS NULL) OR "
)


def _rules(actions: str, simple: str) -> None:
    with op.batch_alter_table("operator_audit") as batch:
        batch.drop_constraint("operator_audit_action", type_="check")
        batch.drop_constraint("operator_audit_target", type_="check")
        batch.create_check_constraint("operator_audit_action", f"action IN ({actions})")
        batch.create_check_constraint(
            "operator_audit_target",
            _TARGET
            + f"(action IN ({simple}) AND candidate_id IS NULL AND job_id IS NULL)",
        )


def upgrade() -> None:
    _rules(
        "'DUPLICATE_RESOLUTION','PROCESSING_RETRY','EXPORT','RECEIPT_DELETE'",
        "'EXPORT','RECEIPT_DELETE'",
    )


def downgrade() -> None:
    op.execute("DELETE FROM operator_audit WHERE action = 'RECEIPT_DELETE'")
    _rules("'DUPLICATE_RESOLUTION','PROCESSING_RETRY','EXPORT'", "'EXPORT'")
