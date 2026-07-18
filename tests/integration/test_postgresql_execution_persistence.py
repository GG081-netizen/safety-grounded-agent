"""M1.4-C execution persistence tests against real PostgreSQL."""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import anyio
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from conversation_agent.application.durable_service import DurableApplicationService
from conversation_agent.application.models import UserRequest
from conversation_agent.application.persistence_mappers import RequestPersistenceMapper
from conversation_agent.application.service import ChatService
from conversation_agent.authorization.models import AuthorizationDecision, ResourceScope
from conversation_agent.database.errors import (
    DuplicateRequestError,
    DurableApplicationExecutionError,
    InvalidRequestTransitionError,
    PersistenceConflictError,
    PersistenceFinalizationError,
    RequestInitializationError,
)
from conversation_agent.database.models import AgentRequest, AgentRun, AuditEvent
from conversation_agent.database.records import NewAgentRun, NewAuditEvent
from conversation_agent.database.sqlalchemy_uow import SQLAlchemyExecutionUnitOfWork
from conversation_agent.identity.models import Principal
from conversation_agent.orchestration.models import OrchestrationResult, TaskRoute
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.runtime.builder import RequestContextBuilder
from conversation_agent.runtime.models import RequestContext, RuntimeVersionSnapshot

pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
]

NOW = datetime(2026, 7, 16, 3, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def execution_postgres_url() -> str:
    url = os.getenv("CONVAGENT_POSTGRES_TEST_URL", "").strip()
    if not url:
        pytest.skip("CONVAGENT_POSTGRES_TEST_URL not set")
    app_url = os.getenv("CONVAGENT_DATABASE_URL", "").strip()
    if app_url and app_url == url:
        pytest.skip("Refusing to use the application database")
    return url


@pytest.fixture(scope="session")
def execution_alembic_cfg(execution_postgres_url: str) -> AlembicConfig:
    config = AlembicConfig("alembic.ini")
    config.set_main_option("sqlalchemy.url", execution_postgres_url)
    return config


@pytest.fixture(scope="session")
def execution_schema(execution_alembic_cfg: AlembicConfig) -> None:
    command.upgrade(execution_alembic_cfg, "head")


@pytest_asyncio.fixture
async def execution_engine(
    execution_postgres_url: str,
    execution_schema: None,
) -> AsyncIterator[AsyncEngine]:
    del execution_schema
    engine = create_async_engine(execution_postgres_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def execution_case(
    execution_engine: AsyncEngine,
) -> AsyncIterator[tuple[str, async_sessionmaker[AsyncSession]]]:
    prefix = f"m14c-{uuid.uuid4()}"
    factory = async_sessionmaker(execution_engine, expire_on_commit=False)
    yield prefix, factory
    async with factory() as session:
        await session.execute(
            delete(AuditEvent).where(
                (AuditEvent.event_id.like(f"{prefix}%"))
                | (AuditEvent.request_id.like(f"{prefix}%"))
            )
        )
        await session.execute(
            delete(AgentRun).where(AgentRun.run_id.like(f"{prefix}%"))
        )
        await session.execute(
            delete(AgentRequest).where(AgentRequest.request_id.like(f"{prefix}%"))
        )
        await session.commit()


def _versions() -> RuntimeVersionSnapshot:
    return RuntimeVersionSnapshot(
        model_registry_version="models-v1",
        model_routing_policy_version="not_implemented",
        application_version="0.1.0",
        policy_version="policy-v1",
        rag_contract_version="rag-v1",
        crm_connector_version="not_configured",
        authorization_policy_version="authz-v1",
    )


def _context(prefix: str) -> RequestContext:
    principal = Principal(
        tenant_id="tenant-test",
        organization_id="org-test",
        user_id="user-test",
        email="must-not-persist@example.com",
        roles=("agent_user",),
    )
    authorization = AuthorizationDecision(
        allowed=True,
        code="allowed",
        permissions=("chat:invoke", "rag:read", "crm:read"),
        resource_scopes=(
            ResourceScope(
                tenant_id="tenant-test",
                organization_id="org-test",
                resource_type="organization",
                scope_type="organization",
            ),
        ),
    )
    return RequestContext(
        request_id=f"{prefix}-request",
        trace_id=f"{prefix}-trace",
        session_id=f"{prefix}-session",
        principal=principal,
        authorization=authorization,
        versions=_versions(),
        received_at=NOW,
    )


def _request_record(prefix: str, text: str = "敏感客户文本🙂"):
    return RequestPersistenceMapper().map(
        context=_context(prefix),
        operation="POST:/v1/chat",
        user_text=text,
        task_override="qa",
        created_at=NOW,
    )


def _audit(prefix: str, suffix: str, event_type: str = "request_accepted"):
    context = _context(prefix)
    return NewAuditEvent(
        event_id=f"{prefix}-{suffix}",
        request_id=context.request_id,
        trace_id=context.trace_id,
        tenant_id=context.principal.tenant_id,
        organization_id=context.principal.organization_id,
        event_type=event_type,
        principal_user_id=context.principal.user_id,
        outcome="accepted",
        details_json={"audit_payload_version": 1},
        created_at=NOW,
    )


def _run(prefix: str, request_record_id: int, suffix: str = "run"):
    return NewAgentRun(
        run_id=f"{prefix}-{suffix}",
        original_request_record_id=request_record_id,
        session_id=f"{prefix}-session",
        status="completed",
        routed_task="qa",
        policy_outcome="SAFE",
        result_snapshot={"outcome": "completed"},
        result_snapshot_schema_version=1,
        confidence=0.8,
        trace_snapshot={"stage_names": ("policy_engine",)},
        trace_snapshot_schema_version=1,
        rag_provider=None,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )


def _uow_factory(factory: async_sessionmaker[AsyncSession]):
    return lambda: SQLAlchemyExecutionUnitOfWork(factory)


class StubCoordinator:
    def __init__(
        self,
        *,
        blocked: bool = False,
        raises: bool = False,
        started: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.blocked = blocked
        self.raises = raises
        self.started = started
        self.release = release
        self.call_count = 0

    def run(self, user_input, session_id=None, task_override=None, *, request_metadata=None):
        del request_metadata
        self.call_count += 1
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            assert self.release.wait(timeout=5)
        if self.raises:
            raise RuntimeError("provider password and stack must not persist")
        return OrchestrationResult(
            session_id=session_id,
            user_input=user_input,
            policy=PolicyDecision(
                status="BLOCKED" if self.blocked else "SAFE",
                matched_rules=["rule-block"] if self.blocked else [],
            ),
            task_route=None if self.blocked else TaskRoute(task="qa"),
            final_response="blocked" if self.blocked else "complete answer",
            confidence=0.8,
        )


def _durable(
    prefix: str,
    factory: async_sessionmaker[AsyncSession],
    coordinator: StubCoordinator,
    *,
    event_ids: list[str] | None = None,
) -> DurableApplicationService:
    builder = RequestContextBuilder(versions=_versions())
    chat = ChatService(coordinator=coordinator, context_builder=builder)  # type: ignore[arg-type]
    times = iter(NOW + timedelta(seconds=index) for index in range(20))
    events = iter(
        event_ids
        or [f"{prefix}-event-{index}" for index in range(20)]
    )
    return DurableApplicationService(
        chat_service=chat,
        uow_factory=_uow_factory(factory),
        clock=lambda: next(times),
        run_id_factory=lambda: f"{prefix}-run",
        event_id_factory=lambda: next(events),
    )


async def test_repository_commit_makes_request_and_audit_visible(execution_case):
    prefix, factory = execution_case
    async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
        ref = await uow.execution_repository.create_request(_request_record(prefix))
        await uow.execution_repository.create_audit_event(_audit(prefix, "accepted"))
        await uow.commit()
    assert ref.database_id > 0
    async with factory() as session:
        request = await session.scalar(
            select(AgentRequest).where(AgentRequest.request_id == ref.request_id)
        )
        audit = await session.scalar(
            select(AuditEvent).where(AuditEvent.event_id == f"{prefix}-accepted")
        )
    assert request is not None and request.status == "in_progress"
    assert audit is not None and audit.event_type == "request_accepted"


async def test_uncommitted_uow_rolls_back(execution_case):
    prefix, factory = execution_case
    async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
        await uow.execution_repository.create_request(_request_record(prefix))
    async with factory() as session:
        assert await session.scalar(
            select(AgentRequest).where(AgentRequest.request_id == f"{prefix}-request")
        ) is None


async def test_request_audit_failure_is_atomic(execution_case):
    prefix, factory = execution_case
    duplicate = _audit(prefix, "duplicate")
    async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
        await uow.execution_repository.create_audit_event(duplicate)
        await uow.commit()
    with pytest.raises(PersistenceConflictError):
        async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
            await uow.execution_repository.create_request(_request_record(prefix))
            await uow.execution_repository.create_audit_event(duplicate)
            await uow.commit()
    async with factory() as session:
        assert await session.scalar(
            select(AgentRequest).where(AgentRequest.request_id == f"{prefix}-request")
        ) is None


async def test_duplicate_request_maps_safe_error(execution_case):
    prefix, factory = execution_case
    async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
        await uow.execution_repository.create_request(_request_record(prefix))
        await uow.commit()
    with pytest.raises(DuplicateRequestError) as exc_info:
        async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
            await uow.execution_repository.create_request(_request_record(prefix))
    assert prefix not in str(exc_info.value)


async def test_run_finalize_and_audit_commit_atomically(execution_case):
    prefix, factory = execution_case
    async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
        ref = await uow.execution_repository.create_request(_request_record(prefix))
        await uow.commit()
    async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
        locked = await uow.execution_repository.get_request_for_update(ref.request_id)
        await uow.execution_repository.create_run(_run(prefix, ref.database_id))
        await uow.execution_repository.finalize_request_completed(
            locked, completed_at=NOW + timedelta(seconds=1)
        )
        await uow.execution_repository.create_audit_event(
            _audit(prefix, "completed", "request_completed")
        )
        await uow.commit()
    async with factory() as session:
        request = await session.scalar(
            select(AgentRequest).where(AgentRequest.request_id == ref.request_id)
        )
        run = await session.scalar(
            select(AgentRun).where(AgentRun.run_id == f"{prefix}-run")
        )
    assert request is not None and request.status == "completed"
    assert run is not None and run.original_request_id == ref.database_id


async def test_finalization_audit_failure_rolls_back_run_and_status(execution_case):
    prefix, factory = execution_case
    duplicate = _audit(prefix, "duplicate")
    async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
        ref = await uow.execution_repository.create_request(_request_record(prefix))
        await uow.execution_repository.create_audit_event(duplicate)
        await uow.commit()
    with pytest.raises(PersistenceConflictError):
        async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
            locked = await uow.execution_repository.get_request_for_update(ref.request_id)
            await uow.execution_repository.create_run(_run(prefix, ref.database_id))
            await uow.execution_repository.finalize_request_completed(
                locked, completed_at=NOW + timedelta(seconds=1)
            )
            await uow.execution_repository.create_audit_event(duplicate)
            await uow.commit()
    async with factory() as session:
        request = await session.scalar(
            select(AgentRequest).where(AgentRequest.request_id == ref.request_id)
        )
        run = await session.scalar(
            select(AgentRun).where(AgentRun.run_id == f"{prefix}-run")
        )
    assert request is not None and request.status == "in_progress"
    assert run is None


async def test_completed_request_cannot_be_finalized_again(execution_case):
    prefix, factory = execution_case
    async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
        ref = await uow.execution_repository.create_request(_request_record(prefix))
        await uow.commit()
    async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
        locked = await uow.execution_repository.get_request_for_update(ref.request_id)
        await uow.execution_repository.finalize_request_completed(
            locked, completed_at=NOW
        )
        await uow.commit()
    with pytest.raises(InvalidRequestTransitionError):
        async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
            await uow.execution_repository.get_request_for_update(ref.request_id)


async def test_agent_run_one_to_one_constraint_through_repository(execution_case):
    prefix, factory = execution_case
    async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
        ref = await uow.execution_repository.create_request(_request_record(prefix))
        await uow.commit()
    async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
        await uow.execution_repository.create_run(_run(prefix, ref.database_id, "run-a"))
        await uow.commit()
    with pytest.raises(PersistenceConflictError):
        async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
            await uow.execution_repository.create_run(
                _run(prefix, ref.database_id, "run-b")
            )


@pytest.mark.parametrize(
    ("blocked", "expected_run", "expected_event"),
    [(False, "completed", "request_completed"), (True, "blocked", "policy_blocked")],
)
async def test_durable_completed_and_blocked_paths(
    execution_case, blocked, expected_run, expected_event
):
    prefix, factory = execution_case
    coordinator = StubCoordinator(blocked=blocked)
    result = await _durable(prefix, factory, coordinator).execute(
        UserRequest(text="customer question"),
        context=_context(prefix),
        operation="POST:/v1/chat",
    )
    assert result.orchestration.policy.is_blocked is blocked
    async with factory() as session:
        request = await session.scalar(
            select(AgentRequest).where(AgentRequest.request_id == f"{prefix}-request")
        )
        run = await session.scalar(
            select(AgentRun).where(AgentRun.run_id == f"{prefix}-run")
        )
        audits = list(
            (
                await session.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.request_id == f"{prefix}-request")
                    .order_by(AuditEvent.id)
                )
            ).all()
        )
    assert request is not None and request.status == "completed"
    assert run is not None and run.status == expected_run
    assert [item.event_type for item in audits] == ["request_accepted", expected_event]


async def test_durable_failed_path_persists_safe_failure(execution_case):
    prefix, factory = execution_case
    coordinator = StubCoordinator(raises=True)
    with pytest.raises(DurableApplicationExecutionError):
        await _durable(prefix, factory, coordinator).execute(
            UserRequest(text="customer question"),
            context=_context(prefix),
            operation="POST:/v1/chat",
        )
    async with factory() as session:
        request = await session.scalar(
            select(AgentRequest).where(AgentRequest.request_id == f"{prefix}-request")
        )
        run = await session.scalar(
            select(AgentRun).where(AgentRun.run_id == f"{prefix}-run")
        )
    assert request is not None and request.status == "failed"
    assert request.failure_code == "application_service_failed"
    assert run is not None and run.status == "failed"
    assert "provider password" not in repr(run.trace_snapshot)


async def test_failed_request_cannot_be_finalized_again(execution_case):
    prefix, factory = execution_case
    with pytest.raises(DurableApplicationExecutionError):
        await _durable(prefix, factory, StubCoordinator(raises=True)).execute(
            UserRequest(text="customer question"),
            context=_context(prefix),
            operation="POST:/v1/chat",
        )
    with pytest.raises(InvalidRequestTransitionError):
        async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
            await uow.execution_repository.get_request_for_update(
                f"{prefix}-request"
            )


async def test_persisted_snapshots_exclude_sensitive_content(execution_case):
    prefix, factory = execution_case
    user_text = "TOP秘密🙂"
    await _durable(prefix, factory, StubCoordinator()).execute(
        UserRequest(text=user_text),
        context=_context(prefix),
        operation="POST:/v1/chat",
    )
    async with factory() as session:
        request = await session.scalar(
            select(AgentRequest).where(AgentRequest.request_id == f"{prefix}-request")
        )
        run = await session.scalar(
            select(AgentRun).where(AgentRun.run_id == f"{prefix}-run")
        )
        audits = list(
            (
                await session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.request_id == f"{prefix}-request"
                    )
                )
            ).all()
        )
    assert request is not None and run is not None
    assert request.user_text_length == len(user_text)
    persisted = repr(
        (
            request.authorization_snapshot,
            run.result_snapshot,
            run.trace_snapshot,
            [item.details_json for item in audits],
        )
    ).lower()
    for forbidden in (
        user_text.lower(),
        "must-not-persist@example.com",
        "complete answer",
        "raw_response",
        "debug.rag_raw_response",
        "prompt",
        "claims",
        "jwt",
        "jwks",
    ):
        assert forbidden not in persisted


