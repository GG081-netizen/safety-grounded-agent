"""Abstract LLM client interface — Phase 8."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


FINISH_STOP = "stop"
FINISH_TOOL_USE = "tool_use"
FINISH_TOOL_CALLS = "tool_calls"
FINISH_MISSING_API_KEY = "missing_api_key"
FINISH_UNSUPPORTED_CAPABILITY = "unsupported_capability"
FINISH_INVALID_TOOL_SCHEMA = "invalid_tool_schema"
FINISH_INVALID_TOOL_CALL = "invalid_tool_call"
FINISH_HTTP_ERROR = "http_error"
FINISH_TIMEOUT = "timeout"
FINISH_INVALID_RESPONSE = "invalid_response"

TERMINAL_CLIENT_FAILURES = frozenset(
    {
        FINISH_MISSING_API_KEY,
        FINISH_UNSUPPORTED_CAPABILITY,
        FINISH_INVALID_TOOL_SCHEMA,
        FINISH_INVALID_TOOL_CALL,
        FINISH_HTTP_ERROR,
        FINISH_TIMEOUT,
        FINISH_INVALID_RESPONSE,
    }
)


@dataclass
class LLMResponse:
    """Structured result from an LLM call."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    finish_reason: str = ""


class BaseLLMClient(ABC):
    """Abstract LLM client — call with messages + optional tools."""

    @property
    def is_configured(self) -> bool:
        """Return whether this adapter has usable credentials/configuration."""
        return False

    @abstractmethod
    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str = "",
        max_tool_rounds: int = 5,
    ) -> LLMResponse:
        """Send messages to the LLM and return a structured response.

        If tools are provided and the model returns tool_use blocks, the
        caller is responsible for executing the tools and calling again
        with the results (the agent loop).
        """
        ...


def is_terminal_client_failure(response: LLMResponse) -> bool:
    """Return whether a client-level failure must stop the agent loop."""
    return response.finish_reason in TERMINAL_CLIENT_FAILURES
