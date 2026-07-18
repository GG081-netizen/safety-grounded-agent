"""Task router: decide how the system should execute a request.

IntentRouter understands what the user wants.  TaskRouter decides how the
system should execute it.
"""

from __future__ import annotations

from conversation_agent.orchestration.models import TaskRoute
from conversation_agent.sales.models import Intent, IntentResult

_REPORT_SIGNALS = ("周报", "报告", "汇总", "总结", "本周", "weekly")
_PODCAST_SIGNALS = ("播客", "podcast", "脚本", "口播")
_QA_SIGNALS = ("知识", "依据", "政策", "SLA", "合同", "产品", "推荐", "为什么", "如何", "什么要求", "要求", "规则")


class TaskRouter:
    """Map semantic intent to an executable task."""

    def route(self, text: str, intent_result: IntentResult | None = None) -> TaskRoute:
        value = (text or "").strip().lower()
        if any(sig.lower() in value for sig in _REPORT_SIGNALS):
            return TaskRoute(task="weekly_report", confidence=0.9, reason="命中报告/周报执行信号")
        if any(sig.lower() in value for sig in _PODCAST_SIGNALS):
            return TaskRoute(task="podcast_script", confidence=0.9, reason="命中播客脚本执行信号")
        if intent_result and intent_result.intent == Intent.EMAIL_DRAFT:
            return TaskRoute(task="email_draft", confidence=max(0.7, intent_result.confidence), reason="邮件意图映射到邮件草稿任务")
        if any(sig.lower() in value for sig in _QA_SIGNALS):
            return TaskRoute(task="qa", confidence=0.75, reason="命中知识问答执行信号")
        if intent_result and intent_result.intent in {Intent.CUSTOMER_INTAKE, Intent.MEETING_NOTE}:
            return TaskRoute(task="sales_analysis", confidence=max(0.7, intent_result.confidence), reason="销售输入映射到销售分析任务")
        return TaskRoute(task="sales_analysis", confidence=0.65, reason="默认进入销售分析任务")
