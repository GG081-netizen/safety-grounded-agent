"""DeepSeek API client — OpenAI-compatible chat completions.

Uses the openai SDK pointed at api.deepseek.com.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from conversation_agent.config import get_config
from conversation_agent.llm.base import (
    BaseLLMClient,
    FINISH_MISSING_API_KEY,
    LLMResponse,
)

logger = logging.getLogger(__name__)

# DeepSeek pricing (USD per 1M tokens) — approximate
_DEEPSEEK_PRICING: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.14, 0.28),
}

_RETRYABLE_STATUSES = {429, 500, 502, 503}


class DeepSeekClient(BaseLLMClient):
    """DeepSeek chat-completions client (OpenAI-compatible protocol).

    Usage::

        client = DeepSeekClient()
        resp = client.call(
            messages=[{"role": "user", "content": "..."}],
            tools=[...],
            system="You are helpful.",
        )
    """

    def __init__(self, api_key: str | None = None) -> None:
        cfg = get_config().llm
        self._api_key = api_key or _deepseek_key()
        self._model = cfg.model if cfg.model.startswith("deepseek") else "deepseek-chat"
        self._max_retries = cfg.max_retries
        self._retry_base_delay = cfg.retry_base_delay
        self._timeout = cfg.request_timeout
        self._max_tokens = cfg.max_tokens
        self._temperature = cfg.temperature

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str = "",
        max_tool_rounds: int = 1,
    ) -> LLMResponse:
        if not self.is_configured:
            return LLMResponse(
                text="DeepSeek API key is not configured.",
                model=self._model,
                finish_reason=FINISH_MISSING_API_KEY,
            )

        api_messages = _build_openai_messages(messages, system)
        api_tools = _to_openai_tools(tools)

        input_tokens_total = 0
        output_tokens_total = 0

        for _round in range(max_tool_rounds):
            params: dict[str, Any] = {
                "model": self._model,
                "messages": api_messages,
                "max_tokens": self._max_tokens,
                "temperature": self._temperature,
            }
            if api_tools:
                params["tools"] = api_tools

            result = self._api_call_with_retry(params)

            usage = result.get("usage", {})
            input_tokens_total += usage.get("prompt_tokens", 0)
            output_tokens_total += usage.get("completion_tokens", 0)

            choice = result.get("choices", [{}])[0] if result.get("choices") else {}
            msg = choice.get("message", {})
            finish = choice.get("finish_reason", "")

            # Check for tool calls
            tool_calls = msg.get("tool_calls") or []
            if tool_calls and _round < max_tool_rounds - 1:
                return LLMResponse(
                    text=msg.get("content") or "",
                    tool_calls=[
                        {
                            "id": tc["id"],
                            "name": tc["function"]["name"],
                            "input": json.loads(tc["function"]["arguments"]),
                        }
                        for tc in tool_calls
                    ],
                    input_tokens=input_tokens_total,
                    output_tokens=output_tokens_total,
                    cost_usd=_deepseek_cost(self._model, input_tokens_total, output_tokens_total),
                    model=self._model,
                    finish_reason="tool_calls",
                )

            # Text response
            return LLMResponse(
                text=msg.get("content") or "",
                tool_calls=[],
                input_tokens=input_tokens_total,
                output_tokens=output_tokens_total,
                cost_usd=_deepseek_cost(self._model, input_tokens_total, output_tokens_total),
                model=self._model,
                finish_reason=finish,
            )

        # Exhausted rounds
        return LLMResponse(
            text="已达到最大工具调用轮次",
            input_tokens=input_tokens_total,
            output_tokens=output_tokens_total,
            cost_usd=_deepseek_cost(self._model, input_tokens_total, output_tokens_total),
            model=self._model,
            finish_reason="tool_calls",
        )

    def _api_call_with_retry(self, params: dict) -> dict:
        last_exc = None
        for attempt in range(self._max_retries + 1):
            try:
                from openai import OpenAI
                client = OpenAI(
                    api_key=self._api_key,
                    base_url="https://api.deepseek.com",
                    timeout=self._timeout,
                )
                resp = client.chat.completions.create(**params)
                return _openai_response_to_dict(resp)
            except Exception as exc:
                last_exc = exc
                if attempt < self._max_retries and _is_retryable(exc):
                    delay = self._retry_base_delay * (2 ** attempt)
                    logger.warning(
                        "DeepSeek API call failed; retrying",
                        extra={
                            "provider": "deepseek",
                            "attempt": attempt + 1,
                            "error_type": type(exc).__name__,
                            "retryable": True,
                        },
                    )
                    time.sleep(delay)
                else:
                    break

        logger.error(
            "DeepSeek API call failed",
            extra={
                "provider": "deepseek",
                "attempt": self._max_retries + 1,
                "error_type": type(last_exc).__name__ if last_exc else "unknown",
                "retryable": False,
            },
        )
        return {
            "choices": [{"message": {"content": "LLM 服务暂时不可用。", "tool_calls": []}, "finish_reason": "error"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _deepseek_key() -> str:
    import os
    return os.getenv("DEEPSEEK_API_KEY", "")


def _build_openai_messages(messages: list[dict], system: str) -> list[dict]:
    result: list[dict] = []
    if system:
        result.append({"role": "system", "content": system})
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, str):
            result.append({"role": role, "content": content})
        elif isinstance(content, list):
            # Handle tool_use / tool_result blocks → convert to OpenAI format
            converted = _convert_blocks_to_openai(role, content)
            result.append(converted)
    return result


def _convert_blocks_to_openai(role: str, blocks: list[dict]) -> dict:
    """Convert Anthropic-style content blocks to OpenAI message format."""
    text_parts = []
    tool_calls = []

    for b in blocks:
        if b.get("type") == "text":
            text_parts.append(b["text"])
        elif b.get("type") == "tool_use":
            tool_calls.append({
                "id": b["id"],
                "type": "function",
                "function": {
                    "name": b["name"],
                    "arguments": json.dumps(b.get("input", {}), ensure_ascii=False),
                },
            })
        elif b.get("type") == "tool_result":
            # Already handled — tool results are passed as role="tool" messages
            pass

    if role == "assistant" and tool_calls:
        return {
            "role": "assistant",
            "content": "\n".join(text_parts) or None,
            "tool_calls": tool_calls,
        }
    if role == "tool":
        # Each tool_result block becomes a separate message
        for b in blocks:
            if b.get("type") == "tool_result":
                return {
                    "role": "tool",
                    "tool_call_id": b["tool_use_id"],
                    "content": b.get("content", ""),
                }

    return {"role": role, "content": "\n".join(text_parts)}


def _to_openai_tools(tools: list[dict] | None) -> list[dict] | None:
    if tools is None:
        return None
    if len(tools) == 0:
        return None  # DeepSeek rejects empty tools list
    result = []
    for t in tools:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}, "required": []}),
            },
        })
    return result


def _openai_response_to_dict(resp) -> dict:
    """Convert OpenAI SDK response to a plain dict."""
    choices = []
    for c in getattr(resp, "choices", []):
        msg = c.message
        tc_list = []
        for tc in getattr(msg, "tool_calls", []) or []:
            tc_list.append({
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            })
        choices.append({
            "message": {
                "role": msg.role,
                "content": msg.content,
                "tool_calls": tc_list or None,
            },
            "finish_reason": c.finish_reason,
        })

    usage = {}
    if resp.usage:
        usage = {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "total_tokens": resp.usage.total_tokens,
        }

    return {"choices": choices, "usage": usage}


def _is_retryable(exc: Exception) -> bool:
    name = type(exc).__name__
    if "RateLimit" in name or "Timeout" in name or "Connection" in name:
        return True
    status = getattr(exc, "status_code", None)
    return status in _RETRYABLE_STATUSES


def _deepseek_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = _DEEPSEEK_PRICING.get(model, (0.14, 0.28))
    return round(
        (input_tokens / 1_000_000) * prices[0]
        + (output_tokens / 1_000_000) * prices[1],
        6,
    )
    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self._api_key.strip())
