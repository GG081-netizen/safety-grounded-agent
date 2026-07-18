"""Versioned enterprise business-risk rule catalog."""

from __future__ import annotations

from dataclasses import dataclass

from conversation_agent.policy.models import (
    PolicyStatus,
    RiskAction,
    RiskCategory,
    RiskSeverity,
)

RISK_CATALOG_VERSION = "business_risk_catalog_v1"


@dataclass(frozen=True, slots=True)
class RiskDetectorSpec:
    """One literal detector used for normalized or compact recall."""

    pattern: str
    matcher_type: str = "normalized_literal"


@dataclass(frozen=True, slots=True)
class RiskRule:
    rule_id: str
    category: RiskCategory
    action: RiskAction
    severity: RiskSeverity
    status_hint: PolicyStatus
    detectors: tuple[RiskDetectorSpec, ...]
    priority: int
    reason: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """Deprecated compatibility rule for callers supplying custom catalogs.

    ``negative_patterns`` remains constructible for source compatibility, but
    PolicyEngine deliberately never reads it.
    """

    rule_id: str
    category: str
    status: PolicyStatus
    priority: int
    patterns: tuple[str, ...]
    negative_patterns: tuple[str, ...] = ()
    reason: str = ""
    examples: tuple[str, ...] = ()


def _literals(*patterns: str, compact: bool = False) -> tuple[RiskDetectorSpec, ...]:
    matcher = "compact_literal" if compact else "normalized_literal"
    return tuple(RiskDetectorSpec(pattern=pattern, matcher_type=matcher) for pattern in patterns)


