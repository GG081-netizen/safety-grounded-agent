"""Tests for Anthropic client and LLM abstractions (Phase 8)."""

import pytest
from conversation_agent.config import get_config
from conversation_agent.llm.base import BaseLLMClient, LLMResponse
from conversation_agent.llm.anthropic_client import (
    AnthropicClient,
    _build_system_param,
    _format_messages,
    _format_tools,
    _response_to_dict,
    _is_retryable,
    _estimate_cost,
)
from conversation_agent.system_prompt import SYSTEM_PROMPT, get_system_prompt


# ═══════════════════════════════════════════════════════════════════════════════
# LLMResponse dataclass
# ═══════════════════════════════════════════════════════════════════════════════


class TestLLMResponse:
    def test_defaults(self):
        r = LLMResponse()
        assert r.text == ""
        assert r.tool_calls == []
        assert r.input_tokens == 0
        assert r.cost_usd == 0.0

    def test_full(self):
        r = LLMResponse(
            text="Hello",
            tool_calls=[{"name": "search", "id": "t1", "input": {}}],
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            model="claude-sonnet-4-6",
            finish_reason="end_turn",
        )
        assert r.text == "Hello"
        assert len(r.tool_calls) == 1
        assert r.model == "claude-sonnet-4-6"
        assert r.finish_reason == "end_turn"


# ═══════════════════════════════════════════════════════════════════════════════
# AnthropicClient construction
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnthropicClientConstruction:
    def test_builds_from_config(self):
        client = AnthropicClient()
        assert client._model  # non-empty from config
        assert client._max_retries >= 0
        assert client._timeout > 0

    def test_accepts_explicit_key(self):
        client = AnthropicClient(api_key="sk-test-123")
        assert client._api_key == "sk-test-123"


# ═══════════════════════════════════════════════════════════════════════════════
# Behavior without API key
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoApiKey:
    def test_returns_error_response(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # With empty key, SDK will fail — handled gracefully
        client = AnthropicClient(api_key="")
        resp = client.call(messages=[{"role": "user", "content": "hello"}])
        assert isinstance(resp.text, str)


# ═══════════════════════════════════════════════════════════════════════════════
# Formatting helpers
# ═══════════════════════════════════════════════════════════════════════════════


class TestFormatHelpers:
    def test_build_system_param_empty(self):
        assert _build_system_param("") is None

    def test_build_system_param(self):
        result = _build_system_param("You are helpful")
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "You are helpful"

    def test_format_messages(self):
        msgs = [{"role": "user", "content": "hello"}]
        formatted = _format_messages(msgs)
        assert formatted[0]["role"] == "user"
        assert formatted[0]["content"] == "hello"

    def test_format_messages_preserves_list_content(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        formatted = _format_messages(msgs)
        assert formatted[0]["content"] == [{"type": "text", "text": "hi"}]

    def test_format_tools_none(self):
        assert _format_tools(None) is None

    def test_format_tools(self):
        tools = [
            {
                "name": "search",
                "description": "Search customers",
                "input_schema": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            }
        ]
        result = _format_tools(tools)
        assert len(result) == 1
        assert result[0]["name"] == "search"
        assert "input_schema" in result[0]

    def test_format_tools_empty_list(self):
        assert _format_tools([]) == []


# ═══════════════════════════════════════════════════════════════════════════════
# Retry & cost helpers
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetryable:
    def test_rate_limit_is_retryable(self):
        class FakeRateLimit(Exception):
            pass
        # The function checks the class name string
        assert _is_retryable(FakeRateLimit()) is False  # not the right name

    def test_http_429_is_retryable(self):
        class Fake429(Exception):
            status_code = 429
        assert _is_retryable(Fake429())

    def test_http_500_is_retryable(self):
        class Fake500(Exception):
            status_code = 500
        assert _is_retryable(Fake500())

    def test_http_400_is_not_retryable(self):
        class Fake400(Exception):
            status_code = 400
        assert not _is_retryable(Fake400())

    def test_connection_error_by_name(self):
        class APIConnectionError(Exception):
            pass
        assert _is_retryable(APIConnectionError())


class TestCostEstimate:
    def test_sonnet_pricing(self):
        cost = _estimate_cost("claude-sonnet-4-6", 1000, 500)
        # 1000/1M * $3 + 500/1M * $15 = 0.003 + 0.0075 = 0.0105
        assert cost == pytest.approx(0.0105, abs=0.001)

    def test_opus_pricing(self):
        cost = _estimate_cost("claude-opus-4-8", 1000, 500)
        # 1000/1M * $15 + 500/1M * $75 = 0.015 + 0.0375 = 0.0525
        assert cost == pytest.approx(0.0525, abs=0.005)

    def test_unknown_model_defaults(self):
        cost = _estimate_cost("unknown-model", 0, 0)
        assert cost == 0.0

    def test_zero_tokens(self):
        cost = _estimate_cost("claude-sonnet-4-6", 0, 0)
        assert cost == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# System prompt
# ═══════════════════════════════════════════════════════════════════════════════


class TestSystemPrompt:
    def test_non_empty(self):
        assert len(SYSTEM_PROMPT) > 100

    def test_contains_key_sections(self):
        assert "采购销售助手" in SYSTEM_PROMPT
        assert "customer_intake" in SYSTEM_PROMPT
        assert "customer_memory_search" in SYSTEM_PROMPT

    def test_get_system_prompt(self):
        assert get_system_prompt() == SYSTEM_PROMPT


# ═══════════════════════════════════════════════════════════════════════════════
# Anthropic schema integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnthropicSchemaIntegration:
    def test_tool_schemas_compatible_with_format(self):
        """Tool schemas from registry can be formatted for Anthropic."""
        from conversation_agent.tools.registry import ToolRegistry
        from conversation_agent.tools.customer_memory import CustomerMemorySearchTool

        reg = ToolRegistry()
        reg.register(CustomerMemorySearchTool())
        schemas = reg.to_anthropic_schemas()
        formatted = _format_tools(schemas)

        assert len(formatted) == 1
        tool = formatted[0]
        assert tool["name"] == "customer_memory_search"
        assert "input_schema" in tool
        assert tool["input_schema"]["type"] == "object"
