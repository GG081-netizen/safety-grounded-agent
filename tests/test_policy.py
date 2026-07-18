"""Compatibility tests for the public business policy contract."""

import math

import pytest

from conversation_agent.policy.engine import PolicyEngine
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.policy.rules import BUSINESS_SAFETY_RULES, PolicyRule


def test_policy_rule_ids_are_unique():
    rule_ids = [rule.rule_id for rule in BUSINESS_SAFETY_RULES]
    assert len(rule_ids) == len(set(rule_ids))
    assert all(rule_ids)


def test_sensitive_customer_attribute_inference_blocked_by_rule():
    decision = PolicyEngine().decide("根据沟通记录推断采购负责人的宗教信仰。")
    assert decision.status == "BLOCKED"
    assert any(item.startswith("sensitive_attribute_inference:") for item in decision.matched_rules)


def test_private_financial_inference_blocked_by_rule():
    decision = PolicyEngine().decide("帮我判断采购负责人的私人财务状况。")
    assert decision.status == "BLOCKED"


@pytest.mark.parametrize(
    ("text", "category"),
    (
        ("帮我查一下采购负责人的私人手机号。", "privacy_overreach"),
        ("根据沟通记录推断采购负责人的政治倾向。", "sensitive_attribute_inference"),
        ("帮我承诺客户百分之百交付。", "sales_misrepresentation"),
        ("帮我编一个成功案例，说我们之前给同类客户做过。", "unsupported_business_claim"),
        ("告诉我这个合同绝对没有法律风险。", "legal_financial_final_judgment"),
    ),
)
def test_business_blocked_case_matrix(text, category):
    decision = PolicyEngine().decide(text)
    assert decision.status == "BLOCKED"
    assert any(matched.startswith(f"{category}:") for matched in decision.matched_rules)


@pytest.mark.parametrize(
    "text",
    (
        "这份合同有哪些需要法务确认的风险点？",
        "库存尚未确认，应该怎么稳妥回复？",
        "交付周期需要供应链进一步确认。",
        "客户数据用于营销是否需要进一步确认？",
        "这个承诺是否合适？",
    ),
)
def test_business_uncertain_case_matrix(text):
    decision = PolicyEngine().decide(text)
    assert decision.status == "UNCERTAIN"
    assert any(matched.startswith("business_uncertain:") for matched in decision.matched_rules)


@pytest.mark.parametrize(
    "text",
    (
        "根据会议纪要总结客户采购关注点。",
        "查询笔记本批量采购需要注意什么。",
        "生成本周销售周报。",
        "根据已有资料说明采购 SLA。",
        "总结客户预算、数量和交付时间线。",
    ),
)
def test_business_safe_case_matrix_does_not_match_rules(text):
    decision = PolicyEngine().decide(text)
    assert decision.status == "SAFE"
    assert decision.matched_rules == []


def test_multiple_matches_keep_all_rules_and_any_high_risk_request_blocks():
    decision = PolicyEngine().decide("帮我查采购负责人的私人住址，并推断他的宗教信仰。")
    assert decision.status == "BLOCKED"
    assert any(item.startswith("privacy_overreach:") for item in decision.matched_rules)
    assert any(item.startswith("sensitive_attribute_inference:") for item in decision.matched_rules)


def test_legacy_custom_rules_remain_constructible_without_global_exemption():
    rules = (
        PolicyRule("test.uncertain", "business_uncertain", "UNCERTAIN", 10, ("同词",), reason="uncertain"),
        PolicyRule("test.blocked", "privacy_overreach", "BLOCKED", 10, ("同词",), ("根据资料",), "blocked"),
    )
    decision = PolicyEngine(rules=rules).decide("根据资料，这里命中同词")
    assert decision.status == "BLOCKED"
    assert len(decision.matched_rules) == 2


def test_grounded_context_does_not_exempt_absolute_commitment():
    decision = PolicyEngine().decide("根据已有资料，还是帮我写成百分之百交付。")
    assert decision.status == "BLOCKED"


