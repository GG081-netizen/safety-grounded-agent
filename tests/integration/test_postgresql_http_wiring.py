"""M1.4-E FastAPI durable wiring against real PostgreSQL 17."""

from __future__ import annotations

import os
import asyncio
import threading
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from conversation_agent.api.app import create_app
from conversation_agent.api.projector import ResponseProjector
from conversation_agent.application.service import ChatService
from conversation_agent.config import AppConfig
from conversation_agent.database.models import (
    AgentRequest,
    AgentRun,
    AuditEvent,
    IdempotencyRecord,
)
from conversation_agent.orchestration.models import OrchestrationResult
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.runtime.builder import create_development_context_builder


pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
]


class HTTPRecordingCoordinator:
    def __init__(
        self,
        *,
        blocked: bool = False,
        raises: bool = False,
        started: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.calls = 0
        self.blocked = blocked
        self.raises = raises
        self.started = started
        self.release = release

    def run(self, user_input, session_id=None, task_override=None, *, request_metadata=None):
        del request_metadata
        self.calls += 1
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            assert self.release.wait(timeout=5)
        if self.raises:
            raise RuntimeError("provider failure canary")
        return OrchestrationResult(
            session_id=session_id,
            user_input=user_input,
            policy=PolicyDecision(
                status="BLOCKED" if self.blocked else "SAFE",
                confidence=1.0,
            ),
            final_response="blocked" if self.blocked else "persisted answer",
            confidence=0.9,
        )


class FailOnceProjector(ResponseProjector):
    def __init__(self) -> None:
        self.calls = 0

    def project(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise TypeError("projection failure canary")
        return super().project(*args, **kwargs)


@pytest.fixture(scope="session")
def http_postgres_url() -> str:
    url = os.getenv("CONVAGENT_POSTGRES_TEST_URL", "").strip()
    if not url:
        pytest.skip("CONVAGENT_POSTGRES_TEST_URL not set")
    if os.getenv("CONVAGENT_DATABASE_URL", "").strip() == url:
        pytest.skip("Refusing to use the application database")
    return url


@pytest.fixture(scope="session")
def http_schema(http_postgres_url: str) -> None:
    config = AlembicConfig("alembic.ini")
    config.set_main_option("sqlalchemy.url", http_postgres_url)
    command.upgrade(config, "head")


@pytest_asyncio.fixture
async def http_case(
    http_postgres_url: str,
    http_schema: None,
) -> AsyncIterator[tuple[str, AppConfig]]:
    del http_schema
    prefix = f"m14e-{uuid.uuid4()}"
    config = AppConfig(
        runtime_mode="demo",
        database={
            "persistence_mode": "postgres",
            "idempotency_header_mode": "optional",
            "url": http_postgres_url,
        },
    )
    yield prefix, config

    engine = create_async_engine(http_postgres_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        request_ids = select(AgentRequest.id).where(
            AgentRequest.request_id.like(f"{prefix}%")
        )
        await session.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.owner_request_id.like(f"{prefix}%")
            )
        )
        await session.execute(
            delete(AuditEvent).where(AuditEvent.request_id.like(f"{prefix}%"))
        )
        await session.execute(
            delete(AgentRun).where(
                AgentRun.original_request_id.in_(request_ids)
            )
        )
        await session.execute(
            delete(AgentRequest).where(AgentRequest.request_id.like(f"{prefix}%"))
        )
        await session.commit()
    await engine.dispose()


def _build_app(prefix: str, config: AppConfig, *, projector=None, coordinator=None):
    coordinator = coordinator or HTTPRecordingCoordinator()
    service = ChatService(
        coordinator=coordinator,  # type: ignore[arg-type]
        context_builder=create_development_context_builder(),
    )
    ids = iter(f"{prefix}-{index}" for index in range(100))
    return (
        create_app(
            service=service,
            config=config,
            id_factory=lambda: next(ids),
            projector=projector,
        ),
        coordinator,
    )


@pytest.mark.asyncio
async def test_http_postgres_executes_and_replays(http_case):
    prefix, config = http_case
    app, coordinator = _build_app(prefix, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            first = await client.post(
                "/v1/chat",
                json={"text": "same", "session_id": "stable"},
                headers={"Idempotency-Key": f"{prefix}-postgres-key"},
            )
            second = await client.post(
                "/v1/chat",
                json={"text": "same", "session_id": "stable"},
                headers={"Idempotency-Key": f"{prefix}-postgres-key"},
            )
            ready = await client.get("/readyz")
    assert first.status_code == second.status_code == ready.status_code == 200
    assert first.headers["Idempotency-Status"] == "executed"
    assert second.headers["Idempotency-Status"] == "replayed"
    assert coordinator.calls == 1


@pytest.mark.asyncio
async def test_http_postgres_same_key_different_dto_conflicts(http_case):
    prefix, config = http_case
    app, coordinator = _build_app(prefix, config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            first = await client.post(
                "/v1/qa",
                json={"text": "first"},
                headers={"Idempotency-Key": f"{prefix}-conflict-key"},
            )
            conflict = await client.post(
                "/v1/qa",
                json={"text": "different"},
                headers={"Idempotency-Key": f"{prefix}-conflict-key"},
            )
    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_key_conflict"
    assert "Idempotency-Status" not in conflict.headers
    assert coordinator.calls == 1


@pytest.mark.asyncio
async def test_http_postgres_projection_failure_then_replay(http_case):
    prefix, config = http_case
    projector = FailOnceProjector()
    app, coordinator = _build_app(prefix, config, projector=projector)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            first = await client.post(
                "/v1/chat",
                json={"text": "project"},
                headers={"Idempotency-Key": f"{prefix}-project-key"},
            )
            replay = await client.post(
                "/v1/chat",
                json={"text": "project"},
                headers={"Idempotency-Key": f"{prefix}-project-key"},
            )
    assert first.status_code == 500
    assert first.json()["code"] == "response_projection_failed"
    assert replay.status_code == 200
    assert replay.headers["Idempotency-Status"] == "replayed"
    assert coordinator.calls == 1


@pytest.mark.asyncio
async def test_http_postgres_in_progress_then_replays(http_case):
    prefix, config = http_case
    started = threading.Event()
    release = threading.Event()
    coordinator = HTTPRecordingCoordinator(started=started, release=release)
    app, coordinator = _build_app(
        prefix, config, coordinator=coordinator
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            first_task = asyncio.create_task(
                client.post(
                    "/v1/chat",
                    json={"text": "wait"},
                    headers={"Idempotency-Key": f"{prefix}-wait-key"},
                )
            )
            while not started.is_set():
                await asyncio.sleep(0.01)
            duplicate = await client.post(
                "/v1/chat",
                json={"text": "wait"},
                headers={"Idempotency-Key": f"{prefix}-wait-key"},
            )
            release.set()
            first = await first_task
            replay = await client.post(
                "/v1/chat",
                json={"text": "wait"},
                headers={"Idempotency-Key": f"{prefix}-wait-key"},
            )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "idempotency_request_in_progress"
    assert first.headers["Idempotency-Status"] == "executed"
    assert replay.headers["Idempotency-Status"] == "replayed"
    assert coordinator.calls == 1


@pytest.mark.asyncio
async def test_http_postgres_policy_blocked_is_replayed(http_case):
    prefix, config = http_case
    coordinator = HTTPRecordingCoordinator(blocked=True)
    app, coordinator = _build_app(prefix, config, coordinator=coordinator)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            first = await client.post(
                "/v1/qa",
                json={"text": "blocked"},
                headers={"Idempotency-Key": f"{prefix}-blocked-key"},
            )
            replay = await client.post(
                "/v1/qa",
                json={"text": "blocked"},
                headers={"Idempotency-Key": f"{prefix}-blocked-key"},
            )
    assert first.status_code == replay.status_code == 200
    assert first.headers["Idempotency-Status"] == "executed"
    assert replay.headers["Idempotency-Status"] == "replayed"
    assert first.json()["result"]["policy"]["status"] == "BLOCKED"
    assert replay.json()["result"]["policy"]["status"] == "BLOCKED"
    assert coordinator.calls == 1


@pytest.mark.asyncio
async def test_http_postgres_previous_failure_is_409(http_case):
    prefix, config = http_case
    coordinator = HTTPRecordingCoordinator(raises=True)
    app, coordinator = _build_app(prefix, config, coordinator=coordinator)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            first = await client.post(
                "/v1/chat",
                json={"text": "fail"},
                headers={"Idempotency-Key": f"{prefix}-failure-key"},
            )
            retry = await client.post(
                "/v1/chat",
                json={"text": "fail"},
                headers={"Idempotency-Key": f"{prefix}-failure-key"},
            )
    assert first.status_code == 500
    assert first.json()["code"] == "application_execution_error"
    assert retry.status_code == 409
    assert retry.json()["code"] == "idempotency_previous_failure"
    assert coordinator.calls == 1
