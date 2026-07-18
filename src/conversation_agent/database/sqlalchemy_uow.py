"""SQLAlchemy short-transaction unit of work for execution persistence."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conversation_agent.database.errors import PersistenceWriteError
from conversation_agent.database.sqlalchemy_repository import (
    SQLAlchemyExecutionRepository,
)
from conversation_agent.database.sqlalchemy_idempotency_repository import (
    SQLAlchemyIdempotencyRepository,
)


class SQLAlchemyExecutionUnitOfWork:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._repository: SQLAlchemyExecutionRepository | None = None
        self._entered = False
        self._finished = False
        self._closed = False

    @property
    def execution_repository(self) -> SQLAlchemyExecutionRepository:
        if self._repository is None or not self._entered or self._closed:
            raise RuntimeError("ExecutionUnitOfWork is not active")
        return self._repository

    async def __aenter__(self) -> "SQLAlchemyExecutionUnitOfWork":
        if self._entered:
            raise RuntimeError("ExecutionUnitOfWork cannot be entered twice")
        self._entered = True
        self._session = self._session_factory()
        self._repository = SQLAlchemyExecutionRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_type, traceback
        cleanup_error: Exception | None = None
        if self._session is not None and not self._closed:
            if exc is not None or not self._finished:
                try:
                    await self._session.rollback()
                except Exception as rollback_error:
                    cleanup_error = rollback_error
            try:
                await self._close()
            except Exception as close_error:
                cleanup_error = cleanup_error or close_error
        if cleanup_error is not None:
            if exc is not None:
                exc.add_note("Persistence cleanup also failed.")
                return False
            raise PersistenceWriteError(
                "The persistence transaction could not be cleaned up."
            ) from cleanup_error
        return False

    async def commit(self) -> None:
        session = self._require_open_session()
        if self._finished:
            raise RuntimeError("ExecutionUnitOfWork transaction is already finished")
        try:
            await session.commit()
        except Exception as exc:
            cleanup_failed = False
            try:
                await session.rollback()
            except Exception:
                cleanup_failed = True
            self._finished = True
            try:
                await self._close()
            except Exception:
                cleanup_failed = True
            error = PersistenceWriteError(
                "The persistence transaction could not be committed."
            )
            if cleanup_failed:
                error.add_note("Persistence cleanup also failed.")
            raise error from exc
        self._finished = True

    async def rollback(self) -> None:
        session = self._require_open_session()
        if self._finished:
            raise RuntimeError("ExecutionUnitOfWork transaction is already finished")
        try:
            await session.rollback()
        except Exception as exc:
            self._finished = True
            try:
                await self._close()
            except Exception:
                pass
            raise PersistenceWriteError(
                "The persistence transaction could not be rolled back."
            ) from exc
        self._finished = True

    def _require_open_session(self) -> AsyncSession:
        if self._session is None or not self._entered or self._closed:
            raise RuntimeError("ExecutionUnitOfWork is not active")
        return self._session

    async def _close(self) -> None:
        if self._session is not None and not self._closed:
            await self._session.close()
        self._closed = True


class SQLAlchemyIdempotentExecutionUnitOfWork(SQLAlchemyExecutionUnitOfWork):
    """One short transaction sharing execution and idempotency repositories."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        super().__init__(session_factory)
        self._idempotency_repository: SQLAlchemyIdempotencyRepository | None = None

    @property
    def idempotency_repository(self) -> SQLAlchemyIdempotencyRepository:
        if (
            self._idempotency_repository is None
            or not self._entered
            or self._closed
        ):
            raise RuntimeError("IdempotentExecutionUnitOfWork is not active")
        return self._idempotency_repository

    async def __aenter__(self) -> "SQLAlchemyIdempotentExecutionUnitOfWork":
        await super().__aenter__()
        assert self._session is not None
        self._idempotency_repository = SQLAlchemyIdempotencyRepository(
            self._session
        )
        return self
