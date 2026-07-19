#!/usr/bin/env python3
"""Phase 15-D deterministic portfolio demo generator.

Usage:
    uv run python scripts/generate_portfolio_examples.py
    uv run python scripts/generate_portfolio_examples.py --check
    uv run python scripts/generate_portfolio_examples.py --output-root /tmp/out
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
from contextlib import contextmanager
from collections.abc import Callable, Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

# ═══════════════════════════════════════════════════════════════════════════
# Guard — scoped context manager, restores everything in finally
# ═══════════════════════════════════════════════════════════════════════════

_NETWORK_BLOCKED = "Network blocked during portfolio demo generation."

_original_create_connection = socket.create_connection
_original_socket_connect = socket.socket.connect
_original_HTTPTransport_init = httpx.HTTPTransport.__init__
_original_AsyncHTTPTransport_init = httpx.AsyncHTTPTransport.__init__
_original_httpx_post = httpx.post

_patches: list[tuple[Any, str, Any]] = []


def _blocked(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError(_NETWORK_BLOCKED)


def _blocked_http_transport(self: Any, *args: Any, **kwargs: Any) -> None:
    raise RuntimeError(_NETWORK_BLOCKED)


def _blocked_async_http_transport(self: Any, *args: Any, **kwargs: Any) -> None:
    raise RuntimeError(_NETWORK_BLOCKED)


def _patch_attr(obj: Any, attr: str, replacement: Any) -> None:
    original = getattr(obj, attr, None)
    if original is not None and original is not replacement:
        _patches.append((obj, attr, original))
        setattr(obj, attr, replacement)


def _install_guards() -> None:
    # Socket layer
    socket.create_connection = _blocked
    socket.socket.connect = _blocked

    # HTTP transport layer — block real transports, ASGITransport unaffected
    httpx.HTTPTransport.__init__ = _blocked_http_transport  # type: ignore[method-assign]
    httpx.AsyncHTTPTransport.__init__ = _blocked_async_http_transport  # type: ignore[method-assign]
    httpx.post = _blocked  # type: ignore[method-assign]

    # Patch consumer-module bindings (where imports resolve at runtime)
    import conversation_agent.rag.factory
    import conversation_agent.rag.external_client
    import conversation_agent.orchestration.coordinator
    import conversation_agent.api.app
    import conversation_agent.llm.factory

    # ExternalRagClient: definition module + consumer bindings
    _patch_attr(conversation_agent.rag.external_client, "ExternalRagClient", _blocked)
    _patch_attr(conversation_agent.rag.factory, "ExternalRagClient", _blocked)
    _patch_attr(conversation_agent.rag.factory, "create_rag_client", _blocked)
    _patch_attr(conversation_agent.orchestration.coordinator, "get_config", _blocked)
    _patch_attr(conversation_agent.orchestration.coordinator, "create_rag_client", _blocked)
    _patch_attr(conversation_agent.api.app, "get_config", _blocked)
    _patch_attr(conversation_agent.llm.factory, "create_llm_client", _blocked)

    # Patch definition module get_config
    import conversation_agent.config
    _patch_attr(conversation_agent.config, "get_config", _blocked)

    # dotenv — if present
    try:
        import dotenv
        _patch_attr(dotenv, "load_dotenv", _blocked)
    except ImportError:
        pass


def _uninstall_guards() -> None:
    for obj, attr, original in reversed(_patches):
        try:
            setattr(obj, attr, original)
        except Exception:
            pass
    _patches.clear()

    socket.create_connection = _original_create_connection
    socket.socket.connect = _original_socket_connect
    httpx.HTTPTransport.__init__ = _original_HTTPTransport_init  # type: ignore[method-assign]
    httpx.AsyncHTTPTransport.__init__ = _original_AsyncHTTPTransport_init  # type: ignore[method-assign]
    httpx.post = _original_httpx_post  # type: ignore[method-assign]


@contextmanager
def portfolio_runtime_guard() -> Generator[None, None, None]:
    _install_guards()
    try:
        yield
    finally:
        _uninstall_guards()


# ═══════════════════════════════════════════════════════════════════════════
# FixedIdFactory
# ═══════════════════════════════════════════════════════════════════════════

class FixedIdFactory:
    def __init__(self, ids: list[str]) -> None:
        if not ids:
            raise ValueError("FixedIdFactory requires at least one ID")
        self._ids = list(ids)
        self._position = 0
        self.call_count = 0

    def __call__(self) -> str:
        if self._position >= len(self._ids):
            raise RuntimeError(
                f"FixedIdFactory exhausted: {len(self._ids)} IDs, "
                f"call #{self.call_count + 1} requested."
            )
        id_ = self._ids[self._position]
        self._position += 1
        self.call_count += 1
        return id_

    def verify_consumed(self, expected: int) -> None:
        if self.call_count != expected:
            raise RuntimeError(
                f"FixedIdFactory consumed {self.call_count} IDs, expected {expected}"
            )

    def reset(self) -> None:
        self._position = 0
        self.call_count = 0


# ═══════════════════════════════════════════════════════════════════════════
# Deterministic RAG Adapters
# ═══════════════════════════════════════════════════════════════════════════

from conversation_agent.rag.base import RagTimeoutError  # noqa: E402
from conversation_agent.rag.models import (  # noqa: E402
    RagCallDiagnostic,
    RagEvidence,
    RagResult,
)


class DeterministicPortfolioRagClient:
    def __init__(self, result: RagResult, *, label: str) -> None:
        self._result = result
        self.label = label
        self.call_count = 0

    def query(self, question: str, *, trace_id: str | None = None,
              metadata: dict[str, Any] | None = None) -> RagResult:
        self.call_count += 1
        result = self._result.model_copy(deep=True)
        result.provider = "deterministic_portfolio_rag"
        return result


class TimeoutRagClient:
    def __init__(self) -> None:
        self.call_count = 0

    def query(self, question: str, *, trace_id: str | None = None,
              metadata: dict[str, Any] | None = None) -> RagResult:
        self.call_count += 1
        raise RagTimeoutError("RAG service timeout after 30s")


# ═══════════════════════════════════════════════════════════════════════════
# Instrumented Coordinator & Counting Routers (subclasses, delegate to super)
# ═══════════════════════════════════════════════════════════════════════════

from conversation_agent.orchestration.coordinator import Coordinator  # noqa: E402
from conversation_agent.orchestration.models import (  # noqa: E402
    OrchestrationResult,
    TaskRoute,
)
from conversation_agent.policy.models import PolicyStatus  # noqa: E402
from conversation_agent.sales.intent_router import IntentRouter  # noqa: E402
from conversation_agent.sales.models import IntentResult  # noqa: E402
from conversation_agent.orchestration.task_router import TaskRouter  # noqa: E402


class InstrumentedCoordinator(Coordinator):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.orchestrator_entry_calls = 0
        self.downstream_task_execution_calls = 0

    def run(self, user_input: str, session_id: str | None = None,
            task_override: str | None = None, *,
            request_metadata: Any = None) -> OrchestrationResult:
        self.orchestrator_entry_calls += 1
        return super().run(user_input, session_id=session_id,
                           task_override=task_override,
                           request_metadata=request_metadata)

    def _execute_task(self, text: str, route: TaskRoute, trace: list, *,
                      request_metadata: Any, policy_status: PolicyStatus) -> dict:
        self.downstream_task_execution_calls += 1
        return super()._execute_task(text, route, trace,
                                     request_metadata=request_metadata,
                                     policy_status=policy_status)


class CountingIntentRouter(IntentRouter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.call_count = 0

    def route(self, text: str) -> IntentResult:
        self.call_count += 1
        return super().route(text)


class CountingTaskRouter(TaskRouter):
    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    def route(self, text: str, intent_result: IntentResult | None = None) -> TaskRoute:
        self.call_count += 1
        return super().route(text, intent_result)


# ═══════════════════════════════════════════════════════════════════════════
# Frozen Clock — patches 6 verified artifact-path modules
# ═══════════════════════════════════════════════════════════════════════════

FROZEN_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        return FROZEN_TIME


_CLOCK_MODULES = [
    "conversation_agent.api.app.datetime",
    "conversation_agent.orchestration.coordinator.datetime",
    "conversation_agent.orchestration.models.datetime",
    "conversation_agent.rag.factory.datetime",
    "conversation_agent.rag.external_client.datetime",
    "conversation_agent.runtime.builder.datetime",
    "conversation_agent.sales.models.datetime",
]


@contextmanager
def _freeze_clock() -> Generator[None, None, None]:
    patchers = [patch(m, _FrozenDatetime) for m in _CLOCK_MODULES]
    for p in patchers:
        p.start()
    try:
        yield
    finally:
        for p in reversed(patchers):
            p.stop()


# ═══════════════════════════════════════════════════════════════════════════
# App Construction — explicit DI, no env var manipulation, no get_config
# ═══════════════════════════════════════════════════════════════════════════

from conversation_agent.api.app import create_app  # noqa: E402
from conversation_agent.api.security import RequestSecurityService  # noqa: E402
from conversation_agent.application.service import ChatService  # noqa: E402
from conversation_agent.authorization.service import AuthorizationService  # noqa: E402
from conversation_agent.config import (  # noqa: E402
    AppConfig,
    DatabaseConfig,
    IdempotencyHeaderMode,
    PersistenceMode,
    RagServiceConfig,
)
from conversation_agent.identity.authentication import BearerTokenParser  # noqa: E402
from conversation_agent.runtime.builder import (  # noqa: E402
    create_development_context_builder,
)
from pydantic import SecretStr  # noqa: E402


def _deterministic_config() -> AppConfig:
    return AppConfig(
        runtime_mode="demo",
        database=DatabaseConfig(
            url=SecretStr(""),
            persistence_mode=PersistenceMode.NULL,
            idempotency_header_mode=IdempotencyHeaderMode.OPTIONAL,
        ),
        rag_service=RagServiceConfig(
            provider="local",
            base_url="http://127.0.0.1:8001",
            timeout_seconds=30.0,
            fallback_to_local=True,
        ),
    )


def _raises_if_called_factory(label: str) -> Callable[[], Any]:
    def _raise() -> Any:
        raise RuntimeError(f"{label} factory called — must be injected explicitly")
    return _raise


def _build_app(coordinator: Coordinator, id_factory: FixedIdFactory,
               config: AppConfig) -> Any:
    security = RequestSecurityService(
        runtime_mode="demo",
        bearer_parser=BearerTokenParser(max_token_bytes=4096),
        authorization_service=AuthorizationService(),
    )
    service = ChatService(
        coordinator=coordinator,
        context_builder=create_development_context_builder(),
    )
    return create_app(
        service=service,
        id_factory=id_factory,
        config=config,
        security_service=security,
        http_client_factory=_raises_if_called_factory("http_client_factory"),
        database_engine_factory=_raises_if_called_factory("database_engine_factory"),
    )


# ═══════════════════════════════════════════════════════════════════════════
# HTTP Helper
# ═══════════════════════════════════════════════════════════════════════════

def _request(app: Any, method: str, path: str,
             body: dict[str, Any] | None = None,
             headers: dict[str, str] | None = None) -> httpx.Response:
    async def _send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            return await client.request(method, path, json=body,
                                        headers=headers or {})
    return asyncio.run(_send())


# ═══════════════════════════════════════════════════════════════════════════
# JSON Serialization
# ═══════════════════════════════════════════════════════════════════════════

def _serialize_json(obj: Any) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# RAG Content
# ═══════════════════════════════════════════════════════════════════════════

def _procurement_rag_result() -> RagResult:
    evidence = [
        RagEvidence(source_id="WS_SPEC_2025", title="研发工作站配置建议",
                     source_path="knowledge://procurement/workstation-specs",
                     text="研发工作站建议配置：CPU 8核16线程以上，内存32GB+，NVMe SSD 1TB，GPU可选用于轻量本地模型推理。单机预算6000-7000元可覆盖中高端配置。",
                     score=0.92),
        RagEvidence(source_id="BUDGET_GUIDE", title="IT采购预算指南 v2.1",
                     source_path="knowledge://procurement/budget-guide",
                     text="80人研发团队建议分批采购，优先保障核心开发人员配置。批量采购可争取3-5%价格折扣，包含3年原厂保修。",
                     score=0.88),
        RagEvidence(source_id="VENDOR_COMPARE", title="主流工作站供应商对比",
                     source_path="knowledge://procurement/vendor-compare",
                     text="联想ThinkStation、戴尔Precision、惠普Z系列均在预算范围内。建议选择Linux预装型号，Docker兼容性更佳。",
                     score=0.85),
    ]
    return RagResult(
        answer="为80名研发人员规划工作站采购建议：\n\n1. 推荐配置：CPU 8核16线程 / 内存32GB / NVMe SSD 1TB，单机预算6000-6800元可满足IDE、Docker和轻量本地模型需求。\n2. 建议分两批采购：第一批40台给核心开发，第二批40台给其他人员，批量采购可争取折扣。\n3. 优先选择联想ThinkStation或戴尔Precision Linux预装型号。\n4. 预留每台200-400元用于3年保修升级。\n\n以上建议基于内部配置指南和供应商对比数据，具体价格以实际询价为准。",
        evidence=evidence,
        sources=[{"source_id": e.source_id, "title": e.title or e.source_id,
                   "source_path": e.source_path or "", "confidence": e.score}
                 for e in evidence],
        confidence=0.82, provider="external",
        diagnostics=[RagCallDiagnostic(step_name="rag_query",
                        provider="deterministic_portfolio_rag", success=True,
                        message="Portfolio RAG returned procurement advice with 3 citations",
                        latency_ms=0.0)],
    )


def _fallback_rag_result() -> RagResult:
    evidence = [
        RagEvidence(source_id="LOCAL_PROCUREMENT_GUIDE",
                     title="本地采购知识库：笔记本选型指南",
                     source_path="data/knowledge/procurement/laptop_guide.json",
                     text="笔记本批量采购应关注：CPU代际、内存扩展能力、售后响应SLA、驱动兼容性。建议确认供应商是否提供批量折扣和预装系统服务。",
                     score=0.68),
    ]
    return RagResult(
        answer="本地知识库建议：笔记本批量采购需确认供应商SLA、批量折扣政策和售后响应时间。建议联系至少3家供应商比价，并签订包含交付期限和验收标准的框架协议。",
        evidence=evidence,
        sources=[{"source_id": "LOCAL_PROCUREMENT_GUIDE",
                   "title": "本地采购知识库：笔记本选型指南",
                   "source_path": "data/knowledge/procurement/laptop_guide.json",
                   "confidence": 0.68}],
        confidence=0.76, provider="local",
        diagnostics=[RagCallDiagnostic(step_name="local_rag_query", provider="local",
                        success=True,
                        message="Local keyword RAG returned 1 evidence item",
                        latency_ms=0.0)],
    )


# ═══════════════════════════════════════════════════════════════════════════
# Trace Builder
# ═══════════════════════════════════════════════════════════════════════════

def _build_trace_json(
    scenario_id: str, request_id: str, trace_id: str, session_id: str,
    agent_response: dict[str, Any],
    coordinator: InstrumentedCoordinator,
    intent_router: CountingIntentRouter,
    task_router: CountingTaskRouter,
    rag_client: Any,
    fallback_rag_client: Any | None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = agent_response.get("result", {})
    orch_trace: list[dict[str, Any]] = result.get("trace", [])
    rag_result = result.get("rag_result") or {}
    rag_diagnostics: list[dict[str, Any]] = rag_result.get("diagnostics", [])

    # runtime_steps from real orchestration trace
    runtime_steps = [{
        "step_name": s.get("step_name"),
        "input_summary": s.get("input_summary"),
        "output_summary": s.get("output_summary"),
        "confidence": s.get("confidence"),
        "warnings": s.get("warnings", []),
        "tool_calls": s.get("tool_calls", []),
    } for s in orch_trace]

    # normalized_stages with source annotations
    normalized_stages: list[dict[str, str]] = []
    for s in orch_trace:
        sn = s.get("step_name", "")
        if sn == "policy_engine":
            normalized_stages.append({"stage": "policy", "source_type": "coordinator_method",
                                      "source_name": "PolicyEngine.decide()"})
        elif sn == "router":
            normalized_stages.append({"stage": "router", "source_type": "coordinator_method",
                                      "source_name": "IntentRouter.route() + TaskRouter.route()"})
        elif "rag" in sn.lower():
            if "external" in sn:
                normalized_stages.append({"stage": "external_rag_attempt",
                                          "source_type": "rag_diagnostic",
                                          "source_name": sn})
                if not s.get("success", True):
                    normalized_stages.append({"stage": "external_rag_failure",
                                              "source_type": "rag_diagnostic",
                                              "source_name": sn})
            elif "fallback" in sn or "local" in sn:
                normalized_stages.append({"stage": "fallback_activation",
                                          "source_type": "rag_diagnostic",
                                          "source_name": "FallbackRagClient.fallback"})
                normalized_stages.append({"stage": "fallback_result",
                                          "source_type": "rag_result",
                                          "source_name": "deterministic_local_fallback"})
            else:
                normalized_stages.append({"stage": "rag_query", "source_type": "rag_diagnostic",
                                          "source_name": sn})
    if not result.get("policy", {}).get("status") == "BLOCKED":
        normalized_stages.append({"stage": "response_assembly",
                                  "source_type": "projector",
                                  "source_name": "ResponseProjector.project()"})

    # adapter_call_counts from instrumented counters only
    adapter_call_counts: dict[str, int] = {
        "orchestrator_entry_calls": coordinator.orchestrator_entry_calls,
        "downstream_task_execution_calls": coordinator.downstream_task_execution_calls,
        "intent_router_calls": intent_router.call_count,
        "task_router_calls": task_router.call_count,
    }
    if hasattr(rag_client, "call_count"):
        adapter_call_counts["primary_rag_calls"] = rag_client.call_count
    if fallback_rag_client is not None and hasattr(fallback_rag_client, "call_count"):
        adapter_call_counts["fallback_rag_calls"] = fallback_rag_client.call_count
    else:
        adapter_call_counts["fallback_rag_calls"] = 0

    # observed from trace (NOT adapter counters)
    observed_runtime_counts: dict[str, int] = {
        "tool_calls": sum(len(s.get("tool_calls", [])) for s in orch_trace),
        "domain_agent_calls": 0,
    }

    obj: dict[str, Any] = {
        "scenario_id": scenario_id,
        "request_id": request_id,
        "trace_id": trace_id,
        "session_id": session_id,
        "runtime_steps": runtime_steps,
        "rag_diagnostics": rag_diagnostics,
        "normalized_stages": normalized_stages,
        "adapter_call_counts": adapter_call_counts,
        "observed_runtime_counts": observed_runtime_counts,
        "contract_assertions": {},
    }
    if extra_fields:
        obj.update(extra_fields)
    return obj


# ═══════════════════════════════════════════════════════════════════════════
# README Content
# ═══════════════════════════════════════════════════════════════════════════

_PROCUREMENT_README = """\
# Procurement Planning Demo

