"""Unit-of-work protocols implemented by M1.4-C persistence adapters."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from conversation_agent.database.repository import (
    ExecutionRepository,
    IdempotencyRepository,
)

__all__ = ["ExecutionUnitOfWork", "IdempotentExecutionUnitOfWork", "UnitOfWork"]


@runtime_checkable
class ExecutionUnitOfWork(Protocol):
    """Async context-managed short transaction for execution persistence."""

    @property
    def execution_repository(self) -> ExecutionRepository: ...

    async def __aenter__(self) -> "ExecutionUnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


@runtime_checkable
class IdempotentExecutionUnitOfWork(ExecutionUnitOfWork, Protocol):
    """One short transaction sharing execution and idempotency repositories."""

    @property
    def idempotency_repository(self) -> IdempotencyRepository: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """Abstract unit-of-work boundary for a single business transaction.

    Implementations manage an AsyncSession lifecycle and provide
    explicit begin / commit / rollback hooks so that callers never
    need to reach into the session internals.
    """

    async def begin(self) -> AsyncSession:
        """Start a new transactional boundary, return the active session."""
        ...

    async def commit(self) -> None:
        """Commit the current transaction."""
        ...

    async def rollback(self) -> None:
        """Roll back the current transaction."""
        ...
