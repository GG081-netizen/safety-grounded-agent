"""Deterministic policy decision matrix."""

from __future__ import annotations

from conversation_agent.policy.catalogs.resolution_thresholds_v1 import (
    RESOLUTION_THRESHOLDS_V1,
    PolicyResolutionThresholds,
)
from conversation_agent.policy.models import (
    PolicyDecision,
    RiskCandidate,
    RiskCategory,
    RiskSeverity,
    RiskStance,
    UserStance,
)


class PolicyResolver:
    def __init__(self, thresholds: PolicyResolutionThresholds = RESOLUTION_THRESHOLDS_V1) -> None:
        self._thresholds = thresholds

    def resolve(
        self,
        *,
        candidates: tuple[RiskCandidate, ...],
        stances: tuple[RiskStance, ...],
    ) -> PolicyDecision:
        by_candidate = {stance.candidate_id: stance for stance in stances}
        matched_rules = [
            f"{candidate.category.value}:{candidate.evidence_span.text}"
            for candidate in candidates
        ]

        high_requests = [
            candidate
            for candidate in candidates
            if candidate.severity in {RiskSeverity.HIGH, RiskSeverity.CRITICAL}
            and by_candidate[candidate.candidate_id].stance == UserStance.REQUEST
        ]
        if high_requests:
            top = max(high_requests, key=lambda item: (item.priority, item.severity.value, item.rule_id))
            return PolicyDecision(status="BLOCKED", reason=top.reason, matched_rules=matched_rules, confidence=0.99)

        if any(
            candidate.category == RiskCategory.BUSINESS_UNCERTAIN
            and by_candidate[candidate.candidate_id].stance == UserStance.REQUEST
            for candidate in candidates
        ):
            return PolicyDecision(
                status="UNCERTAIN",
                reason="该业务结论需要进一步证据或授权确认。",
                matched_rules=matched_rules,
                confidence=0.85,
            )

        if any(stance.stance == UserStance.UNKNOWN for stance in stances):
            return PolicyDecision(
                status="UNCERTAIN",
                reason="检测到风险表达，但无法可靠判断用户立场。",
                matched_rules=matched_rules,
                confidence=0.5,
            )

        for stance in stances:
            if stance.stance == UserStance.DISCUSS and stance.confidence < self._thresholds.discuss_safe_min_confidence:
                return self._ambiguous(matched_rules)
            if stance.stance == UserStance.QUOTE and stance.confidence < self._thresholds.quote_safe_min_confidence:
                return self._ambiguous(matched_rules)

        return PolicyDecision(
            status="SAFE",
            reason="风险表达处于禁止、审查、讨论或引用作用域。",
            matched_rules=matched_rules,
            confidence=min((stance.confidence for stance in stances), default=0.9),
        )

    @staticmethod
    def _ambiguous(matched_rules: list[str]) -> PolicyDecision:
        return PolicyDecision(
            status="UNCERTAIN",
            reason="风险表达的讨论或引用作用域不完整。",
            matched_rules=matched_rules,
            confidence=0.5,
        )
