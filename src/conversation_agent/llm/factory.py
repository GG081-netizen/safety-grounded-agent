"""Create the configured runtime LLM client."""

from __future__ import annotations

from conversation_agent.config import LLMConfig, get_config
from conversation_agent.llm.anthropic_client import AnthropicClient
from conversation_agent.llm.base import BaseLLMClient
from conversation_agent.llm.dashscope_client import DashScopeClient
from conversation_agent.llm.deepseek_client import DeepSeekClient
from conversation_agent.llm.errors import LLMConfigurationError
from conversation_agent.llm.models import ModelProfile


def create_llm_client(
    profile: ModelProfile = ModelProfile.STANDARD,
    *,
    config: LLMConfig | None = None,
) -> BaseLLMClient:
    app_config = get_config()
    cfg = config or app_config.llm
    profile_config = cfg.model_registry.resolve_runtime(profile)
    if profile_config.provider == "dashscope":
        client: BaseLLMClient = DashScopeClient(
            api_key=cfg.api_key_value(),
            base_url=cfg.base_url,
            model_config=profile_config,
        )
    elif profile_config.provider == "deepseek":
        client = DeepSeekClient(api_key=cfg.api_key_value())
    elif profile_config.provider == "anthropic":
        client = AnthropicClient(
            api_key=cfg.api_key_value() or cfg.auth_token_value()
        )
    else:
        raise LLMConfigurationError(
            f"Unsupported runtime LLM provider: {profile_config.provider}"
        )

    if app_config.runtime_mode in {"test", "production"} and not client.is_configured:
        raise LLMConfigurationError(
            f"LLM provider {profile_config.provider} is not configured"
        )
    return client
