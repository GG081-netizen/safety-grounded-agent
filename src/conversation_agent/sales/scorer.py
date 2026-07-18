"""Deal scoring engine — weighted dimensions + risk penalty + confidence.

Phase 4: Reads configurable weights from AppConfig.
"""

from __future__ import annotations

from conversation_agent.config import get_config
from conversation_agent.sales.models import (
    DealLevel,
    DealScore,
    RiskItem,
    RiskLevel,
)

# Dimension keys expected by the scorer
DIMENSION_KEYS = (
    "need_clarity",
    "budget_fit",
    "decision_maker_access",
    "urgency",
    "engagement",
)

_RISK_PENALTY_MAP: dict[str, int] = {
    "low": 3,
    "medium": 6,
    "high": 10,
    "critical": 15,
}


class DealScorer:
    """Calculate a DealScore from dimension scores and risk items.

    Usage::

        scorer = DealScorer()
        result = scorer.score(
            dimensions={
                "need_clarity": 80,
                "budget_fit": 70,
                "decision_maker_access": 60,
                "urgency": 85,
                "engagement": 70,
            },
            risks=[RiskItem(level=RiskLevel.LOW, reason="...")],
        )
    """

    def __init__(self) -> None:
        cfg = get_config()
        self._weights = cfg.scoring  # ScoringWeights model
        self._score_levels = cfg.score_levels  # ScoreLevelConfig

    def score(
        self,
        dimensions: dict[str, int],
        risks: list[RiskItem] | None = None,
    ) -> DealScore:
        """Calculate a DealScore from dimension scores and risks.

        Args:
            dimensions: Dict mapping dimension name → 0-100 score.
                        Missing dimensions should be omitted from the dict.
            risks: Optional list of identified risks.

        Returns:
            A fully-populated DealScore model.
        """
        risks = risks or []

        # ── Validate and fill dimensions ──
        filled: dict[str, int] = {}
        missing: list[str] = []
        for key in DIMENSION_KEYS:
            val = dimensions.get(key)
            if val is not None and 0 <= val <= 100:
                filled[key] = val
            else:
                filled[key] = 0
                missing.append(key)

        # ── Weighted score ──
        weighted = sum(
            filled[key] * getattr(self._weights, key)
            for key in DIMENSION_KEYS
        )

        # ── Risk penalty ──
        risk_penalty = sum(
            _RISK_PENALTY_MAP.get(r.level.value, 0) for r in risks
        )

        raw_score = round(weighted - risk_penalty)
        score = max(0, min(100, raw_score))
        level = DealLevel.from_score(score)

        # ── Confidence ──
        filled_count = len(DIMENSION_KEYS) - len(missing)
        ratio = filled_count / len(DIMENSION_KEYS)
        if ratio >= 0.8:
            confidence: str = "high"
        elif ratio >= 0.5:
            confidence = "medium"
        else:
            confidence = "low"

        # ── Reasoning ──
        reasoning: dict[str, str] = {}
        for key in DIMENSION_KEYS:
            w = getattr(self._weights, key)
            reasoning[key] = (
                f"{key}={filled[key]}×{w:.2f}={filled[key] * w:.1f}"
            )

        risk_lines = [f"{r.level.value}({r.reason})" for r in risks]
        summary_parts = [
            f"综合评分{score}分({level.value}级)",
            f"置信度{confidence}",
            f"已填充{filled_count}/{len(DIMENSION_KEYS)}维度",
        ]
        if missing:
            summary_parts.append(f"缺失: {', '.join(missing)}")
        if risk_penalty > 0:
            summary_parts.append(
                f"风险扣分-{risk_penalty} ({', '.join(risk_lines)})"
            )

        return DealScore(
            score=score,
            level=level,
            need_clarity=filled["need_clarity"],
            budget_fit=filled["budget_fit"],
            decision_maker_access=filled["decision_maker_access"],
            urgency=filled["urgency"],
            engagement=filled["engagement"],
            risk_penalty=risk_penalty,
            confidence=confidence,  # type: ignore[arg-type]
            filled_dimensions=filled_count,
            total_dimensions=len(DIMENSION_KEYS),
            missing_dimensions=missing,
            reasoning=reasoning,
            summary="；".join(summary_parts),
        )

    @property
    def weights(self) -> dict[str, float]:
        """Current scoring weights as a dict."""
        return {
            k: getattr(self._weights, k)
            for k in DIMENSION_KEYS
        }
