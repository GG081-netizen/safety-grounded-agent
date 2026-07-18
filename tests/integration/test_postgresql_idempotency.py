"""M1.4-D persistent idempotency tests against real PostgreSQL 17."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from conversation_agent.application.idempotent_durable_service import (
    IdempotentDurableApplicationService,
)
from conversation_agent.application.models import UserRequest
from conversation_agent.application.persistence_mappers import RequestPersistenceMapper
from conversation_agent.application.service import ApplicationExecutionError, ApplicationResult
from conversation_agent.authorization.models import AuthorizationDecision, ResourceScope
from conversation_agent.database.errors import (
    DurableApplicationExecutionError,
    FingerprintVersionError,
    IdempotencyOwnershipLostError,
    InvalidIdempotencyStateError,
    PersistenceFinalizationError,
)
from conversation_agent.database.idempotency import scope_from_values
from conversation_agent.database.models import (
    AgentRequest,
    AgentRun,
    AuditEvent,
    IdempotencyRecord,
)
from conversation_agent.database.records import (
    ClaimOutcome,
    IdempotencyClaimRequest,
    IdempotencyPolicy,
    IdempotentResultOutcome,
)
from conversation_agent.database.sqlalchemy_idempotency_repository import (
    SQLAlchemyIdempotencyRepository,
)
from conversation_agent.database.sqlalchemy_repository import (
    SQLAlchemyExecutionRepository,
)
from conversation_agent.database.sqlalchemy_uow import (
    SQLAlchemyIdempotentExecutionUnitOfWork,
)
from conversation_agent.identity.models import Principal
from conversation_agent.orchestration.models import OrchestrationResult, TaskRoute
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.runtime.models import RequestContext, RuntimeVersionSnapshot


pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
]

BUSINESS_TIME = datetime(2026, 7, 16, 5, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def idempotency_postgres_url() -> str:
    url = os.getenv("CONVAGENT_POSTGRES_TEST_URL", "").strip()
    if not url:
        pytest.skip("CONVAGENT_POSTGRES_TEST_URL not set")
    app_url = os.getenv("CONVAGENT_DATABASE_URL", "").strip()
    if app_url and app_url == url:
        pytest.skip("Refusing to use the application database")
    return url


@pytest.fixture(scope="session")
def idempotency_alembic_cfg(idempotency_postgres_url: str) -> AlembicConfig:
    config = AlembicConfig("alembic.ini")
    config.set_main_option("sqlalchemy.url", idempotency_postgres_url)
    return config


@pytest.fixture(scope="session")
def idempotency_schema(idempotency_alembic_cfg: AlembicConfig) -> None:
    command.upgrade(idempotency_alembic_cfg, "head")


@pytest_asyncio.fixture
async def idempotency_engine(
    idempotency_postgres_url: str,
    idempotency_schema: None,
) -> AsyncIterator[AsyncEngine]:
    del idempotency_schema
    engine = create_async_engine(idempotency_postgres_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def idempotency_case(
    idempotency_engine: AsyncEngine,
) -> AsyncIterator[tuple[str, async_sessionmaker[AsyncSession]]]:
    prefix = f"m14d-{uuid.uuid4()}"
    factory = async_sessionmaker(idempotency_engine, expire_on_commit=False)
    yield prefix, factory
    async with factory() as session:
        await session.execute(
            delete(AuditEvent).where(
                (AuditEvent.event_id.like(f"{prefix}%"))
                | (AuditEvent.request_id.like(f"{prefix}%"))
                | (AuditEvent.tenant_id == f"{prefix}-tenant")
            )
        )
        await session.execute(
            delete(AgentRun).where(AgentRun.run_id.like(f"{prefix}%"))
        )
        await session.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.tenant_id.like(f"{prefix}%")
            )
        )
        await session.execute(
            delete(AgentRequest).where(AgentRequest.request_id.like(f"{prefix}%"))
        )
        await session.commit()


def _context(prefix: str, suffix: str = "owner") -> RequestContext:
    principal = Principal(
        tenant_id=f"{prefix}-tenant",
        organization_id=f"{prefix}-org",
        user_id=f"{prefix}-user",
        roles=("agent_user",),
    )
    authorization = AuthorizationDecision(
        allowed=True,
        code="allowed",
        permissions=("chat:invoke", "rag:read", "crm:read"),
        resource_scopes=(
            ResourceScope(
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                resource_type="organization",
                scope_type="organization",
            ),
        ),
    )
    return RequestContext(
        request_id=f"{prefix}-{suffix}",
        trace_id=f"{prefix}-{suffix}-trace",
        session_id=f"{prefix}-{suffix}-session",
        principal=principal,
        authorization=authorization,
        versions=RuntimeVersionSnapshot(
            model_registry_version="models-v1",
            model_routing_policy_version="not_implemented",
            application_version="0.1.0",
            policy_version="policy-v1",
            rag_contract_version="rag-v1",
            crm_connector_version="not_configured",
            authorization_policy_version="authz-v1",
        ),
        received_at=BUSINESS_TIME,
    )


def _claim(prefix: str, *, suffix: str = "owner", text: str = "same"):
    context = _context(prefix, suffix)
    scope = scope_from_values(
        tenant_id=context.principal.tenant_id,
        organization_id=context.principal.organization_id,
        principal_user_id=context.principal.user_id,
        operation="chat",
        raw_key="raw-key-canary",
    )
    record = RequestPersistenceMapper().map(
        context=context,
        operation="chat",
        user_text=text,
        task_override=None,
        created_at=BUSINESS_TIME,
        idempotency_key_hash=scope.key_hash,
    )
    return context, record, IdempotencyClaimRequest(
        scope=scope,
        request_fingerprint=record.request_fingerprint,
        fingerprint_version=record.fingerprint_version,
        owner_request_id=context.request_id,
        lease_duration_seconds=60,
    )


class DatabaseChatService:
    def __init__(self, *, blocked: bool = False, raises: bool = False) -> None:
        self.blocked = blocked
        self.raises = raises
        self.call_count = 0

    def execute_with_context(self, request, *, context, forced_task=None):
        self.call_count += 1
        if self.raises:
            raise ApplicationExecutionError("provider secret")
        return ApplicationResult(
            context=context,
            orchestration=OrchestrationResult(
                session_id=context.session_id,
                user_input=request.text,
                policy=PolicyDecision(
                    status="BLOCKED" if self.blocked else "SAFE"
                ),
                task_route=None if self.blocked else TaskRoute(task="qa"),
                final_response="blocked" if self.blocked else "answer",
                confidence=0.8,
            ),
        )


class BlockingDatabaseChatService(DatabaseChatService):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def execute_with_context(self, request, *, context, forced_task=None):
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test execution was not released")
        return super().execute_with_context(
            request,
            context=context,
            forced_task=forced_task,
        )


def _service(factory, chat, prefix, *, policy=None):
    return IdempotentDurableApplicationService(
        chat_service=chat,
        uow_factory=lambda: SQLAlchemyIdempotentExecutionUnitOfWork(factory),
        policy=policy or IdempotencyPolicy(),
        clock=lambda: BUSINESS_TIME,
        run_id_factory=lambda: f"{prefix}-run-{uuid.uuid4()}",
        event_id_factory=lambda: f"{prefix}-event-{uuid.uuid4()}",
    )


@pytest.mark.asyncio
async def test_alembic_does_not_disable_application_loggers(idempotency_case):
    del idempotency_case
    assert not logging.getLogger(
        "conversation_agent.llm.dashscope_client"
    ).disabled


@pytest.mark.asyncio
async def test_atomic_claim_uses_database_time_and_scoped_unique(
    idempotency_case,
):
    prefix, factory = idempotency_case
    _, _, request = _claim(prefix)
    async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
        first = await uow.idempotency_repository.claim(request)
        await uow.commit()
    assert first.outcome is ClaimOutcome.ACQUIRED
    assert first.token is not None
    assert first.token.claimed_at.year != BUSINESS_TIME.year or (
        first.token.claimed_at - BUSINESS_TIME
    ).total_seconds() != 0
    assert first.token.lease_expires_at - first.token.claimed_at == timedelta(
        seconds=60
    )
    async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
        duplicate = await uow.idempotency_repository.claim(request)
        await uow.rollback()
    assert duplicate.outcome is ClaimOutcome.IN_PROGRESS
    async with factory() as session:
        count = (
            await session.execute(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.tenant_id == f"{prefix}-tenant"
                )
            )
        ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_claim_rollback_leaves_no_record(idempotency_case):
    prefix, factory = idempotency_case
    _, _, request = _claim(prefix)
    async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
        await uow.idempotency_repository.claim(request)
    async with factory() as session:
        count = (
            await session.execute(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.tenant_id == f"{prefix}-tenant"
                )
            )
        ).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_fingerprint_conflict_and_version_mismatch_fail_closed(
    idempotency_case,
):
    prefix, factory = idempotency_case
    _, _, first_request = _claim(prefix, text="one")
    async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
        await uow.idempotency_repository.claim(first_request)
        await uow.commit()
    _, _, conflict_request = _claim(prefix, suffix="other", text="two")
    async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
        conflict = await uow.idempotency_repository.claim(conflict_request)
        await uow.rollback()
    assert conflict.outcome is ClaimOutcome.CONFLICT
    version_request = IdempotencyClaimRequest(
        scope=first_request.scope,
        request_fingerprint=first_request.request_fingerprint,
        fingerprint_version=999,
        owner_request_id=f"{prefix}-version-owner",
        lease_duration_seconds=60,
    )
    async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
        with pytest.raises(FingerprintVersionError):
            await uow.idempotency_repository.claim(version_request)


@pytest.mark.asyncio
async def test_service_completed_replay_and_canonical_multiple_replay(
    idempotency_case,
):
    prefix, factory = idempotency_case
    chat = DatabaseChatService()
    first = await _service(factory, chat, prefix).execute(
        UserRequest(text="same"),
        context=_context(prefix, "original"),
        operation="chat",
        idempotency_key="raw-key-canary",
    )
    assert first.outcome is IdempotentResultOutcome.EXECUTED
    for suffix in ("replay-1", "replay-2"):
        replay_chat = DatabaseChatService()
        replay = await _service(factory, replay_chat, prefix).execute(
            UserRequest(text="same"),
            context=_context(prefix, suffix),
            operation="chat",
            idempotency_key="raw-key-canary",
        )
        assert replay.outcome is IdempotentResultOutcome.REPLAYED
        assert replay.original_request_id == f"{prefix}-original"
        assert replay_chat.call_count == 0
        assert replay.application_result.context.request_id == f"{prefix}-{suffix}"
    async with factory() as session:
        requests = (
            await session.execute(
                select(AgentRequest).where(
                    AgentRequest.request_id.like(f"{prefix}%")
                )
            )
        ).scalars().all()
        runs = (
            await session.execute(
                select(AgentRun).where(AgentRun.run_id.like(f"{prefix}%"))
            )
        ).scalars().all()
        original = next(item for item in requests if item.request_id.endswith("original"))
        replayed = [item for item in requests if "replay-" in item.request_id]
        assert len(runs) == 1
        assert all(item.replayed_from_request_id == original.id for item in replayed)
        persisted = repr(requests) + repr(runs)
        assert "raw-key-canary" not in persisted


@pytest.mark.asyncio
async def test_failed_terminal_returns_previous_failure_without_execution(
    idempotency_case,
):
    prefix, factory = idempotency_case
    with pytest.raises(DurableApplicationExecutionError):
        await _service(factory, DatabaseChatService(raises=True), prefix).execute(
            UserRequest(text="same"),
            context=_context(prefix, "failed-owner"),
            operation="chat",
            idempotency_key="raw-key-canary",
        )
    retry_chat = DatabaseChatService()
    retry = await _service(factory, retry_chat, prefix).execute(
        UserRequest(text="same"),
        context=_context(prefix, "retry"),
        operation="chat",
        idempotency_key="raw-key-canary",
    )
    assert retry.outcome is IdempotentResultOutcome.PREVIOUS_FAILURE
    assert retry.safe_failure_code == "application_service_failed"
    assert retry_chat.call_count == 0


@pytest.mark.asyncio
async def test_policy_blocked_finalizes_atomically_and_replays(idempotency_case):
    prefix, factory = idempotency_case
    first = await _service(
        factory,
        DatabaseChatService(blocked=True),
        prefix,
    ).execute(
        UserRequest(text="neutral text"),
        context=_context(prefix, "blocked-original"),
        operation="chat",
        idempotency_key="raw-key-canary",
    )
    assert first.application_result.orchestration.policy.is_blocked
    replay_chat = DatabaseChatService()
    replay = await _service(factory, replay_chat, prefix).execute(
        UserRequest(text="neutral text"),
        context=_context(prefix, "blocked-replay"),
        operation="chat",
        idempotency_key="raw-key-canary",
    )
    assert replay.outcome is IdempotentResultOutcome.REPLAYED
    assert replay.application_result.orchestration.policy.is_blocked
    assert replay_chat.call_count == 0
    async with factory() as session:
        original = (
            await session.execute(
                select(AgentRequest).where(
                    AgentRequest.request_id == f"{prefix}-blocked-original"
                )
            )
        ).scalar_one()
        run = (
            await session.execute(
                select(AgentRun).where(AgentRun.original_request_id == original.id)
            )
        ).scalar_one()
        record = (
            await session.execute(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.tenant_id == f"{prefix}-tenant"
                )
            )
        ).scalar_one()
        assert original.status == "completed"
        assert run.status == "blocked"
        assert record.status == "completed"
        assert record.completed_run_record_id == run.id


@pytest.mark.asyncio
async def test_expired_active_reclaim_creates_failed_management_run(
    idempotency_case,
):
    prefix, factory = idempotency_case
    context, request_record, claim_request = _claim(prefix, suffix="old-owner")
    async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
        decision = await uow.idempotency_repository.claim(claim_request)
        await uow.execution_repository.create_request(request_record)
        await uow.commit()
    assert decision.token is not None
    async with factory() as session:
        await session.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.id == decision.token.idempotency_record_id)
            .values(
                claimed_at=func.clock_timestamp() - timedelta(seconds=2),
                lease_expires_at=func.clock_timestamp() - timedelta(seconds=1),
            )
        )
        await session.commit()
    result = await _service(factory, DatabaseChatService(), prefix).execute(
        UserRequest(text="same"),
        context=_context(prefix, "new-owner"),
        operation="chat",
        idempotency_key="raw-key-canary",
    )
    assert result.outcome is IdempotentResultOutcome.EXECUTED
    async with factory() as session:
        old = (
            await session.execute(
                select(AgentRequest).where(
                    AgentRequest.request_id == f"{prefix}-old-owner"
                )
            )
        ).scalar_one()
        records = (
            await session.execute(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.tenant_id == f"{prefix}-tenant"
                )
            )
        ).scalars().all()
        management_run = (
            await session.execute(
                select(AgentRun).where(AgentRun.original_request_id == old.id)
            )
        ).scalar_one()
        assert old.status == "failed"
        assert old.failure_code == "idempotency_lease_reclaimed"
        assert management_run.status == "failed"
        assert records[0].claim_version == 2


@pytest.mark.asyncio
async def test_stale_claim_token_cannot_complete_after_owner_change(
    idempotency_case,
):
    prefix, factory = idempotency_case
    _, _, claim_request = _claim(prefix, suffix="old-owner")
    async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
        old = await uow.idempotency_repository.claim(claim_request)
        await uow.commit()
    assert old.token is not None
    async with factory() as session:
        await session.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.id == old.token.idempotency_record_id)
            .values(
                owner_request_id=f"{prefix}-new-owner",
                claim_version=2,
            )
        )
        await session.commit()
    async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
        with pytest.raises(IdempotencyOwnershipLostError):
            await uow.idempotency_repository.assert_current_owner(old.token)


@pytest.mark.asyncio
async def test_invalid_completed_state_is_not_replayed_or_repaired(
    idempotency_case,
):
    prefix, factory = idempotency_case
    _, _, claim_request = _claim(prefix)
    async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
        decision = await uow.idempotency_repository.claim(claim_request)
        await uow.commit()
    assert decision.token is not None
    async with factory() as session:
        await session.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.id == decision.token.idempotency_record_id)
            .values(status="completed")
        )
        await session.commit()
    async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
        with pytest.raises(InvalidIdempotencyStateError):
            await uow.idempotency_repository.claim(claim_request)


@pytest.mark.asyncio
async def test_concurrent_first_claim_has_one_owner(idempotency_case):
    prefix, factory = idempotency_case
    _, _, template = _claim(prefix)

    async def claim(index: int):
        request = IdempotencyClaimRequest(
            scope=template.scope,
            request_fingerprint=template.request_fingerprint,
            fingerprint_version=template.fingerprint_version,
            owner_request_id=f"{prefix}-concurrent-{index}",
            lease_duration_seconds=60,
        )
        async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
            decision = await uow.idempotency_repository.claim(request)
            await uow.commit()
            return decision.outcome

    outcomes = await asyncio.gather(*(claim(index) for index in range(5)))
    assert outcomes.count(ClaimOutcome.ACQUIRED) == 1
    assert outcomes.count(ClaimOutcome.IN_PROGRESS) == 4


@pytest.mark.asyncio
async def test_terminal_ttl_expiry_reacquires_and_increments_version(
    idempotency_case,
):
    prefix, factory = idempotency_case
    await _service(factory, DatabaseChatService(), prefix).execute(
        UserRequest(text="same"),
        context=_context(prefix, "owner-1"),
        operation="chat",
        idempotency_key="raw-key-canary",
    )
    async with factory() as session:
        await session.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.tenant_id == f"{prefix}-tenant")
            .values(
                expires_at=func.clock_timestamp(),
                fingerprint_version=2,
            )
        )
        await session.commit()
    result = await _service(factory, DatabaseChatService(), prefix).execute(
        UserRequest(text="new request"),
        context=_context(prefix, "owner-2"),
        operation="chat",
        idempotency_key="raw-key-canary",
    )
    assert result.outcome is IdempotentResultOutcome.EXECUTED
    async with factory() as session:
        record = (
            await session.execute(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.tenant_id == f"{prefix}-tenant"
                )
            )
        ).scalar_one()
        assert record.claim_version == 2
        assert record.fingerprint_version == 2


@pytest.mark.asyncio
async def test_scoped_unique_isolates_all_five_scope_dimensions(
    idempotency_case,
):
    prefix, factory = idempotency_case
    _, _, template = _claim(prefix)
    scopes = (
        template.scope,
        scope_from_values(
            tenant_id=f"{prefix}-other-tenant",
            organization_id=template.scope.organization_id,
            principal_user_id=template.scope.principal_user_id,
            operation=template.scope.operation,
            raw_key="raw-key-canary",
        ),
        scope_from_values(
            tenant_id=template.scope.tenant_id,
            organization_id=f"{prefix}-other-org",
            principal_user_id=template.scope.principal_user_id,
            operation=template.scope.operation,
            raw_key="raw-key-canary",
        ),
        scope_from_values(
            tenant_id=template.scope.tenant_id,
            organization_id=template.scope.organization_id,
            principal_user_id=f"{prefix}-other-user",
            operation=template.scope.operation,
            raw_key="raw-key-canary",
        ),
        scope_from_values(
            tenant_id=template.scope.tenant_id,
            organization_id=template.scope.organization_id,
            principal_user_id=template.scope.principal_user_id,
            operation="qa",
            raw_key="raw-key-canary",
        ),
    )
    for index, scope in enumerate(scopes):
        request = IdempotencyClaimRequest(
            scope=scope,
            request_fingerprint=template.request_fingerprint,
            fingerprint_version=template.fingerprint_version,
            owner_request_id=f"{prefix}-scope-{index}",
            lease_duration_seconds=60,
        )
        async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
            decision = await uow.idempotency_repository.claim(request)
            await uow.commit()
        assert decision.outcome is ClaimOutcome.ACQUIRED
    async with factory() as session:
        count = (
            await session.execute(
                select(func.count(IdempotencyRecord.id)).where(
                    IdempotencyRecord.tenant_id.like(f"{prefix}%")
                )
            )
        ).scalar_one()
    assert count == len(scopes)


@pytest.mark.asyncio
async def test_service_level_concurrent_duplicate_executes_once_then_replays(
    idempotency_case,
):
    prefix, factory = idempotency_case
    chat = BlockingDatabaseChatService()
    service = _service(factory, chat, prefix)
    first_task = asyncio.create_task(
        service.execute(
            UserRequest(text="same"),
            context=_context(prefix, "concurrent-original"),
            operation="chat",
            idempotency_key="raw-key-canary",
        )
    )
    assert await asyncio.to_thread(chat.entered.wait, 5)
    duplicate = await service.execute(
        UserRequest(text="same"),
        context=_context(prefix, "concurrent-duplicate"),
        operation="chat",
        idempotency_key="raw-key-canary",
    )
    assert duplicate.outcome is IdempotentResultOutcome.IN_PROGRESS
    chat.release.set()
    first = await first_task
    assert first.outcome is IdempotentResultOutcome.EXECUTED
    assert chat.call_count == 1

    replay_chat = DatabaseChatService()
    replay = await _service(factory, replay_chat, prefix).execute(
        UserRequest(text="same"),
        context=_context(prefix, "concurrent-replay"),
        operation="chat",
        idempotency_key="raw-key-canary",
    )
    assert replay.outcome is IdempotentResultOutcome.REPLAYED
    assert replay.original_request_id == f"{prefix}-concurrent-original"
    assert replay_chat.call_count == 0


@pytest.mark.asyncio
async def test_oversized_replay_snapshot_rolls_back_real_finalization(
    idempotency_case,
):
    prefix, factory = idempotency_case
    policy = IdempotencyPolicy(max_replay_snapshot_bytes=32)
    with pytest.raises(PersistenceFinalizationError):
        await _service(
            factory,
            DatabaseChatService(),
            prefix,
            policy=policy,
        ).execute(
            UserRequest(text="same"),
            context=_context(prefix, "snapshot-owner"),
            operation="chat",
            idempotency_key="raw-key-canary",
        )
    async with factory() as session:
        record = (
            await session.execute(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.tenant_id == f"{prefix}-tenant"
                )
            )
        ).scalar_one()
        request = (
            await session.execute(
                select(AgentRequest).where(
                    AgentRequest.request_id == f"{prefix}-snapshot-owner"
                )
            )
        ).scalar_one()
        run_count = (
            await session.execute(
                select(func.count(AgentRun.id)).where(
                    AgentRun.run_id.like(f"{prefix}%")
                )
            )
        ).scalar_one()
        assert record.status == "in_progress"
        assert record.response_snapshot is None
        assert record.completed_run_record_id is None
        assert request.status == "in_progress"
        assert run_count == 0


@pytest.mark.asyncio
async def test_stale_claim_token_cannot_complete_or_fail_fenced(
    idempotency_case,
):
    prefix, factory = idempotency_case
    _, _, claim_request = _claim(prefix, suffix="stale-owner")
    async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
        decision = await uow.idempotency_repository.claim(claim_request)
        await uow.commit()
    assert decision.token is not None
    async with factory() as session:
        await session.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.id == decision.token.idempotency_record_id)
            .values(owner_request_id=f"{prefix}-replacement", claim_version=2)
        )
        await session.commit()

    async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
        with pytest.raises(IdempotencyOwnershipLostError):
            await uow.idempotency_repository.complete_fenced(
                decision.token,
                completed_run_record_id=1,
                response_snapshot={"status": "SAFE"},
                response_snapshot_version=1,
                replay_ttl_seconds=60,
            )
        await uow.rollback()
    async with SQLAlchemyIdempotentExecutionUnitOfWork(factory) as uow:
        with pytest.raises(IdempotencyOwnershipLostError):
            await uow.idempotency_repository.fail_fenced(
                decision.token,
                failure_ttl_seconds=60,
            )
        await uow.rollback()
