"""Bounded process-crash, multi-instance, and prune concurrency drills."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
import httpx
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url

from conversation_agent.application.idempotent_durable_service import (
    IdempotentDurableApplicationService,
)
from conversation_agent.application.models import UserRequest
from conversation_agent.application.service import ApplicationResult
from conversation_agent.api.app import create_app
from conversation_agent.config import AppConfig, DatabaseConfig, PersistenceMode
from conversation_agent.authorization.models import AuthorizationDecision, ResourceScope
from conversation_agent.database.models import AgentRequest, AgentRun, AuditEvent, IdempotencyRecord
from conversation_agent.database.records import (
    IdempotencyPolicy,
    IdempotencyScope,
    IdempotentResultOutcome,
)
from conversation_agent.database.sqlalchemy_uow import SQLAlchemyIdempotentExecutionUnitOfWork
from conversation_agent.identity.models import Principal
from conversation_agent.operations import IdempotencyPruner
from conversation_agent.orchestration.models import OrchestrationResult, TaskRoute
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.runtime.models import RequestContext, RuntimeVersionSnapshot


pytestmark = [
    pytest.mark.operational_integration,
    pytest.mark.postgres_integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(40),
]


@pytest.fixture(scope="session")
def recovery_postgres_url() -> str:
    url = os.getenv("CONVAGENT_POSTGRES_TEST_URL", "").strip()
    if not url:
        pytest.skip("CONVAGENT_POSTGRES_TEST_URL not set")
    config = AlembicConfig("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return url


@pytest_asyncio.fixture
async def recovery_engine(recovery_postgres_url: str) -> AsyncEngine:
    engine = create_async_engine(recovery_postgres_url, pool_size=2, max_overflow=0)
    yield engine
    await engine.dispose()


def _context(prefix: str, owner: str) -> RequestContext:
    principal = Principal(
        tenant_id=f"{prefix}-tenant",
        organization_id=f"{prefix}-org",
        user_id=f"{prefix}-user",
        roles=("agent_user",),
    )
    return RequestContext(
        request_id=f"{prefix}-{owner}",
        trace_id=f"{prefix}-{owner}-trace",
        session_id=f"{prefix}-{owner}-session",
        principal=principal,
        authorization=AuthorizationDecision(
            allowed=True,
            code="allowed",
            permissions=("chat:invoke", "crm:read", "rag:read"),
            resource_scopes=(
                ResourceScope(
                    tenant_id=principal.tenant_id,
                    organization_id=principal.organization_id,
                    resource_type="organization",
                    scope_type="organization",
                ),
            ),
        ),
        versions=RuntimeVersionSnapshot(
            model_registry_version="models-v1",
            model_routing_policy_version="not_implemented",
            application_version="0.1.0",
            policy_version="policy-v1",
            rag_contract_version="rag-v1",
            crm_connector_version="not_configured",
            authorization_policy_version="authz-v1",
        ),
        received_at=datetime.now(timezone.utc),
    )


class SuccessfulChatService:
    def __init__(self, gate: threading.Event | None = None) -> None:
        self.call_count = 0
        self.entered = threading.Event()
        self.gate = gate

    def execute_with_context(self, request, *, context, forced_task=None):
        self.call_count += 1
        self.entered.set()
        if self.gate is not None and not self.gate.wait(10):
            raise TimeoutError("bounded coordinator gate expired")
        return ApplicationResult(
            context=context,
            orchestration=OrchestrationResult(
                session_id=context.session_id,
                user_input=request.text,
                policy=PolicyDecision(status="SAFE"),
                task_route=TaskRoute(task="qa"),
                final_response="answer",
                confidence=0.8,
            ),
        )


class _IncreasingUtcClock:
    """Thread-safe event clock immune to host wall-clock adjustments."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        with self._lock:
            self._value += timedelta(microseconds=1)
            return self._value


