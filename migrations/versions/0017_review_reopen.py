"""Review audit records a receipt brought back to Review (REOPEN).

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _actions(allowed: str) -> None:
    with op.batch_alter_table("receipt_review_audit") as batch:
        batch.drop_constraint("receipt_review_action", type_="check")
        batch.create_check_constraint("receipt_review_action", f"action IN ({allowed})")


def upgrade() -> None:
    _actions("'ACCEPT','CORRECT','REJECT','REOPEN'")


def downgrade() -> None:
    op.execute("DELETE FROM receipt_review_audit WHERE action = 'REOPEN'")
    _actions("'ACCEPT','CORRECT','REJECT'")
