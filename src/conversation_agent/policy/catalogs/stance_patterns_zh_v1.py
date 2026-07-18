"""Versioned Chinese interaction-pattern catalog for stance resolution."""

from __future__ import annotations

from dataclasses import dataclass

from conversation_agent.policy.models import RiskAction, UserStance

STANCE_PATTERN_CATALOG_VERSION = "stance_patterns_zh_v1"


@dataclass(frozen=True, slots=True)
class StancePattern:
    pattern_id: str
    stance: UserStance
    matcher_type: str
    pattern: str
    confidence: float
    priority: int
    applies_to_actions: frozenset[RiskAction] | None = None


STANCE_PATTERNS_ZH_V1: tuple[StancePattern, ...] = (
    StancePattern("zh.prohibit.forbid", UserStance.PROHIBIT, "prefix_literal", "禁止", 1.0, 100),
    StancePattern("zh.prohibit.must_not", UserStance.PROHIBIT, "prefix_literal", "不得", 1.0, 100),
    StancePattern("zh.prohibit.do_not", UserStance.PROHIBIT, "prefix_literal", "不要", 1.0, 100),
    StancePattern("zh.audit.review", UserStance.AUDIT, "clause_literal", "审核", 0.98, 90),
    StancePattern("zh.audit.check", UserStance.AUDIT, "clause_literal", "检查", 0.98, 90),
    StancePattern("zh.audit.identify", UserStance.AUDIT, "clause_literal", "识别", 0.96, 88),
    StancePattern("zh.discuss.why", UserStance.DISCUSS, "clause_literal", "为什么", 0.96, 80),
    StancePattern("zh.discuss.risk", UserStance.DISCUSS, "clause_literal", "有什么风险", 0.94, 78),
    StancePattern("zh.request.help", UserStance.REQUEST, "clause_literal", "帮我", 0.98, 70),
    StancePattern("zh.request.for_me", UserStance.REQUEST, "clause_literal", "替我", 0.98, 70),
    StancePattern("zh.request.still", UserStance.REQUEST, "clause_literal", "还是", 0.96, 72),
    StancePattern("zh.request.then_add", UserStance.REQUEST, "clause_literal", "再补", 0.96, 72),
    StancePattern("zh.request.write_as", UserStance.REQUEST, "clause_literal", "写成", 0.94, 68),
    StancePattern("zh.request.tell", UserStance.REQUEST, "clause_literal", "告诉", 0.94, 68),
    StancePattern("zh.request.promise", UserStance.REQUEST, "clause_literal", "承诺", 0.94, 68),
)
