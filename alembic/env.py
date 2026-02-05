"""Alembic environment configuration for async SQLAlchemy.

Important:
- The application reads its DB URL from env (e.g. AGENT_DATABASE_URL).
- Alembic defaults to the URL in alembic.ini.

If these diverge (common in containers where the DB lives on a mounted volume),
migrations may apply to the wrong database file.

To prevent schema drift, we allow overriding the Alembic URL with environment
variables.
"""

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import models for autogenerate support
# Import Base and all models so metadata is populated
from app.database import Base
from app.models.orm import (  # noqa: F401
    CronLockModel,
    CronTaskModel,
    LogModel,
    LongMemoryModel,
    SkillMetadataModel,
    TaskModel,
    UserModel,
)

target_metadata = Base.metadata


def _normalize_async_db_url(url: str) -> str:
    """Normalize a DB URL for SQLAlchemy async engines.

    We accept the application's DB URL formats and coerce common sync URLs
    into async-driver variants used by this project's async Alembic env.
    """

    u = (url or "").strip()
    if not u:
        return u

    # SQLite: ensure aiosqlite.
    if u.startswith("sqlite:///") and "aiosqlite" not in u:
        return u.replace("sqlite:///", "sqlite+aiosqlite:///")

    # Postgres: ensure asyncpg.
    if u.startswith("postgresql://") and "+asyncpg" not in u:
        return u.replace("postgresql://", "postgresql+asyncpg://")

    return u


def _maybe_override_alembic_url_from_env() -> None:
    """Override alembic.ini sqlalchemy.url from env when configured."""

    raw = (
        os.environ.get("AGENT_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("SQLALCHEMY_URL")
    )
    if not raw:
        return

    url = _normalize_async_db_url(raw)
    if url:
        config.set_main_option("sqlalchemy.url", url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    _maybe_override_alembic_url_from_env()
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with the given connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    _maybe_override_alembic_url_from_env()
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