def _service(engine: AsyncEngine, chat: SuccessfulChatService, prefix: str):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return IdempotentDurableApplicationService(
        chat_service=chat,
        uow_factory=lambda: SQLAlchemyIdempotentExecutionUnitOfWork(factory),
        policy=IdempotencyPolicy(lease_duration_seconds=300),
        clock=_IncreasingUtcClock(),
        run_id_factory=lambda: f"{prefix}-run-{uuid.uuid4()}",
        event_id_factory=lambda: f"{prefix}-event-{uuid.uuid4()}",
    )


async def _cleanup(engine: AsyncEngine, prefix: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(delete(AuditEvent).where(AuditEvent.event_id.like(f"{prefix}%")))
        await connection.execute(delete(IdempotencyRecord).where(IdempotencyRecord.tenant_id == f"{prefix}-tenant"))
        await connection.execute(delete(AgentRun).where(AgentRun.run_id.like(f"{prefix}%")))
        await connection.execute(delete(AgentRequest).where(AgentRequest.request_id.like(f"{prefix}%")))


async def _wait_for(predicate, *, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("bounded operational wait expired")


class BoundedTcpProxy:
    def __init__(self, upstream_host: str, upstream_port: int) -> None:
        self._upstream_host = upstream_host
        self._upstream_port = upstream_port
        self._server = None
        self._port: int | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task] = set()

    @property
    def port(self) -> int:
        assert self._port is not None
        return self._port

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._accept,
            "127.0.0.1",
            self._port or 0,
        )
        await self._server.start_serving()
        self._port = int(self._server.sockets[0].getsockname()[1])

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await asyncio.wait_for(self._server.wait_closed(), timeout=3)
            self._server = None
        for writer in tuple(self._writers):
            writer.close()
        for writer in tuple(self._writers):
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1)
            except Exception:
                pass
        self._writers.clear()
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=2)
        self._tasks.clear()

    async def _accept(
        self,
        downstream_reader: asyncio.StreamReader,
        downstream_writer: asyncio.StreamWriter,
    ) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(self._upstream_host, self._upstream_port),
                timeout=3,
            )
        except Exception:
            downstream_writer.close()
            return
        self._writers.update((downstream_writer, upstream_writer))
        tasks = {
            asyncio.create_task(self._pump(downstream_reader, upstream_writer)),
            asyncio.create_task(self._pump(upstream_reader, downstream_writer)),
        }
        self._tasks.update(tasks)
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
                self._tasks.discard(task)
            for writer in (downstream_writer, upstream_writer):
                writer.close()
                self._writers.discard(writer)

    @staticmethod
    async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            chunk = await reader.read(64 * 1024)
            if not chunk:
                return
            writer.write(chunk)
            await writer.drain()


