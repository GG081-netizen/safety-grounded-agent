"""Single-entry coordinator for the modular agent pipeline."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from conversation_agent.memory.customer_store import CustomerStore
from conversation_agent.orchestration.models import (
    AgentStep,
    OrchestrationRequestMetadata,
    OrchestrationResult,
    TaskRoute,
)
from conversation_agent.orchestration.task_router import TaskRouter
from conversation_agent.policy.engine import PolicyEngine
from conversation_agent.policy.models import PolicyStatus
from conversation_agent.config import get_config
from conversation_agent.rag.base import RagClient
from conversation_agent.rag.factory import create_rag_client
from conversation_agent.rag.models import RagResult
from conversation_agent.sales.intent_router import IntentRouter
from conversation_agent.sales.models import IntentResult, InteractionMetadata


class Coordinator:
    """Coordinate policy, routing, RAG, sales, and writer modules."""

    def __init__(
        self,
        policy: PolicyEngine | None = None,
        intent_router: IntentRouter | None = None,
        task_router: TaskRouter | None = None,
        store: CustomerStore | None = None,
        rag_client: RagClient | None = None,
    ) -> None:
        self._policy = policy or PolicyEngine()
        self._intent_router = intent_router or IntentRouter()
        self._task_router = task_router or TaskRouter()
        self._store = store or CustomerStore()
        self._rag_client = rag_client or create_rag_client(get_config().rag_service)

    def run(
        self,
        user_input: str,
        session_id: str | None = None,
        task_override: str | None = None,
        *,
        request_metadata: OrchestrationRequestMetadata | None = None,
    ) -> OrchestrationResult:
        metadata = _resolve_request_metadata(session_id, request_metadata)
        session_id = metadata.session_id
        start = datetime.now(timezone.utc)
        trace: list[AgentStep] = []

        policy_start = datetime.now(timezone.utc)
        decision = self._policy.decide(user_input)
        trace.append(AgentStep(
            step_name="policy_engine",
            input_summary=_compact(user_input),
            output_summary=f"{decision.status}: {decision.reason}",
            confidence=decision.confidence,
            latency_ms=_elapsed(policy_start),
            warnings=decision.warnings,
        ))

        if decision.is_blocked:
            final = self._policy.rejection_message(decision)
            latency = _elapsed(start)
            return OrchestrationResult(
                session_id=session_id,
                user_input=user_input,
                policy=decision,
                final_response=final,
                confidence=decision.confidence,
                trace=trace,
                metadata=InteractionMetadata(
                    session_id=session_id,
                    intent="blocked",
                    intent_confidence=decision.confidence,
                    latency_ms=latency,
                ),
            )

        route_start = datetime.now(timezone.utc)
        intent_result = self._intent_router.route(user_input)
        task_route = self._task_router.route(user_input, intent_result)
        if task_override is not None:
            task_route = TaskRoute(task=task_override, confidence=1.0, reason="命令显式指定执行任务")
        trace.append(AgentStep(
            step_name="router",
            input_summary=f"intent={intent_result.intent.value}",
            output_summary=f"task={task_route.task}: {task_route.reason}",
            confidence=task_route.confidence,
            latency_ms=_elapsed(route_start),
            warnings=[] if not decision.is_uncertain else [self._policy.rejection_message(decision)],
        ))

        exec_result = self._execute_task(
            user_input,
            task_route,
            trace,
            request_metadata=metadata,
            policy_status=decision.status,
        )
        final_response = exec_result["response"]
        rag_result = exec_result.get("rag_result")
        confidence = exec_result.get("confidence", task_route.confidence)
        if decision.is_uncertain:
            final_response = self._policy.rejection_message(decision) + "\n\n" + final_response
            confidence = min(confidence, decision.confidence)

        latency = _elapsed(start)
        return OrchestrationResult(
            session_id=session_id,
            user_input=user_input,
            policy=decision,
            intent_result=intent_result,
            task_route=task_route,
            final_response=final_response,
            rag_result=rag_result,
            citations=rag_result.sources if rag_result else [],
            confidence=confidence,
            trace=trace,
            metadata=InteractionMetadata(
                session_id=session_id,
                intent=intent_result.intent.value,
                intent_confidence=intent_result.confidence,
                tools_called=[step.step_name for step in trace],
                llm_calls=0,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                latency_ms=latency,
            ),
        )

    def _execute_task(
        self,
        text: str,
        route: TaskRoute,
        trace: list[AgentStep],
        *,
        request_metadata: OrchestrationRequestMetadata,
        policy_status: PolicyStatus,
    ) -> dict:
        if route.task == "qa":
            return self._run_qa(
                text,
                trace,
                request_metadata=request_metadata,
                policy_status=policy_status,
                task_type="qa",
            )
        if route.task == "weekly_report":
            return self._run_weekly_report(
                trace,
                request_metadata=request_metadata,
                policy_status=policy_status,
            )
        if route.task == "podcast_script":
            return self._run_podcast(
                text,
                trace,
                request_metadata=request_metadata,
                policy_status=policy_status,
            )
        if route.task == "email_draft":
            return self._run_email(
                text,
                trace,
                request_metadata=request_metadata,
                policy_status=policy_status,
            )
        return self._run_sales_analysis(
            text,
            trace,
            request_metadata=request_metadata,
            policy_status=policy_status,
        )

    def _run_qa(
        self,
        text: str,
        trace: list[AgentStep],
        *,
        request_metadata: OrchestrationRequestMetadata,
        policy_status: PolicyStatus,
        task_type: str,
    ) -> dict:
        rag_result = self._rag_client.query(
            text,
            trace_id=request_metadata.trace_id,
            metadata={
                "request_id": request_metadata.request_id,
                "trace_id": request_metadata.trace_id,
                "session_id": request_metadata.session_id,
                "task_type": task_type,
                "policy_status": policy_status,
            },
        )
        for diagnostic in rag_result.diagnostics:
            trace.append(AgentStep(
                step_name=diagnostic.step_name,
                input_summary=_compact(text),
                output_summary=diagnostic.message or "RAG query completed",
                confidence=rag_result.confidence if diagnostic.success else 0.0,
                latency_ms=int(diagnostic.latency_ms or 0),
                tool_calls=["POST /query"] if diagnostic.provider == "external" else [],
                warnings=rag_result.warnings if not diagnostic.success or diagnostic.step_name == "local_rag_fallback" else [],
            ))
        if not rag_result.diagnostics:
            trace.append(AgentStep(
                step_name="rag_query",
                input_summary=_compact(text),
                output_summary=f"provider={rag_result.provider}, confidence={rag_result.confidence}",
                confidence=rag_result.confidence,
                warnings=rag_result.warnings,
            ))
        return {"response": _format_rag_response(rag_result), "rag_result": rag_result, "confidence": rag_result.confidence}

    def _run_sales_analysis(
        self,
        text: str,
        trace: list[AgentStep],
        *,
        request_metadata: OrchestrationRequestMetadata,
        policy_status: PolicyStatus,
    ) -> dict:
        del request_metadata, policy_status
        started = datetime.now(timezone.utc)
        profiles = self._select_profiles(text)
        if not profiles:
            profiles = self._store.list_all()[:5]
        lines = ["销售分析结果："]
        if not profiles:
            lines.append("暂无客户数据。可先运行 seed 命令生成演示数据。")
        for profile in profiles[:5]:
            deal = f"{profile.deal_score.score}({profile.deal_score.level.value})" if profile.deal_score else "未评分"
            health = f"{profile.health_score.health_score}({profile.health_score.status.value})" if profile.health_score else "未评分"
            lines.append(f"- {profile.customer_name}: 阶段={profile.sales_stage.value}, 成交={deal}, 健康={health}")
        trace.append(AgentStep(
            step_name="sales_module",
            input_summary=_compact(text),
            output_summary=f"profiles={len(profiles)}",
            confidence=0.72 if profiles else 0.35,
            latency_ms=_elapsed(started),
        ))
        return {"response": "\n".join(lines), "confidence": 0.72 if profiles else 0.35}

    def _run_weekly_report(
        self,
        trace: list[AgentStep],
        *,
        request_metadata: OrchestrationRequestMetadata,
        policy_status: PolicyStatus,
    ) -> dict:
        del request_metadata, policy_status
        started = datetime.now(timezone.utc)
        profiles = self._store.list_all()
        scored = [p for p in profiles if p.deal_score]
        high = [p for p in scored if p.deal_score and p.deal_score.score >= 70]
        risks = [p for p in profiles if p.high_priority_risk_count > 0]
        lines = [
            "本周销售周报",
            f"- 客户总数：{len(profiles)}",
            f"- 已评分客户：{len(scored)}",
            f"- 高潜客户：{len(high)}",
            f"- 高风险客户：{len(risks)}",
            "下周建议：优先跟进高潜客户，针对高风险客户补充竞品对比和交付保障说明。",
        ]
        trace.append(AgentStep(
            step_name="writer_module.weekly_report",
            input_summary="weekly_report",
            output_summary=f"customers={len(profiles)}, high={len(high)}, risks={len(risks)}",
            confidence=0.75 if profiles else 0.3,
            latency_ms=_elapsed(started),
        ))
        return {"response": "\n".join(lines), "confidence": 0.75 if profiles else 0.3}

    def _run_podcast(
        self,
        text: str,
        trace: list[AgentStep],
        *,
        request_metadata: OrchestrationRequestMetadata,
        policy_status: PolicyStatus,
    ) -> dict:
        qa = self._run_qa(
            text,
            trace,
            request_metadata=request_metadata,
            policy_status=policy_status,
            task_type="podcast_script",
        )
        rag_result: RagResult | None = qa.get("rag_result")
        title = text.replace("生成", "").replace("播客", "").strip() or "企业采购趋势"
        evidence_line = ""
        if rag_result and rag_result.sources:
            source_ids = ", ".join(src["source_id"] for src in rag_result.sources)
            evidence_line = f"\n引用素材：{source_ids}"
        script = "\n".join([
            f"播客脚本：{title}",
            "开场：欢迎收听本期企业采购洞察。",
            "第一段：介绍业务背景和关键问题。",
            f"第二段：结合知识库证据展开分析。{evidence_line}",
            "结尾：总结行动建议，并提醒听众关注后续采购风险。",
        ])
        trace.append(AgentStep(step_name="writer_module.podcast", output_summary="podcast_script_generated", confidence=qa.get("confidence", 0.5)))
        return {"response": script, "rag_result": rag_result, "confidence": qa.get("confidence", 0.5)}

    def _run_email(
        self,
        text: str,
        trace: list[AgentStep],
        *,
        request_metadata: OrchestrationRequestMetadata,
        policy_status: PolicyStatus,
    ) -> dict:
        del request_metadata, policy_status
        started = datetime.now(timezone.utc)
        response = "\n".join([
            "邮件草稿：",
            "尊敬的客户，您好！",
            "",
            "感谢您近期对采购方案的沟通与反馈。我们已根据当前需求整理后续跟进事项，建议安排一次短会确认预算、时间线和决策流程。",
            "",
            "如您方便，请告知本周可沟通时间。",
            "",
            "此致",
            "销售团队",
        ])
        trace.append(AgentStep(step_name="writer_module.email", input_summary=_compact(text), output_summary="email_draft_generated", confidence=0.68, latency_ms=_elapsed(started)))
        return {"response": response, "confidence": 0.68}

    def _select_profiles(self, text: str):
        profiles = []
        for profile in self._store.list_all():
            names = [profile.customer_name, *profile.aliases]
            if any(name and name in text for name in names):
                profiles.append(profile)
        if profiles:
            return profiles
        match = re.search(r"[a-zA-Z0-9_\-]{3,}", text)
        if match:
            profile = self._store.load(match.group(0))
            return [profile] if profile else []
        return []


def _format_rag_response(result: RagResult) -> str:
    lines = [result.answer, "", f"置信度：{result.confidence:.2f}"]
    if result.sources:
        lines.append("引用来源：")
        for src in result.sources:
            lines.append(f"- {src['source_id']}：{src['title']}")
    if result.warnings:
        lines.append("提示：" + "；".join(result.warnings))
    return "\n".join(lines)


def _elapsed(start: datetime) -> int:
    return int((datetime.now(timezone.utc) - start).total_seconds() * 1000)


def _compact(text: str, max_len: int = 80) -> str:
    compact = " ".join((text or "").split())
    return compact if len(compact) <= max_len else compact[: max_len - 1] + "..."


def _resolve_request_metadata(
    session_id: str | None,
    request_metadata: OrchestrationRequestMetadata | None,
) -> OrchestrationRequestMetadata:
    if request_metadata is not None:
        if session_id is not None and session_id != request_metadata.session_id:
            raise ValueError("session_id conflicts with trusted request metadata")
        return request_metadata
    return OrchestrationRequestMetadata(
        request_id=f"req_{uuid.uuid4().hex}",
        trace_id=f"trace_{uuid.uuid4().hex}",
        session_id=session_id or uuid.uuid4().hex[:8],
    )
