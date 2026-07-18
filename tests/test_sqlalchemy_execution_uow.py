from __future__ import annotations

import pytest

from conversation_agent.database.errors import PersistenceWriteError
from conversation_agent.database.sqlalchemy_uow import SQLAlchemyExecutionUnitOfWork

pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(
        self,
        *,
        commit_error: Exception | None = None,
        rollback_error: Exception | None = None,
    ) -> None:
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    async def commit(self):
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self):
        self.rollback_calls += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    async def close(self):
        self.close_calls += 1


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_closes_and_cannot_repeat():
    session = FakeSession(commit_error=RuntimeError("sql and password secret"))
    uow = SQLAlchemyExecutionUnitOfWork(lambda: session)  # type: ignore[arg-type]
    with pytest.raises(PersistenceWriteError) as exc_info:
        async with uow:
            await uow.commit()
    assert session.commit_calls == 1
    assert session.rollback_calls == 1
    assert session.close_calls == 1
    assert "secret" not in str(exc_info.value)
    with pytest.raises(RuntimeError):
        await uow.commit()


@pytest.mark.asyncio
async def test_commit_error_remains_cause_when_rollback_also_fails():
    commit_error = RuntimeError("commit internals")
    session = FakeSession(
        commit_error=commit_error,
        rollback_error=RuntimeError("rollback internals"),
    )
    uow = SQLAlchemyExecutionUnitOfWork(lambda: session)  # type: ignore[arg-type]
    with pytest.raises(PersistenceWriteError) as exc_info:
        async with uow:
            await uow.commit()
    assert exc_info.value.__cause__ is commit_error
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_business_error_survives_rollback_cleanup_failure():
    session = FakeSession(rollback_error=RuntimeError("rollback internals"))
    uow = SQLAlchemyExecutionUnitOfWork(lambda: session)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="business failure") as exc_info:
        async with uow:
            raise ValueError("business failure")
    assert "Persistence cleanup also failed." in getattr(
        exc_info.value, "__notes__", []
    )
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_uncommitted_exit_rolls_back_and_closes():
    session = FakeSession()
    uow = SQLAlchemyExecutionUnitOfWork(lambda: session)  # type: ignore[arg-type]
    async with uow:
        assert uow.execution_repository is not None
    assert session.rollback_calls == 1
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_business_exception_is_not_swallowed():
    session = FakeSession()
    uow = SQLAlchemyExecutionUnitOfWork(lambda: session)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="business failure"):
        async with uow:
            raise ValueError("business failure")
    assert session.rollback_calls == 1
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_uow_cannot_be_reentered_or_double_committed():
    session = FakeSession()
    uow = SQLAlchemyExecutionUnitOfWork(lambda: session)  # type: ignore[arg-type]
    async with uow:
        with pytest.raises(RuntimeError):
            await uow.__aenter__()
        await uow.commit()
        with pytest.raises(RuntimeError):
            await uow.commit()
