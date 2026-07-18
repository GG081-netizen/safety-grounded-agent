"""DatabaseEngine — SQLAlchemy 2 async engine lifecycle and session factory.

Design invariants:
- No connection is established at module import time.
- async_sessionmaker is created in start(), not in __init__.
- stop() / dispose() is idempotent.
- A configured URL that fails to connect raises an exception (no silent fallback).
- Null persistence (M1.4-A) never creates a network connection.
- M1.4-E FastAPI lifespan owns the engine for PostgreSQL mode.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import stat
from pathlib import Path
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from conversation_agent.config import DatabaseConfig, DatabaseTlsMode
from conversation_agent.database.errors import (
    DatabaseRevisionError,
    PersistenceConnectionError,
)

logger = logging.getLogger(__name__)


class DatabaseEngine:
    """Own the SQLAlchemy async engine and session factory lifecycle.

    Usage::

        engine = DatabaseEngine(config)
        await engine.start()          # creates async engine + sessionmaker
        async with engine.session() as session:
            ...
        await engine.stop()           # disposes engine (idempotent)
    """

    def __init__(self, config: DatabaseConfig) -> None:
        if not config.url_value:
            raise ValueError("DatabaseConfig.url must be a non-empty string")
        self._config = config
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    # ── properties ──────────────────────────────────────────────────────

    @property
    def engine(self) -> AsyncEngine:
        """Return the underlying AsyncEngine (raises if not started)."""
        if self._engine is None:
            raise RuntimeError("DatabaseEngine has not been started")
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Return the async_sessionmaker (raises if not started)."""
        if self._session_factory is None:
            raise RuntimeError("DatabaseEngine has not been started")
        return self._session_factory

    @property
    def is_started(self) -> bool:
        return self._engine is not None and self._session_factory is not None

    # ── lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """Create the async engine and session factory.

        Must be called before any session is requested.  Safe to call
        multiple times — subsequent calls are no-ops.
        """
        if self._engine is not None:
            return

        cfg = self._config
        try:
            ssl_context = self._ssl_context(cfg)
        except Exception as exc:
            raise PersistenceConnectionError(
                "Database TLS configuration is invalid."
            ) from exc
        connect_args = {
            "timeout": cfg.connect_timeout_seconds,
            "server_settings": {
                "statement_timeout": str(cfg.statement_timeout_ms),
                "lock_timeout": str(cfg.lock_timeout_ms),
                "idle_in_transaction_session_timeout": str(
                    cfg.idle_in_transaction_session_timeout_ms
                ),
                "search_path": f'"{cfg.schema_name}", pg_catalog',
            },
            "ssl": ssl_context,
        }
        self._engine = create_async_engine(
            cfg.url_value,
            pool_size=cfg.pool_size,
            max_overflow=cfg.max_overflow,
            pool_timeout=cfg.pool_timeout_seconds,
            pool_recycle=cfg.pool_recycle_seconds,
            echo=cfg.echo,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.info("DatabaseEngine started (pool_size=%d)", cfg.pool_size)

    async def stop(self) -> None:
        """Dispose the engine and clear the session factory.

        Idempotent — safe to call on a stopped or never-started engine.
        """
        if self._engine is not None:
            engine = self._engine
            self._engine = None
            self._session_factory = None
            try:
                await asyncio.wait_for(
                    engine.dispose(),
                    timeout=self._config.graceful_shutdown_timeout_seconds,
                )
            finally:
                logger.info("DatabaseEngine stopped")

    async def check_connectivity(self) -> None:
        """Verify a live connection without exposing connection details."""
        try:
            async with asyncio.timeout(self._config.readiness_timeout_seconds):
                async with self.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
        except Exception as exc:
            raise PersistenceConnectionError(
                "Database connectivity validation failed."
            ) from exc

    async def current_revision(self) -> str:
        try:
            async with self.engine.connect() as connection:
                value = await connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
        except Exception as exc:
            raise PersistenceConnectionError(
                "Database revision validation failed."
            ) from exc
        if not isinstance(value, str) or not value:
            raise DatabaseRevisionError("Database revision is unavailable.")
        return value

    @staticmethod
    def _ssl_context(config: DatabaseConfig) -> ssl.SSLContext | bool:
        if config.tls_mode is DatabaseTlsMode.DISABLE:
            return False
        if config.tls_mode is DatabaseTlsMode.REQUIRE:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        else:
            context = ssl.create_default_context(cafile=config.tls_ca_file)
            context.check_hostname = config.tls_mode is DatabaseTlsMode.VERIFY_FULL
            context.verify_mode = ssl.CERT_REQUIRED
        if config.tls_client_cert_file:
            key_mode = stat.S_IMODE(Path(config.tls_client_key_file).stat().st_mode)
            if key_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise ValueError("database client key permissions are too broad")
            context.load_cert_chain(
                config.tls_client_cert_file,
                config.tls_client_key_file,
            )
        return context

    async def check_revision(self, expected_revision: str) -> None:
        if await self.current_revision() != expected_revision:
            raise DatabaseRevisionError(
                "Database revision does not match the application contract."
            )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield an AsyncSession from the session factory.

        The caller is responsible for committing / rolling back and
        closing the session.  This context manager only ensures the
        session is obtained from the current factory.
        """
        if self._session_factory is None:
            raise RuntimeError("DatabaseEngine has not been started")
        async with self._session_factory() as session:
            yield session
