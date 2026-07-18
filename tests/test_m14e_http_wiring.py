import asyncio
from datetime import datetime, timezone
import threading

import httpx
import pytest
from pydantic import ValidationError

from conversation_agent.api.app import create_app
from conversation_agent.api.projector import ResponseProjector
from conversation_agent.api.security import RequestSecurityService
from conversation_agent.api.models import RequestTraceStep
from conversation_agent.application.service import ChatService
from conversation_agent.authorization.service import AuthorizationService
from conversation_agent.authorization.models import AuthorizationDecision, ResourceScope
from conversation_agent.config import AppConfig
from conversation_agent.database.errors import DatabaseRevisionError
from conversation_agent.database.fake_execution import FakeIdempotentUnitOfWorkFactory
from conversation_agent.identity.authentication import BearerTokenParser
from conversation_agent.identity.models import Principal
from conversation_agent.api.security import SecurityContext
from conversation_agent.orchestration.models import OrchestrationResult
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.runtime.builder import create_development_context_builder


pytestmark = pytest.mark.unit


class RecordingCoordinator:
    def __init__(
        self,
        *,
        raises: bool = False,
        started: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.calls = 0
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
            raise RuntimeError("coordinator failure canary")
        return OrchestrationResult(
            session_id=session_id,
            user_input=user_input,
            policy=PolicyDecision(status="SAFE", confidence=1.0),
            final_response=f"answer-{self.calls}",
            confidence=0.9,
        )


class FailOnceProjector(ResponseProjector):
    def __init__(self) -> None:
        self.calls = 0

    def project(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise TypeError("projection canary must not escape")
        return super().project(*args, **kwargs)


def _fake_config(*, header_mode: str = "optional") -> AppConfig:
    return AppConfig(
        runtime_mode="test",
        database={
            "persistence_mode": "fake",
            "idempotency_header_mode": header_mode,
        },
    )


def _app(*, header_mode="optional", projector=None, coordinator=None):
    coordinator = coordinator or RecordingCoordinator()
    service = ChatService(
        coordinator=coordinator,  # type: ignore[arg-type]
        context_builder=create_development_context_builder(),
    )
    factory = FakeIdempotentUnitOfWorkFactory(
        database_clock=lambda: datetime.now(timezone.utc)
    )
    app = create_app(
        service=service,
        config=_fake_config(header_mode=header_mode),
        fake_uow_factory=factory,
        projector=projector,
        security_service=RequestSecurityService(
            runtime_mode="demo",
            bearer_parser=BearerTokenParser(8192),
            authorization_service=AuthorizationService(),
        ),
    )
    return app, coordinator, factory


async def _send(app, method, path, **kwargs):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_persistence_and_header_mode_matrix_is_fail_closed():
    AppConfig(
        runtime_mode="demo",
        database={
            "persistence_mode": "postgres",
            "idempotency_header_mode": "optional",
            "url": "postgresql+asyncpg://localhost/test",
        },
    )
    with pytest.raises(ValidationError):
        AppConfig(
            database={
                "persistence_mode": "null",
                "idempotency_header_mode": "required",
            }
        )
    with pytest.raises(ValidationError):
        AppConfig(
            runtime_mode="demo",
            database={"persistence_mode": "fake"},
        )


def test_database_url_is_redacted_from_repr_and_validation_error():
    canary = "postgresql+asyncpg://user:CANARY_PASSWORD@db.example/test"
    config = AppConfig(
        database={
            "persistence_mode": "postgres",
            "url": canary,
        }
    )
    assert canary not in repr(config)
    assert "CANARY_PASSWORD" not in repr(config)
    with pytest.raises(ValidationError) as exc_info:
        AppConfig(
            database={
                "persistence_mode": "postgres",
                "url": "postgresql://user:CANARY_PASSWORD@db.example/test",
            }
        )
    assert "CANARY_PASSWORD" not in str(exc_info.value)


def test_duplicate_raw_idempotency_headers_are_rejected_before_execution():
    app, coordinator, factory = _app()
    response = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "same"},
            headers=[
                ("Idempotency-Key", "same-key"),
                ("Idempotency-Key", "same-key"),
            ],
        )
    )
    assert response.status_code == 400
    assert response.json()["code"] == "duplicate_idempotency_key"
    assert "Idempotency-Status" not in response.headers
    assert coordinator.calls == 0
    assert factory.created_uow_count == 0


