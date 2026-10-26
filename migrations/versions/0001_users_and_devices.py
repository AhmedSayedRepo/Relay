"""Initial administration users and generic devices.

Revision ID: 0001
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("external_device_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("app_version", sa.String(64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "DISABLED",
                "PENDING",
                name="device_status",
                native_enum=False,
                create_constraint=False,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_devices"),
        sa.UniqueConstraint("external_device_id", name="uq_devices_external_device_id"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED', 'PENDING')",
            name=op.f("ck_devices_device_status"),
        ),
    )
    op.create_index("ix_devices_created_at", "devices", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_devices_created_at", table_name="devices")
    op.drop_table("devices")
    op.drop_table("users")
