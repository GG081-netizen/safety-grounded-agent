"""Comprehensive tests for sales business Pydantic models (Phase 2)."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from conversation_agent.sales.models import (
    # Enums
    Intent,
    SalesStage,
    CustomerStatus,
    ProductCategory,
    RiskLevel,
    InteractionType,
    DealLevel,
    HealthStatus,
    # Components
    Contact,
    ProcurementItem,
    RiskItem,
    ProcurementSignals,
    DealScore,
    HealthScore,
    FollowUpSuggestion,
    # Records
    CustomerProfile,
    InteractionRecord,
    IntentResult,
    # Protocol (Phase 1)
    InteractionMetadata,
    ToolResult,
    Interaction,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Enum tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestSalesStage:
    def test_linear_progression(self):
        """Stages should follow the defined order."""
        order = SalesStage.active_stages()
        assert len(order) == 6
        assert order == [
            SalesStage.LEAD,
            SalesStage.REQUIREMENT_CONFIRMATION,
            SalesStage.QUOTATION,
            SalesStage.NEGOTIATION,
            SalesStage.PROCUREMENT_APPROVAL,
            SalesStage.CONTRACT_SIGNING,
        ]

    def test_next_stages_normal(self):
        assert SalesStage.LEAD.next_stages() == [SalesStage.REQUIREMENT_CONFIRMATION]
        assert SalesStage.QUOTATION.next_stages() == [SalesStage.NEGOTIATION]
        assert SalesStage.CONTRACT_SIGNING.next_stages() == []

    def test_next_stages_terminal(self):
        assert SalesStage.WON.next_stages() == []
        assert SalesStage.LOST.next_stages() == []

    def test_can_transition_forward(self):
        assert SalesStage.LEAD.can_transition_to(SalesStage.REQUIREMENT_CONFIRMATION)
        assert SalesStage.NEGOTIATION.can_transition_to(SalesStage.PROCUREMENT_APPROVAL)

    def test_can_transition_skip_rejected(self):
        """Cannot skip stages in normal flow."""
        assert not SalesStage.LEAD.can_transition_to(SalesStage.QUOTATION)

    def test_can_transition_to_won_lost_from_any(self):
        """Any non-terminal can close as WON or LOST."""
        for stage in SalesStage.active_stages():
            assert stage.can_transition_to(SalesStage.WON), f"{stage} → WON"
            assert stage.can_transition_to(SalesStage.LOST), f"{stage} → LOST"

    def test_can_transition_re_qualification(self):
        """Any non-terminal can go back to LEAD."""
        assert SalesStage.QUOTATION.can_transition_to(SalesStage.LEAD)
        assert SalesStage.CONTRACT_SIGNING.can_transition_to(SalesStage.LEAD)

    def test_cannot_transition_from_terminal(self):
        assert not SalesStage.WON.can_transition_to(SalesStage.LEAD)
        assert not SalesStage.LOST.can_transition_to(SalesStage.LEAD)
        assert not SalesStage.WON.can_transition_to(SalesStage.QUOTATION)

    def test_is_terminal(self):
        assert SalesStage.WON.is_terminal()
        assert SalesStage.LOST.is_terminal()
        assert not SalesStage.LEAD.is_terminal()
        assert not SalesStage.CONTRACT_SIGNING.is_terminal()

    def test_is_won_is_lost(self):
        assert SalesStage.WON.is_won()
        assert not SalesStage.WON.is_lost()
        assert SalesStage.LOST.is_lost()
        assert not SalesStage.LOST.is_won()

    def test_from_string_valid(self):
        assert SalesStage.from_string("lead") == SalesStage.LEAD
        assert SalesStage.from_string("  QuOtAtiOn  ") == SalesStage.QUOTATION
        assert SalesStage.from_string("WON") == SalesStage.WON

    def test_from_string_invalid(self):
        assert SalesStage.from_string("garbage") is None
        assert SalesStage.from_string("") is None


class TestDealLevel:
    def test_from_score_boundaries(self):
        assert DealLevel.from_score(0) == DealLevel.C
        assert DealLevel.from_score(40) == DealLevel.C
        assert DealLevel.from_score(41) == DealLevel.B
        assert DealLevel.from_score(60) == DealLevel.B
        assert DealLevel.from_score(61) == DealLevel.A
        assert DealLevel.from_score(80) == DealLevel.A
        assert DealLevel.from_score(81) == DealLevel.S
        assert DealLevel.from_score(100) == DealLevel.S


class TestHealthStatus:
    def test_from_score_boundaries(self):
        assert HealthStatus.from_score(0) == HealthStatus.COLD
        assert HealthStatus.from_score(40) == HealthStatus.COLD
        assert HealthStatus.from_score(41) == HealthStatus.WARM
        assert HealthStatus.from_score(70) == HealthStatus.WARM
        assert HealthStatus.from_score(71) == HealthStatus.HEALTHY
        assert HealthStatus.from_score(100) == HealthStatus.HEALTHY


# ═══════════════════════════════════════════════════════════════════════════════
# Component validation tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestContact:
    def test_create_minimal(self):
        c = Contact(name="张三")
        assert c.name == "张三"
        assert c.influence_level == "unknown"
        assert not c.is_decision_maker

    def test_create_full(self):
        c = Contact(
            name="李经理",
            title="采购主管",
            department="采购部",
            influence_level="high",
            email="li@example.com",
            phone="13800001111",
        )
        assert c.is_decision_maker
        assert c.department == "采购部"

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            Contact(name="")


class TestProcurementItem:
    def test_create_minimal(self):
        pi = ProcurementItem(product_name="笔记本电脑")
        assert pi.product_name == "笔记本电脑"
        assert pi.category is None
        assert not pi.has_budget
        assert not pi.has_category

    def test_create_full(self):
        pi = ProcurementItem(
            product_name="服务器",
            category=ProductCategory.IT_EQUIPMENT,
            quantity=50,
            unit_budget=100000,
            total_budget=5000000,
            requirements=["3年保修", "7x24支持"],
        )
        assert pi.has_budget
        assert pi.has_category
        assert len(pi.requirements) == 2

    def test_negative_quantity_rejected(self):
        with pytest.raises(ValidationError):
            ProcurementItem(product_name="test", quantity=0)

    def test_negative_budget_rejected(self):
        with pytest.raises(ValidationError):
            ProcurementItem(product_name="test", total_budget=-100)


class TestRiskItem:
    def test_create(self):
        r = RiskItem(level=RiskLevel.HIGH, reason="预算审批未完成")
        assert r.level == RiskLevel.HIGH
        assert r.is_high_priority
        assert not r.is_critical

    def test_critical(self):
        r = RiskItem(level=RiskLevel.CRITICAL, reason="客户已明确倾向竞品")
        assert r.is_critical
        assert r.is_high_priority

    def test_empty_reason_rejected(self):
        with pytest.raises(ValidationError):
            RiskItem(level=RiskLevel.LOW, reason="")


class TestProcurementSignals:
    def test_empty(self):
        ps = ProcurementSignals()
        assert ps.filled_signal_count == 0
        assert not ps.has_competition

    def test_full(self):
        ps = ProcurementSignals(
            urgency_signal="30天内交付",
            budget_signal="预算已批复",
            decision_signal="CTO直接决策",
            competition_signal="友商浪潮报价更低",
            engagement_signal="每周主动沟通",
        )
        assert ps.filled_signal_count == 5
        assert ps.has_competition


class TestDealScore:
    def test_create_minimal(self):
        ds = DealScore(
            score=50,
            level=DealLevel.B,
            need_clarity=50,
            budget_fit=50,
            decision_maker_access=50,
            urgency=50,
            engagement=50,
            risk_penalty=0,
        )
        assert ds.score == 50
        assert ds.coverage_ratio == 1.0

    def test_high_score_high_confidence(self):
        ds = DealScore(
            score=90,
            level=DealLevel.S,
            need_clarity=95,
            budget_fit=90,
            decision_maker_access=85,
            urgency=95,
            engagement=85,
            risk_penalty=0,
            missing_dimensions=[],
        )
        assert ds.high_confidence
        assert ds.coverage_ratio == 1.0

    def test_low_coverage_drives_low_confidence(self):
        ds = DealScore(
            score=30,
            level=DealLevel.C,
            need_clarity=30,
            budget_fit=0,
            decision_maker_access=0,
            urgency=0,
            engagement=0,
            risk_penalty=10,
            missing_dimensions=["budget_fit", "decision_maker_access", "urgency", "engagement"],
        )
        assert ds.low_confidence
        assert ds.filled_dimensions == 1
        assert ds.coverage_ratio == 0.2

    def test_score_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            DealScore(
                score=150,
                level=DealLevel.S,
                need_clarity=10,
                budget_fit=10,
                decision_maker_access=10,
                urgency=10,
                engagement=10,
                risk_penalty=0,
            )

    def test_dimension_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            DealScore(
                score=50,
                level=DealLevel.B,
                need_clarity=150,
                budget_fit=50,
                decision_maker_access=50,
                urgency=50,
                engagement=50,
                risk_penalty=0,
            )

    def test_dimension_values_dict(self):
        ds = DealScore(
            score=50,
            level=DealLevel.B,
            need_clarity=60,
            budget_fit=50,
            decision_maker_access=40,
            urgency=55,
            engagement=45,
            risk_penalty=0,
        )
        dims = ds.dimension_values
        assert dims["need_clarity"] == 60
        assert dims["engagement"] == 45
        assert len(dims) == 5


class TestHealthScore:
    def test_create(self):
        hs = HealthScore(
            health_score=75,
            status=HealthStatus.HEALTHY,
            recent_contact=15,
            responsiveness=15,
            decision_maker_involvement=15,
            need_clarity=15,
            budget_timeline_clarity=15,
        )
        assert hs.health_score == 75
        assert hs.is_healthy
        assert not hs.is_cold

    def test_cold_health(self):
        hs = HealthScore(
            health_score=20,
            status=HealthStatus.COLD,
            recent_contact=5,
            responsiveness=5,
            decision_maker_involvement=3,
            need_clarity=4,
            budget_timeline_clarity=3,
        )
        assert hs.is_cold
        assert not hs.is_healthy

    def test_dim_sum_exceeds_100_rejected(self):
        with pytest.raises(ValidationError):
            HealthScore(
                health_score=50,
                status=HealthStatus.WARM,
                recent_contact=30,
                responsiveness=30,
                decision_maker_involvement=30,
                need_clarity=30,
                budget_timeline_clarity=30,
            )

    def test_dimension_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            HealthScore(
                health_score=50,
                status=HealthStatus.WARM,
                recent_contact=25,
                responsiveness=10,
                decision_maker_involvement=10,
                need_clarity=10,
                budget_timeline_clarity=10,
            )

    def test_dimension_values(self):
        hs = HealthScore(
            health_score=50,
            status=HealthStatus.WARM,
            recent_contact=10,
            responsiveness=10,
            decision_maker_involvement=10,
            need_clarity=10,
            budget_timeline_clarity=10,
        )
        dims = hs.dimension_values
        assert len(dims) == 5
        assert dims["recent_contact"] == 10


class TestFollowUpSuggestion:
    def test_create(self):
        fs = FollowUpSuggestion(
            follow_up_priority="high",
            recommended_date="2026-06-15",
            recommended_action="电话确认报价反馈",
            reason="报价已发送3天未回复",
        )
        assert fs.is_urgent
        assert fs.has_date

    def test_low_priority_not_urgent(self):
        fs = FollowUpSuggestion(
            follow_up_priority="low",
            recommended_action="发送行业白皮书",
            reason="保持联系",
        )
        assert not fs.is_urgent
        assert not fs.has_date


# ═══════════════════════════════════════════════════════════════════════════════
# Top-level record tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCustomerProfile:
    def test_create_minimal(self):
        cp = CustomerProfile(customer_id="c001", customer_name="测试公司")
        assert cp.customer_name == "测试公司"
        assert cp.schema_version == 1
        assert cp.version == 1
        assert cp.sales_stage == SalesStage.LEAD
        assert cp.status == CustomerStatus.ACTIVE
        assert cp.contact_count == 0
        assert not cp.has_contacts
        assert cp.primary_contact is None
        assert not cp.has_deal_score
        assert not cp.has_health_score

    def test_create_full(self):
        cp = CustomerProfile(
            customer_id="cust_huawei",
            customer_name="华为技术",
            aliases=["华为", "Huawei"],
            source="展会",
            industry="Telecom",
            company_size="10000+",
            contacts=[
                Contact(name="王总", title="CTO", influence_level="high"),
                Contact(name="张经理", title="采购经理", influence_level="medium"),
            ],
            procurement_items=[
                ProcurementItem(
                    product_name="服务器",
                    category=ProductCategory.IT_EQUIPMENT,
                    quantity=50,
                    total_budget=5000000,
                ),
            ],
            budget_range="500-800万",
            procurement_cycle="3个月",
            competitors=["浪潮", "戴尔"],
            decision_makers=["王总"],
        )
        assert cp.customer_name == "华为技术"
        assert len(cp.aliases) == 2
        assert cp.contact_count == 2
        assert cp.has_contacts
        assert cp.primary_contact.name == "王总"
        assert cp.has_decision_maker_contact
        assert len(cp.decision_maker_contacts) == 1
        assert cp.item_count == 1
        assert cp.has_procurement_items

    def test_empty_customer_id_rejected(self):
        with pytest.raises(ValidationError):
            CustomerProfile(customer_id="", customer_name="test")

    def test_invalid_customer_id_pattern_rejected(self):
        with pytest.raises(ValidationError):
            CustomerProfile(customer_id="invalid id", customer_name="test")

    def test_empty_customer_name_rejected(self):
        with pytest.raises(ValidationError):
            CustomerProfile(customer_id="c001", customer_name="")

    def test_transition_to_valid(self):
        cp = CustomerProfile(customer_id="c001", customer_name="test")
        assert cp.transition_to(SalesStage.REQUIREMENT_CONFIRMATION)
        assert cp.sales_stage == SalesStage.REQUIREMENT_CONFIRMATION

    def test_transition_to_invalid(self):
        cp = CustomerProfile(customer_id="c001", customer_name="test")
        assert not cp.transition_to(SalesStage.QUOTATION)  # skip stage
        assert cp.sales_stage == SalesStage.LEAD  # unchanged

    def test_transition_to_won(self):
        cp = CustomerProfile(customer_id="c001", customer_name="test")
        assert cp.transition_to(SalesStage.WON)
        assert cp.is_won
        assert cp.is_terminal_stage

    def test_transition_to_lost(self):
        cp = CustomerProfile(customer_id="c001", customer_name="test")
        assert cp.transition_to(SalesStage.LOST)
        assert cp.is_lost
        assert cp.is_terminal_stage

    def test_add_contact(self):
        cp = CustomerProfile(customer_id="c001", customer_name="test")
        assert cp.contact_count == 0
        cp.add_contact(Contact(name="新联系人"))
        assert cp.contact_count == 1

    def test_add_risk(self):
        cp = CustomerProfile(customer_id="c001", customer_name="test")
        cp.add_risk(RiskItem(level=RiskLevel.HIGH, reason="test"))
        assert cp.has_risks
        assert cp.high_priority_risk_count == 1

    def test_set_deal_score(self):
        cp = CustomerProfile(customer_id="c001", customer_name="test")
        assert not cp.has_deal_score
        ds = DealScore(
            score=80, level=DealLevel.A,
            need_clarity=80, budget_fit=80, decision_maker_access=80,
            urgency=80, engagement=80, risk_penalty=0,
        )
        cp.set_deal_score(ds)
        assert cp.has_deal_score
        assert cp.deal_score.score == 80

    def test_set_health_score(self):
        cp = CustomerProfile(customer_id="c001", customer_name="test")
        hs = HealthScore(
            health_score=80, status=HealthStatus.HEALTHY,
            recent_contact=20, responsiveness=15, decision_maker_involvement=15,
            need_clarity=15, budget_timeline_clarity=15,
        )
        cp.set_health_score(hs)
        assert cp.has_health_score
        assert cp.health_score.health_score == 80

    def test_bump_version(self):
        cp = CustomerProfile(customer_id="c001", customer_name="test")
        v1 = cp.version
        cp.bump_version()
        assert cp.version == v1 + 1

    def test_days_since_update(self):
        cp = CustomerProfile(customer_id="c001", customer_name="test")
        # Should be 0 since just created
        assert cp.days_since_update >= 0

    def test_no_deal_score_by_default(self):
        cp = CustomerProfile(customer_id="c001", customer_name="test")
        assert not cp.has_deal_score
        assert cp.deal_score is None

    def test_no_risks_by_default(self):
        cp = CustomerProfile(customer_id="c001", customer_name="test")
        assert not cp.has_risks
        assert cp.high_priority_risk_count == 0

    def test_no_decision_maker_by_default(self):
        cp = CustomerProfile(customer_id="c001", customer_name="test")
        assert not cp.has_decision_maker_contact
        assert cp.decision_maker_contacts == []


class TestInteractionRecord:
    def test_create_minimal(self):
        ir = InteractionRecord(interaction_id="int_001", customer_id="c001")
        assert ir.interaction_id == "int_001"
        assert ir.customer_id == "c001"
        assert ir.type == InteractionType.NOTE
        assert not ir.has_signals
        assert not ir.has_raw_text
        assert ir.word_count == 0

    def test_create_full(self):
        ir = InteractionRecord(
            interaction_id="int_002",
            customer_id="c001",
            type=InteractionType.MEETING,
            raw_text="会议讨论了服务器采购需求，客户预算约500万，需30天交付。",
            summary="服务器采购需求确认会",
            key_quotes=["预算500万左右", "30天内必须交付"],
            extracted_facts={"budget": "500万", "timeline": "30天"},
            procurement_signals=ProcurementSignals(
                urgency_signal="30天交付",
                budget_signal="预算500万已批复",
            ),
            risks=[RiskItem(level=RiskLevel.MEDIUM, reason="友商报价更低")],
            next_actions=["发送方案书", "安排技术演示"],
        )
        assert ir.word_count > 0
        assert ir.has_raw_text
        assert ir.has_signals
        assert ir.has_risks
        assert len(ir.key_quotes) == 2
        assert len(ir.next_actions) == 2

    def test_empty_interaction_id_rejected(self):
        with pytest.raises(ValidationError):
            InteractionRecord(interaction_id="", customer_id="c001")

    def test_empty_customer_id_rejected(self):
        with pytest.raises(ValidationError):
            InteractionRecord(interaction_id="int_001", customer_id="")


class TestIntentResult:
    def test_high_confidence(self):
        ir = IntentResult(intent=Intent.CUSTOMER_INTAKE, confidence=0.92)
        assert ir.high_confidence
        assert not ir.medium_confidence
        assert not ir.low_confidence
        assert ir.confidence_label == "high"
        assert not ir.is_ambiguous

    def test_medium_confidence(self):
        ir = IntentResult(intent=Intent.QUERY, confidence=0.55)
        assert not ir.high_confidence
        assert ir.medium_confidence
        assert not ir.low_confidence
        assert ir.confidence_label == "medium"

    def test_low_confidence(self):
        ir = IntentResult(intent=Intent.EMAIL_DRAFT, confidence=0.3)
        assert not ir.high_confidence
        assert not ir.medium_confidence
        assert ir.low_confidence
        assert ir.confidence_label == "low"

    def test_ambiguous(self):
        ir = IntentResult(
            intent=Intent.MEETING_NOTE,
            confidence=0.6,
            alternative_intents=[Intent.CUSTOMER_INTAKE, Intent.QUERY],
        )
        assert ir.is_ambiguous
        assert len(ir.alternative_intents) == 2

    def test_confidence_boundary(self):
        assert IntentResult(intent=Intent.QUERY, confidence=0.8).high_confidence
        assert IntentResult(intent=Intent.QUERY, confidence=0.79).medium_confidence
        assert IntentResult(intent=Intent.QUERY, confidence=0.5).medium_confidence
        assert IntentResult(intent=Intent.QUERY, confidence=0.49).low_confidence

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            IntentResult(intent=Intent.QUERY, confidence=1.5)
        with pytest.raises(ValidationError):
            IntentResult(intent=Intent.QUERY, confidence=-0.1)


# ═══════════════════════════════════════════════════════════════════════════════
# Agent Runtime Protocol tests (Phase 1 compatibility)
# ═══════════════════════════════════════════════════════════════════════════════


class TestInteractionMetadata:
    def test_defaults(self):
        meta = InteractionMetadata()
        assert meta.session_id == ""
        assert meta.intent is None
        assert meta.llm_calls == 0
        assert meta.input_tokens == 0
        assert meta.output_tokens == 0
        assert meta.cost_usd == 0.0
        assert meta.latency_ms == 0

    def test_full(self):
        meta = InteractionMetadata(
            session_id="sess-001",
            intent="customer_intake",
            intent_confidence=0.92,
            tools_called=["customer_memory_search", "customer_memory_update"],
            llm_calls=2,
            input_tokens=1200,
            output_tokens=800,
            cost_usd=0.015,
            latency_ms=3400,
        )
        assert meta.session_id == "sess-001"
        assert len(meta.tools_called) == 2


class TestToolResult:
    def test_success(self):
        tr = ToolResult(success=True, tool_name="customer_memory_search", summary="Found 1 customer")
        assert tr.success
        assert not tr.has_errors
        assert not tr.has_warnings
        assert not tr.is_partial

    def test_failure(self):
        tr = ToolResult(success=False, tool_name="update", errors=["Permission denied"], summary="Failed")
        assert not tr.success
        assert tr.has_errors
        assert not tr.is_partial

    def test_partial_success(self):
        tr = ToolResult(
            success=True,
            tool_name="search",
            warnings=["Results truncated to 100"],
            summary="Found 500, showing 100",
        )
        assert tr.success
        assert tr.has_warnings
        assert not tr.has_errors
        assert tr.is_partial

    def test_data_types(self):
        """ToolResult.data should accept dict, list, str, or None."""
        assert ToolResult(success=True, data={"key": "val"}).data == {"key": "val"}
        assert ToolResult(success=True, data=[1, 2, 3]).data == [1, 2, 3]
        assert ToolResult(success=True, data="string").data == "string"
        assert ToolResult(success=True, data=None).data is None


class TestInteraction:
    def test_create_minimal(self):
        interaction = Interaction(session_id="s1", user_input="hello")
        assert interaction.session_id == "s1"
        assert interaction.user_input == "hello"
        assert interaction.intent_result is None
        assert interaction.tools_called == []
        assert interaction.agent_response == ""
        assert interaction.tool_success_count == 0
        assert interaction.tool_failure_count == 0
        assert not interaction.all_tools_succeeded

    def test_create_full(self):
        interaction = Interaction(
            session_id="sess-002",
            user_input="分析华为服务器采购会议",
            intent_result=IntentResult(intent=Intent.MEETING_NOTE, confidence=0.9, reasoning="会议关键词"),
            tools_called=[
                ToolResult(success=True, tool_name="search", data={"id": "c001"}, summary="found"),
                ToolResult(success=True, tool_name="update", summary="updated"),
            ],
            agent_response="已完成分析，客户成交概率较高。",
            metadata=InteractionMetadata(session_id="sess-002", input_tokens=800, output_tokens=400),
        )
        assert interaction.intent_result.intent == Intent.MEETING_NOTE
        assert interaction.tool_success_count == 2
        assert interaction.tool_failure_count == 0
        assert interaction.all_tools_succeeded

    def test_mixed_tool_results(self):
        interaction = Interaction(
            session_id="s3",
            user_input="test",
            tools_called=[
                ToolResult(success=True, tool_name="t1", summary="ok"),
                ToolResult(success=False, tool_name="t2", errors=["fail"], summary="bad"),
            ],
        )
        assert interaction.tool_success_count == 1
        assert interaction.tool_failure_count == 1
        assert not interaction.all_tools_succeeded

    def test_empty_session_id_rejected(self):
        with pytest.raises(ValidationError):
            Interaction(session_id="", user_input="test")


# ═══════════════════════════════════════════════════════════════════════════════
# JSON round-trip tests — model → json → model
# ═══════════════════════════════════════════════════════════════════════════════


class TestRoundTrip:
    """Verify every core model survives a full JSON serialize-deserialize cycle.

    This is critical for Phase 3 (storage layer) which is fundamentally
    object ↔ JSON on disk.
    """

    @staticmethod
    def _round_trip(obj):
        """Serialize to JSON and deserialize back, returning the reconstructed object."""
        json_str = obj.model_dump_json()
        return type(obj).model_validate_json(json_str)

    @staticmethod
    def _assert_field_equal(a, b, fields: list[str]):
        """Compare named fields between two model instances."""
        for f in fields:
            va = getattr(a, f)
            vb = getattr(b, f)
            assert va == vb, f"Field '{f}' mismatch: {va!r} != {vb!r}"

    # ── Component models ──

    def test_contact_round_trip(self):
        c1 = Contact(
            name="李经理",
            title="采购主管",
            department="采购部",
            influence_level="high",
            email="li@example.com",
            phone="13800001111",
        )
        c2 = self._round_trip(c1)
        assert c1 == c2
        assert c2.is_decision_maker

    def test_contact_minimal_round_trip(self):
        c1 = Contact(name="张三")
        c2 = self._round_trip(c1)
        assert c1 == c2

    def test_procurement_item_round_trip(self):
        pi1 = ProcurementItem(
            product_name="服务器",
            category=ProductCategory.IT_EQUIPMENT,
            quantity=50,
            unit_budget=100000,
            total_budget=5000000,
            requirements=["3年保修", "7x24支持"],
        )
        pi2 = self._round_trip(pi1)
        assert pi1 == pi2
        assert pi2.has_budget
        assert pi2.has_category

    def test_risk_item_round_trip(self):
        r1 = RiskItem(level=RiskLevel.CRITICAL, reason="客户已明确倾向竞品")
        r2 = self._round_trip(r1)
        assert r1 == r2
        assert r2.is_critical

    def test_procurement_signals_round_trip(self):
        ps1 = ProcurementSignals(
            urgency_signal="30天交付",
            budget_signal="预算已批复",
            decision_signal="CTO直接决策",
            competition_signal="友商报价更低",
            engagement_signal="每周沟通",
        )
        ps2 = self._round_trip(ps1)
        assert ps1 == ps2
        assert ps2.filled_signal_count == 5
        assert ps2.has_competition

    def test_procurement_signals_empty_round_trip(self):
        ps1 = ProcurementSignals()
        ps2 = self._round_trip(ps1)
        assert ps1 == ps2
        assert ps2.filled_signal_count == 0

    # ── Scoring models ──

    def test_deal_score_round_trip(self):
        ds1 = DealScore(
            score=75,
            level=DealLevel.A,
            need_clarity=80,
            budget_fit=70,
            decision_maker_access=60,
            urgency=85,
            engagement=70,
            risk_penalty=3,
            confidence="high",
            missing_dimensions=["decision_maker_access"],
            reasoning={"need_clarity": "客户需求明确", "budget_fit": "预算匹配"},
            summary="成交概率较高",
        )
        ds2 = self._round_trip(ds1)
        assert ds1 == ds2
        assert ds2.coverage_ratio == ds1.coverage_ratio
        assert ds2.high_confidence
        assert ds2.missing_dimensions == ["decision_maker_access"]

    def test_deal_score_minimal_round_trip(self):
        ds1 = DealScore(
            score=50,
            level=DealLevel.B,
            need_clarity=50,
            budget_fit=50,
            decision_maker_access=50,
            urgency=50,
            engagement=50,
            risk_penalty=0,
        )
        ds2 = self._round_trip(ds1)
        assert ds1 == ds2

    def test_health_score_round_trip(self):
        hs1 = HealthScore(
            health_score=75,
            status=HealthStatus.HEALTHY,
            recent_contact=15,
            responsiveness=15,
            decision_maker_involvement=15,
            need_clarity=15,
            budget_timeline_clarity=15,
            summary="客户关系健康",
        )
        hs2 = self._round_trip(hs1)
        assert hs1 == hs2
        assert hs2.is_healthy
        assert hs2.dimension_values == hs1.dimension_values

    def test_health_score_cold_round_trip(self):
        hs1 = HealthScore(
            health_score=20,
            status=HealthStatus.COLD,
            recent_contact=5,
            responsiveness=5,
            decision_maker_involvement=3,
            need_clarity=4,
            budget_timeline_clarity=3,
        )
        hs2 = self._round_trip(hs1)
        assert hs1 == hs2
        assert hs2.is_cold

    def test_follow_up_suggestion_round_trip(self):
        fs1 = FollowUpSuggestion(
            follow_up_priority="high",
            recommended_date="2026-06-15",
            recommended_action="电话确认报价反馈",
            reason="报价已发送3天",
        )
        fs2 = self._round_trip(fs1)
        assert fs1 == fs2
        assert fs2.is_urgent

    # ── Top-level records ──

    def test_customer_profile_minimal_round_trip(self):
        cp1 = CustomerProfile(customer_id="c001", customer_name="测试公司")
        cp2 = self._round_trip(cp1)
        self._assert_field_equal(cp1, cp2, [
            "customer_id", "customer_name", "schema_version", "version",
            "sales_stage", "status",
        ])
        assert cp2.contact_count == 0
        assert cp2.deal_score is None
        assert cp2.health_score is None

    def test_customer_profile_full_round_trip(self):
        cp1 = CustomerProfile(
            customer_id="cust_huawei",
            customer_name="华为技术",
            aliases=["华为", "Huawei"],
            source="展会",
            industry="Telecom",
            company_size="10000+",
            contacts=[
                Contact(name="王总", title="CTO", influence_level="high", email="wang@huawei.com"),
                Contact(name="张经理", title="采购经理", influence_level="medium"),
            ],
            procurement_items=[
                ProcurementItem(
                    product_name="服务器",
                    category=ProductCategory.IT_EQUIPMENT,
                    quantity=50,
                    total_budget=5000000,
                    requirements=["3年保修"],
                ),
            ],
            budget_range="500-800万",
            procurement_cycle="3个月",
            timeline="Q3交付",
            competitors=["浪潮", "戴尔"],
            decision_makers=["王总"],
            sales_stage=SalesStage.REQUIREMENT_CONFIRMATION,
            status=CustomerStatus.ACTIVE,
            deal_score=DealScore(
                score=80,
                level=DealLevel.A,
                need_clarity=85,
                budget_fit=80,
                decision_maker_access=75,
                urgency=80,
                engagement=80,
                risk_penalty=0,
                missing_dimensions=[],
                reasoning={"need_clarity": "非常明确"},
            ),
            health_score=HealthScore(
                health_score=80,
                status=HealthStatus.HEALTHY,
                recent_contact=20,
                responsiveness=15,
                decision_maker_involvement=15,
                need_clarity=15,
                budget_timeline_clarity=15,
            ),
            risks=[RiskItem(level=RiskLevel.MEDIUM, reason="友商报价更低")],
            next_actions=["发送方案书", "安排技术演示"],
            tags=["VIP", "大客户"],
        )
        cp2 = self._round_trip(cp1)

        # Structural comparison (datetimes won't be byte-identical)
        self._assert_field_equal(cp1, cp2, [
            "customer_id", "customer_name", "aliases", "source", "industry",
            "company_size", "budget_range", "procurement_cycle", "timeline",
            "competitors", "decision_makers", "sales_stage", "status",
            "next_actions", "tags", "schema_version",
        ])

        # Nested models
        assert cp2.contact_count == cp1.contact_count
        assert cp2.primary_contact.name == cp1.primary_contact.name
        assert cp2.decision_maker_contacts[0].name == cp1.decision_maker_contacts[0].name
        assert cp2.item_count == cp1.item_count
        assert cp2.has_deal_score
        assert cp2.deal_score.score == 80
        assert cp2.deal_score.level == DealLevel.A
        assert cp2.has_health_score
        assert cp2.health_score.health_score == 80
        assert len(cp2.risks) == 1
        assert cp2.risks[0].level == RiskLevel.MEDIUM

    def test_interaction_record_round_trip(self):
        ir1 = InteractionRecord(
            interaction_id="int_002",
            customer_id="c001",
            type=InteractionType.MEETING,
            raw_text="会议讨论了服务器采购需求，预算约500万，30天交付。",
            summary="服务器采购需求确认会",
            key_quotes=["预算500万左右", "30天内必须交付"],
            extracted_facts={"budget": "500万", "timeline": "30天"},
            procurement_signals=ProcurementSignals(
                urgency_signal="30天交付",
                budget_signal="预算500万已批复",
            ),
            risks=[RiskItem(level=RiskLevel.MEDIUM, reason="友商报价更低")],
            next_actions=["发送方案书"],
        )
        ir2 = self._round_trip(ir1)
        self._assert_field_equal(ir1, ir2, [
            "interaction_id", "customer_id", "type", "raw_text", "summary",
            "key_quotes",
        ])
        assert ir2.extracted_facts == ir1.extracted_facts
        assert ir2.has_signals
        assert ir2.procurement_signals.urgency_signal == "30天交付"
        assert ir2.has_risks
        assert len(ir2.risks) == 1

    def test_interaction_record_minimal_round_trip(self):
        ir1 = InteractionRecord(interaction_id="int_001", customer_id="c001")
        ir2 = self._round_trip(ir1)
        self._assert_field_equal(ir1, ir2, ["interaction_id", "customer_id"])
        assert ir2.type == InteractionType.NOTE

    # ── IntentResult ──

    def test_intent_result_round_trip(self):
        i1 = IntentResult(
            intent=Intent.CUSTOMER_INTAKE,
            confidence=0.92,
            reasoning="明确提到了客户名称和采购需求",
            alternative_intents=[Intent.MEETING_NOTE],
        )
        i2 = self._round_trip(i1)
        assert i1 == i2
        assert i2.intent == Intent.CUSTOMER_INTAKE
        assert i2.confidence == 0.92
        assert i2.alternative_intents == [Intent.MEETING_NOTE]
        assert i2.confidence_label == "high"

    def test_intent_result_simple_round_trip(self):
        i1 = IntentResult(intent=Intent.QUERY, confidence=0.55)
        i2 = self._round_trip(i1)
        assert i1 == i2
        assert i2.confidence_label == "medium"

    # ── Agent Runtime Protocol ──

    def test_interaction_metadata_round_trip(self):
        m1 = InteractionMetadata(
            session_id="sess-001",
            intent="customer_intake",
            intent_confidence=0.92,
            tools_called=["search", "update"],
            llm_calls=2,
            input_tokens=1200,
            output_tokens=800,
            cost_usd=0.015,
            latency_ms=3400,
        )
        m2 = self._round_trip(m1)
        assert m1 == m2
        assert m2.tools_called == ["search", "update"]

    def test_tool_result_round_trip(self):
        tr1 = ToolResult(
            success=True,
            tool_name="customer_memory_search",
            data={"customer_id": "c001", "customer_name": "华为"},
            warnings=["结果已截断"],
            summary="找到 50 条，显示前 20 条",
        )
        tr2 = self._round_trip(tr1)
        assert tr1 == tr2
        assert tr2.success
        assert tr2.data == {"customer_id": "c001", "customer_name": "华为"}
        assert tr2.is_partial

    def test_tool_result_failure_round_trip(self):
        tr1 = ToolResult(
            success=False,
            tool_name="customer_memory_update",
            errors=["Permission denied", "File not found"],
            summary="更新失败",
        )
        tr2 = self._round_trip(tr1)
        assert tr1 == tr2
        assert not tr2.success
        assert tr2.has_errors
        assert len(tr2.errors) == 2

    def test_interaction_round_trip(self):
        i1 = Interaction(
            session_id="sess-002",
            user_input="分析华为服务器采购会议",
            intent_result=IntentResult(
                intent=Intent.MEETING_NOTE,
                confidence=0.9,
                reasoning="会议关键词",
            ),
            tools_called=[
                ToolResult(success=True, tool_name="search", data={"id": "c001"}, summary="found"),
                ToolResult(success=True, tool_name="update", summary="updated"),
            ],
            agent_response="已完成分析。",
            metadata=InteractionMetadata(session_id="sess-002", input_tokens=800),
        )
        i2 = self._round_trip(i1)

        assert i2.session_id == i1.session_id
        assert i2.user_input == i1.user_input
        assert i2.intent_result.intent == Intent.MEETING_NOTE
        assert i2.tool_success_count == 2
        assert i2.all_tools_succeeded
        assert i2.agent_response == "已完成分析。"
        assert i2.metadata.input_tokens == 800
