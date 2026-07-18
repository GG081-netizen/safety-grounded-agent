"""Customer health scoring — 5 dimensions with time decay.

Phase 4: Reads configurable weights and decay thresholds from AppConfig.
"""

from __future__ import annotations

from datetime import datetime, timezone

from conversation_agent.config import get_config
from conversation_agent.sales.models import (
    HealthScore,
    HealthStatus,
)

HEALTH_DIMENSION_KEYS = (
    "recent_contact",
    "responsiveness",
    "decision_maker_involvement",
    "need_clarity",
    "budget_timeline_clarity",
)


class HealthScorer:
    """Calculate a HealthScore with time decay applied to recent_contact.

    Usage::

        scorer = HealthScorer()
        result = scorer.score(
            dimensions={
                "recent_contact": 18,
                "responsiveness": 15,
                "decision_maker_involvement": 12,
                "need_clarity": 15,
                "budget_timeline_clarity": 10,
            },
            days_since_last_contact=10,
        )
    """

    def __init__(self) -> None:
        cfg = get_config()
        self._weights = cfg.health_weights  # HealthWeights
        self._decay = cfg.health_time_decay  # HealthTimeDecay
        self._status_cfg = cfg.health_status  # HealthStatusConfig

    def score(
        self,
        dimensions: dict[str, int],
        days_since_last_contact: int = 0,
    ) -> HealthScore:
        """Calculate a HealthScore from dimension scores.

        Args:
            dimensions: Dict mapping dimension name → 0-20 score.
                        Missing dimensions default to 0.
            days_since_last_contact: Days since the most recent interaction.
                                     Determines the time-decay multiplier for
                                     the recent_contact dimension.

        Returns:
            A fully-populated HealthScore model.
        """
        # ── Fill dimensions ──
        filled: dict[str, int] = {}
        for key in HEALTH_DIMENSION_KEYS:
            val = dimensions.get(key)
            if val is not None and 0 <= val <= 20:
                filled[key] = val
            else:
                filled[key] = 0

        # ── Apply time decay to recent_contact ──
        multiplier = self._decay.resolve(days_since_last_contact)
        original_recent = filled["recent_contact"]
        filled["recent_contact"] = round(original_recent * multiplier)

        # ── Total score ──
        total = sum(filled.values())
        status = HealthStatus.from_score(total)

        # ── Summary ──
        summary_parts = [
            f"健康度{total}分({status.value})",
        ]
        if multiplier < 1.0:
            summary_parts.append(
                f"距上次联系{days_since_last_contact}天，"
                f"时间衰减{multiplier:.1f}×"
            )

        return HealthScore(
            health_score=total,
            status=status,
            recent_contact=filled["recent_contact"],
            responsiveness=filled["responsiveness"],
            decision_maker_involvement=filled["decision_maker_involvement"],
            need_clarity=filled["need_clarity"],
            budget_timeline_clarity=filled["budget_timeline_clarity"],
            summary="；".join(summary_parts),
        )

    def resolve_decay(self, days: int) -> float:
        """Return the time-decay multiplier for a given number of days."""
        return self._decay.resolve(days)

    @property
    def max_score(self) -> int:
        return self._weights.max_total
