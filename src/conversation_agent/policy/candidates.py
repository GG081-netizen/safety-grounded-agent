"""Deterministic risk-candidate detection."""

from __future__ import annotations

from hashlib import sha256

from conversation_agent.policy.models import DetectionSource, RiskCandidate, TextSpan
from conversation_agent.policy.normalization import NormalizedPolicyText
from conversation_agent.policy.rules import BUSINESS_RISK_RULES, RISK_CATALOG_VERSION, RiskRule


def build_candidate_id(
    *,
    catalog_version: str,
    rule: RiskRule,
    normalized_text: str,
    start: int,
    end: int,
) -> str:
    input_fingerprint = sha256(normalized_text.encode("utf-8")).hexdigest()
    material = "\0".join(
        (
            catalog_version,
            rule.rule_id,
            rule.action.value,
            input_fingerprint,
            str(start),
            str(end),
        )
    )
    return f"rc_{sha256(material.encode('utf-8')).hexdigest()[:24]}"


class RiskCandidateDetector:
    def __init__(
        self,
        rules: tuple[RiskRule, ...] = BUSINESS_RISK_RULES,
        *,
        catalog_version: str = RISK_CATALOG_VERSION,
    ) -> None:
        self._rules = rules
        self._catalog_version = catalog_version

    def detect(self, text: NormalizedPolicyText) -> tuple[RiskCandidate, ...]:
        candidates: list[RiskCandidate] = []
        seen: set[tuple[str, int, int]] = set()
        for rule in self._rules:
            if not rule.enabled:
                continue
            for detector in rule.detectors:
                for start, end in self._occurrences(text, detector.pattern, detector.matcher_type):
                    occurrence = (rule.rule_id, start, end)
                    if occurrence in seen:
                        continue
                    seen.add(occurrence)
                    candidates.append(
                        RiskCandidate(
                            candidate_id=build_candidate_id(
                                catalog_version=self._catalog_version,
                                rule=rule,
                                normalized_text=text.normalized,
                                start=start,
                                end=end,
                            ),
                            rule_id=rule.rule_id,
                            category=rule.category,
                            action=rule.action,
                            severity=rule.severity,
                            evidence_span=TextSpan(start=start, end=end, text=text.normalized[start:end]),
                            source=DetectionSource.DETERMINISTIC_RULE,
                            confidence=1.0,
                            priority=rule.priority,
                            status_hint=rule.status_hint,
                            reason=rule.reason,
                        )
                    )
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.evidence_span.start,
                    item.evidence_span.end,
                    -item.priority,
                    item.rule_id,
                    item.action.value,
                ),
            )
        )

    @staticmethod
    def _occurrences(
        text: NormalizedPolicyText,
        pattern: str,
        matcher_type: str,
    ) -> tuple[tuple[int, int], ...]:
        needle = pattern.casefold()
        haystack = text.compact if matcher_type == "compact_literal" else text.normalized
        if matcher_type not in {"compact_literal", "normalized_literal"} or not needle:
            return ()
        found: list[tuple[int, int]] = []
        cursor = 0
        while True:
            index = haystack.find(needle, cursor)
            if index < 0:
                break
            if matcher_type == "compact_literal":
                start = text.compact_to_normalized[index]
                end = text.compact_to_normalized[index + len(needle) - 1] + 1
            else:
                start, end = index, index + len(needle)
            found.append((start, end))
            cursor = index + 1
        return tuple(found)
