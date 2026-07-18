"""Comprehensive tests for MockAgent (Phase 7)."""

import uuid
from pathlib import Path

import pytest

from conversation_agent.config import reset_config, get_config
from conversation_agent.agent import Agent, MockAgent, RealAgent
from conversation_agent.sales.intent_router import IntentRouter
from conversation_agent.sales.models import (
    Intent,
    IntentResult,
    Interaction,
    InteractionMetadata,
    ToolResult,
)
from conversation_agent.tools.registry import ToolRegistry
from conversation_agent.tools.base import BaseTool


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    # Clear API keys so tests default to MockAgent
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    reset_config()
    cfg = get_config()
    cfg.storage.data_dir = tmp_path / "data"
    yield
    reset_config()


@pytest.fixture
def agent():
    return MockAgent()


# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline
# ═══════════════════════════════════════════════════════════════════════════════


class TestMockAgentPipeline:
    def test_returns_interaction(self, agent):
        result = agent.run("联想需要采购100台笔记本")
        assert isinstance(result, Interaction)
        assert result.user_input == "联想需要采购100台笔记本"
        assert result.session_id  # non-empty

    def test_has_intent_result(self, agent):
        result = agent.run("帮我查一下华为")
        assert result.intent_result is not None
        assert isinstance(result.intent_result, IntentResult)

    def test_has_tools_called(self, agent):
        result = agent.run("新客户字节跳动需要服务器")
        assert len(result.tools_called) > 0
        for tr in result.tools_called:
            assert isinstance(tr, ToolResult)

    def test_has_agent_response(self, agent):
        result = agent.run("这是会议纪要...")
        assert result.agent_response
        assert isinstance(result.agent_response, str)

    def test_has_metadata(self, agent):
        result = agent.run("写邮件给王总")
        meta = result.metadata
        assert isinstance(meta, InteractionMetadata)
        assert meta.session_id == result.session_id
        assert meta.llm_calls == 0  # mock: no LLM
        assert meta.latency_ms >= 0

    def test_custom_session_id(self, agent):
        result = agent.run("测试", session_id="my-session-42")
        assert result.session_id == "my-session-42"
        assert result.metadata.session_id == "my-session-42"


# ═══════════════════════════════════════════════════════════════════════════════
# Intent → tool mapping
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntentToolMapping:
    def test_intake_calls_search_update_score(self, agent):
        result = agent.run("新客户采购100台电脑")
        assert result.intent_result.intent == Intent.CUSTOMER_INTAKE
        names = [t.tool_name for t in result.tools_called]
        assert "customer_memory_search" in names
        assert "customer_memory_update" in names
        assert "sales_score_calculator" in names

    def test_meeting_note_calls_update_score(self, agent):
        result = agent.run("会议纪要：讨论了采购")
        assert result.intent_result.intent == Intent.MEETING_NOTE
        names = [t.tool_name for t in result.tools_called]
        assert "customer_memory_update" in names
        assert "sales_score_calculator" in names

    def test_query_calls_search(self, agent):
        result = agent.run("查一下联想集团的进展")
        assert result.intent_result.intent == Intent.QUERY
        names = [t.tool_name for t in result.tools_called]
        assert "customer_memory_search" in names

    def test_email_calls_search(self, agent):
        result = agent.run("写邮件给客户")
        assert result.intent_result.intent == Intent.EMAIL_DRAFT
        names = [t.tool_name for t in result.tools_called]
        assert "customer_memory_search" in names


# ═══════════════════════════════════════════════════════════════════════════════
# Batch
# ═══════════════════════════════════════════════════════════════════════════════


class TestBatch:
    def test_run_batch(self, agent):
        results = agent.run_batch([
            "新客户采购",
            "查一下华为",
            "会议纪要讨论",
        ])
        assert len(results) == 3
        for r in results:
            assert isinstance(r, Interaction)


# ═══════════════════════════════════════════════════════════════════════════════
# Custom injection (for Phase 8 extensibility)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCustomInjection:
    def test_custom_router(self):
        """IntentRouter can be injected for testing or LLM upgrade."""
        class FixedRouter(IntentRouter):
            def route(self, text):
                return IntentResult(intent=Intent.QUERY, confidence=1.0, reasoning="fixed")

        agent = MockAgent(router=FixedRouter())
        result = agent.run("anything")
        assert result.intent_result.intent == Intent.QUERY
        assert result.intent_result.confidence == 1.0

    def test_custom_registry(self):
        """ToolRegistry can be injected — tools resolve from injected registry."""

        call_log = []

        class SpySearchTool(BaseTool):
            name = "customer_memory_search"
            description = "spy search"
            parameters_schema = {}
            def execute(self, **kwargs):
                call_log.append("search_called")
                return ToolResult(success=True, tool_name=self.name, summary="spied")

        reg = ToolRegistry()
        reg.register(SpySearchTool())
        # Note: the intent map for QUERY calls customer_memory_search,
        # which now resolves to our spy in the injected registry.

        class FixedRouter(IntentRouter):
            def route(self, text):
                return IntentResult(intent=Intent.QUERY, confidence=1.0)

        agent = MockAgent(router=FixedRouter(), registry=reg)
        result = agent.run("test")
        assert "search_called" in call_log
        assert result.tools_called[0].summary == "spied"


# ═══════════════════════════════════════════════════════════════════════════════
# RealAgent (V1.1)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRealAgent:
    def test_construction(self):
        agent = RealAgent()
        assert agent._system
        assert agent._registry.list_tools()

    def test_has_api_key_false_by_default(self):
        agent = RealAgent()
        assert not agent.has_api_key

    def test_has_api_key_true(self):
        from conversation_agent.llm.anthropic_client import AnthropicClient
        llm = AnthropicClient(api_key="sk-test")
        agent = RealAgent(llm=llm)
        assert agent.has_api_key

    def test_run_without_key_returns_graceful_error(self):
        agent = RealAgent()
        result = agent.run("测试")
        assert isinstance(result, Interaction)
        assert "失败" in result.agent_response or "错误" in result.agent_response or "API" in result.agent_response

    def test_run_returns_interaction_structure(self):
        agent = RealAgent()
        result = agent.run("查一下客户")
        assert result.session_id
        assert result.user_input == "查一下客户"
        assert isinstance(result.metadata, InteractionMetadata)

    def test_run_batch(self):
        agent = RealAgent()
        results = agent.run_batch(["测试1", "测试2"])
        assert len(results) == 2
        for r in results:
            assert isinstance(r, Interaction)


class TestAgentFactory:
    def test_returns_mock_when_no_key(self):
        a = Agent()
        assert isinstance(a, MockAgent)

    def test_returns_real_when_key_set(self):
        import os
        old = os.environ.get("ANTHROPIC_API_KEY", "")
        old_provider = os.environ.get("CONVAGENT_LLM_PROVIDER", "")
        os.environ["CONVAGENT_LLM_PROVIDER"] = "anthropic"
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        reset_config()

        a = Agent()
        assert isinstance(a, RealAgent)

        if old:
            os.environ["ANTHROPIC_API_KEY"] = old
        else:
            del os.environ["ANTHROPIC_API_KEY"]
        if old_provider:
            os.environ["CONVAGENT_LLM_PROVIDER"] = old_provider
        else:
            del os.environ["CONVAGENT_LLM_PROVIDER"]
        reset_config()
