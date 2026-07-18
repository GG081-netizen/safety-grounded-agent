"""Comprehensive tests for DealScorer and HealthScorer (Phase 4)."""

import pytest
from conversation_agent.config import reset_config, get_config, ScoringWeights
from conversation_agent.sales.scorer import DealScorer, DIMENSION_KEYS
from conversation_agent.sales.health import HealthScorer, HEALTH_DIMENSION_KEYS
from conversation_agent.sales.models import (
    DealLevel,
    DealScore,
    HealthScore,
    HealthStatus,
    RiskItem,
    RiskLevel,
)


# ═══════════════════════════════════════════════════════════════════════════════
# DealScorer
# ═══════════════════════════════════════════════════════════════════════════════


class TestDealScorer:
    def test_full_dimensions_no_risks(self):
        scorer = DealScorer()
        result = scorer.score(
            dimensions={
                "need_clarity": 80,
                "budget_fit": 70,
                "decision_maker_access": 60,
                "urgency": 85,
                "engagement": 70,
            },
        )
        assert 0 <= result.score <= 100
        assert result.level in DealLevel
        assert result.filled_dimensions == 5
        assert result.total_dimensions == 5
        assert result.missing_dimensions == []
        assert result.confidence == "high"
        assert result.risk_penalty == 0

    def test_score_calculation_accuracy(self):
        """Verify the weighted arithmetic."""
        scorer = DealScorer()
        result = scorer.score(
            dimensions={
                "need_clarity": 50,
                "budget_fit": 50,
                "decision_maker_access": 50,
                "urgency": 50,
                "engagement": 50,
            },
            risks=[],
        )
        # Equal weights: 50 * 1.0 = 50
        assert result.score == 50

    def test_risk_penalty_applied(self):
        scorer = DealScorer()
        result = scorer.score(
            dimensions={
                "need_clarity": 80,
                "budget_fit": 80,
                "decision_maker_access": 80,
                "urgency": 80,
                "engagement": 80,
            },
            risks=[
                RiskItem(level=RiskLevel.HIGH, reason="预算不足"),
                RiskItem(level=RiskLevel.LOW, reason="轻微延迟"),
            ],
        )
        # HIGH=10, LOW=3 → penalty=13, 80-13=67
        assert result.risk_penalty == 13
        assert result.score == 67

    def test_critical_risk_heavy_penalty(self):
        scorer = DealScorer()
        result = scorer.score(
            dimensions={
                "need_clarity": 90,
                "budget_fit": 90,
                "decision_maker_access": 90,
                "urgency": 90,
                "engagement": 90,
            },
            risks=[RiskItem(level=RiskLevel.CRITICAL, reason="客户已有长期供应商")],
        )
        assert result.risk_penalty == 15
        assert result.score == 75  # 90 - 15

    def test_score_clamped_to_zero(self):
        scorer = DealScorer()
        result = scorer.score(
            dimensions={
                "need_clarity": 5,
                "budget_fit": 5,
                "decision_maker_access": 5,
                "urgency": 5,
                "engagement": 5,
            },
            risks=[RiskItem(level=RiskLevel.CRITICAL, reason="极差")],
        )
        # ~5 - 15 → clamped to 0
        assert result.score == 0
        assert result.level == DealLevel.C

    def test_score_clamped_to_100(self):
        scorer = DealScorer()
        result = scorer.score(
            dimensions={
                "need_clarity": 100,
                "budget_fit": 100,
                "decision_maker_access": 100,
                "urgency": 100,
                "engagement": 100,
            },
            risks=[],
        )
        assert result.score == 100
        assert result.level == DealLevel.S

    def test_missing_dimensions_reduce_confidence(self):
        scorer = DealScorer()
        result = scorer.score(
            dimensions={
                "need_clarity": 80,
                "budget_fit": 70,
            },
        )
        assert result.filled_dimensions == 2
        assert result.total_dimensions == 5
        assert len(result.missing_dimensions) == 3
        assert result.confidence == "low"

    def test_partial_dimensions_medium_confidence(self):
        scorer = DealScorer()
        result = scorer.score(
            dimensions={
                "need_clarity": 80,
                "budget_fit": 70,
                "decision_maker_access": 60,
            },
        )
        # 3/5 = 0.6 → medium
        assert result.confidence == "medium"

    def test_out_of_range_values_treated_as_missing(self):
        scorer = DealScorer()
        result = scorer.score(
            dimensions={
                "need_clarity": 150,  # out of range → treated as missing
                "budget_fit": 80,
            },
        )
        assert "need_clarity" in result.missing_dimensions
        assert result.filled_dimensions == 1

    def test_negative_values_treated_as_missing(self):
        scorer = DealScorer()
        result = scorer.score(
            dimensions={"need_clarity": -10, "budget_fit": 80},
        )
        assert "need_clarity" in result.missing_dimensions

    def test_empty_dimensions_all_missing(self):
        scorer = DealScorer()
        result = scorer.score(dimensions={})
        assert result.filled_dimensions == 0
        assert len(result.missing_dimensions) == 5
        assert result.confidence == "low"
        assert result.score == 0

    def test_reasoning_populated(self):
        scorer = DealScorer()
        result = scorer.score(
            dimensions={"need_clarity": 80, "budget_fit": 70},
        )
        assert len(result.reasoning) == 5  # all dimensions get reasoning
        assert "need_clarity" in result.reasoning
        assert "budget_fit" in result.reasoning

    def test_summary_non_empty(self):
        scorer = DealScorer()
        result = scorer.score(dimensions={"need_clarity": 60})
        assert result.summary
        assert "综合评分" in result.summary

    def test_dimension_keys_constant(self):
        """Ensure the scorer dimension keys match the model field names."""
        assert DIMENSION_KEYS == (
            "need_clarity",
            "budget_fit",
            "decision_maker_access",
            "urgency",
            "engagement",
        )

    def test_weights_from_config(self):
        """Scorer reads weights from AppConfig, not hardcoded."""
        scorer = DealScorer()
        w = scorer.weights
        assert len(w) == 5
        total = sum(w.values())
        assert abs(total - 1.0) < 0.01  # must sum to 1.0

    def test_score_level_boundaries(self):
        scorer = DealScorer()
        dims = {
            "need_clarity": 40, "budget_fit": 40,
            "decision_maker_access": 40, "urgency": 40, "engagement": 40,
        }
        assert scorer.score(dims, risks=[]).level == DealLevel.C

        dims = {k: 50 for k in DIMENSION_KEYS}
        assert scorer.score(dims, risks=[]).level == DealLevel.B

        dims = {k: 65 for k in DIMENSION_KEYS}
        assert scorer.score(dims, risks=[]).level == DealLevel.A

        dims = {k: 85 for k in DIMENSION_KEYS}
        assert scorer.score(dims, risks=[]).level == DealLevel.S