def test_idempotency_header_parser_ows_controls_and_length_contract():
    app, coordinator, factory = _app()
    accepted = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "same"},
            headers={"Idempotency-Key": "  key-with-ows  "},
        )
    )
    too_long = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "same"},
            headers={"Idempotency-Key": "x" * 256},
        )
    )
    assert accepted.status_code == 200
    assert accepted.headers["Idempotency-Status"] == "executed"
    assert too_long.status_code == 400
    assert too_long.json()["code"] == "idempotency_key_too_long"
    assert "Idempotency-Status" not in too_long.headers
    assert coordinator.calls == 1


def test_fake_persistence_executes_then_replays_with_current_projection():
    app, coordinator, factory = _app()
    first = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "same", "session_id": "session-a"},
            headers={"Idempotency-Key": "key-a"},
        )
    )
    second = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "same", "session_id": "session-a"},
            headers={"Idempotency-Key": "key-a"},
        )
    )
    assert first.status_code == second.status_code == 200
    assert first.headers["Idempotency-Status"] == "executed"
    assert second.headers["Idempotency-Status"] == "replayed"
    assert first.json()["result"]["final_response"] == second.json()["result"][
        "final_response"
    ]
    assert first.json()["request_id"] != second.json()["request_id"]
    assert coordinator.calls == 1
    assert len(factory.state.runs) == 1
    record = next(iter(factory.state.idempotency.values()))
    assert record["scope"].operation == "v1.chat"


def test_parsed_session_input_is_part_of_request_fingerprint():
    app, coordinator, factory = _app()
    first = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "same", "session_id": "session-a"},
            headers={"Idempotency-Key": "session-key"},
        )
    )
    conflict = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "same", "session_id": "session-b"},
            headers={"Idempotency-Key": "session-key"},
        )
    )
    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_key_conflict"
    assert coordinator.calls == 1


def test_projection_failure_after_commit_replays_without_reexecution():
    projector = FailOnceProjector()
    app, coordinator, factory = _app(projector=projector)
    first = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/qa",
            json={"text": "same"},
            headers={"Idempotency-Key": "key-projection"},
        )
    )
    second = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/qa",
            json={"text": "same"},
            headers={"Idempotency-Key": "key-projection"},
        )
    )
    assert first.status_code == 500
    assert first.json()["code"] == "response_projection_failed"
    assert "Idempotency-Status" not in first.headers
    assert second.status_code == 200
    assert second.headers["Idempotency-Status"] == "replayed"
    assert coordinator.calls == 1
    assert len(factory.state.runs) == 1


def test_unsupported_replay_snapshot_version_fails_closed():
    app, coordinator, factory = _app()
    first = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "versioned"},
            headers={"Idempotency-Key": "version-key"},
        )
    )
    assert first.status_code == 200
    record = next(iter(factory.state.idempotency.values()))
    record["response_snapshot_version"] = 999
    replay = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "versioned"},
            headers={"Idempotency-Key": "version-key"},
        )
    )
    assert replay.status_code == 409
    assert replay.json()["code"] == "replay_snapshot_version_unsupported"
    assert "Idempotency-Status" not in replay.headers
    assert coordinator.calls == 1


