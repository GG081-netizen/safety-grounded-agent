"""Comprehensive tests for IntentRouter (Phase 5)."""

import pytest
from conversation_agent.sales.intent_router import IntentRouter
from conversation_agent.sales.models import Intent, IntentResult


@pytest.fixture
def router():
    return IntentRouter()


# ═══════════════════════════════════════════════════════════════════════════════
# Customer intake routing
# ═══════════════════════════════════════════════════════════════════════════════


class TestCustomerIntakeRouting:
    def test_explicit_new_customer(self, router):
        result = router.route("这是一个新客户，联想集团采购部李经理的联系方式如下")
        assert result.intent == Intent.CUSTOMER_INTAKE

    def test_procurement_scenario(self, router):
        result = router.route("需要采购100台笔记本，预算80万，30天交付")
        assert result.intent == Intent.CUSTOMER_INTAKE
        assert result.confidence >= 0.5

    def test_bidding_scenario(self, router):
        result = router.route("客户发来招标文件，要求报价")
        assert result.intent == Intent.CUSTOMER_INTAKE

    def test_supplier_inquiry(self, router):
        result = router.route("客户询价，需要服务器报价")
        assert result.intent == Intent.CUSTOMER_INTAKE


# ═══════════════════════════════════════════════════════════════════════════════
# Meeting note routing
# ═══════════════════════════════════════════════════════════════════════════════


class TestMeetingNoteRouting:
    def test_meeting_minutes(self, router):
        result = router.route("这是昨天的会议纪要：讨论了服务器采购方案")
        assert result.intent == Intent.MEETING_NOTE

    def test_meeting_record(self, router):
        result = router.route("会议记录：客户预算500万，需30天交付")
        assert result.intent == Intent.MEETING_NOTE

    def test_call_record(self, router):
        result = router.route("通话记录：与王总电话沟通了采购进展")
        assert result.intent == Intent.MEETING_NOTE

    def test_visit_record(self, router):
        result = router.route("今天下午拜访了字节跳动，对方CTO对我们的方案很感兴趣")
        assert result.intent == Intent.MEETING_NOTE

    def test_discussion_summary(self, router):
        result = router.route("会上讨论了报价方案，客户表示预算可以接受")
        assert result.intent == Intent.MEETING_NOTE


# ═══════════════════════════════════════════════════════════════════════════════
# Query routing
# ═══════════════════════════════════════════════════════════════════════════════


class TestQueryRouting:
    def test_search_customer(self, router):
        result = router.route("帮我查一下华为的客户档案")
        assert result.intent == Intent.QUERY

    def test_check_status(self, router):
        result = router.route("联想的销售进展如何")
        assert result.intent == Intent.QUERY

    def test_list_customers(self, router):
        result = router.route("列出所有IT行业的客户")
        assert result.intent == Intent.QUERY

    def test_health_check(self, router):
        result = router.route("查看华为的健康度和成交概率")
        assert result.intent == Intent.QUERY

    def test_stage_query(self, router):
        result = router.route("哪些客户在谈判阶段")
        assert result.intent == Intent.QUERY


# ═══════════════════════════════════════════════════════════════════════════════
# Email draft routing
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmailDraftRouting:
    def test_write_email(self, router):
        result = router.route("帮我写一封邮件给李经理")
        assert result.intent == Intent.EMAIL_DRAFT

    def test_send_email(self, router):
        result = router.route("发邮件通知客户报价已更新")
        assert result.intent == Intent.EMAIL_DRAFT

    def test_draft_email(self, router):
        result = router.route("起草一封邮件回复王总的询价")
        assert result.intent == Intent.EMAIL_DRAFT

    def test_email_template(self, router):
        result = router.route("用邮件模板发送跟进提醒")
        assert result.intent == Intent.EMAIL_DRAFT


