"""Secure installation identity, enrollment, rotation and configuration history.

Revision ID: 0002
Revises: 0001 (unchanged)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "enrollment_challenges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replace_existing", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= 5", name="challenge_attempts"
        ),
    )
    op.create_index(
        "ix_enrollment_challenges_device_id", "enrollment_challenges", ["device_id"]
    )
    op.create_index(
        "ix_enrollment_challenges_expires_at", "enrollment_challenges", ["expires_at"]
    )
    op.create_table(
        "device_installations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_version", sa.Integer()),
        sa.Column("reported_config_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "acknowledged_version IS NULL OR acknowledged_version > 0",
            name="ack_positive",
        ),
        sa.CheckConstraint("reported_config_version >= 0", name="reported_nonnegative"),
    )
    op.create_index(
        "ix_device_installations_device_id", "device_installations", ["device_id"]
    )
    op.create_index(
        "uq_device_installations_active_device",
        "device_installations",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
        sqlite_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "device_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "installation_id",
            sa.Uuid(),
            sa.ForeignKey("device_installations.id"),
            nullable=False,
        ),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_request_id", sa.Uuid()),
        sa.Column("successor_id", sa.Uuid()),
    )
    for column in ("installation_id", "device_id", "expires_at"):
        op.create_index(
            f"ix_device_credentials_{column}", "device_credentials", [column]
        )
    op.create_index(
        "uq_device_credentials_active_installation",
        "device_credentials",
        ["installation_id"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL AND revoked_at IS NULL"),
        sqlite_where=sa.text("consumed_at IS NULL AND revoked_at IS NULL"),
    )
    op.create_table(
        "device_configurations",
        sa.Column(
            "device_id", sa.Uuid(), sa.ForeignKey("devices.id"), primary_key=True
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.CheckConstraint("version > 0", name="version_positive"),
        sa.CheckConstraint("schema_version = 1", name="supported_schema"),
    )
    # Existing S1 records gain version 1 without changing any identity/status data.
    configurations = sa.table(
        "device_configurations",
        sa.column("device_id", sa.Uuid()),
        sa.column("version", sa.Integer()),
        sa.column("schema_version", sa.Integer()),
        sa.column("settings", sa.JSON()),
    )
    devices = sa.table("devices", sa.column("id", sa.Uuid()))
    op.execute(
        configurations.insert().from_select(
            ["device_id", "version", "schema_version", "settings"],
            sa.select(
                devices.c.id,
                sa.literal(1),
                sa.literal(1),
                sa.literal_column("'{}'", type_=sa.JSON()),
            ),
        )
    )
    op.create_table(
        "configuration_deliveries",
        sa.Column(
            "installation_id",
            sa.Uuid(),
            sa.ForeignKey("device_installations.id"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id"), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id", "version"],
            ["device_configurations.device_id", "device_configurations.version"],
        ),
    )


def downgrade() -> None:
    op.drop_table("configuration_deliveries")
    op.drop_table("device_configurations")
    op.drop_table("device_credentials")
    op.drop_table("device_installations")
    op.drop_table("enrollment_challenges")