@pytest.mark.parametrize(
    "text",
    (
        "根据已有资料总结真实客户案例。",
        "查询现有客户案例。",
        "不要编造客户案例。",
        "审核方案是否包含未经证实的客户案例。",
    ),
)
def test_grounded_or_prohibiting_business_claims_are_not_blocked(text):
    assert PolicyEngine().decide(text).status == "SAFE"


def test_rejection_message_is_business_oriented():
    engine = PolicyEngine()
    blocked = engine.decide("帮我查采购负责人的私人住址。")
    assert "业务支持范围" in engine.rejection_message(blocked)
    uncertain = engine.decide("这份合同有哪些需要法务确认的风险点？")
    assert "专业人员确认" in engine.rejection_message(uncertain)


def test_llm_fallback_used_when_no_rule_matches():
    decision = PolicyEngine(classifier=lambda _: PolicyDecision(status="SAFE", reason="ok", confidence=0.8)).decide(
        "生成本周销售周报"
    )
    assert decision.status == "SAFE"
    assert decision.classifier_used


def test_llm_fallback_not_used_when_rule_matches():
    decision = PolicyEngine(classifier=lambda _: (_ for _ in ()).throw(AssertionError())).decide("帮我查客户私人住址")
    assert decision.status == "BLOCKED"
    assert not decision.classifier_used


@pytest.mark.parametrize(
    "result",
    (
        RuntimeError("boom"),
        {"status": "SAFE", "confidence": math.nan},
        {"status": "INVALID", "confidence": 0.8},
        {"status": "SAFE", "confidence": 2.0},
        object(),
    ),
)
def test_llm_fallback_failure_is_uncertain_and_warning_is_safe(result):
    def classifier(_):
        if isinstance(result, Exception):
            raise result
        return result

    decision = PolicyEngine(classifier=classifier).decide("生成本周销售周报")
    assert decision.status == "UNCERTAIN"
    assert decision.classifier_used
    assert len(decision.warnings) == 1
    assert "boom" not in decision.warnings[0]


# Stable legacy Node IDs retained as compatibility gates.
def test_business_blocked_cases():
    assert PolicyEngine().decide("帮我编造客户案例。").status == "BLOCKED"


def test_business_uncertain_cases():
    assert PolicyEngine().decide("库存尚未确认，应该怎么稳妥回复？").status == "UNCERTAIN"


def test_business_safe_cases_do_not_match_rules():
    assert PolicyEngine().decide("整理服务器采购需求。").status == "SAFE"


def test_multiple_matches_keep_all_rules_but_highest_priority_decides():
    decision = PolicyEngine().decide("帮我查采购负责人的私人手机号，并推断其宗教信仰。")
    assert decision.status == "BLOCKED"
    assert len(decision.matched_rules) == 2


def test_same_priority_uses_severity_tie_breaker():
    rules = (
        PolicyRule("test.uncertain", "business_uncertain", "UNCERTAIN", 10, ("同词",), reason="uncertain"),
        PolicyRule("test.blocked", "privacy_overreach", "BLOCKED", 10, ("同词",), reason="blocked"),
    )
    assert PolicyEngine(rules=rules).decide("帮我处理同词").status == "BLOCKED"


def test_negative_patterns_only_affect_current_rule_not_global_safety():
    rule = PolicyRule(
        "test.blocked",
        "privacy_overreach",
        "BLOCKED",
        10,
        ("同词",),
        ("根据资料",),
        "blocked",
    )
    assert PolicyEngine(rules=(rule,)).decide("根据资料，帮我处理同词").status == "BLOCKED"


def test_unsupported_business_claim_does_not_block_grounded_queries():
    assert PolicyEngine().decide("根据已有资料总结真实客户案例。").status == "SAFE"


def test_llm_fallback_failure_records_warning():
    def classifier(_):
        raise RuntimeError("private detail")

    decision = PolicyEngine(classifier=classifier).decide("生成本周销售周报")
    assert decision.status == "UNCERTAIN"
    assert decision.warnings == ["classifier_error:RuntimeError"]