async def test_process_crash_preserves_active_then_request_driven_reclaims(
    recovery_engine: AsyncEngine,
):
    prefix = f"m14f-crash-{uuid.uuid4()}"
    marker = Path(tempfile.gettempdir()) / f"{prefix}.marker"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.operational.crash_worker",
        prefix,
        str(marker),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async def transaction_a_visible() -> bool:
            if not marker.exists():
                return False
            async with recovery_engine.connect() as connection:
                status = await connection.scalar(
                    select(IdempotencyRecord.status).where(
                        IdempotencyRecord.tenant_id == f"{prefix}-tenant"
                    )
                )
                run_count = int(
                    await connection.scalar(
                        select(__import__("sqlalchemy").func.count()).select_from(AgentRun).where(
                            AgentRun.run_id.like(f"{prefix}%")
                        )
                    )
                    or 0
                )
            return status == "in_progress" and run_count == 0

        await _wait_for(transaction_a_visible)
        process.kill()
        await asyncio.wait_for(process.wait(), timeout=5)

        waiting_chat = SuccessfulChatService()
        waiting = await _service(recovery_engine, waiting_chat, prefix).execute(
            UserRequest(text="crash recovery"),
            context=_context(prefix, "before-expiry"),
            operation="v1.chat",
            idempotency_key="crash-key",
        )
        assert waiting.outcome is IdempotentResultOutcome.IN_PROGRESS
        assert waiting_chat.call_count == 0

        async with recovery_engine.begin() as connection:
            await connection.execute(
                update(IdempotencyRecord)
                .where(IdempotencyRecord.tenant_id == f"{prefix}-tenant")
                .values(
                    claimed_at=text("clock_timestamp() - interval '10 minutes'"),
                    lease_expires_at=text("clock_timestamp() - interval '5 minutes'"),
                )
            )
        recovered_chat = SuccessfulChatService()
        recovered = await _service(recovery_engine, recovered_chat, prefix).execute(
            UserRequest(text="crash recovery"),
            context=_context(prefix, "owner-b"),
            operation="v1.chat",
            idempotency_key="crash-key",
        )
        assert recovered.outcome is IdempotentResultOutcome.EXECUTED
        assert recovered.claim_version == 2
        async with recovery_engine.connect() as connection:
            statuses = tuple(
                (await connection.execute(
                    select(AgentRequest.request_id, AgentRequest.status).where(
                        AgentRequest.request_id.like(f"{prefix}%")
                    )
                )).all()
            )
        assert sorted(status for _, status in statuses) == ["completed", "failed"]
    finally:
        if process.returncode is None:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=5)
        marker.unlink(missing_ok=True)
        await _cleanup(recovery_engine, prefix)


async def test_two_independent_engines_coordinate_one_active_claim(
    recovery_postgres_url: str,
):
    prefix = f"m14f-multi-{uuid.uuid4()}"
    engine_a = create_async_engine(recovery_postgres_url, pool_size=1, max_overflow=0)
    engine_b = create_async_engine(recovery_postgres_url, pool_size=1, max_overflow=0)
    gate = threading.Event()
    chat_a = SuccessfulChatService(gate)
    chat_b = SuccessfulChatService()
    try:
        task_a = asyncio.create_task(
            _service(engine_a, chat_a, prefix).execute(
                UserRequest(text="same"),
                context=_context(prefix, "owner-a"),
                operation="v1.chat",
                idempotency_key="shared-key",
            )
        )
        assert await asyncio.to_thread(chat_a.entered.wait, 5)
        result_b = await _service(engine_b, chat_b, prefix).execute(
            UserRequest(text="same"),
            context=_context(prefix, "owner-b"),
            operation="v1.chat",
            idempotency_key="shared-key",
        )
        assert result_b.outcome is IdempotentResultOutcome.IN_PROGRESS
        assert chat_b.call_count == 0
        gate.set()
        result_a = await asyncio.wait_for(task_a, timeout=10)
        assert result_a.outcome is IdempotentResultOutcome.EXECUTED

        replay = await _service(engine_b, chat_b, prefix).execute(
            UserRequest(text="same"),
            context=_context(prefix, "replay"),
            operation="v1.chat",
            idempotency_key="shared-key",
        )
        assert replay.outcome is IdempotentResultOutcome.REPLAYED
        assert chat_a.call_count + chat_b.call_count == 1
        await engine_a.dispose()
        async with engine_b.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
    finally:
        gate.set()
        await engine_a.dispose()
        await _cleanup(engine_b, prefix)
        await engine_b.dispose()


