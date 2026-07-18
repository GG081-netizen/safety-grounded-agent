import asyncio

import httpx
import pytest

from conversation_agent.api.app import create_app
from conversation_agent.application.service import ChatService
from conversation_agent.orchestration.models import OrchestrationResult
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.rag.models import RagResult
from conversation_agent.runtime.builder import create_development_context_builder


pytestmark = pytest.mark.unit


class RecordingCoordinator:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict] = []
        self.raises = raises

    def run(
        self,
        user_input,
        session_id=None,
        task_override=None,
        *,
        request_metadata=None,
    ):
        self.calls.append(
            {
                "user_input": user_input,
                "session_id": session_id,
                "task_override": task_override,
                "request_metadata": request_metadata,
            }
        )
        if self.raises:
            raise RuntimeError("database password must not leak")
        rag_result = RagResult(
            answer="grounded answer",
            confidence=0.8,
            provider="external",
            raw_response={"debug_trace": "private"},
        )
        return OrchestrationResult(
            session_id=session_id,
            user_input=user_input,
            policy=PolicyDecision(status="SAFE", confidence=1.0),
            final_response="grounded answer",
            rag_result=rag_result,
            confidence=0.8,
        )


def _app(coordinator: RecordingCoordinator):
    ids = iter(f"api-id-{index}" for index in range(100))
    service = ChatService(
        coordinator=coordinator,  # type: ignore[arg-type]
        context_builder=create_development_context_builder(),
    )
    return create_app(service=service, id_factory=lambda: next(ids))


def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_healthz_is_process_liveness_only():
    response = _request(_app(RecordingCoordinator()), "GET", "/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "conversation-agent",
        "version": "0.1.0",
    }


def test_chat_returns_public_envelope_and_request_headers():
    coordinator = RecordingCoordinator()
    response = _request(
        _app(coordinator),
        "POST",
        "/v1/chat",
        json={"text": "查询采购 SLA", "session_id": "session-client"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["request_id"] == response.headers["X-Request-ID"]
    assert data["trace_id"] == response.headers["X-Trace-ID"]
    assert data["session_id"] == "session-client"
    assert data["result"]["session_id"] == "session-client"
    assert "raw_response" not in data["result"]["rag_result"]
    assert coordinator.calls[0]["task_override"] is None


def test_qa_endpoint_forces_qa_task():
    coordinator = RecordingCoordinator()
    response = _request(
        _app(coordinator),
        "POST",
        "/v1/qa",
        json={"text": "查询采购 SLA", "task_override": "email_draft"},
    )

    assert response.status_code == 200
    assert coordinator.calls[0]["task_override"] == "qa"


def test_request_body_cannot_supply_trusted_identity_fields():
    coordinator = RecordingCoordinator()
    response = _request(
        _app(coordinator),
        "POST",
        "/v1/chat",
        json={
            "text": "查询客户",
            "tenant_id": "attacker-tenant",
            "roles": ["system_admin"],
        },
    )

    assert response.status_code == 422
    data = response.json()
    assert data["code"] == "request_validation_error"
    assert data["request_id"] == response.headers["X-Request-ID"]
    assert {item["field"] for item in data["details"]} == {
        "body.tenant_id",
        "body.roles",
    }
    assert coordinator.calls == []


def test_blank_idempotency_header_uses_validation_error_contract():
    coordinator = RecordingCoordinator()
    response = _request(
        _app(coordinator),
        "POST",
        "/v1/chat",
        json={"text": "查询客户"},
        headers={"Idempotency-Key": ""},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_idempotency_key"
    assert coordinator.calls == []


def test_null_optional_rejects_present_idempotency_key():
    coordinator = RecordingCoordinator()
    response = _request(
        _app(coordinator),
        "POST",
        "/v1/chat",
        json={"text": "查询客户"},
        headers={"Idempotency-Key": "idem-1"},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "idempotency_unavailable"
    assert "Idempotency-Status" not in response.headers
    assert coordinator.calls == []


def test_application_error_uses_public_error_contract():
    response = _request(
        _app(RecordingCoordinator(raises=True)),
        "POST",
        "/v1/chat",
        json={"text": "query"},
    )

    assert response.status_code == 500
    data = response.json()
    assert data["code"] == "application_execution_error"
    assert "database password" not in data["message"]
    assert data["request_id"] == response.headers["X-Request-ID"]
