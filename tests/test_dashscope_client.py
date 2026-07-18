from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from conversation_agent.agent import RealAgent
from conversation_agent.config import get_config, reset_config
from conversation_agent.llm.base import (
    FINISH_INVALID_TOOL_CALL,
    FINISH_INVALID_TOOL_SCHEMA,
    FINISH_MISSING_API_KEY,
    FINISH_UNSUPPORTED_CAPABILITY,
)
from conversation_agent.llm.dashscope_client import DashScopeClient
from conversation_agent.llm.errors import LLMConfigurationError, RuntimeModelProfileError
from conversation_agent.llm.factory import create_llm_client
from conversation_agent.llm.models import ModelProfile, ModelProfileConfig, default_model_registry
from tests.fakes import FakeLLMClient


pytestmark = pytest.mark.unit


class FakeTime:
    def __init__(self) -> None:
        self.monotonic_value = 0.0
        self.wall_value = datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc)
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.monotonic_value

    def wall(self) -> datetime:
        return self.wall_value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic_value += seconds


class CountingFactory:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.call_count = 0

    def __call__(self) -> httpx.Client:
        self.call_count += 1
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def _success(content="ok", tool_calls=None):
    return {
        "model": "qwen3-8b",
        "choices": [
            {
                "message": {"content": content, "tool_calls": tool_calls or []},
                "finish_reason": "stop" if not tool_calls else "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }


def _profile(profile: ModelProfile = ModelProfile.STANDARD) -> ModelProfileConfig:
    return default_model_registry().resolve(profile)


def test_missing_key_does_not_construct_http_client():
    factory = CountingFactory(lambda request: httpx.Response(500, request=request))
    client = DashScopeClient(
        api_key=" ", model_config=_profile(), http_client_factory=factory
    )
    result = client.call([{"role": "user", "content": "hello"}])
    assert result.finish_reason == FINISH_MISSING_API_KEY
    assert factory.call_count == 0


def test_standard_request_is_non_streaming_and_non_thinking():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_success(), request=request)

    client = DashScopeClient(
        api_key="key", model_config=_profile(), http_client_factory=CountingFactory(handler)
    )
    result = client.call([{"role": "user", "content": "hello"}])
    assert result.text == "ok"
    assert captured["model"] == "qwen3-8b"
    assert captured["stream"] is False
    assert captured["enable_thinking"] is False


def test_tool_schema_conversion_is_pure_and_openai_compatible():
    captured = {}
    tools = [
        {
            "name": "search_customer",
            "description": "Search",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        }
    ]
    original = json.loads(json.dumps(tools))

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_success(), request=request)

    client = DashScopeClient(
        api_key="key", model_config=_profile(), http_client_factory=CountingFactory(handler)
    )
    client.call([{"role": "user", "content": "hello"}], tools=tools)
    function = captured["tools"][0]["function"]
    assert function["parameters"] == tools[0]["input_schema"]
    assert "input_schema" not in function
    assert tools == original


def test_invalid_request_tool_schema_fails_before_http():
    factory = CountingFactory(lambda request: httpx.Response(500, request=request))
    client = DashScopeClient(
        api_key="key", model_config=_profile(), http_client_factory=factory
    )
    result = client.call(
        [{"role": "user", "content": "hello"}],
        tools=[{"description": 3, "input_schema": {"type": "array"}}],
    )
    assert result.finish_reason == FINISH_INVALID_TOOL_SCHEMA
    assert factory.call_count == 0


def test_evaluator_rejects_tools_before_http():
    factory = CountingFactory(lambda request: httpx.Response(500, request=request))
    client = DashScopeClient(
        api_key="key",
        model_config=_profile(ModelProfile.EVALUATOR),
        http_client_factory=factory,
    )
    result = client.call(
        [{"role": "user", "content": "judge"}],
        tools=[{"name": "x", "input_schema": {"type": "object"}}],
    )
    assert result.finish_reason == FINISH_UNSUPPORTED_CAPABILITY
    assert factory.call_count == 0


def test_evaluator_plain_request_uses_thinking():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_success(), request=request)

    client = DashScopeClient(
        api_key="key",
        model_config=_profile(ModelProfile.EVALUATOR),
        http_client_factory=CountingFactory(handler),
    )
    client.call([{"role": "user", "content": "judge"}])
    assert captured["enable_thinking"] is True


def test_multiple_tool_calls_and_null_content_are_preserved():
    calls = [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "a", "arguments": '{"x": 1}'},
        },
        {
            "id": "c2",
            "type": "function",
            "function": {"name": "b", "arguments": '{"y": 2}'},
        },
    ]

    def handler(request):
        return httpx.Response(200, json=_success(None, calls), request=request)

    client = DashScopeClient(
        api_key="key", model_config=_profile(), http_client_factory=CountingFactory(handler)
    )
    result = client.call([{"role": "user", "content": "hello"}])
    assert result.text == ""
    assert [call["id"] for call in result.tool_calls] == ["c1", "c2"]


@pytest.mark.parametrize("arguments", ["not-json", "[]"])
def test_invalid_tool_call_arguments_are_controlled(arguments):
    calls = [
        {
            "id": "c1",
            "function": {"name": "a", "arguments": arguments},
        }
    ]

    def handler(request):
        return httpx.Response(200, json=_success(None, calls), request=request)

    client = DashScopeClient(
        api_key="key", model_config=_profile(), http_client_factory=CountingFactory(handler)
    )
    assert client.call([{"role": "user", "content": "hello"}]).finish_reason == FINISH_INVALID_TOOL_CALL


