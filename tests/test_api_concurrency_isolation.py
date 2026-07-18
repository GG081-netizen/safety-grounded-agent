from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from conversation_agent.api.app import create_app
from conversation_agent.application.service import ChatService
from conversation_agent.orchestration.coordinator import Coordinator
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.rag.models import RagResult
from conversation_agent.runtime.builder import create_development_context_builder


pytestmark = pytest.mark.unit


class SafePolicy:
    def decide(self, text: str) -> PolicyDecision:
        return PolicyDecision(status="SAFE", reason="controlled", confidence=1.0)

    def rejection_message(self, decision: PolicyDecision) -> str:
        return ""


class ApiRecordingRag:
    def __init__(self) -> None:
        self.barrier = threading.Barrier(2)
        self.lock = threading.Lock()
        self.calls: list[dict[str, object]] = []

    def query(self, question, *, trace_id=None, metadata=None):
        self.barrier.wait(timeout=3)
        with self.lock:
            self.calls.append(
                {"question": question, "trace_id": trace_id, "metadata": dict(metadata or {})}
            )
        return RagResult(answer=question, confidence=0.8, provider="external")


def test_api_concurrent_requests_propagate_distinct_trusted_metadata() -> None:
    rag = ApiRecordingRag()
    coordinator = Coordinator(policy=SafePolicy(), rag_client=rag)
    service = ChatService(
        coordinator=coordinator,
        context_builder=create_development_context_builder(),
    )
    app = create_app(service=service)

    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await asyncio.gather(
                client.post("/v1/qa", json={"text": "request-a", "session_id": "shared"}),
                client.post("/v1/qa", json={"text": "request-b", "session_id": "shared"}),
            )

    first, second = asyncio.run(send())

    assert first.status_code == second.status_code == 200
    calls = {call["question"]: call for call in rag.calls}
    for response, question in ((first, "request-a"), (second, "request-b")):
        payload = response.json()
        call = calls[question]
        assert response.headers["X-Request-ID"] == payload["request_id"]
        assert response.headers["X-Trace-ID"] == payload["trace_id"]
        assert call["trace_id"] == payload["trace_id"]
        assert call["metadata"]["request_id"] == payload["request_id"]
        assert call["metadata"]["session_id"] == "shared"
