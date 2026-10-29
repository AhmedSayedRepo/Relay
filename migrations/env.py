"""Alembic uses configured credentials directly, never ConfigParser interpolation."""

import asyncio

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from relay.infrastructure import (
    agent_models,  # noqa: F401
    binary_models,  # noqa: F401
    event_models,  # noqa: F401
    models,  # noqa: F401
    operation_models,  # noqa: F401
    processing_models,  # noqa: F401
    recovery_models,  # noqa: F401
    sms_verification_models,  # noqa: F401
    training_models,  # noqa: F401
)
from relay.infrastructure.config import load_settings
from relay.infrastructure.database import Base

target_metadata = Base.metadata


def run_offline() -> None:
    settings = load_settings()
    context.configure(
        url=settings.database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def migrate(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    settings = load_settings()
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        poolclass=pool.NullPool,
        echo=False,
        hide_parameters=True,
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
