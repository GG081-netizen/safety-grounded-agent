"""Sales score calculator tool — compute deal score and health score."""

from __future__ import annotations

from typing import Any

from conversation_agent.memory.customer_store import CustomerStore
from conversation_agent.memory.interaction_store import InteractionStore
from conversation_agent.sales.scorer import DealScorer
from conversation_agent.sales.health import HealthScorer
from conversation_agent.sales.models import ToolResult
from conversation_agent.tools.base import BaseTool


class SalesScoreCalculatorTool(BaseTool):
    """Calculate deal score and/or health score for a customer.

    This tool is deterministic — it does NOT call an LLM.  It reads the
    customer profile from disk, applies the scoring formulas (Phase 4),
    saves the updated profile, and returns the results.
    """

    name = "sales_score_calculator"
    description = (
        "计算客户的成交评分和/或健康度评分。"
        "需要提供 customer_id。可选择指定要计算的评分类型："
        "deal（成交评分）、health（健康度）、或 both（两者都算）。"
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "customer_id": {
                "type": "string",
                "description": "要评分的客户ID",
            },
            "score_type": {
                "type": "string",
                "description": "评分类型: deal, health, both",
                "enum": ["deal", "health", "both"],
            },
        },
        "required": ["customer_id"],
    }

    def __init__(
        self,
        customer_store: CustomerStore | None = None,
        interaction_store: InteractionStore | None = None,
    ) -> None:
        self._store = customer_store or CustomerStore()
        self._int_store = interaction_store or InteractionStore()
        self._deal_scorer = DealScorer()
        self._health_scorer = HealthScorer()

    def execute(self, **kwargs: Any) -> ToolResult:
        customer_id = kwargs.get("customer_id")
        if not customer_id:
            return ToolResult(
                success=False,
                tool_name=self.name,
                errors=["缺少必填参数: customer_id"],
                summary="评分失败：未指定 customer_id",
            )

        profile = self._store.load(customer_id)
        if profile is None:
            return ToolResult(
                success=False,
                tool_name=self.name,
                errors=[f"客户 '{customer_id}' 不存在"],
                summary=f"评分失败：客户 {customer_id} 未找到",
            )

        score_type = kwargs.get("score_type", "both")
        result_data: dict[str, Any] = {"customer_id": customer_id}
        warnings: list[str] = []

        if score_type in ("deal", "both"):
            ds = self._compute_deal_score(profile)
            profile.set_deal_score(ds)
            result_data["deal_score"] = {
                "score": ds.score,
                "level": ds.level.value,
                "confidence": ds.confidence,
                "missing_dimensions": ds.missing_dimensions,
                "risk_penalty": ds.risk_penalty,
                "summary": ds.summary,
            }
            if ds.low_confidence:
                warnings.append(
                    f"成交评分置信度低({ds.confidence})，"
                    f"缺失维度: {', '.join(ds.missing_dimensions)}"
                )

        if score_type in ("health", "both"):
            last_date = self._int_store.last_interaction_date(customer_id)
            days = 0
            if last_date:
                from datetime import datetime, timezone
                delta = datetime.now(timezone.utc) - last_date
                days = max(0, delta.days)

            hs = self._health_scorer.score(
                dimensions={
                    "recent_contact": _map_recent_contact(days),
                    "responsiveness": _map_responsiveness(profile),
                    "decision_maker_involvement": (
                        20 if profile.has_decision_maker_contact else 5
                    ),
                    "need_clarity": (
                        profile.deal_score.need_clarity // 5
                        if profile.deal_score
                        else 10
                    ),
                    "budget_timeline_clarity": (
                        15 if profile.budget_range and profile.timeline else 5
                    ),
                },
                days_since_last_contact=days,
            )
            profile.set_health_score(hs)
            result_data["health_score"] = {
                "score": hs.health_score,
                "status": hs.status.value,
                "recent_contact": hs.recent_contact,
                "summary": hs.summary,
            }

        self._store.save(profile)

        return ToolResult(
            success=True,
            tool_name=self.name,
            data=result_data,
            warnings=warnings,
            summary=(
                f"客户 {customer_id} 评分完成"
                + (f"，成交分={result_data.get('deal_score', {}).get('score', 'N/A')}"
                   if "deal_score" in result_data else "")
                + (f"，健康度={result_data.get('health_score', {}).get('score', 'N/A')}"
                   if "health_score" in result_data else "")
            ),
        )

    def _compute_deal_score(self, profile):
        """Extract dimension scores from profile signals and compute deal score."""
        dims = {
            "need_clarity": _estimate_need_clarity(profile),
            "budget_fit": _estimate_budget(profile),
            "decision_maker_access": (
                80 if profile.has_decision_maker_contact
                else 40 if profile.has_contacts else 10
            ),
            "urgency": _estimate_urgency(profile),
            "engagement": (
                80 if profile.has_procurement_items
                else 40 if profile.has_contacts else 10
            ),
        }
        return self._deal_scorer.score(dimensions=dims, risks=profile.risks)


# ── Dimension estimation helpers ──────────────────────────────────────────────

def _estimate_need_clarity(profile) -> int:
    score = 0
    if profile.procurement_items:
        score += 40
    if profile.budget_range:
        score += 20
    if profile.timeline:
        score += 20
    if profile.procurement_cycle:
        score += 10
    return min(100, max(0, score + 10))


def _estimate_budget(profile) -> int:
    score = 0
    if profile.budget_range:
        score += 50
    has_budget_items = any(
        item.has_budget for item in profile.procurement_items
    )
    if has_budget_items:
        score += 30
    return min(100, max(0, score + 10))


def _estimate_urgency(profile) -> int:
    score = 30  # baseline
    if profile.sales_stage.value in ("negotiation", "procurement_approval", "contract_signing"):
        score += 40
    elif profile.sales_stage.value in ("quotation",):
        score += 25
    if profile.timeline:
        score += 15
    return min(100, max(0, score))


def _map_recent_contact(days: int) -> int:
    if days <= 1:
        return 20
    if days <= 3:
        return 18
    if days <= 7:
        return 14
    if days <= 14:
        return 10
    if days <= 30:
        return 6
    return 2


def _map_responsiveness(profile) -> int:
    # Proxy: if there are risks with high priority, responsiveness may be lower
    if profile.has_risks:
        high_risks = profile.high_priority_risk_count
        return max(2, 20 - high_risks * 5)
    return 15