def test_first_execution_failure_then_same_key_returns_previous_failure():
    coordinator = RecordingCoordinator(raises=True)
    app, coordinator, factory = _app(coordinator=coordinator)
    first = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "fail"},
            headers={"Idempotency-Key": "failure-key"},
        )
    )
    second = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "fail"},
            headers={"Idempotency-Key": "failure-key"},
        )
    )
    assert first.status_code == 500
    assert first.json()["code"] == "application_execution_error"
    assert second.status_code == 409
    assert second.json()["code"] == "idempotency_previous_failure"
    assert "Idempotency-Status" not in first.headers
    assert "Idempotency-Status" not in second.headers
    assert coordinator.calls == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_is_in_progress_then_replays():
    started = threading.Event()
    release = threading.Event()
    coordinator = RecordingCoordinator(started=started, release=release)
    app, coordinator, factory = _app(coordinator=coordinator)
    first_task = asyncio.create_task(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "wait"},
            headers={"Idempotency-Key": "concurrent-key"},
        )
    )
    while not started.is_set():
        await asyncio.sleep(0.01)
    duplicate = await _send(
        app,
        "POST",
        "/v1/chat",
        json={"text": "wait"},
        headers={"Idempotency-Key": "concurrent-key"},
    )
    release.set()
    first = await first_task
    replay = await _send(
        app,
        "POST",
        "/v1/chat",
        json={"text": "wait"},
        headers={"Idempotency-Key": "concurrent-key"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "idempotency_request_in_progress"
    assert first.headers["Idempotency-Status"] == "executed"
    assert replay.headers["Idempotency-Status"] == "replayed"
    assert coordinator.calls == 1


@pytest.mark.asyncio
async def test_client_cancellation_leaves_active_claim_without_failed_run():
    started = threading.Event()
    release = threading.Event()
    coordinator = RecordingCoordinator(started=started, release=release)
    app, coordinator, factory = _app(coordinator=coordinator)
    request_task = asyncio.create_task(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "cancel"},
            headers={"Idempotency-Key": "cancel-key"},
        )
    )
    while not started.is_set():
        await asyncio.sleep(0.01)
    request_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request_task
    release.set()
    await asyncio.sleep(0.05)
    record = next(iter(factory.state.idempotency.values()))
    assert record["status"] == "in_progress"
    assert len(factory.state.requests) == 1
    assert len(factory.state.runs) == 0
    assert factory.active_uow_count == 0


def test_required_mode_rejects_missing_key_without_uow():
    app, coordinator, factory = _app(header_mode="required")
    response = asyncio.run(
        _send(app, "POST", "/v1/chat", json={"text": "same"})
    )
    assert response.status_code == 400
    assert response.json()["code"] == "missing_idempotency_key"
    assert coordinator.calls == 0
    assert factory.created_uow_count == 0


def test_transaction_a_failure_returns_503_without_coordinator_execution():
    app, coordinator, factory = _app()
    factory.fail_commit_attempts.add(1)
    response = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "same"},
            headers={"Idempotency-Key": "commit-failure"},
        )
    )
    assert response.status_code == 503
    assert response.json()["code"] == "persistence_unavailable"
    assert "Idempotency-Status" not in response.headers
    assert coordinator.calls == 0


class FixedSecurityService:
    def __init__(self, permissions):
        principal = Principal(
            tenant_id="single_tenant",
            organization_id="default_organization",
            user_id="debug-user",
            roles=("agent_user", "debug_viewer"),
        )
        self.context = SecurityContext(
            principal=principal,
            authorization=AuthorizationDecision(
                allowed=True,
                code="allowed",
                permissions=tuple(sorted(permissions)),
                resource_scopes=(
                    ResourceScope(
                        tenant_id=principal.tenant_id,
                        organization_id=principal.organization_id,
                        resource_type="organization",
                        scope_type="organization",
                    ),
                ),
            ),
            trace=(
                RequestTraceStep(
                    component="authentication",
                    status="succeeded",
                    code="authenticated",
                    summary="Authenticated.",
                ),
            ),
        )

    async def secure(self, raw_headers, required_permissions):
        del raw_headers, required_permissions
        return self.context


