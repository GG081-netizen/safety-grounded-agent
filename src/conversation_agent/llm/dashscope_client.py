"""DashScope OpenAI-compatible non-streaming chat client."""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from conversation_agent.config import get_config
from conversation_agent.llm.base import (
    BaseLLMClient,
    FINISH_HTTP_ERROR,
    FINISH_INVALID_RESPONSE,
    FINISH_INVALID_TOOL_CALL,
    FINISH_INVALID_TOOL_SCHEMA,
    FINISH_MISSING_API_KEY,
    FINISH_TIMEOUT,
    FINISH_TOOL_CALLS,
    FINISH_UNSUPPORTED_CAPABILITY,
    LLMResponse,
)
from conversation_agent.llm.models import ModelCapability, ModelProfileConfig


logger = logging.getLogger(__name__)

HttpClientFactory = Callable[[], httpx.Client]


class DashScopeClient(BaseLLMClient):
    """A profile-bound, non-streaming DashScope client."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model_config: ModelProfileConfig | None = None,
        http_client_factory: HttpClientFactory | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock_utc: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        jitter_source: Callable[[], float] = random.random,
    ) -> None:
        cfg = get_config().llm
        self._profile = model_config or cfg.model_registry.resolve_runtime(
            cfg.default_profile
        )
        configured_key = cfg.api_key_value() if api_key is None else api_key
        self._api_key = configured_key.strip()
        self._base_url = (base_url or cfg.base_url).rstrip("/")
        self._http_client_factory = http_client_factory or httpx.Client
        self._monotonic_clock = monotonic_clock
        self._wall_clock_utc = wall_clock_utc or (
            lambda: datetime.now(timezone.utc)
        )
        self._sleeper = sleeper
        self._jitter_source = jitter_source

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key) and self._profile.configured

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str = "",
        max_tool_rounds: int = 5,
    ) -> LLMResponse:
        del max_tool_rounds
        if not self._api_key:
            return self._failure(
                FINISH_MISSING_API_KEY,
                "DashScope API key is not configured.",
            )
        if ModelCapability.CHAT not in self._profile.capabilities:
            return self._failure(
                FINISH_UNSUPPORTED_CAPABILITY,
                "The selected model profile is not approved for chat.",
            )
        if tools and (
            ModelCapability.TOOL_CALLING not in self._profile.capabilities
            or self._profile.enable_thinking
        ):
            return self._failure(
                FINISH_UNSUPPORTED_CAPABILITY,
                "The selected model profile is not approved for tool calling.",
            )

        try:
            api_tools = _to_openai_tools(tools)
            api_messages = _to_openai_messages(messages, system)
        except (KeyError, TypeError, ValueError) as exc:
            reason = FINISH_INVALID_TOOL_SCHEMA if tools else FINISH_INVALID_RESPONSE
            return self._failure(reason, f"Invalid request schema: {exc}")

        payload: dict[str, Any] = {
            "model": self._profile.model,
            "messages": api_messages,
            "stream": False,
            "enable_thinking": self._profile.enable_thinking,
            "max_tokens": self._profile.max_output_tokens,
        }
        if api_tools:
            payload["tools"] = api_tools

        return self._request_with_retry(payload)

    def _request_with_retry(self, payload: dict[str, Any]) -> LLMResponse:
        profile = self._profile
        deadline = self._monotonic_clock() + profile.overall_deadline_seconds
        retry_delay_remaining = profile.retry_total_budget_seconds
        read_timeout_retries = 0
        endpoint = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            with self._http_client_factory() as client:
                for attempt in range(profile.max_retries + 1):
                    remaining = deadline - self._monotonic_clock()
                    if remaining <= 0:
                        return self._failure(FINISH_TIMEOUT, "LLM overall deadline exceeded.")
                    attempt_timeout = min(profile.timeout_seconds, remaining)
                    response: httpx.Response | None = None
                    retryable_exc: Exception | None = None
                    try:
                        response = client.post(
                            endpoint,
                            headers=headers,
                            json=payload,
                            timeout=attempt_timeout,
                        )
                    except httpx.ReadTimeout as exc:
                        read_timeout_retries += 1
                        logger.warning(
                            "DashScope read timeout may duplicate inference cost",
                            extra={
                                "warning_code": "possible_duplicate_inference_cost",
                                "provider": "dashscope",
                                "model": profile.model,
                                "attempt": attempt + 1,
                                "timeout_category": "read_timeout",
                            },
                        )
                        if read_timeout_retries > profile.read_timeout_max_retries:
                            return self._failure(FINISH_TIMEOUT, "DashScope read timeout.")
                        retryable_exc = exc
                    except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
                        retryable_exc = exc
                    except httpx.TimeoutException:
                        return self._failure(FINISH_TIMEOUT, "DashScope request timed out.")
                    except httpx.HTTPError:
                        return self._failure(FINISH_HTTP_ERROR, "LLM 服务暂时不可用。")

                    if response is not None and 200 <= response.status_code < 300:
                        return self._parse_response(response)

                    if response is not None and not _is_retryable_status(
                        response.status_code
                    ):
                        return self._failure(
                            FINISH_HTTP_ERROR,
                            f"DashScope returned HTTP {response.status_code}.",
                        )

                    if attempt >= profile.max_retries:
                        reason = FINISH_TIMEOUT if retryable_exc else FINISH_HTTP_ERROR
                        return self._failure(reason, "DashScope retry limit reached.")

                    delay = _retry_delay(
                        response=response,
                        attempt=attempt,
                        wall_clock_utc=self._wall_clock_utc,
                        jitter_source=self._jitter_source,
                    )
                    remaining = deadline - self._monotonic_clock()
                    if delay > retry_delay_remaining or delay >= remaining:
                        reason = FINISH_TIMEOUT if retryable_exc else FINISH_HTTP_ERROR
                        return self._failure(reason, "DashScope retry budget exhausted.")
                    self._sleeper(delay)
                    retry_delay_remaining -= delay
        except Exception:
            return self._failure(FINISH_HTTP_ERROR, "LLM 服务暂时不可用。")

        return self._failure(FINISH_HTTP_ERROR, "DashScope request failed.")

    def _parse_response(self, response: httpx.Response) -> LLMResponse:
        try:
            data = response.json()
        except ValueError:
            return self._failure(FINISH_INVALID_RESPONSE, "DashScope returned invalid JSON.")
        if not isinstance(data, dict):
            return self._failure(FINISH_INVALID_RESPONSE, "DashScope response must be an object.")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return self._failure(FINISH_INVALID_RESPONSE, "DashScope response has no valid choice.")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            return self._failure(FINISH_INVALID_RESPONSE, "DashScope response has no message.")
        content = message.get("content")
        if content is None:
            text = ""
        elif isinstance(content, str):
            text = content
        else:
            return self._failure(FINISH_INVALID_RESPONSE, "DashScope content must be text or null.")

        tool_calls = message.get("tool_calls", [])
        if tool_calls is None:
            tool_calls = []
        if not isinstance(tool_calls, list):
            return self._failure(FINISH_INVALID_TOOL_CALL, "tool_calls must be a list.")
        parsed_calls: list[dict[str, Any]] = []
        try:
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    raise ValueError("tool call must be an object")
                call_id = tool_call.get("id")
                function = tool_call.get("function")
                if not isinstance(call_id, str) or not call_id.strip():
                    raise ValueError("tool call id is required")
                if not isinstance(function, dict):
                    raise ValueError("tool call function is required")
                name = function.get("name")
                arguments = function.get("arguments")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("function name is required")
                if not isinstance(arguments, str):
                    raise ValueError("function arguments must be a JSON string")
                parsed_arguments = json.loads(arguments)
                if not isinstance(parsed_arguments, dict):
                    raise ValueError("function arguments must decode to an object")
                parsed_calls.append(
                    {"id": call_id, "name": name, "input": parsed_arguments}
                )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._failure(FINISH_INVALID_TOOL_CALL, f"Invalid tool call: {exc}")

        usage = data.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}
        return LLMResponse(
            text=text,
            tool_calls=parsed_calls,
            input_tokens=_safe_int(usage.get("prompt_tokens")),
            output_tokens=_safe_int(usage.get("completion_tokens")),
            cost_usd=0.0,
            model=str(data.get("model") or self._profile.model),
            finish_reason=FINISH_TOOL_CALLS if parsed_calls else str(
                choice.get("finish_reason") or "stop"
            ),
        )

    def _failure(self, reason: str, text: str) -> LLMResponse:
        return LLMResponse(text=text, model=self._profile.model, finish_reason=reason)


def _to_openai_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not tools:
        return []
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise TypeError("tool definition must be an object")
        name = tool.get("name")
        description = tool.get("description")
        schema = tool.get("input_schema")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("tool name is required")
        if description is not None and not isinstance(description, str):
            raise TypeError("tool description must be a string")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("tool input_schema must be an object schema")
        function: dict[str, Any] = {
            "name": name,
            "parameters": deepcopy(schema),
        }
        if description is not None:
            function["description"] = description
        converted.append({"type": "function", "function": function})
    return converted


def _to_openai_messages(
    messages: list[dict[str, Any]], system: str
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if system:
        result.append({"role": "system", "content": system})
    for message in messages:
        if not isinstance(message, dict):
            raise TypeError("message must be an object")
        role = message.get("role", "user")
        content = message.get("content", "")
        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            raise TypeError("message content must be text or a block list")
        tool_uses = [block for block in content if block.get("type") == "tool_use"]
        tool_results = [block for block in content if block.get("type") == "tool_result"]
        text = "\n".join(
            str(block.get("text", ""))
            for block in content
            if block.get("type") == "text"
        )
        if tool_uses:
            result.append(
                {
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [
                        {
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(
                                    block.get("input", {}), ensure_ascii=False
                                ),
                            },
                        }
                        for block in tool_uses
                    ],
                }
            )
        elif text:
            result.append({"role": role, "content": text})
        for block in tool_results:
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": block["tool_use_id"],
                    "content": str(block.get("content", "")),
                }
            )
    return result


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 408 or status_code == 429 or 500 <= status_code <= 599


def _retry_delay(
    *,
    response: httpx.Response | None,
    attempt: int,
    wall_clock_utc: Callable[[], datetime],
    jitter_source: Callable[[], float],
) -> float:
    retry_after = response.headers.get("Retry-After") if response is not None else None
    if retry_after:
        value = retry_after.strip()
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(
                    0.0,
                    (retry_at.astimezone(timezone.utc) - wall_clock_utc()).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                pass
    return 0.5 * (2**attempt) + max(0.0, jitter_source())


def _safe_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
