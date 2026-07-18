"""Candidate-and-stance business safety policy engine."""

from __future__ import annotations

import math
from collections.abc import Callable

from conversation_agent.policy.candidates import RiskCandidateDetector
from conversation_agent.policy.models import (
    PolicyDecision,
    RiskAction,
    RiskCategory,
    RiskSeverity,
)
from conversation_agent.policy.normalization import normalize_policy_text
from conversation_agent.policy.resolver import PolicyResolver
from conversation_agent.policy.rules import (
    BUSINESS_RISK_RULES,
    PolicyRule,
    RiskDetectorSpec,
    RiskRule,
)
from conversation_agent.policy.stance import DeterministicStanceResolver
from conversation_agent.policy.templates import blocked_message, uncertain_message

Classifier = Callable[[str], PolicyDecision | str | dict]


class PolicyEngine:
    """Rule-catalog safety firewall with fail-closed classifier fallback."""

    def __init__(
        self,
        classifier: Classifier | None = None,
        fallback_to_uncertain: bool = True,
        rules: tuple[RiskRule | PolicyRule, ...] = BUSINESS_RISK_RULES,
        *,
        detector: RiskCandidateDetector | None = None,
        stance_resolver: DeterministicStanceResolver | None = None,
        policy_resolver: PolicyResolver | None = None,
    ) -> None:
        del fallback_to_uncertain  # Classifier failures are always fail-closed.
        normalized_rules = tuple(self._coerce_rule(rule) for rule in rules)
        self._classifier = classifier
        self._detector = detector or RiskCandidateDetector(normalized_rules)
        self._stance_resolver = stance_resolver or DeterministicStanceResolver()
        self._policy_resolver = policy_resolver or PolicyResolver()

    def decide(self, text: str) -> PolicyDecision:
        normalized = normalize_policy_text(text)
        if not normalized.normalized:
            return PolicyDecision(status="SAFE", reason="空输入不触发安全规则", confidence=0.5)

        candidates = self._detector.detect(normalized)
        if candidates:
            stances = self._stance_resolver.resolve(text=normalized, candidates=candidates)
            return self._policy_resolver.resolve(candidates=candidates, stances=stances)

        if self._classifier is None:
            return PolicyDecision(status="SAFE", reason="未命中本地安全规则", confidence=0.72)

        try:
            return self._coerce_classifier_result(self._classifier(text))
        except Exception as exc:
            return PolicyDecision(
                status="UNCERTAIN",
                reason="分类器不可用，已按保守策略处理。",
                warnings=[f"classifier_error:{type(exc).__name__}"],
                classifier_used=True,
                confidence=0.0,
            )

    def rejection_message(self, decision: PolicyDecision) -> str:
        if decision.status == "BLOCKED":
            return blocked_message(decision)
        if decision.status == "UNCERTAIN":
            return uncertain_message(decision)
        return ""

    @staticmethod
    def _coerce_classifier_result(result: PolicyDecision | str | dict) -> PolicyDecision:
        if isinstance(result, PolicyDecision):
            if not math.isfinite(result.confidence):
                raise ValueError("classifier_confidence_invalid")
            return result.model_copy(update={"classifier_used": True})
        if isinstance(result, str):
            status = result.upper()
            if status not in {"SAFE", "UNCERTAIN", "BLOCKED"}:
                raise ValueError("classifier_status_invalid")
            return PolicyDecision(status=status, reason="LLM fallback 分类", classifier_used=True, confidence=0.65)
        if type(result) is not dict:
            raise TypeError("classifier_result_invalid")
        status = result.get("status")
        confidence = result.get("confidence", 0.65)
        if type(status) is not str or status.upper() not in {"SAFE", "UNCERTAIN", "BLOCKED"}:
            raise ValueError("classifier_status_invalid")
        if type(confidence) not in {int, float} or isinstance(confidence, bool):
            raise ValueError("classifier_confidence_invalid")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("classifier_confidence_invalid")
        reason = result.get("reason", "LLM fallback 分类")
        matched = result.get("matched_rules", [])
        warnings = result.get("warnings", [])
        if type(reason) is not str or type(matched) is not list or type(warnings) is not list:
            raise ValueError("classifier_result_invalid")
        if not all(type(item) is str for item in (*matched, *warnings)):
            raise ValueError("classifier_result_invalid")
        return PolicyDecision(
            status=status.upper(),
            reason=reason,
            matched_rules=matched,
            warnings=warnings,
            classifier_used=True,
            confidence=confidence,
        )

    @staticmethod
    def _coerce_rule(rule: RiskRule | PolicyRule) -> RiskRule:
        if isinstance(rule, RiskRule):
            return rule
        try:
            category = RiskCategory(rule.category)
        except ValueError:
            category = RiskCategory.BUSINESS_UNCERTAIN
        blocked = rule.status == "BLOCKED"
        return RiskRule(
            rule_id=rule.rule_id,
            category=category,
            action=RiskAction.FABRICATE_CUSTOMER_CASE if blocked else RiskAction.REQUEST_BUSINESS_CONFIRMATION,
            severity=RiskSeverity.HIGH if blocked else RiskSeverity.MEDIUM,
            status_hint=rule.status,
            detectors=tuple(RiskDetectorSpec(pattern=pattern) for pattern in rule.patterns),
            priority=rule.priority,
            reason=rule.reason,
        )
