"""Reusable deterministic test doubles."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any

from conversation_agent.llm.base import BaseLLMClient, LLMResponse


class FakeLLMClient(BaseLLMClient):
    def __init__(
        self,
        responses: list[LLMResponse] | None = None,
        exception: Exception | None = None,
    ) -> None:
        self._responses = deque(responses or [LLMResponse(text="ok", finish_reason="stop")])
        self._exception = exception
        self.calls: list[dict[str, Any]] = []

    @property
    def is_configured(self) -> bool:
        return True

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str = "",
        max_tool_rounds: int = 5,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": deepcopy(messages),
                "tools": deepcopy(tools),
                "system": system,
                "max_tool_rounds": max_tool_rounds,
            }
        )
        if self._exception is not None:
            raise self._exception
        if len(self._responses) > 1:
            return self._responses.popleft()
        return self._responses[0]