def test_http_401_is_not_retried():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, request=request)

    client = DashScopeClient(
        api_key="bad", model_config=_profile(), http_client_factory=CountingFactory(handler)
    )
    client.call([{"role": "user", "content": "hello"}])
    assert attempts == 1


def test_retry_after_seconds_is_used():
    attempts = 0
    clock = FakeTime()

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, request=request)
        return httpx.Response(200, json=_success(), request=request)

    client = DashScopeClient(
        api_key="key",
        model_config=_profile(),
        http_client_factory=CountingFactory(handler),
        monotonic_clock=clock.monotonic,
        wall_clock_utc=clock.wall,
        sleeper=clock.sleep,
        jitter_source=lambda: 0,
    )
    assert client.call([{"role": "user", "content": "hello"}]).text == "ok"
    assert clock.sleeps == [2.0]


def test_retry_after_over_budget_stops_without_sleep():
    clock = FakeTime()

    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "20"}, request=request)

    client = DashScopeClient(
        api_key="key",
        model_config=_profile(),
        http_client_factory=CountingFactory(handler),
        monotonic_clock=clock.monotonic,
        wall_clock_utc=clock.wall,
        sleeper=clock.sleep,
    )
    client.call([{"role": "user", "content": "hello"}])
    assert clock.sleeps == []


def test_retry_after_http_date_uses_wall_clock():
    attempts = 0
    clock = FakeTime()

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "Tue, 14 Jul 2026 10:00:02 GMT"},
                request=request,
            )
        return httpx.Response(200, json=_success(), request=request)

    client = DashScopeClient(
        api_key="key",
        model_config=_profile(),
        http_client_factory=CountingFactory(handler),
        monotonic_clock=clock.monotonic,
        wall_clock_utc=clock.wall,
        sleeper=clock.sleep,
        jitter_source=lambda: 0,
    )
    assert client.call([{"role": "user", "content": "hello"}]).text == "ok"
    assert clock.sleeps == [2.0]


def test_invalid_retry_after_uses_backoff():
    attempts = 0
    clock = FakeTime()

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429, headers={"Retry-After": "invalid"}, request=request
            )
        return httpx.Response(200, json=_success(), request=request)

    client = DashScopeClient(
        api_key="key",
        model_config=_profile(),
        http_client_factory=CountingFactory(handler),
        monotonic_clock=clock.monotonic,
        wall_clock_utc=clock.wall,
        sleeper=clock.sleep,
        jitter_source=lambda: 0,
    )
    client.call([{"role": "user", "content": "hello"}])
    assert clock.sleeps == [0.5]


def test_deadline_exhausted_before_retry():
    attempts = 0
    clock = FakeTime()

    def handler(request):
        nonlocal attempts
        attempts += 1
        clock.monotonic_value = 45
        return httpx.Response(500, request=request)

    client = DashScopeClient(
        api_key="key",
        model_config=_profile(),
        http_client_factory=CountingFactory(handler),
        monotonic_clock=clock.monotonic,
        sleeper=clock.sleep,
        jitter_source=lambda: 0,
    )
    client.call([{"role": "user", "content": "hello"}])
    assert attempts == 1
    assert clock.sleeps == []


def test_read_timeout_retries_only_once(caplog):
    attempts = 0
    clock = FakeTime()

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=_success(), request=request)

    client = DashScopeClient(
        api_key="key",
        model_config=_profile(),
        http_client_factory=CountingFactory(handler),
        monotonic_clock=clock.monotonic,
        sleeper=clock.sleep,
        jitter_source=lambda: 0,
    )
    result = client.call([{"role": "user", "content": "hello"}])
    assert result.finish_reason == "timeout"
    assert attempts == 2
    assert "duplicate inference cost" in caplog.text


def test_attempt_timeout_is_clamped_to_deadline():
    seen_timeout = None
    clock = FakeTime()
    data = _profile().model_dump()
    data["overall_deadline_seconds"] = 5
    profile = ModelProfileConfig.model_validate(data)

    def handler(request):
        nonlocal seen_timeout
        seen_timeout = request.extensions["timeout"]["read"]
        return httpx.Response(200, json=_success(), request=request)

    client = DashScopeClient(
        api_key="key",
        model_config=profile,
        http_client_factory=CountingFactory(handler),
        monotonic_clock=clock.monotonic,
    )
    client.call([{"role": "user", "content": "hello"}])
    assert seen_timeout == pytest.approx(5)


def test_trim_aware_api_key_precedence(monkeypatch):
    monkeypatch.setenv("CONVAGENT_DASHSCOPE_API_KEY", "   ")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "standard-key")
    reset_config()
    assert get_config().llm.api_key_value() == "standard-key"


def test_project_api_key_takes_precedence(monkeypatch):
    monkeypatch.setenv("CONVAGENT_DASHSCOPE_API_KEY", "project-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "standard-key")
    reset_config()
    assert get_config().llm.api_key_value() == "project-key"


def test_test_mode_factory_fails_without_key(monkeypatch):
    monkeypatch.setenv("CONVAGENT_RUNTIME_MODE", "test")
    reset_config()
    with pytest.raises(LLMConfigurationError):
        create_llm_client()


def test_factory_rejects_configured_but_not_runtime_selectable_profile():
    with pytest.raises(RuntimeModelProfileError, match="advanced"):
        create_llm_client(ModelProfile.ADVANCED)


def test_explicit_fake_injection_does_not_use_factory(monkeypatch):
    monkeypatch.setattr(
        "conversation_agent.agent._build_default_llm",
        lambda: (_ for _ in ()).throw(AssertionError("factory called")),
    )
    result = RealAgent(llm=FakeLLMClient()).run("hello")
    assert result.agent_response == "ok"