## 场景

为80名研发人员规划办公工作站采购，通过安全策略编排系统生成配置建议。

## 请求路径

```
POST /v1/qa  (demo mode, no auth, task_override=qa)
  → PolicyEngine: SAFE
  → IntentRouter: intent detection
  → TaskRouter + task_override: qa
  → DeterministicRagClient: procurement knowledge query
  → ResponseProjector: AgentResponse assembly
```

## 证明能力

- Policy 放行正常业务请求
- 确定性任务编排（通过 /v1/qa endpoint 的 task_override）
- RAG 返回结构化引用和证据
- 全链路 Trace 完整记录
- 零网络访问的确定性生成

## 生成命令

```bash
uv run python scripts/generate_portfolio_examples.py
uv run python scripts/generate_portfolio_examples.py --check
```
"""

_BLOCKED_README = """\
# Policy Blocked Demo

## 场景

请求查询客户负责人的私人住址和宗教信仰，Policy 硬阻断。

## 请求路径

```
POST /v1/chat  (demo mode, no auth)
  → Coordinator 进入并执行 PolicyEngine
  → PolicyEngine: BLOCKED (privacy violation)
  → IntentRouter、TaskRouter、Task Execution 和 RAG 均未执行
  → 返回标准拒绝消息
```

## 证明能力

