"""Candidate-level deterministic stance resolution."""

from __future__ import annotations

from conversation_agent.policy.catalogs.stance_patterns_zh_v1 import (
    STANCE_PATTERN_CATALOG_VERSION,
    STANCE_PATTERNS_ZH_V1,
    StancePattern,
)
from conversation_agent.policy.models import RiskCandidate, RiskStance, UserStance
from conversation_agent.policy.normalization import NormalizedPolicyText

_CLAUSE_DELIMITERS = frozenset(",.!?;\n")
_TRANSITIONS = ("但是", "不过", "但", "然后", "随后")
_QUOTE_PAIRS = (("“", "”"), ("\"", "\""), ("'", "'"), ("‘", "’"))


class DeterministicStanceResolver:
    def __init__(self, patterns: tuple[StancePattern, ...] = STANCE_PATTERNS_ZH_V1) -> None:
        self._patterns = patterns

    def resolve(
        self,
        *,
        text: NormalizedPolicyText,
        candidates: tuple[RiskCandidate, ...],
    ) -> tuple[RiskStance, ...]:
        return tuple(self._resolve_one(text, candidate) for candidate in candidates)

    def _resolve_one(self, text: NormalizedPolicyText, candidate: RiskCandidate) -> RiskStance:
        start, end = self._clause_bounds(text.normalized, candidate.evidence_span.start, candidate.evidence_span.end)
        clause = text.normalized[start:end]
        relative_start = candidate.evidence_span.start - start
        quote_context = self._quote_context(text.normalized, candidate.evidence_span.start, candidate.evidence_span.end)

        if quote_context is not None:
            outer = quote_context
            safe_scope = any(marker in outer for marker in ("审核", "检查", "为什么", "为何", "违规"))
            request_scope = any(marker in outer for marker in ("帮我", "替我", "照着写", "照做", "还是", "再补"))
            if safe_scope and not request_scope:
                return RiskStance(candidate.candidate_id, UserStance.QUOTE, 0.98, "quoted_risk_scope", STANCE_PATTERN_CATALOG_VERSION)

        matches: list[StancePattern] = []
        for pattern in self._patterns:
            if pattern.applies_to_actions is not None and candidate.action not in pattern.applies_to_actions:
                continue
            if pattern.pattern not in clause:
                continue
            if pattern.matcher_type == "prefix_literal":
                marker_position = clause.rfind(pattern.pattern, 0, relative_start + 1)
                if marker_position < 0:
                    continue
            matches.append(pattern)

        if matches:
            winner = sorted(matches, key=lambda item: (-item.priority, -item.confidence, item.pattern_id))[0]
            return RiskStance(
                candidate.candidate_id,
                winner.stance,
                winner.confidence,
                winner.pattern_id,
                STANCE_PATTERN_CATALOG_VERSION,
            )

        if candidate.status_hint == "UNCERTAIN":
            return RiskStance(candidate.candidate_id, UserStance.REQUEST, 0.92, "business_confirmation_request", STANCE_PATTERN_CATALOG_VERSION)
        if clause.strip() == candidate.evidence_span.text or clause.rstrip().endswith(candidate.evidence_span.text):
            return RiskStance(candidate.candidate_id, UserStance.REQUEST, 0.90, "direct_imperative", STANCE_PATTERN_CATALOG_VERSION)
        return RiskStance(candidate.candidate_id, UserStance.UNKNOWN, 1.0, "stance_not_resolved", STANCE_PATTERN_CATALOG_VERSION)

    @staticmethod
    def _clause_bounds(value: str, span_start: int, span_end: int) -> tuple[int, int]:
        boundaries = {0, len(value)}
        for index, character in enumerate(value):
            if character in _CLAUSE_DELIMITERS:
                boundaries.add(index)
                boundaries.add(index + 1)
        for transition in _TRANSITIONS:
            cursor = 0
            while True:
                index = value.find(transition, cursor)
                if index < 0:
                    break
                boundaries.add(index)
                boundaries.add(index + len(transition))
                cursor = index + len(transition)
        ordered = sorted(boundaries)
        start = max(boundary for boundary in ordered if boundary <= span_start)
        end = min(boundary for boundary in ordered if boundary >= span_end)
        return start, end

    @staticmethod
    def _quote_context(value: str, span_start: int, span_end: int) -> str | None:
        for opening, closing in _QUOTE_PAIRS:
            opening_index = value.rfind(opening, 0, span_start + 1)
            if opening_index < 0:
                continue
            closing_index = value.find(closing, span_end)
            if closing_index >= span_end:
                sentence_start = max((value.rfind(mark, 0, opening_index) for mark in ".!?;"), default=-1) + 1
                sentence_ends = [value.find(mark, closing_index + 1) for mark in ".!?;"]
                sentence_end = min((index for index in sentence_ends if index >= 0), default=len(value))
                return f"{value[sentence_start:opening_index]}{value[closing_index + 1:sentence_end]}"
        return None
