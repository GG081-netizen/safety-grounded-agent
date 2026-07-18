"""Procurement Sales Copilot Agent — Mock and Real implementations.

MockAgent (Phase 7): rule-based routing + template responses.  Zero LLM.
RealAgent (V1.1):   provider-neutral tool-use agent loop.  Full LLM-driven.

Use the ``Agent`` factory to auto-select based on API key availability.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from conversation_agent.config import get_config
from conversation_agent.llm.base import BaseLLMClient, is_terminal_client_failure
from conversation_agent.llm.factory import create_llm_client
from conversation_agent.sales.intent_router import IntentRouter
from conversation_agent.sales.models import (
    Intent,
    IntentResult,
    Interaction,
    InteractionMetadata,
    ToolResult,
)
from conversation_agent.system_prompt import SYSTEM_PROMPT
from conversation_agent.tools.customer_memory import (
    CustomerMemorySearchTool,
    CustomerMemoryUpdateTool,
)
from conversation_agent.tools.registry import ToolRegistry
from conversation_agent.tools.sales_score import SalesScoreCalculatorTool

logger = logging.getLogger(__name__)

# ── Shared defaults ───────────────────────────────────────────────────────────


def _build_default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(CustomerMemorySearchTool())
    reg.register(CustomerMemoryUpdateTool())
    reg.register(SalesScoreCalculatorTool())
    return reg


# ── MockAgent ─────────────────────────────────────────────────────────────────
# Kept as fallback and for testing without API key.

_RESPONSE_TEMPLATES: dict[Intent, str] = {
    Intent.CUSTOMER_INTAKE: (
        "已收到客户信息。我会根据提供的内容提取关键采购需求、"
        "创建或更新客户档案，并给出初步的成交评分和跟进建议。"
    ),
    Intent.MEETING_NOTE: (
        "已记录会议纪要。我会提取关键事实和采购信号，"
        "更新客户档案，并重新计算成交评分和健康度。"
    ),
    Intent.QUERY: (
        "已执行客户查询。你可以查看搜索结果获取客户详情。"
        "如需查看具体客户，请提供客户名称或ID。"
    ),
    Intent.EMAIL_DRAFT: (
        "已生成邮件草稿。请确认内容后发送。"
        "如需调整邮件语气或内容，请告诉我。"
    ),
}

_INTENT_TOOL_MAP: dict[Intent, list[dict]] = {
    Intent.CUSTOMER_INTAKE: [
        {"tool": "customer_memory_search", "args": {}},
        {"tool": "customer_memory_update", "args": {}},
        {"tool": "sales_score_calculator", "args": {"score_type": "both"}},
    ],
    Intent.MEETING_NOTE: [
        {"tool": "customer_memory_update", "args": {}},
        {"tool": "sales_score_calculator", "args": {"score_type": "both"}},
    ],
    Intent.QUERY: [
        {"tool": "customer_memory_search", "args": {}},
    ],
    Intent.EMAIL_DRAFT: [
        {"tool": "customer_memory_search", "args": {}},
    ],
}


class MockAgent:
    """Rule-based agent — no LLM dependency.

    Uses keyword IntentRouter for intent classification and template
    responses.  Always available, good for CI / offline dev.
    """

    def __init__(
        self,
        router: IntentRouter | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self._router = router or IntentRouter()
        self._registry = registry or _build_default_registry()

    def run(self, user_input: str, session_id: str | None = None) -> Interaction:
        session_id = session_id or str(uuid.uuid4())[:8]
        start = datetime.now(timezone.utc)
        tools_called: list[ToolResult] = []

        intent_result = self._router.route(user_input)
        for spec in _INTENT_TOOL_MAP.get(intent_result.intent, []):
            result = self._registry.execute(spec["tool"], **spec["args"])
            tools_called.append(result)

        response = _RESPONSE_TEMPLATES.get(
            intent_result.intent, "已收到您的输入，正在处理中。"
        )
        latency_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

        return Interaction(
            session_id=session_id,
            user_input=user_input,
            intent_result=intent_result,
            tools_called=tools_called,
            agent_response=response,
            metadata=InteractionMetadata(
                session_id=session_id,
                intent=intent_result.intent.value,
                intent_confidence=intent_result.confidence,
                tools_called=[t.tool_name for t in tools_called],
                llm_calls=0,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                latency_ms=latency_ms,
            ),
        )

    def run_batch(self, inputs: list[str]) -> list[Interaction]:
        return [self.run(text) for text in inputs]


# ── RealAgent (V1.1) ─────────────────────────────────────────────────────────


class RealAgent:
    """LLM-powered tool-use agent.

    Supports any BaseLLMClient. Sends user input
    to the LLM with tool definitions.  If the LLM requests a tool call,
    the agent executes it via the ToolRegistry and feeds the result back.

    Usage::

        agent = RealAgent()           # DashScope standard profile
        result = agent.run("联想需要采购100台笔记本，预算80万")
    """

    def __init__(
        self,
        llm: BaseLLMClient | None = None,
        registry: ToolRegistry | None = None,
        system_prompt: str = "",
    ) -> None:
        self._llm = llm if llm is not None else _build_default_llm()
        self._registry = registry or _build_default_registry()
        self._system = system_prompt or SYSTEM_PROMPT

    @property
    def has_api_key(self) -> bool:
        return self._llm.is_configured

    # ── Main entry point ──────────────────────────────────────────────────

    def run(
        self,
        user_input: str,
        session_id: str | None = None,
        max_tool_rounds: int = 5,
    ) -> Interaction:
        """Execute the full LLM-driven agent loop.

        Returns an Interaction with intent_result (derived from LLM),
        tools_called, agent_response, and full token/cost metadata.
        """
        session_id = session_id or str(uuid.uuid4())[:8]
        start = datetime.now(timezone.utc)
        tools_called: list[ToolResult] = []
        total_input_tokens = 0
        total_output_tokens = 0
        total_cost = 0.0
        llm_call_count = 0
        final_intent: str | None = None
        final_confidence: float | None = None

        # Build the conversation
        messages: list[dict] = [{"role": "user", "content": user_input}]
        tool_schemas = self._registry.to_anthropic_schemas()

        final_text = ""

        for _round in range(max_tool_rounds):
            llm_call_count += 1
            llm_resp = self._llm.call(
                messages=messages,
                tools=tool_schemas,
                system=self._system,
                max_tool_rounds=1,  # we handle the loop ourselves
            )

            total_input_tokens += llm_resp.input_tokens
            total_output_tokens += llm_resp.output_tokens
            total_cost += llm_resp.cost_usd

            if is_terminal_client_failure(llm_resp):
                final_text = llm_resp.text or "LLM client failed to process the request."
                final_intent = "query"
                final_confidence = 0.1
                break

            # If the LLM wants to use tools, execute them
            if llm_resp.tool_calls:
                # Append assistant message (tool_use blocks)
                tool_use_blocks = [
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["input"],
                    }
                    for tc in llm_resp.tool_calls
                ]
                messages.append({"role": "assistant", "content": tool_use_blocks})

                # Execute each tool and collect results
                tool_result_blocks: list[dict] = []
                for tc in llm_resp.tool_calls:
                    tr = self._registry.execute(tc["name"], **tc.get("input", {}))
                    tools_called.append(tr)
                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": _tool_result_to_content(tr),
                    })

                messages.append({"role": "user", "content": tool_result_blocks})
                continue  # next LLM round

            # Text response — agent is done
            final_text = llm_resp.text

            # Derive intent heuristically from first tool called (or LLM content)
            if tools_called:
                first_tool = tools_called[0].tool_name
                final_intent, final_confidence = _infer_intent_from_tools(
                    tools_called
                )
            else:
                final_intent = "query"
                final_confidence = 0.5

            break

        # If loop exhausted without text response
        if not final_text and tools_called:
            final_text = "已完成工具调用，请查看结果。"
            final_intent = final_intent or "query"
            final_confidence = final_confidence or 0.5

        if not final_text:
            final_text = "抱歉，处理请求时遇到问题，请重试。"
            final_intent = final_intent or "query"
            final_confidence = final_confidence or 0.1

        latency_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

        # Build a synthetic IntentResult for protocol compatibility
        from conversation_agent.sales.models import Intent as IntentEnum
        intent_enum = IntentEnum.QUERY
        try:
            intent_enum = IntentEnum(final_intent or "query")
        except ValueError:
            pass

        intent_result = IntentResult(
            intent=intent_enum,
            confidence=final_confidence or 0.5,
            reasoning=f"LLM 驱动，{llm_call_count} 轮 LLM 调用",
        )

        return Interaction(
            session_id=session_id,
            user_input=user_input,
            intent_result=intent_result,
            tools_called=tools_called,
            agent_response=final_text,
            metadata=InteractionMetadata(
                session_id=session_id,
                intent=final_intent,
                intent_confidence=final_confidence,
                tools_called=[t.tool_name for t in tools_called],
                llm_calls=llm_call_count,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cost_usd=round(total_cost, 6),
                latency_ms=latency_ms,
            ),
        )

    def run_batch(self, inputs: list[str]) -> list[Interaction]:
        return [self.run(text) for text in inputs]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _tool_result_to_content(tr: ToolResult) -> str:
    """Convert a ToolResult to a string for the Anthropic tool_result block."""
    if tr.success:
        parts = [tr.summary]
        if tr.data is not None:
            import json
            parts.append(json.dumps(tr.data, ensure_ascii=False, default=str))
        return "\n".join(parts)
    return f"错误: {tr.summary}\n" + "\n".join(tr.errors)


def _infer_intent_from_tools(
    tools_called: list[ToolResult],
) -> tuple[str, float]:
    """Heuristically infer the user intent from the tools called."""
    names = [t.tool_name for t in tools_called]
    if "sales_score_calculator" in names and "customer_memory_update" in names:
        if "customer_memory_search" in names:
            return ("customer_intake", 0.85)
        return ("meeting_note", 0.80)
    if "customer_memory_search" in names and len(names) == 1:
        return ("query", 0.80)
    return ("query", 0.60)


# ── Factory ───────────────────────────────────────────────────────────────────


def _build_default_llm() -> BaseLLMClient:
    """Create the standard runtime LLM client."""
    return create_llm_client()


def Agent() -> MockAgent | RealAgent:
    """Return a configured RealAgent or the demo-only MockAgent fallback.

    Usage::

        agent = Agent()
        result = agent.run("联想需要采购100台笔记本")
    """
    client = create_llm_client()
    if client.is_configured:
        logger.debug("Using RealAgent (configured LLM provider)")
        return RealAgent(llm=client)
    logger.warning("Using MockAgent in demo mode because the LLM is not configured")
    return MockAgent()