- Policy 在 Router 和 RAG 之前阻断高风险请求
- Coordinator 被调用（orchestrator_entry_calls=1），但下游任务执行被阻止（downstream_task_execution_calls=0）
- 不返回私人信息推测或虚假 Citation
- Trace 仅包含 policy_engine stage

## 生成命令

```bash
uv run python scripts/generate_portfolio_examples.py
uv run python scripts/generate_portfolio_examples.py --check
```
"""

_FALLBACK_README = """\
# RAG Fallback Demo

## 场景

确定性外部 RAG 适配器超时模拟 → 本地 Fallback → 置信度上限。
本场景不涉及真实外部服务故障。

## 请求路径

```
POST /v1/qa  (demo mode, no auth, task_override=qa)
  → PolicyEngine: SAFE
  → IntentRouter → TaskRouter: qa task
  → TimeoutRagClient (deterministic stub): RagTimeoutError
  → FallbackRagClient: activate local fallback
  → confidence capped at 0.55
  → warning: "External RAG unavailable; used local keyword fallback."
```

## 证明能力

- External RAG 超时时的优雅降级
- Fallback 置信度上限 0.55
- Provider 标记为 fallback（非 external）
- Warning 对用户可见
- 不存在伪造的 External Citation
- 全流程零网络访问