async def test_durable_transaction_a_failure_prevents_coordinator(execution_case):
    prefix, factory = execution_case
    duplicate_event = f"{prefix}-duplicate"
    async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
        await uow.execution_repository.create_audit_event(
            _audit(prefix, "duplicate")
        )
        await uow.commit()
    coordinator = StubCoordinator()
    service = _durable(
        prefix,
        factory,
        coordinator,
        event_ids=[duplicate_event],
    )
    with pytest.raises(RequestInitializationError):
        await service.execute(
            UserRequest(text="customer question"),
            context=_context(prefix),
            operation="POST:/v1/chat",
        )
    assert coordinator.call_count == 0


async def test_durable_transaction_b_failure_returns_no_success(execution_case):
    prefix, factory = execution_case
    duplicate_event = f"{prefix}-duplicate"
    async with SQLAlchemyExecutionUnitOfWork(factory) as uow:
        await uow.execution_repository.create_audit_event(
            _audit(prefix, "duplicate")
        )
        await uow.commit()
    coordinator = StubCoordinator()
    service = _durable(
        prefix,
        factory,
        coordinator,
        event_ids=[f"{prefix}-accepted", duplicate_event],
    )
    with pytest.raises(PersistenceFinalizationError):
        await service.execute(
            UserRequest(text="customer question"),
            context=_context(prefix),
            operation="POST:/v1/chat",
        )
    async with factory() as session:
        request = await session.scalar(
            select(AgentRequest).where(AgentRequest.request_id == f"{prefix}-request")
        )
        run = await session.scalar(
            select(AgentRun).where(AgentRun.run_id == f"{prefix}-run")
        )
    assert request is not None and request.status == "in_progress"
    assert run is None