def test_replay_incompatible_raw_response_is_rejected_before_uow():
    config = AppConfig(
        runtime_mode="test",
        database={
            "persistence_mode": "fake",
            "idempotency_header_mode": "optional",
        },
        rag_service={"include_raw_response": True},
    )
    coordinator = RecordingCoordinator()
    service = ChatService(
        coordinator=coordinator,  # type: ignore[arg-type]
        context_builder=create_development_context_builder(),
    )
    factory = FakeIdempotentUnitOfWorkFactory(
        database_clock=lambda: datetime.now(timezone.utc)
    )
    app = create_app(
        service=service,
        config=config,
        fake_uow_factory=factory,
        security_service=FixedSecurityService(
            ("chat:invoke", "rag:read", "crm:read", "raw_response:view")
        ),  # type: ignore[arg-type]
    )
    response = asyncio.run(
        _send(
            app,
            "POST",
            "/v1/chat",
            json={"text": "same"},
            headers={"Idempotency-Key": "raw-key"},
        )
    )
    assert response.status_code == 400
    assert response.json()["code"] == "idempotency_not_supported_for_raw_response"
    assert coordinator.calls == 0
    assert factory.created_uow_count == 0


def test_operational_and_preflight_routes_bypass_execution_gateway():
    app, coordinator, factory = _app()
    health = asyncio.run(_send(app, "GET", "/healthz"))
    ready = asyncio.run(_send(app, "GET", "/readyz"))
    options = asyncio.run(_send(app, "OPTIONS", "/v1/chat"))
    assert health.status_code == 200
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert options.status_code == 405
    assert coordinator.calls == 0
    assert factory.created_uow_count == 0


def test_openapi_documents_idempotency_header_without_framework_parsing():
    app, coordinator, factory = _app(header_mode="required")
    response = asyncio.run(_send(app, "GET", "/openapi.json"))
    assert response.status_code == 200
    operation = response.json()["paths"]["/v1/chat"]["post"]
    header = next(
        item
        for item in operation["parameters"]
        if item["name"] == "Idempotency-Key"
    )
    assert header["in"] == "header"
    assert header["required"] is True
    assert coordinator.calls == 0
    assert factory.created_uow_count == 0


class RevisionFailingEngine:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def check_connectivity(self):
        return None

    async def check_revision(self, expected):
        raise DatabaseRevisionError("safe revision failure")


@pytest.mark.asyncio
async def test_partial_startup_failure_disposes_started_database_engine():
    engine = RevisionFailingEngine()
    config = AppConfig(
        database={
            "persistence_mode": "postgres",
            "idempotency_header_mode": "optional",
            "url": "postgresql+asyncpg://user:CANARY_PASSWORD@localhost/test",
        }
    )
    app = create_app(config=config, database_engine_factory=lambda: engine)  # type: ignore[arg-type]
    with pytest.raises(DatabaseRevisionError):
        async with app.router.lifespan_context(app):
            pass
    assert engine.started is True
    assert engine.stopped is True
    assert app.state.readiness is False


class RuntimeFailingEngine:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.connectivity_checks = 0
        self.session_factory = object()

    @property
    def is_started(self):
        return self.started

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def check_connectivity(self):
        self.connectivity_checks += 1
        if self.connectivity_checks > 1:
            from conversation_agent.database.errors import PersistenceConnectionError

            raise PersistenceConnectionError("runtime failure")

    async def check_revision(self, expected):
        return None


@pytest.mark.asyncio
async def test_readiness_becomes_503_after_runtime_database_failure():
    engine = RuntimeFailingEngine()
    config = AppConfig(
        database={
            "persistence_mode": "postgres",
            "idempotency_header_mode": "optional",
            "url": "postgresql+asyncpg://localhost/test",
        }
    )
    app = create_app(config=config, database_engine_factory=lambda: engine)  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        response = await _send(app, "GET", "/readyz")
        health = await _send(app, "GET", "/healthz")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
        assert health.status_code == 200
    assert engine.stopped is True