async def test_prune_skip_locked_loses_safely_to_terminal_reacquire(
    recovery_engine: AsyncEngine,
):
    prefix = f"m14f-prune-race-{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    async with recovery_engine.begin() as connection:
        await connection.execute(
            IdempotencyRecord.__table__.insert().values(
                tenant_id=f"{prefix}-tenant",
                organization_id=f"{prefix}-org",
                principal_user_id=f"{prefix}-user",
                operation="v1.chat",
                idempotency_key_hash="a" * 64,
                request_fingerprint="b" * 64,
                fingerprint_version=2,
                status="failed",
                claim_version=7,
                owner_request_id=f"{prefix}-old",
                claimed_at=now.replace(year=now.year - 1),
                lease_expires_at=now.replace(year=now.year - 1),
                created_at=now.replace(year=now.year - 1),
                updated_at=now.replace(year=now.year - 1),
                expires_at=now.replace(year=now.year - 1),
            )
        )
    lock_connection = await recovery_engine.connect()
    lock_transaction = await lock_connection.begin()
    try:
        await lock_connection.execute(
            select(IdempotencyRecord.id)
            .where(IdempotencyRecord.tenant_id == f"{prefix}-tenant")
            .with_for_update()
        )
        pruned = await IdempotencyPruner(
            recovery_engine,
            batch_size=10,
            safety_margin_seconds=1,
        ).run(apply=True)
        assert pruned.deleted_count == 0
        await lock_transaction.commit()
        await lock_connection.close()

        scope = IdempotencyScope(
            tenant_id=f"{prefix}-tenant",
            organization_id=f"{prefix}-org",
            principal_user_id=f"{prefix}-user",
            operation="v1.chat",
            key_hash="a" * 64,
        )
        from conversation_agent.database.records import IdempotencyClaimRequest
        from conversation_agent.database.sqlalchemy_idempotency_repository import SQLAlchemyIdempotencyRepository
        factory = async_sessionmaker(recovery_engine, expire_on_commit=False)
        async with factory() as session:
            decision = await SQLAlchemyIdempotencyRepository(session).claim(
                IdempotencyClaimRequest(
                    scope=scope,
                    request_fingerprint="c" * 64,
                    fingerprint_version=3,
                    owner_request_id=f"{prefix}-new",
                    lease_duration_seconds=300,
                )
            )
            await session.commit()
        assert decision.outcome.value == "acquired"
        assert decision.token is not None
        assert decision.token.claim_version == 8
        second_prune = await IdempotencyPruner(
            recovery_engine,
            safety_margin_seconds=1,
        ).run(apply=True)
        assert second_prune.deleted_count == 0
    finally:
        if lock_transaction.is_active:
            await lock_transaction.rollback()
        if not lock_connection.closed:
            await lock_connection.close()
        await _cleanup(recovery_engine, prefix)


