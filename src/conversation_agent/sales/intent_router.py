"""Intent router — classify user input into one of 4 intents.

Phase 5: Rule-based keyword matching.  Deterministic, testable, no LLM
dependency.  Designed to be extended with an LLM-based router in Phase 8
(via the same `IntentRouter` interface).

Intent types:
    customer_intake  — new customer / procurement information
    meeting_note     — meeting / call / discussion records
    query            — search / status check / information retrieval
    email_draft      — compose / draft / send email
    report           — weekly/monthly sales reports
    podcast_script   — podcast or narration script generation
"""

from __future__ import annotations

from conversation_agent.sales.models import Intent, IntentResult

# ── Keyword signal tables ────────────────────────────────────────────────────
# Each entry is (keyword_phrase, signal_weight).
# Weights: 3 = very strong, 2 = strong, 1 = moderate.

_CUSTOMER_INTAKE_SIGNALS: list[tuple[str, int]] = [
    # Strong signals — clearly a new customer / procurement scenario
    ("新客户", 3),
    ("客户档案", 3),
    ("录入客户", 3),
    ("创建客户", 3),
    ("采购需求", 3),
    ("采购部", 3),
    ("需要采购", 3),
    ("询价", 3),
    ("招标", 3),
    # Moderate signals
    ("公司名称", 2),
    ("联系人", 2),
    ("联系电话", 2),
    ("预算", 2),
    ("采购计划", 2),
    ("供应商", 2),
    ("报价", 2),
    ("订购", 2),
    ("采购", 2),
    # Weaker signals
    ("公司", 1),
    ("产品", 1),
    ("数量", 1),
    ("交付", 1),
]


_MEETING_NOTE_SIGNALS: list[tuple[str, int]] = [
    # Strong signals
    ("会议纪要", 3),
    ("会议记录", 3),
    ("会议讨论", 3),
    ("通话记录", 3),
    ("拜访记录", 3),
    ("电话沟通", 3),
    ("面谈", 3),
    ("客户拜访", 3),
    ("沟通记录", 3),
    ("纪要", 3),
    # Moderate signals
    ("会议", 2),
    ("讨论了", 2),
    ("会上", 2),
    ("会谈", 2),
    ("对方提到", 2),
    ("客户表示", 2),
    ("他说", 2),
    ("拜访", 2),
    ("表示", 2),
    # Weaker signals
    ("沟通", 1),
    ("聊到", 1),
    ("反馈", 1),
    ("确认", 1),
    ("记录", 1),
]


_QUERY_SIGNALS: list[tuple[str, int]] = [
    # Strong signals
    ("查询客户", 3),
    ("查一下", 3),
    ("帮我查", 3),
    ("搜索客户", 3),
    ("查找", 3),
    ("客户查询", 3),
    # Moderate signals
    ("查看", 2),
    ("列出", 2),
    ("有哪些", 2),
    ("哪些", 2),
    ("什么状态", 2),
    ("进展如何", 2),
    ("最近", 2),
    ("健康度", 2),
    ("成交概率", 2),
    ("销售阶段", 2),
    ("阶段", 2),
    # Weaker signals
    ("查询", 1),
    ("搜索", 1),
    ("找", 1),
    ("哪个", 1),
    ("怎么样", 1),
    ("情况", 1),
    ("进展", 1),
]


_EMAIL_DRAFT_SIGNALS: list[tuple[str, int]] = [
    # Strong signals
    ("写邮件", 3),
    ("发邮件", 3),
    ("起草邮件", 3),
    ("回复邮件", 3),
    ("邮件模板", 3),
    ("生成邮件", 3),
    ("帮忙写", 3),
    ("撰写邮件", 3),
    ("帮我写", 3),
    ("写一封", 3),
    # Moderate signals
    ("邮件", 2),
    ("发信", 2),
    ("致", 2),
    ("尊敬的", 2),
    ("此致", 2),
    ("签名", 2),
    ("起草", 2),
    ("草拟", 2),
    # Weaker signals
    ("发送", 1),
    ("通知", 1),
    ("邀请", 1),
]


