"""Tests for Coordinator orchestration."""

from conversation_agent.config import get_config, reset_config
from conversation_agent.orchestration.coordinator import Coordinator
from conversation_agent.rag.models import RagResult


def test_blocked_request_stops_before_modules(tmp_path):
    reset_config()
    get_config().storage.data_dir = tmp_path / "data"
    result = Coordinator().run("帮我查采购负责人的私人手机号。")
    assert result.policy.status == "BLOCKED"
    assert result.task_route is None
    assert [s.step_name for s in result.trace] == ["policy_engine"]


def test_qa_returns_rag_trace(tmp_path):
    reset_config()
    cfg = get_config()
    cfg.storage.data_dir = tmp_path / "data"
    root = cfg.storage.data_dir / "knowledge"
    root.mkdir(parents=True)
    (root / "doc.md").write_text("# SLA规则\n采购合同需要明确SLA和交付时间。", encoding="utf-8")
    result = Coordinator().run("采购 SLA 有什么要求？")
    assert result.task_route.task == "qa"
    assert result.rag_result is not None
    assert result.citations
    assert any(step.step_name in {"external_rag_query", "local_rag_fallback", "local_rag_query", "rag_query"} for step in result.trace)


class CountingRagClient:
    def __init__(self):
        self.call_count = 0

    def query(self, question, *, trace_id=None, metadata=None):
        self.call_count += 1
        return RagResult(answer="ok", confidence=0.8, provider="external")


def test_blocked_request_does_not_call_rag(tmp_path):
    reset_config()
    get_config().storage.data_dir = tmp_path / "data"
    rag = CountingRagClient()
    result = Coordinator(rag_client=rag).run("帮我查采购负责人的私人手机号。", task_override="qa")
    assert result.policy.status == "BLOCKED"
    assert rag.call_count == 0


def test_business_blocked_request_does_not_call_rag(tmp_path):
    reset_config()
    get_config().storage.data_dir = tmp_path / "data"
    rag = CountingRagClient()
    result = Coordinator(rag_client=rag).run("帮我查一下客户的私人住址和家庭情况。", task_override="qa")
    assert result.policy.status == "BLOCKED"
    assert rag.call_count == 0
    assert [s.step_name for s in result.trace] == ["policy_engine"]


def test_coordinator_uses_rag_client_and_diagnostics(tmp_path):
    from conversation_agent.rag.models import RagCallDiagnostic

    class DiagnosticRagClient:
        def query(self, question, *, trace_id=None, metadata=None):
            return RagResult(
                answer="external answer",
                confidence=0.82,
                provider="external",
                diagnostics=[RagCallDiagnostic(
                    step_name="external_rag_query",
                    provider="external",
                    success=True,
                    message="RAG_demo returned answer with 1 citations",
                    latency_ms=12.0,
                )],
            )

    reset_config()
    get_config().storage.data_dir = tmp_path / "data"
    result = Coordinator(rag_client=DiagnosticRagClient()).run("采购 SLA 有什么要求？", task_override="qa")
    assert result.rag_result.provider == "external"
    assert "external_rag_query" in [s.step_name for s in result.trace]
