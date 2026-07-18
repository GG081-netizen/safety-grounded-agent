"""Alembic async migration environment for conversation_agent.

URL resolution (first wins):
  1. -x database_url=<url>  CLI option
  2. sqlalchemy.url in alembic.ini (only if non-empty)
  3. CONVAGENT_DATABASE_URL env var
  4. Fail with clear error — no silent fallback

Design:
- Does NOT import AppConfig (no circular dependency).
- Online mode uses NullPool (migrations are single-connection).
- Does NOT auto-execute at import time.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from conversation_agent.database.models import Base

# ── Alembic Config object ───────────────────────────────────────────────

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


# ── URL resolution ──────────────────────────────────────────────────────

def _resolve_database_url() -> str:
    """Resolve database URL from CLI -x, ini, or env var.  Fail if none."""
    # 1. -x database_url=... (highest priority)
    x_args = context.get_x_argument(as_dictionary=True)
    if "database_url" in x_args:
        return x_args["database_url"]

    # 2. sqlalchemy.url from alembic.ini (only if non-empty)
    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url and ini_url.strip():
        return ini_url.strip()

    # 3. CONVAGENT_DATABASE_URL env var
    env_url = os.getenv("CONVAGENT_DATABASE_URL", "").strip()
    if env_url:
        return env_url

    # 4. Nothing available
    raise RuntimeError(
        "No database URL available.  Provide one via:\n"
        "  -x database_url=<url>  (recommended)\n"
        "  CONVAGENT_DATABASE_URL  environment variable\n"
        "  sqlalchemy.url in alembic.ini"
    )


# ── Migration runners ───────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without connecting)."""
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Synchronous inner runner for online mode."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to DB, execute DDL)."""
    url = _resolve_database_url()
    connectable = create_async_engine(url, poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


# ── Entry point ─────────────────────────────────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    import asyncio

    asyncio.run(run_migrations_online())