# ═══════════════════════════════════════════════════════════════════════════════
# HealthScorer
# ═══════════════════════════════════════════════════════════════════════════════


class TestHealthScorer:
    def test_full_dimensions_no_decay(self):
        scorer = HealthScorer()
        result = scorer.score(
            dimensions={
                "recent_contact": 20,
                "responsiveness": 15,
                "decision_maker_involvement": 15,
                "need_clarity": 15,
                "budget_timeline_clarity": 15,
            },
            days_since_last_contact=0,
        )
        assert result.health_score == 80
        assert result.status == HealthStatus.HEALTHY
        assert result.recent_contact == 20  # no decay

    def test_no_decay_at_day_one(self):
        scorer = HealthScorer()
        result = scorer.score(
            dimensions={
                "recent_contact": 20,
                "responsiveness": 10,
                "decision_maker_involvement": 10,
                "need_clarity": 10,
                "budget_timeline_clarity": 10,
            },
            days_since_last_contact=1,
        )
        # multiplier = 1.0
        assert result.recent_contact == 20

    def test_partial_decay_at_day_ten(self):
        scorer = HealthScorer()
        result = scorer.score(
            dimensions={
                "recent_contact": 20,
                "responsiveness": 10,
                "decision_maker_involvement": 10,
                "need_clarity": 10,
                "budget_timeline_clarity": 10,
            },
            days_since_last_contact=10,
        )
        # multiplier = 0.5 (falls in 14-day bucket)
        assert result.recent_contact == 10
        assert result.health_score == 50

    def test_heavy_decay_after_thirty_days(self):
        scorer = HealthScorer()
        result = scorer.score(
            dimensions={
                "recent_contact": 20,
                "responsiveness": 10,
                "decision_maker_involvement": 10,
                "need_clarity": 10,
                "budget_timeline_clarity": 10,
            },
            days_since_last_contact=60,
        )
        # multiplier = 0.3
        assert result.recent_contact == 6
        assert result.health_score == 46

    def test_cold_status(self):
        scorer = HealthScorer()
        result = scorer.score(
            dimensions={
                "recent_contact": 5,
                "responsiveness": 5,
                "decision_maker_involvement": 5,
                "need_clarity": 5,
                "budget_timeline_clarity": 5,
            },
        )
        assert result.status == HealthStatus.COLD

    def test_warm_status(self):
        scorer = HealthScorer()
        result = scorer.score(
            dimensions={
                "recent_contact": 10,
                "responsiveness": 10,
                "decision_maker_involvement": 10,
                "need_clarity": 10,
                "budget_timeline_clarity": 10,
            },
        )
        assert result.status == HealthStatus.WARM

    def test_healthy_status(self):
        scorer = HealthScorer()
        result = scorer.score(
            dimensions={
                "recent_contact": 20,
                "responsiveness": 15,
                "decision_maker_involvement": 15,
                "need_clarity": 15,
                "budget_timeline_clarity": 15,
            },
        )
        assert result.status == HealthStatus.HEALTHY

    def test_missing_dimensions_default_to_zero(self):
        scorer = HealthScorer()
        result = scorer.score(
            dimensions={"recent_contact": 20},
        )
        assert result.health_score == 20
        assert result.responsiveness == 0
        assert result.decision_maker_involvement == 0

    def test_out_of_range_clamped(self):
        scorer = HealthScorer()
        result = scorer.score(
            dimensions={"recent_contact": 25},  # >20, treated as missing → 0
        )
        assert result.recent_contact == 0

    def test_max_score_100(self):
        scorer = HealthScorer()
        result = scorer.score(
            dimensions={
                "recent_contact": 20,
                "responsiveness": 20,
                "decision_maker_involvement": 20,
                "need_clarity": 20,
                "budget_timeline_clarity": 20,
            },
            days_since_last_contact=0,
        )
        assert result.health_score == 100
        assert result.status == HealthStatus.HEALTHY

    def test_dimension_keys_constant(self):
        assert HEALTH_DIMENSION_KEYS == (
            "recent_contact",
            "responsiveness",
            "decision_maker_involvement",
            "need_clarity",
            "budget_timeline_clarity",
        )

    def test_resolve_decay(self):
        scorer = HealthScorer()
        assert scorer.resolve_decay(0) == 1.0
        assert scorer.resolve_decay(1) == 1.0
        assert scorer.resolve_decay(3) == 0.9
        assert scorer.resolve_decay(7) == 0.7
        assert scorer.resolve_decay(14) == 0.5
        assert scorer.resolve_decay(30) == 0.3
        assert scorer.resolve_decay(100) == 0.3  # floor

    def test_summary_includes_decay_info(self):
        scorer = HealthScorer()
        result = scorer.score(
            dimensions={
                "recent_contact": 20,
                "responsiveness": 15,
                "decision_maker_involvement": 15,
                "need_clarity": 15,
                "budget_timeline_clarity": 15,
            },
            days_since_last_contact=14,
        )
        assert "时间衰减" in result.summary

    def test_max_score_property(self):
        scorer = HealthScorer()
        assert scorer.max_score == 100

    def test_decay_boundary_behavior(self):
        """Test exact boundary thresholds."""
        scorer = HealthScorer()
        # Day 1 → 1.0
        assert scorer.resolve_decay(1) == 1.0
        # Day 3 → 0.9
        assert scorer.resolve_decay(3) == 0.9
        # Day 4 → 0.9 (still in 3-day bucket? No, should fall to next)
        # Actually: thresholds are [(1,1.0), (3,0.9), (7,0.7), (14,0.5), (30,0.3)]
        # Day 4 > 3, so it goes to the 7-day bucket → 0.7
        assert scorer.resolve_decay(4) == 0.7
        # Day 7 → 0.7
        assert scorer.resolve_decay(7) == 0.7
        # Day 8 → 0.5 (falls in 14-day bucket)
        assert scorer.resolve_decay(8) == 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# Scorer + Model integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestScorerIntegration:
    def test_deal_score_round_trip(self):
        scorer = DealScorer()
        result = scorer.score(
            dimensions={
                "need_clarity": 80,
                "budget_fit": 70,
                "decision_maker_access": 60,
                "urgency": 85,
                "engagement": 70,
            },
            risks=[RiskItem(level=RiskLevel.MEDIUM, reason="竞争")],
        )
        json_str = result.model_dump_json()
        reloaded = DealScore.model_validate_json(json_str)
        assert reloaded.score == result.score
        assert reloaded.level == result.level
        assert reloaded.confidence == result.confidence

    def test_health_score_round_trip(self):
        scorer = HealthScorer()
        result = scorer.score(
            dimensions={
                "recent_contact": 20,
                "responsiveness": 15,
                "decision_maker_involvement": 15,
                "need_clarity": 15,
                "budget_timeline_clarity": 15,
            },
            days_since_last_contact=10,
        )
        json_str = result.model_dump_json()
        reloaded = HealthScore.model_validate_json(json_str)
        assert reloaded.health_score == result.health_score
        assert reloaded.status == result.status

    def test_scorer_can_attach_to_profile(self):
        """Scorer output can be attached to CustomerProfile."""
        from conversation_agent.sales.models import CustomerProfile

        scorer = DealScorer()
        ds = scorer.score(
            dimensions={
                "need_clarity": 80,
                "budget_fit": 70,
                "decision_maker_access": 60,
                "urgency": 85,
                "engagement": 70,
            },
        )
        hs = HealthScorer().score(
            dimensions={
                "recent_contact": 20,
                "responsiveness": 15,
                "decision_maker_involvement": 15,
                "need_clarity": 15,
                "budget_timeline_clarity": 15,
            },
        )
        cp = CustomerProfile(customer_id="c001", customer_name="test")
        cp.set_deal_score(ds)
        cp.set_health_score(hs)
        assert cp.has_deal_score
        assert cp.has_health_score
        assert cp.deal_score.score == ds.score
        assert cp.health_score.health_score == hs.health_score
