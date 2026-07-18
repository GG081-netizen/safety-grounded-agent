from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from conversation_agent.config import DatabaseConfig, LLMConfig


pytestmark = pytest.mark.unit


def test_llm_secrets_are_masked_but_available_at_terminal_boundary() -> None:
    cfg = LLMConfig(api_key="sk-test-canary-not-real", auth_token="token-canary")

    assert isinstance(cfg.api_key, SecretStr)
    assert isinstance(cfg.auth_token, SecretStr)
    assert cfg.api_key_value() == "sk-test-canary-not-real"
    assert cfg.auth_token_value() == "token-canary"
    assert "sk-test-canary-not-real" not in repr(cfg)
    assert "token-canary" not in repr(cfg)
    assert "sk-test-canary-not-real" not in str(cfg.model_dump())


def test_database_url_is_masked_but_available_to_database_boundary() -> None:
    value = "postgresql+asyncpg://user:canary-password@localhost/test_db"
    cfg = DatabaseConfig(url=value)

    assert isinstance(cfg.url, SecretStr)
    assert cfg.url_value == value
    assert cfg.is_configured is True
    assert "canary-password" not in repr(cfg)
    assert "canary-password" not in str(cfg.model_dump())


def test_validation_error_hides_secret_input() -> None:
    canary = "postgresql+asyncpg://user:canary-password@localhost/test_db"
    with pytest.raises(ValidationError) as caught:
        DatabaseConfig(url=canary, pool_size=0)

    assert canary not in str(caught.value)
    assert "canary-password" not in str(caught.value)