BUSINESS_RISK_RULES: tuple[RiskRule, ...] = (
    RiskRule(
        rule_id="privacy_overreach.private_customer_data",
        category=RiskCategory.PRIVACY_OVERREACH,
        action=RiskAction.REQUEST_PRIVATE_CUSTOMER_DATA,
        severity=RiskSeverity.CRITICAL,
        status_hint="BLOCKED",
        priority=100,
        detectors=_literals(
            "私人住址", "家庭住址", "私人手机号", "私人联系方式", "家庭成员",
            "家庭收入", "私人财务状况", "个人银行流水",
        ),
        reason="请求涉及客户隐私或越权查询。",
    ),
    RiskRule(
        rule_id="sensitive_attribute_inference.private_attribute",
        category=RiskCategory.SENSITIVE_ATTRIBUTE_INFERENCE,
        action=RiskAction.INFER_SENSITIVE_ATTRIBUTE,
        severity=RiskSeverity.CRITICAL,
        status_hint="BLOCKED",
        priority=98,
        detectors=_literals(
            "宗教信仰", "政治倾向", "族群身份", "家庭收入", "私人财务状况",
        ),
        reason="请求涉及敏感个人属性推断。",
    ),
    RiskRule(
        rule_id="legal_financial_final_judgment.absolute_legal",
        category=RiskCategory.LEGAL_FINANCIAL_FINAL_JUDGMENT,
        action=RiskAction.MAKE_FINAL_LEGAL_JUDGMENT,
        severity=RiskSeverity.HIGH,
        status_hint="BLOCKED",
        priority=96,
        detectors=_literals("一定合法", "绝对没有法律风险", "没有法律风险", "合同绝对没风险"),
        reason="请求要求代替专业人员作出最终法律判断。",
    ),
    RiskRule(
        rule_id="legal_financial_final_judgment.guaranteed_return",
        category=RiskCategory.LEGAL_FINANCIAL_FINAL_JUDGMENT,
        action=RiskAction.GUARANTEE_FINANCIAL_RETURN,
        severity=RiskSeverity.HIGH,
        status_hint="BLOCKED",
        priority=95,
        detectors=_literals("保证收益", "稳赚不赔", "绝对盈利"),
        reason="请求涉及不受支持的财务收益保证。",
    ),
    RiskRule(
        rule_id="sales_misrepresentation.absolute_delivery",
        category=RiskCategory.SALES_MISREPRESENTATION,
        action=RiskAction.MAKE_ABSOLUTE_DELIVERY_COMMITMENT,
        severity=RiskSeverity.HIGH,
        status_hint="BLOCKED",
        priority=94,
        detectors=_literals(
            "100%交付", "百分之百交付", "百分百交付", "绝不会延期", "百分之百按期交付",
            compact=True,
        ),
        reason="请求涉及无法证实的绝对交付承诺。",
    ),
    RiskRule(
        rule_id="sales_misrepresentation.guaranteed_outcome",
        category=RiskCategory.SALES_MISREPRESENTATION,
        action=RiskAction.GUARANTEE_SALES_OUTCOME,
        severity=RiskSeverity.HIGH,
        status_hint="BLOCKED",
        priority=93,
        detectors=_literals("保证一定中标", "保证中标", "一定中标"),
        reason="请求涉及无法证实的销售结果保证。",
    ),
    RiskRule(
        rule_id="unsupported_business_claim.fabricated_case",
        category=RiskCategory.UNSUPPORTED_BUSINESS_CLAIM,
        action=RiskAction.FABRICATE_CUSTOMER_CASE,
        severity=RiskSeverity.HIGH,
        status_hint="BLOCKED",
        priority=92,
        detectors=_literals(
            "编造一个成功案例", "编造客户案例", "编造案例", "虚构一个标杆客户",
            "虚构一个成功案例", "虚构客户案例", "编一个客户案例", "编一个成功案例",
            "编一个案例", "帮我编一个", "假装我们做过", "随便写一个客户案例",
        ),
        reason="请求涉及编造客户案例或业务事实。",
    ),
    RiskRule(
        rule_id="unsupported_business_claim.fabricated_inventory",
        category=RiskCategory.UNSUPPORTED_BUSINESS_CLAIM,
        action=RiskAction.FABRICATE_INVENTORY,
        severity=RiskSeverity.HIGH,
        status_hint="BLOCKED",
        priority=91,
        detectors=_literals("假装我们目前有充足库存", "假装有库存", "编造库存", "未确认库存写成事实"),
        reason="请求涉及编造库存事实。",
    ),
    RiskRule(
        rule_id="unsupported_business_claim.fabricated_certification",
        category=RiskCategory.UNSUPPORTED_BUSINESS_CLAIM,
        action=RiskAction.FABRICATE_CERTIFICATION,
        severity=RiskSeverity.HIGH,
        status_hint="BLOCKED",
        priority=90,
        detectors=_literals("没有证据也写成已经获得认证", "编造认证", "虚构认证"),
        reason="请求涉及编造认证事实。",
    ),
    RiskRule(
        rule_id="unsupported_business_claim.fabricated_delivery_record",
        category=RiskCategory.UNSUPPORTED_BUSINESS_CLAIM,
        action=RiskAction.FABRICATE_DELIVERY_RECORD,
        severity=RiskSeverity.HIGH,
        status_hint="BLOCKED",
        priority=89,
        detectors=_literals("编造交付记录", "虚构交付记录"),
        reason="请求涉及编造交付记录。",
    ),
    RiskRule(
        rule_id="business_uncertain.confirmation_required",
        category=RiskCategory.BUSINESS_UNCERTAIN,
        action=RiskAction.REQUEST_BUSINESS_CONFIRMATION,
        severity=RiskSeverity.MEDIUM,
        status_hint="UNCERTAIN",
        priority=60,
        detectors=_literals(
            "合同有哪些需要法务确认", "合同风险点", "法务确认的风险点", "库存尚未确认",
            "库存不确定", "交付周期需要供应链进一步确认", "客户数据用于营销是否需要进一步确认",
            "这个承诺是否合适", "表述中提到了客户案例", "sla条款是否需要进一步确认",
            "交付周期比较紧", "交付周期紧",
        ),
        reason="该业务结论需要进一步证据或授权确认。",
    ),
)

# Historical public name retained; values now carry action-level semantics.
BUSINESS_SAFETY_RULES = BUSINESS_RISK_RULES