async def test_database_network_outage_and_readiness_recover_without_fallback(
    recovery_postgres_url: str,
):
    parsed = make_url(recovery_postgres_url)
    ready_file = Path(tempfile.gettempdir()) / f"m14f-proxy-{uuid.uuid4()}.ready"

    async def start_proxy(port: int):
        last_error: BaseException | None = None
        for _ in range(2):
            attempt_error: BaseException | None = None
            ready_file.unlink(missing_ok=True)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "tests.operational.tcp_proxy",
                parsed.host or "127.0.0.1",
                str(parsed.port or 5432),
                str(port),
                str(ready_file),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

            async def ready() -> bool:
                return (
                    process.returncode is None
                    and ready_file.exists()
                    and bool(ready_file.read_text(encoding="ascii").strip())
                )

            try:
                await _wait_for(ready, timeout=5)
                selected_port = int(ready_file.read_text(encoding="ascii"))
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", selected_port), timeout=2
                )
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), timeout=2)
                del reader
                if process.returncode is None:
                    return process, selected_port
            except BaseException as exc:
                attempt_error = exc
                last_error = exc
            finally:
                if process.returncode is not None:
                    ready_file.unlink(missing_ok=True)
                elif attempt_error is not None:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=5)
                    ready_file.unlink(missing_ok=True)
        raise RuntimeError("bounded TCP proxy failed to become ready") from last_error

    async def stop_proxy(process) -> None:
        if process.returncode is None:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=5)
        ready_file.unlink(missing_ok=True)

    proxy_process, proxy_port = await start_proxy(0)
    proxied_url = parsed.set(host="127.0.0.1", port=proxy_port).render_as_string(
        hide_password=False
    )
    prefix = f"m14f-outage-{uuid.uuid4()}"
    chat = SuccessfulChatService()
    config = AppConfig(
        runtime_mode="demo",
        database=DatabaseConfig(
            url=proxied_url,
            persistence_mode=PersistenceMode.POSTGRES,
            readiness_timeout_seconds=1,
            connect_timeout_seconds=1,
            pool_timeout_seconds=1,
        ),
    )
    app = create_app(service=chat, config=config)
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                assert (await client.get("/healthz")).status_code == 200
                assert (await client.get("/readyz")).status_code == 200
                await stop_proxy(proxy_process)
                assert (await client.get("/healthz")).status_code == 200
                assert (await client.get("/readyz")).status_code == 503
                failed = await client.post("/v1/chat", json={"text": "during outage"})
                assert failed.status_code == 503
                assert chat.call_count == 0

                proxy_process, restarted_port = await start_proxy(proxy_port)
                assert restarted_port == proxy_port

                async def ready_again() -> bool:
                    return (await client.get("/readyz")).status_code == 200

                await _wait_for(ready_again, timeout=8)
                recovered = await client.post(
                    "/v1/chat",
                    json={"text": "after recovery", "session_id": prefix},
                )
                assert recovered.status_code == 200
                assert chat.call_count == 1
    finally:
        await stop_proxy(proxy_process)
        # Clean only the request identified by this test's unique session.
        async_engine = create_async_engine(recovery_postgres_url)
        async with async_engine.begin() as connection:
            request_rows = tuple(
                (
                    await connection.execute(
                        select(AgentRequest.id, AgentRequest.request_id).where(
                            AgentRequest.session_id == prefix
                        )
                    )
                ).all()
            )
            request_database_ids = [row.id for row in request_rows]
            request_ids = [row.request_id for row in request_rows]
            if request_ids:
                await connection.execute(
                    delete(AuditEvent).where(AuditEvent.request_id.in_(request_ids))
                )
            if request_database_ids:
                await connection.execute(
                    delete(AgentRun).where(
                        AgentRun.original_request_id.in_(request_database_ids)
                    )
                )
                await connection.execute(
                    delete(AgentRequest).where(AgentRequest.id.in_(request_database_ids))
                )
        await async_engine.dispose()


async def test_bounded_pool_soak_releases_all_connections(
    recovery_postgres_url: str,
):
    prefix = f"m14f-soak-{uuid.uuid4()}"
    engine = create_async_engine(
        recovery_postgres_url,
        pool_size=4,
        max_overflow=2,
        pool_timeout=3,
        pool_pre_ping=True,
    )
    chat = SuccessfulChatService()
    service = _service(engine, chat, prefix)
    try:
        for index in range(30):
            first = await service.execute(
                UserRequest(text=f"sequential-{index}"),
                context=_context(prefix, f"sequential-{index}"),
                operation="v1.chat",
                idempotency_key=f"sequential-key-{index}",
            )
            replay = await service.execute(
                UserRequest(text=f"sequential-{index}"),
                context=_context(prefix, f"sequential-replay-{index}"),
                operation="v1.chat",
                idempotency_key=f"sequential-key-{index}",
            )
            assert first.outcome is IdempotentResultOutcome.EXECUTED
            assert replay.outcome is IdempotentResultOutcome.REPLAYED

        concurrent = await asyncio.gather(
            *(
                service.execute(
                    UserRequest(text=f"concurrent-{index}"),
                    context=_context(prefix, f"concurrent-{index}"),
                    operation="v1.chat",
                    idempotency_key=f"concurrent-key-{index}",
                )
                for index in range(20)
            )
        )
        assert all(
            item.outcome is IdempotentResultOutcome.EXECUTED
            for item in concurrent
        )
        assert chat.call_count == 50
        assert engine.sync_engine.pool.checkedout() == 0
    finally:
        await _cleanup(engine, prefix)
        assert engine.sync_engine.pool.checkedout() == 0
        await engine.dispose()
