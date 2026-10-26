"""Operator audit and operational query indexes.

Revision ID: 0006
Revises: 0005 (unchanged)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "operator_audit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "actor_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        # Audit target identifiers are retained after the shorter-lived source rows.
        sa.Column("candidate_id", sa.Uuid()),
        sa.Column("job_id", sa.Uuid()),
        sa.Column("context", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('DUPLICATE_RESOLUTION','PROCESSING_RETRY','EXPORT')",
            name="operator_audit_action",
        ),
        sa.CheckConstraint(
            "(action = 'DUPLICATE_RESOLUTION' AND candidate_id IS NOT NULL "
            "AND job_id IS NULL) OR "
            "(action = 'PROCESSING_RETRY' AND job_id IS NOT NULL "
            "AND candidate_id IS NULL) OR "
            "(action = 'EXPORT' AND candidate_id IS NULL AND job_id IS NULL)",
            name="operator_audit_target",
        ),
    )
    op.create_index(
        "ix_operator_audit_actor", "operator_audit", ["actor_id", "created_at"]
    )
    op.create_index(
        "ix_operator_audit_candidate",
        "operator_audit",
        ["candidate_id", "created_at"],
    )
    op.create_index("ix_operator_audit_job", "operator_audit", ["job_id", "created_at"])
    op.create_index("ix_operator_audit_expires", "operator_audit", ["expires_at"])
    op.create_index(
        "ix_receipt_candidates_created",
        "receipt_candidates",
        ["created_at", "id"],
    )
    op.create_index(
        "ix_receipt_candidates_currency_created",
        "receipt_candidates",
        ["currency", "created_at"],
    )
    op.create_index(
        "ix_receipt_candidates_duplicate",
        "receipt_candidates",
        ["duplicate_of_candidate_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_receipt_candidates_duplicate", table_name="receipt_candidates")
    op.drop_index(
        "ix_receipt_candidates_currency_created", table_name="receipt_candidates"
    )
    op.drop_index("ix_receipt_candidates_created", table_name="receipt_candidates")
    op.drop_index("ix_operator_audit_expires", table_name="operator_audit")
    op.drop_index("ix_operator_audit_job", table_name="operator_audit")
    op.drop_index("ix_operator_audit_candidate", table_name="operator_audit")
    op.drop_index("ix_operator_audit_actor", table_name="operator_audit")
    op.drop_table("operator_audit")