async def test_transaction_a_visible_while_coordinator_is_blocked(execution_case):
    prefix, factory = execution_case
    started = threading.Event()
    release = threading.Event()
    coordinator = StubCoordinator(started=started, release=release)
    service = _durable(prefix, factory, coordinator)
    task = asyncio.create_task(
        service.execute(
            UserRequest(text="customer question"),
            context=_context(prefix),
            operation="POST:/v1/chat",
        )
    )
    assert await anyio.to_thread.run_sync(started.wait, 5)
    async with factory() as session:
        request = await session.scalar(
            select(AgentRequest).where(AgentRequest.request_id == f"{prefix}-request")
        )
        run = await session.scalar(
            select(AgentRun).where(AgentRun.run_id == f"{prefix}-run")
        )
        accepted = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.request_id == f"{prefix}-request",
                AuditEvent.event_type == "request_accepted",
            )
        )
    assert request is not None and request.status == "in_progress"
    assert accepted is not None
    assert run is None
    release.set()
    await task


async def test_runtime_sql_never_references_idempotency_records(execution_case, execution_engine):
    prefix, factory = execution_case
    statements: list[str] = []

    def record_statement(conn, cursor, statement, parameters, context, executemany):
        del conn, cursor, parameters, context, executemany
        statements.append(statement)

    event.listen(execution_engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        await _durable(prefix, factory, StubCoordinator()).execute(
            UserRequest(text="customer question"),
            context=_context(prefix),
            operation="POST:/v1/chat",
        )
    finally:
        event.remove(
            execution_engine.sync_engine, "before_cursor_execute", record_statement
        )
    assert statements
    assert all("idempotency_records" not in statement.lower() for statement in statements)
