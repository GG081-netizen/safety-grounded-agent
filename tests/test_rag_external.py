"""Tests for external RAG client and fallback behavior."""

import json

import httpx
import pytest

from conversation_agent.rag.external_client import ExternalRagClient
from conversation_agent.rag.factory import FallbackRagClient
from conversation_agent.rag.local_client import LocalKeywordRagClient
from conversation_agent.rag.models import RagResult


class DummyResponse:
    def __init__(self, data=None, status_code=200, json_error=None):
        self._data = data
        self.status_code = status_code
        self._json_error = json_error

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://rag/query")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._data


def test_external_success_maps_answer_citations_and_raw(monkeypatch):
    def fake_post(url, json, timeout):
        return DummyResponse({
            "answer": "答案",
            "confidence": 0.82,
            "citations": [{"source_id": "S1", "title": "Doc", "snippet": "证据", "rerank_score": 0.7}],
            "debug_trace": {"k": 3},
        })

    monkeypatch.setattr(httpx, "post", fake_post)
    result = ExternalRagClient("http://rag", timeout=1).query("问题")
    assert result.provider == "external"
    assert result.answer == "答案"
    assert result.confidence == 0.82
    assert result.evidence[0].source_id == "S1"
    assert result.raw_response["debug_trace"] == {"k": 3}
    assert result.diagnostics[0].step_name == "external_rag_query"


def test_external_string_citation(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: DummyResponse({"answer": "A", "citations": ["DOC_1"]}))
    result = ExternalRagClient("http://rag").query("Q")
    assert result.sources[0]["source_id"] == "DOC_1"


@pytest.mark.parametrize("exc,error_type", [
    (httpx.TimeoutException("slow"), "timeout"),
    (httpx.ConnectError("down"), "connection_error"),
])
def test_external_request_errors(monkeypatch, exc, error_type):
    def fake_post(*args, **kwargs):
        raise exc
    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(Exception) as err:
        ExternalRagClient("http://rag", timeout=1).query("Q")
    assert getattr(err.value, "error_type") == error_type


def test_external_http_error(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: DummyResponse({}, status_code=500))
    with pytest.raises(Exception) as err:
        ExternalRagClient("http://rag").query("Q")
    assert getattr(err.value, "error_type") == "http_error"


def test_external_invalid_json(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: DummyResponse(json_error=ValueError("bad")))
    with pytest.raises(Exception) as err:
        ExternalRagClient("http://rag").query("Q")
    assert getattr(err.value, "error_type") == "invalid_json"


def test_external_missing_answer(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: DummyResponse({"citations": []}))
    with pytest.raises(Exception) as err:
        ExternalRagClient("http://rag").query("Q")
    assert getattr(err.value, "error_type") == "missing_answer"


class FailingClient:
    def query(self, question, *, trace_id=None, metadata=None):
        from conversation_agent.rag.base import RagTimeoutError
        raise RagTimeoutError("timeout")


class StaticLocalClient:
    def query(self, question, *, trace_id=None, metadata=None):
        from conversation_agent.rag.models import RagCallDiagnostic
        return RagResult(
            answer="local",
            confidence=0.9,
            provider="local",
            diagnostics=[RagCallDiagnostic(step_name="local_rag_query", provider="local", success=True)],
        )


def test_fallback_caps_confidence_and_records_diagnostics():
    result = FallbackRagClient(FailingClient(), StaticLocalClient(), fallback_enabled=True).query("Q")
    assert result.provider == "fallback"
    assert result.confidence == 0.55
    assert "External RAG unavailable" in result.warnings[0]
    assert [d.step_name for d in result.diagnostics] == ["external_rag_query", "local_rag_fallback"]


def test_no_fallback_returns_none_provider():
    result = FallbackRagClient(FailingClient(), None, fallback_enabled=False).query("Q")
    assert result.provider == "none"
    assert result.confidence <= 0.2
    assert result.diagnostics[0].error_type == "timeout"


def test_public_dict_hides_raw_response_by_default():
    result = RagResult(answer="A", raw_response={"debug_trace": {"secret": True}})
    assert "raw_response" not in result.to_public_dict()
    assert "raw_response" in result.to_public_dict(include_raw_response=True)