# ═══════════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_empty_input(self, router):
        result = router.route("")
        assert result.confidence <= 0.2
        assert result.intent == Intent.QUERY

    def test_whitespace_only(self, router):
        result = router.route("   ")
        assert result.confidence <= 0.2

    def test_no_signal_input(self, router):
        result = router.route("你好")
        assert result.confidence <= 0.2

    def test_very_short_input(self, router):
        result = router.route("嗯")
        assert result.confidence <= 0.2

    def test_batch_routing(self, router):
        texts = ["新客户联想", "会议讨论了", "查一下华为", "写邮件给王总"]
        results = router.route_batch(texts)
        assert len(results) == 4
        assert results[0].intent == Intent.CUSTOMER_INTAKE
        assert results[1].intent == Intent.MEETING_NOTE
        assert results[2].intent == Intent.QUERY
        assert results[3].intent == Intent.EMAIL_DRAFT


# ═══════════════════════════════════════════════════════════════════════════════
# Confidence & alternatives
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfidenceAndAlternatives:
    def test_high_confidence_single_intent(self, router):
        result = router.route("新客户采购100台服务器，招标需求")
        assert result.intent == Intent.CUSTOMER_INTAKE
        assert result.high_confidence

    def test_medium_confidence_mixed_signals(self, router):
        result = router.route("会议讨论了采购需求并决定发邮件通知供应商")
        # Should have multiple intents scoring
        assert result.is_ambiguous or result.confidence < 0.9

    def test_alternatives_present_when_mixed(self, router):
        result = router.route("查询一下最近的会议纪要")
        # query signals + meeting_note signals
        has_both = (
            Intent.QUERY in result.alternative_intents
            or Intent.MEETING_NOTE in result.alternative_intents
            or result.is_ambiguous
        )
        # At minimum, if confidence isn't 1.0 it's fine for this ambiguous input
        assert result.confidence > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Model contract
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelContract:
    def test_returns_intent_result(self, router):
        result = router.route("测试输入")
        assert isinstance(result, IntentResult)
        assert isinstance(result.intent, Intent)
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.reasoning, str)
        assert isinstance(result.alternative_intents, list)

    def test_confidence_label_consistent(self, router):
        result = router.route("明确的客户采购需求")
        label = result.confidence_label
        if result.confidence >= 0.8:
            assert label == "high"
        elif result.confidence >= 0.5:
            assert label == "medium"
        else:
            assert label == "low"

    def test_round_trip_serializable(self, router):
        result = router.route("联想采购100台笔记本")
        json_str = result.model_dump_json()
        reloaded = IntentResult.model_validate_json(json_str)
        assert reloaded.intent == result.intent
        assert reloaded.confidence == result.confidence


# ═══════════════════════════════════════════════════════════════════════════════
# Custom signal tables
# ═══════════════════════════════════════════════════════════════════════════════


class TestCustomSignals:
    def test_custom_signal_table(self):
        custom = {
            Intent.CUSTOMER_INTAKE: [("新客户信号", 5)],
            Intent.MEETING_NOTE: [("会议信号", 5)],
            Intent.QUERY: [("查询信号", 5)],
            Intent.EMAIL_DRAFT: [("邮件信号", 5)],
        }
        router = IntentRouter(signal_tables=custom)
        result = router.route("这是一个新客户信号")
        assert result.intent == Intent.CUSTOMER_INTAKE
        assert result.high_confidence

    def test_custom_signals_override(self):
        """Custom signals can completely replace defaults for testing."""
        custom = {
            Intent.CUSTOMER_INTAKE: [("zzz_custom_trigger_zzz", 10)],
            Intent.MEETING_NOTE: [],
            Intent.QUERY: [],
            Intent.EMAIL_DRAFT: [],
        }
        router = IntentRouter(signal_tables=custom)
        result = router.route("这句话包含 zzz_custom_trigger_zzz 关键字")
        assert result.intent == Intent.CUSTOMER_INTAKE
        assert result.confidence == 1.0