## 生成命令

```bash
uv run python scripts/generate_portfolio_examples.py
uv run python scripts/generate_portfolio_examples.py --check
```
"""


# ═══════════════════════════════════════════════════════════════════════════
# Demo Generators — return dict[filename, bytes]
# ═══════════════════════════════════════════════════════════════════════════

def _generate_demo_1(app: Any, coordinator: InstrumentedCoordinator,
                     intent_router: CountingIntentRouter,
                     task_router: CountingTaskRouter,
                     rag_client: DeterministicPortfolioRagClient,
                     id_factory: FixedIdFactory) -> dict[str, bytes]:
    scenario_id = "procurement-planning"
    text = ("为80名研发人员规划办公工作站，主要用于IDE、Docker和轻量本地模型，"
            "单机预算不超过7000元，请给出采购建议。")
    session_id = "demo-procurement-001"

    coordinator.orchestrator_entry_calls = 0
    coordinator.downstream_task_execution_calls = 0
    intent_router.call_count = 0
    task_router.call_count = 0
    rag_client.call_count = 0
    id_factory.reset()

    resp = _request(app, "POST", "/v1/qa",
                    body={"text": text, "session_id": session_id})
    assert resp.status_code == 200, f"Demo 1 HTTP {resp.status_code}"

    agent_response = resp.json()
    result = agent_response.get("result", {})

    # request.json — only HTTP request + expected IDs + endpoint contract
    request_obj = {
        "scenario_id": scenario_id,
        "method": "POST",
        "endpoint": "/v1/qa",
        "body": {"text": text, "session_id": session_id},
        "expected_request_id": agent_response["request_id"],
        "expected_trace_id": agent_response["trace_id"],
    }

    policy_status = result.get("policy", {}).get("status")
    rag_result = result.get("rag_result") or {}
    citations = result.get("citations", [])
    evidence = rag_result.get("evidence", [])
    confidence = result.get("confidence", 0)
    trace_steps = [s.get("step_name") for s in result.get("trace", [])]
    intent_result = result.get("intent_result") or {}
    task_route = result.get("task_route") or {}

    contract = {
        "policy_decision": policy_status,
        "orchestrator_entry_calls": coordinator.orchestrator_entry_calls,
        "intent_router_calls": intent_router.call_count,
        "task_router_calls": task_router.call_count,
        "downstream_task_execution_calls": coordinator.downstream_task_execution_calls,
        "primary_rag_calls": rag_client.call_count,
        "fallback_rag_calls": 0,
        "fallback_used": False,
        "citations_count": len(citations),
        "evidence_count": len(evidence),
        "trace_complete": all(s in trace_steps for s in ["policy_engine", "router"]),
        "provider": rag_result.get("provider"),
        "network_access": False,
        "confidence": confidence,
    }

    # trace.json — real intent result, effective task, override info
    trace_extra = {
        "real_intent_result": intent_result,
        "effective_task": task_route.get("task"),
        "task_override_applied": True,
        "task_override_source": "endpoint:/v1/qa",
    }
    trace_obj = _build_trace_json(
        scenario_id=scenario_id,
        request_id=agent_response["request_id"],
        trace_id=agent_response["trace_id"],
        session_id=session_id,
        agent_response=agent_response,
        coordinator=coordinator,
        intent_router=intent_router,
        task_router=task_router,
        rag_client=rag_client,
        fallback_rag_client=None,
        extra_fields={"contract_assertions": contract, **trace_extra},
    )

    id_factory.verify_consumed(2)
    return {
        "request.json": _serialize_json(request_obj),
        "response.json": _serialize_json(agent_response),
        "trace.json": _serialize_json(trace_obj),
        "README.md": _PROCUREMENT_README.encode("utf-8"),
    }


def _generate_demo_2(app: Any, coordinator: InstrumentedCoordinator,
                     intent_router: CountingIntentRouter,
                     task_router: CountingTaskRouter,
                     id_factory: FixedIdFactory) -> dict[str, bytes]:
    scenario_id = "policy-blocked"
    text = ("帮我查一下客户负责人的私人住址和宗教信仰，"
            "方便销售人员安排拜访。")
    session_id = "demo-blocked-001"

    coordinator.orchestrator_entry_calls = 0
    coordinator.downstream_task_execution_calls = 0
    intent_router.call_count = 0
    task_router.call_count = 0
    id_factory.reset()

    resp = _request(app, "POST", "/v1/chat",
                    body={"text": text, "session_id": session_id})
    assert resp.status_code == 200, f"Demo 2 HTTP {resp.status_code}"

    agent_response = resp.json()
    result = agent_response.get("result", {})
    policy_status = result.get("policy", {}).get("status")

    # HARD STOP — text must trigger BLOCKED, no adjustment allowed
    if policy_status != "BLOCKED":
        raise RuntimeError(
            f"STOP: Policy decision is {policy_status}, expected BLOCKED. "
            f"Reason: {result.get('policy', {}).get('reason')}. "
            f"Rules: {result.get('policy', {}).get('matched_rules')}"
        )

    request_obj = {
        "scenario_id": scenario_id,
        "method": "POST",
        "endpoint": "/v1/chat",
        "body": {"text": text, "session_id": session_id},
        "expected_request_id": agent_response["request_id"],
        "expected_trace_id": agent_response["trace_id"],
    }

    trace_steps = [s.get("step_name") for s in result.get("trace", [])]

    contract = {
        "policy_decision": policy_status,
        "orchestrator_entry_calls": coordinator.orchestrator_entry_calls,
        "intent_router_calls": intent_router.call_count,
        "task_router_calls": task_router.call_count,
        "downstream_task_execution_calls": coordinator.downstream_task_execution_calls,
        "primary_rag_calls": 0,
        "fallback_rag_calls": 0,
        "tool_calls": 0,
        "domain_agent_calls": 0,
        "runtime_steps": trace_steps,
        "normalized_stages": ["policy"],
    }

    trace_obj = _build_trace_json(
        scenario_id=scenario_id,
        request_id=agent_response["request_id"],
        trace_id=agent_response["trace_id"],
        session_id=session_id,
        agent_response=agent_response,
        coordinator=coordinator,
        intent_router=intent_router,
        task_router=task_router,
        rag_client=None,
        fallback_rag_client=None,
        extra_fields={"contract_assertions": contract},
    )

    id_factory.verify_consumed(2)
    return {
        "request.json": _serialize_json(request_obj),
        "response.json": _serialize_json(agent_response),
        "trace.json": _serialize_json(trace_obj),
        "README.md": _BLOCKED_README.encode("utf-8"),
    }


def _generate_demo_3(app: Any, coordinator: InstrumentedCoordinator,
                     intent_router: CountingIntentRouter,
                     task_router: CountingTaskRouter,
                     timeout_rag: TimeoutRagClient,
                     fallback_rag: DeterministicPortfolioRagClient,
                     id_factory: FixedIdFactory) -> dict[str, bytes]:
    scenario_id = "rag-fallback"
    text = "笔记本批量采购需要注意什么？"
    session_id = "demo-fallback-001"

    coordinator.orchestrator_entry_calls = 0
    coordinator.downstream_task_execution_calls = 0
    intent_router.call_count = 0
    task_router.call_count = 0
    timeout_rag.call_count = 0
    fallback_rag.call_count = 0
    id_factory.reset()

    resp = _request(app, "POST", "/v1/qa",
                    body={"text": text, "session_id": session_id})
    assert resp.status_code == 200, f"Demo 3 HTTP {resp.status_code}"

    agent_response = resp.json()
    result = agent_response.get("result", {})

    request_obj = {
        "scenario_id": scenario_id,
        "method": "POST",
        "endpoint": "/v1/qa",
        "body": {"text": text, "session_id": session_id},
        "expected_request_id": agent_response["request_id"],
        "expected_trace_id": agent_response["trace_id"],
    }

    policy_status = result.get("policy", {}).get("status")
    rag_result = result.get("rag_result") or {}
    diagnostics = rag_result.get("diagnostics", [])
    confidence = result.get("confidence", 0)
    warnings = rag_result.get("warnings", [])
    trace_steps = [s.get("step_name") for s in result.get("trace", [])]

    external_error_type = None
    for d in diagnostics:
        if d.get("step_name") == "external_rag_query" and not d.get("success"):
            external_error_type = d.get("error_type")

    # Verify citation sources: must be from deterministic local data
    sources = rag_result.get("sources", [])
    has_external_source = any(
        s.get("source_path", "").startswith(("http://", "https://"))
        or s.get("source_id", "").startswith(("external", "remote", "live"))
        for s in sources
    )

    contract = {
        "policy_decision": policy_status,
        "orchestrator_entry_calls": coordinator.orchestrator_entry_calls,
        "intent_router_calls": intent_router.call_count,
        "task_router_calls": task_router.call_count,
        "downstream_task_execution_calls": coordinator.downstream_task_execution_calls,
        "attempted_provider": "deterministic_external_stub",
        "primary_rag_calls": timeout_rag.call_count,
        "external_failure_type": external_error_type,
        "fallback_rag_calls": fallback_rag.call_count,
        "fallback_used": True,
        "result_provider": rag_result.get("provider"),
        "fallback_source_adapter": "deterministic_local_fallback",
        "network_access": False,
        "confidence": confidence,
        "confidence_within_fallback_cap": confidence <= 0.55,
        "warning_visible": len(warnings) > 0,
        "warning_text": warnings[0] if warnings else "",
        "no_fake_external_citation": rag_result.get("provider") != "external",
        "citation_sources_all_local": not has_external_source,
        "runtime_steps": trace_steps,
    }

    trace_obj = _build_trace_json(
        scenario_id=scenario_id,
        request_id=agent_response["request_id"],
        trace_id=agent_response["trace_id"],
        session_id=session_id,
        agent_response=agent_response,
        coordinator=coordinator,
        intent_router=intent_router,
        task_router=task_router,
        rag_client=timeout_rag,
        fallback_rag_client=fallback_rag,
        extra_fields={"contract_assertions": contract},
    )

    id_factory.verify_consumed(2)
    return {
        "request.json": _serialize_json(request_obj),
        "response.json": _serialize_json(agent_response),
        "trace.json": _serialize_json(trace_obj),
        "README.md": _FALLBACK_README.encode("utf-8"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Top-Level: generate_all, _generate_all_in_memory, check_all
# ═══════════════════════════════════════════════════════════════════════════

def _write_demo_dir(path: Path, files: dict[str, bytes]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (path / name).write_bytes(content)


def generate_all(output_root: Path) -> None:
    with portfolio_runtime_guard(), _freeze_clock():
        cfg = _deterministic_config()

        # Demo 1
        ids1 = FixedIdFactory(["req-demo-procurement-001", "trace-demo-procurement-001"])
        rag1 = DeterministicPortfolioRagClient(_procurement_rag_result(), label="procurement")
        intent1, task1 = CountingIntentRouter(), CountingTaskRouter()
        coord1 = InstrumentedCoordinator(rag_client=rag1, intent_router=intent1, task_router=task1)
        _write_demo_dir(output_root / "procurement-planning",
                        _generate_demo_1(_build_app(coord1, ids1, cfg), coord1, intent1, task1, rag1, ids1))

        # Demo 2
        ids2 = FixedIdFactory(["req-demo-blocked-001", "trace-demo-blocked-002"])
        intent2, task2 = CountingIntentRouter(), CountingTaskRouter()
        # Must inject rag_client even for blocked demo — default constructor calls get_config()
        dummy_rag2 = DeterministicPortfolioRagClient(_procurement_rag_result(), label="dummy")
        coord2 = InstrumentedCoordinator(rag_client=dummy_rag2, intent_router=intent2, task_router=task2)
        _write_demo_dir(output_root / "policy-blocked",
                        _generate_demo_2(_build_app(coord2, ids2, cfg), coord2, intent2, task2, ids2))

        # Demo 3
        ids3 = FixedIdFactory(["req-demo-fallback-001", "trace-demo-fallback-003"])
        timeout3, fb3 = TimeoutRagClient(), DeterministicPortfolioRagClient(_fallback_rag_result(), label="fallback")
        from conversation_agent.rag.factory import FallbackRagClient  # noqa: E402
        wrapper3 = FallbackRagClient(primary=timeout3, fallback=fb3, fallback_enabled=True)
        intent3, task3 = CountingIntentRouter(), CountingTaskRouter()
        coord3 = InstrumentedCoordinator(rag_client=wrapper3, intent_router=intent3, task_router=task3)
        _write_demo_dir(output_root / "rag-fallback",
                        _generate_demo_3(_build_app(coord3, ids3, cfg), coord3, intent3, task3, timeout3, fb3, ids3))


def _check_directory_structure(demo_dir: Path, scenario_id: str) -> None:
    if not demo_dir.is_dir():
        raise FileNotFoundError(f"Missing: {demo_dir}")
    entries = list(demo_dir.iterdir())
    for e in entries:
        if e.is_symlink():
            raise RuntimeError(f"Symlink: {e}")
        if e.is_dir():
            raise RuntimeError(f"Subdir: {e}")
    expected = {"request.json", "response.json", "trace.json", "README.md"}
    actual = {e.name for e in entries}
    if actual != expected:
        raise RuntimeError(f"Files in {scenario_id}: missing={expected - actual} extra={actual - expected}")


def _generate_all_in_memory() -> dict[str, dict[str, bytes]]:
    """Pure in-memory generation — no tempfile, no directory creation."""
    with portfolio_runtime_guard(), _freeze_clock():
        cfg = _deterministic_config()

        ids1 = FixedIdFactory(["req-demo-procurement-001", "trace-demo-procurement-001"])
        rag1 = DeterministicPortfolioRagClient(_procurement_rag_result(), label="procurement")
        intent1, task1 = CountingIntentRouter(), CountingTaskRouter()
        coord1 = InstrumentedCoordinator(rag_client=rag1, intent_router=intent1, task_router=task1)
        d1 = _generate_demo_1(_build_app(coord1, ids1, cfg), coord1, intent1, task1, rag1, ids1)

        ids2 = FixedIdFactory(["req-demo-blocked-001", "trace-demo-blocked-002"])
        intent2, task2 = CountingIntentRouter(), CountingTaskRouter()
        dummy_rag2 = DeterministicPortfolioRagClient(_procurement_rag_result(), label="dummy")
        coord2 = InstrumentedCoordinator(rag_client=dummy_rag2, intent_router=intent2, task_router=task2)
        d2 = _generate_demo_2(_build_app(coord2, ids2, cfg), coord2, intent2, task2, ids2)

        ids3 = FixedIdFactory(["req-demo-fallback-001", "trace-demo-fallback-003"])
        timeout3, fb3 = TimeoutRagClient(), DeterministicPortfolioRagClient(_fallback_rag_result(), label="fallback")
        from conversation_agent.rag.factory import FallbackRagClient  # noqa: E402
        wrapper3 = FallbackRagClient(primary=timeout3, fallback=fb3, fallback_enabled=True)
        intent3, task3 = CountingIntentRouter(), CountingTaskRouter()
        coord3 = InstrumentedCoordinator(rag_client=wrapper3, intent_router=intent3, task_router=task3)
        d3 = _generate_demo_3(_build_app(coord3, ids3, cfg), coord3, intent3, task3, timeout3, fb3, ids3)

    return {"procurement-planning": d1, "policy-blocked": d2, "rag-fallback": d3}


def check_all(output_root: Path) -> int:
    failed = False
    for scenario in ["procurement-planning", "policy-blocked", "rag-fallback"]:
        demo_dir = output_root / scenario
        try:
            _check_directory_structure(demo_dir, scenario)
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"STRUCTURE: {exc}")
            failed = True
            continue
        try:
            regenerated = _generate_all_in_memory()
        except Exception as exc:
            print(f"GENERATION: {exc}")
            failed = True
            continue
        for fn in ["request.json", "response.json", "trace.json", "README.md"]:
            existing = (demo_dir / fn).read_bytes()
            expected = regenerated.get(scenario, {}).get(fn, b"")
            if existing != expected:
                print(f"MISMATCH: {scenario}/{fn}")
                failed = True
    return 1 if failed else 0


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    p = argparse.ArgumentParser(description="Portfolio demo generator")
    p.add_argument("--check", action="store_true")
    p.add_argument("--output-root", type=Path, default=None)
    args = p.parse_args()

    root = Path(__file__).resolve().parents[1]
    output_root = args.output_root or (root / "examples")

    if args.check:
        print("Checking (read-only, no writes)...")
        rc = check_all(output_root)
        if rc == 0:
            print("All match.")
        else:
            print("MISMATCH — re-run without --check to regenerate.")
        sys.exit(rc)

    print("Generating...")
    generate_all(output_root)
    for s in ["procurement-planning", "policy-blocked", "rag-fallback"]:
        print(f"  {output_root / s}/")
    print("Done.")


if __name__ == "__main__":
    main()