_REPORT_SIGNALS: list[tuple[str, int]] = [
    ("周报", 3),
    ("销售周报", 3),
    ("生成报告", 3),
    ("月报", 2),
    ("报告", 2),
    ("汇总", 2),
    ("总结", 1),
]


_PODCAST_SCRIPT_SIGNALS: list[tuple[str, int]] = [
    ("播客", 3),
    ("podcast", 3),
    ("口播", 2),
    ("脚本", 2),
    ("旁白", 1),
]


# All signals keyed by intent
_SIGNAL_TABLES: dict[Intent, list[tuple[str, int]]] = {
    Intent.CUSTOMER_INTAKE: _CUSTOMER_INTAKE_SIGNALS,
    Intent.MEETING_NOTE: _MEETING_NOTE_SIGNALS,
    Intent.QUERY: _QUERY_SIGNALS,
    Intent.EMAIL_DRAFT: _EMAIL_DRAFT_SIGNALS,
    Intent.REPORT: _REPORT_SIGNALS,
    Intent.PODCAST_SCRIPT: _PODCAST_SCRIPT_SIGNALS,
}


class IntentRouter:
    """Route user input to the best-matching intent.

    The router scores every intent against keyword signal tables and
    returns the winner with a confidence score and reasoning.

    Usage::

        router = IntentRouter()
        result = router.route("联想需要采购100台笔记本，预算80万")
        # → IntentResult(intent=CUSTOMER_INTAKE, confidence=0.85, ...)
    """

    def __init__(self, signal_tables: dict | None = None) -> None:
        """Initialise with optional custom signal tables (useful for testing)."""
        self._tables = signal_tables or _SIGNAL_TABLES

    def route(self, text: str) -> IntentResult:
        """Classify `text` into one of the 4 intents.

        Args:
            text: Raw user input (Chinese or mixed language).

        Returns:
            An ``IntentResult`` with the winning intent, confidence,
            reasoning, and alternative intents.
        """
        if not text or not text.strip():
            return IntentResult(
                intent=Intent.QUERY,
                confidence=0.1,
                reasoning="空输入，默认归类为查询",
            )

        text_lower = text.lower().strip()

        # ── Score every intent ──
        scores: dict[Intent, int] = {}
        matches: dict[Intent, list[str]] = {}
        for intent, signals in self._tables.items():
            total = 0
            hit_keywords: list[str] = []
            for keyword, weight in signals:
                if keyword.lower() in text_lower:
                    total += weight
                    hit_keywords.append(keyword)
            scores[intent] = total
            matches[intent] = hit_keywords

        # ── Determine winner ──
        total_score = sum(scores.values())
        sorted_intents = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        winner, winner_score = sorted_intents[0]

        # ── Confidence ──
        if total_score > 0:
            confidence = min(1.0, winner_score / max(total_score, 1))
        else:
            confidence = 0.1  # no signals matched at all

        # Boost confidence when winner is dominant
        if len(sorted_intents) > 1:
            runner_up_score = sorted_intents[1][1]
            if runner_up_score > 0 and winner_score > 0:
                dominance = winner_score / (winner_score + runner_up_score)
                confidence = max(confidence, dominance)

        # ── Alternative intents ──
        alternatives: list[Intent] = []
        if len(sorted_intents) > 1:
            threshold = max(1, winner_score * 0.4)  # within 40% of winner
            for intent, score in sorted_intents[1:]:
                if score >= threshold and score > 0:
                    alternatives.append(intent)

        # ── Reasoning ──
        winner_hits = matches[winner]
        if winner_hits:
            hit_str = "、".join(winner_hits[:5])
            reasoning = f"命中关键词: {hit_str}"
            if alternatives:
                reasoning += (
                    f"；备选意图: "
                    + "、".join(a.value for a in alternatives)
                )
        else:
            reasoning = "无明确关键词匹配，按最低置信度归类"

        return IntentResult(
            intent=winner,
            confidence=round(confidence, 2),
            reasoning=reasoning,
            alternative_intents=alternatives,
        )

    def route_batch(self, texts: list[str]) -> list[IntentResult]:
        """Route multiple inputs at once."""
        return [self.route(t) for t in texts]
