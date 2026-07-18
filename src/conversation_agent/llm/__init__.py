"""LLM client contracts."""

from conversation_agent.llm.base import BaseLLMClient, LLMResponse
from conversation_agent.llm.models import ModelProfile, ModelProfileConfig

__all__ = [
    "BaseLLMClient",
    "LLMResponse",
    "ModelProfile",
    "ModelProfileConfig",
    "AnthropicClient",
    "DeepSeekClient",
    "DashScopeClient",
]


def __getattr__(name: str):
    """Lazily expose adapters without creating config import cycles."""
    if name == "AnthropicClient":
        from conversation_agent.llm.anthropic_client import AnthropicClient

        return AnthropicClient
    if name == "DeepSeekClient":
        from conversation_agent.llm.deepseek_client import DeepSeekClient

        return DeepSeekClient
    if name == "DashScopeClient":
        from conversation_agent.llm.dashscope_client import DashScopeClient

        return DashScopeClient
    raise AttributeError(name)
