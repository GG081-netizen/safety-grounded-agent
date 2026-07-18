"""Anthropic Messages API client with retry, timeout, and token tracking.

Phase 8: Production LLM integration.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from conversation_agent.config import get_config
from conversation_agent.llm.base import BaseLLMClient, LLMResponse
from conversation_agent.llm.base import FINISH_MISSING_API_KEY

logger = logging.getLogger(__name__)

# Approximate per-model pricing (USD per 1M tokens)
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (input_price_per_MTok, output_price_per_MTok)
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-opus-4-8": (15.0, 75.0),
    "claude-haiku-4-5": (0.80, 4.0),
}

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class AnthropicClient(BaseLLMClient):
    """Anthropic Messages API client.

    Features:
      - Automatic retry with exponential backoff on transient errors
      - Token usage tracking (input / output) from API response
      - Cost estimation per call
      - Tool-use loop (model calls tools → results sent back → repeat)
      - Timeout via config

    Usage::

        client = AnthropicClient()
        resp = client.call(
            messages=[{"role": "user", "content": "..."}],
            tools=[...],
            system="You are a sales assistant.",
        )
    """

    def __init__(self, api_key: str | None = None) -> None:
        cfg = get_config().llm
        configured_key = (
            cfg.api_key_value() or cfg.auth_token_value()
            if cfg.provider == "anthropic"
            else ""
        )
        self._api_key = (
            api_key
            if api_key is not None
            else configured_key
            or os.getenv("ANTHROPIC_API_KEY", "")
            or os.getenv("ANTHROPIC_AUTH_TOKEN", "")
        )
        self._base_url = cfg.base_url or None if cfg.provider == "anthropic" else None
        self._model = (
            cfg.model if cfg.provider == "anthropic" else "claude-sonnet-4-6"
        )
        self._max_retries = cfg.max_retries
        self._retry_base_delay = cfg.retry_base_delay
        self._timeout = cfg.request_timeout
        self._max_tokens = cfg.max_tokens
        self._temperature = cfg.temperature

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self._api_key.strip())

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str = "",
        max_tool_rounds: int = 5,
    ) -> LLMResponse:
        """Send a conversation to the Anthropic API.

        Returns LLMResponse with text, tool_calls, and token usage.
        If the model requests tool use, the tool_use blocks are returned
        in `tool_calls` — it's the caller's responsibility to execute them
        and continue the loop.
        """
        if not self.is_configured:
            return LLMResponse(
                text="Anthropic API key is not configured.",
                model=self._model,
                finish_reason=FINISH_MISSING_API_KEY,
            )
        system_msg = _build_system_param(system)
        formatted_messages = _format_messages(messages)
        formatted_tools = _format_tools(tools)

        input_tokens_total = 0
        output_tokens_total = 0

        for round_num in range(max_tool_rounds):
            params: dict[str, Any] = {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "temperature": self._temperature,
                "messages": formatted_messages,
            }
            if system_msg:
                params["system"] = system_msg
            if formatted_tools:
                params["tools"] = formatted_tools

            content = self._api_call_with_retry(params)

            # Accumulate tokens
            tok = content.get("usage", {})
            input_tokens_total += tok.get("input_tokens", 0)
            output_tokens_total += tok.get("output_tokens", 0)

            # Check stop reason
            stop_reason = content.get("stop_reason", "")
            text_blocks = content.get("content", [])

            # Separate text from tool_use
            text_parts: list[str] = []
            tool_use_blocks: list[dict] = []

            for block in text_blocks:
                if block.get("type") == "text":
                    text_parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    tool_use_blocks.append(block)

            if tool_use_blocks and round_num < max_tool_rounds - 1:
                # Return tool use to caller for execution
                cost = _estimate_cost(
                    self._model, input_tokens_total, output_tokens_total
                )
                return LLMResponse(
                    text="\n".join(text_parts),
                    tool_calls=[
                        {
                            "id": tb["id"],
                            "name": tb["name"],
                            "input": tb["input"],
                        }
                        for tb in tool_use_blocks
                    ],
                    input_tokens=input_tokens_total,
                    output_tokens=output_tokens_total,
                    cost_usd=cost,
                    model=self._model,
                    finish_reason="tool_use",
                )

            # Text response (stop_reason is end_turn or stop_sequence)
            cost = _estimate_cost(
                self._model, input_tokens_total, output_tokens_total
            )
            return LLMResponse(
                text="\n".join(text_parts),
                tool_calls=[],
                input_tokens=input_tokens_total,
                output_tokens=output_tokens_total,
                cost_usd=cost,
                model=self._model,
                finish_reason=stop_reason,
            )

        # Exhausted tool rounds
        cost = _estimate_cost(
            self._model, input_tokens_total, output_tokens_total
        )
        return LLMResponse(
            text="已达到最大工具调用轮次",
            tool_calls=[],
            input_tokens=input_tokens_total,
            output_tokens=output_tokens_total,
            cost_usd=cost,
            model=self._model,
            finish_reason="tool_use",
        )

    # ── Internals ─────────────────────────────────────────────────────────

    def _api_call_with_retry(self, params: dict) -> dict:
        """Call the Anthropic API with exponential-backoff retry."""
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                import anthropic

                kwargs: dict[str, Any] = {"auth_token": self._api_key}
                if self._base_url:
                    kwargs["base_url"] = self._base_url
                client = anthropic.Anthropic(**kwargs)
                response = client.messages.create(**params)
                # Convert to dict for easy handling
                return _response_to_dict(response)
            except Exception as exc:
                last_exc = exc
                if attempt < self._max_retries and _is_retryable(exc):
                    delay = self._retry_base_delay * (2 ** attempt)
                    logger.warning(
                        "Anthropic API call failed; retrying",
                        extra={
                            "provider": "anthropic",
                            "attempt": attempt + 1,
                            "error_type": type(exc).__name__,
                            "retryable": True,
                        },
                    )
                    time.sleep(delay)
                else:
                    break

        logger.error(
            "Anthropic API call failed",
            extra={
                "provider": "anthropic",
                "attempt": self._max_retries + 1,
                "error_type": type(last_exc).__name__ if last_exc else "unknown",
                "retryable": False,
            },
        )
        return {
            "content": [
                {"type": "text", "text": "LLM 服务暂时不可用。"}
            ],
            "stop_reason": "error",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build_system_param(system: str) -> str | list[dict] | None:
    if not system:
        return None
    return [{"type": "text", "text": system}]


def _format_messages(messages: list[dict]) -> list[dict]:
    """Ensure messages have valid role/content structure."""
    formatted = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, str):
            formatted.append({"role": role, "content": content})
        else:
            formatted.append({"role": role, "content": content})
    return formatted


def _format_tools(tools: list[dict] | None) -> list[dict] | None:
    if tools is None:
        return None
    if len(tools) == 0:
        return []
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t.get("input_schema", {"type": "object", "properties": {}, "required": []}),
        }
        for t in tools
    ]


def _response_to_dict(response) -> dict:
    """Convert an Anthropic SDK response object to a plain dict."""
    content_blocks = []
    for b in getattr(response, "content", []):
        block_type = b.type
        if block_type == "text":
            content_blocks.append({"type": "text", "text": b.text})
        elif block_type == "tool_use":
            content_blocks.append({
                "type": "tool_use",
                "id": getattr(b, "id", ""),
                "name": b.name,
                "input": b.input,
            })
        elif block_type == "thinking":
            # thinking blocks are internal — skip them
            continue
        else:
            # Unknown block type — include as-is with safe attribute access
            block_data = {"type": block_type}
            for attr in ("text", "id", "name", "input", "thinking", "signature"):
                if hasattr(b, attr) and attr not in block_data:
                    val = getattr(b, attr, None)
                    if val is not None:
                        block_data[attr] = val
            content_blocks.append(block_data)

    return {
        "id": getattr(response, "id", ""),
        "model": getattr(response, "model", ""),
        "stop_reason": getattr(response, "stop_reason", ""),
        "content": content_blocks,
        "usage": {
            "input_tokens": getattr(response, "usage", None).input_tokens
            if response.usage else 0,
            "output_tokens": getattr(response, "usage", None).output_tokens
            if response.usage else 0,
        },
    }


def _is_retryable(exc: Exception) -> bool:
    """Check if an exception is transient and worth retrying."""
    name = type(exc).__name__
    if name in ("RateLimitError", "APITimeoutError", "APIConnectionError",
                 "InternalServerError", "ServiceUnavailableError"):
        return True
    status = getattr(exc, "status_code", None)
    return status in _RETRYABLE_STATUSES


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost from token counts."""
    prices = _MODEL_PRICING.get(model)
    if not prices:
        # Check for deepseek models
        if "deepseek" in model.lower():
            prices = (0.14, 0.28)
        else:
            # Default conservative estimate
            prices = (3.0, 15.0)
    input_price, output_price = prices
    cost = (input_tokens / 1_000_000) * input_price + (
        output_tokens / 1_000_000
    ) * output_price
    return round(cost, 6)
