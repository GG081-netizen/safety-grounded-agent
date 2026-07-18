"""Generate seed data for development and demo.

Usage:
    python -m conversation_agent seed --count 5
    python -m conversation_agent seed --count 10 --output-dir ./data
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from conversation_agent.config import get_config
from conversation_agent.memory.customer_store import CustomerStore
from conversation_agent.memory.interaction_store import InteractionStore
from conversation_agent.sales.models import (
    Contact,
    CustomerProfile,
    CustomerStatus,
    DealScore,
    HealthScore,
    HealthStatus,
    InteractionRecord,
    InteractionType,
    ProcurementItem,
    ProcurementSignals,
    ProductCategory,
    RiskItem,
    RiskLevel,
    SalesStage,
)

logger = logging.getLogger(__name__)

# Realistic sample data pools

_COMPANY_POOL = [
    {
        "name": "联想集团",
        "aliases": ["联想", "Lenovo"],
        "industry": "IT",
        "company_size": "10000+",
        "budget_range": "500-1000万",
        "procurement_cycle": "3个月",
    },
    {
        "name": "华为技术",
        "aliases": ["华为", "Huawei"],
        "industry": "Telecom",
        "company_size": "10000+",
        "budget_range": "1000-3000万",
        "procurement_cycle": "6个月",
    },
    {
        "name": "字节跳动",
        "aliases": ["字节", "ByteDance"],
        "industry": "Internet",
        "company_size": "10000+",
        "budget_range": "300-800万",
        "procurement_cycle": "2个月",
    },
    {
        "name": "比亚迪",
        "aliases": ["BYD"],
        "industry": "Manufacturing",
        "company_size": "10000+",
        "budget_range": "200-500万",
        "procurement_cycle": "4个月",
    },
    {
        "name": "招商银行",
        "aliases": ["招行", "CMB"],
        "industry": "Finance",
        "company_size": "5000-10000",
        "budget_range": "500-1000万",
        "procurement_cycle": "6个月",
    },
    {
        "name": "中国移动",
        "aliases": ["移动", "CMCC"],
        "industry": "Telecom",
        "company_size": "10000+",
        "budget_range": "1000-5000万",
        "procurement_cycle": "12个月",
    },
    {
        "name": "美团",
        "aliases": ["Meituan"],
        "industry": "Internet",
        "company_size": "5000-10000",
        "budget_range": "100-300万",
        "procurement_cycle": "1个月",
    },
    {
        "name": "宁德时代",
        "aliases": ["宁德", "CATL"],
        "industry": "Manufacturing",
        "company_size": "5000-10000",
        "budget_range": "300-600万",
        "procurement_cycle": "3个月",
    },
    {
        "name": "平安科技",
        "aliases": ["平安", "PingAn"],
        "industry": "Finance",
        "company_size": "5000-10000",
        "budget_range": "500-800万",
        "procurement_cycle": "4个月",
    },
    {
        "name": "小米科技",
        "aliases": ["小米", "Xiaomi"],
        "industry": "IT",
        "company_size": "10000+",
        "budget_range": "200-500万",
        "procurement_cycle": "2个月",
    },
]

_CONTACT_POOL = [
    {"name": "王总", "title": "CTO", "department": "技术部", "influence_level": "high"},
    {"name": "李经理", "title": "采购主管", "department": "采购部", "influence_level": "high"},
    {"name": "张工", "title": "IT经理", "department": "IT部", "influence_level": "medium"},
    {"name": "赵总监", "title": "财务总监", "department": "财务部", "influence_level": "medium"},
    {"name": "陈经理", "title": "项目经理", "department": "项目部", "influence_level": "medium"},
    {"name": "刘助理", "title": "采购专员", "department": "采购部", "influence_level": "low"},
    {"name": "孙总", "title": "CEO", "department": "总经办", "influence_level": "high"},
    {"name": "周经理", "title": "行政经理", "department": "行政部", "influence_level": "low"},
]

_PRODUCT_POOL: list[dict] = [
    {
        "product_name": "ThinkPad X1 Carbon",
        "category": ProductCategory.IT_EQUIPMENT,
        "unit_budget": 12000,
    },
    {
        "product_name": "Dell PowerEdge R750 服务器",
        "category": ProductCategory.IT_EQUIPMENT,
        "unit_budget": 85000,
    },
    {
        "product_name": "Microsoft 365 E5 订阅",
        "category": ProductCategory.ENTERPRISE_SOFTWARE,
        "unit_budget": 300,
    },
    {
        "product_name": "MaxHub 会议平板",
        "category": ProductCategory.MEETING_DEVICES,
        "unit_budget": 25000,
    },
    {
        "product_name": "Steelcase Leap 人体工学椅",
        "category": ProductCategory.OFFICE_FURNITURE,
        "unit_budget": 8000,
    },
    {
        "product_name": "HP LaserJet 打印机",
        "category": ProductCategory.OFFICE_EQUIPMENT,
        "unit_budget": 5000,
    },
    {
        "product_name": "Slack Enterprise Grid",
        "category": ProductCategory.ENTERPRISE_SOFTWARE,
        "unit_budget": 150,
    },
]

_RISK_REASONS = [
    ("high", "友商报价低20%，价格竞争激烈"),
    ("medium", "采购审批周期较长，可能延期"),
    ("low", "客户联系人变动中"),
    ("high", "预算尚未最终批复"),
    ("medium", "交付时间要求紧张"),
    ("critical", "客户已有长期合作供应商"),
    ("low", "技术细节待确认"),
    ("medium", "决策人近期出差"),
]

_SIGNAL_TEMPLATES = [
    ("urgency_signal", [
        "30天内必须交付",
        "项目已立项，急需启动",
        "领导要求尽快落地",
    ]),
    ("budget_signal", [
        "预算已批复",
        "预算审批中，预计2周内完成",
        "年度采购预算充足",
    ]),
    ("decision_signal", [
        "CTO直接决策",
        "采购委员会集体决策",
        "部门总监即可拍板",
    ]),
    ("competition_signal", [
        "友商浪潮已报价",
        "戴尔在竞争此单",
        "华为也在接触中",
    ]),
    ("engagement_signal", [
        "每周主动沟通",
        "客户回复及时",
        "已安排技术演示",
    ]),
]

_INTERACTION_SUMMARIES = [
    "初次电话沟通，了解客户基本采购需求",
    "发送产品方案和报价，等待客户反馈",
    "面对面会议，深入讨论技术方案",
    "客户反馈报价偏高，需要调整方案",
    "技术演示顺利完成，客户对产品满意",
    "商务谈判，讨论付款条款和交付周期",
    "客户内部审批中，预计下周有结果",
    "合同细节确认，进入最终签署阶段",
]

_NEXT_ACTIONS = [
    "发送行业案例",
    "安排技术演示",
    "提供详细报价单",
    "电话跟进审批进度",
    "发送合同模板",
    "确认交付时间表",
    "安排高层会面",
    "提供竞品对比分析",
]


def _make_customer_id(index: int) -> str:
    return f"cust_{index:04d}"


def _make_interaction_id(index: int) -> str:
    return f"int_{index:04d}"


def _random_contacts() -> list[Contact]:
    n = random.randint(1, 3)
    contacts = random.sample(_CONTACT_POOL, n)
    return [
        Contact(
            name=c["name"],
            title=c["title"],
            department=c["department"],
            influence_level=c["influence_level"],  # type: ignore[arg-type]
            email=f"{c['name']}@example.com",
        )
        for c in contacts
    ]


def _random_procurement_items() -> list[ProcurementItem]:
    n = random.randint(1, 3)
    items = random.sample(_PRODUCT_POOL, n)
    results: list[ProcurementItem] = []
    for item in items:
        quantity = random.randint(10, 200)
        unit = item["unit_budget"]
        results.append(
            ProcurementItem(
                product_name=item["product_name"],
                category=item["category"],
                quantity=quantity,
                unit_budget=unit,
                total_budget=unit * quantity,
                requirements=random.sample(
                    ["30天交付", "3年保修", "7x24支持", "免费安装", "定期巡检"],
                    random.randint(1, 3),
                ),
            )
        )
    return results


def _random_risks() -> list[RiskItem]:
    n = random.randint(0, 3)
    risks = random.sample(_RISK_REASONS, min(n, len(_RISK_REASONS)))
    return [RiskItem(level=RiskLevel(r[0]), reason=r[1]) for r in risks]


def _random_signals() -> ProcurementSignals:
    signals: dict = {}
    for key, options in _SIGNAL_TEMPLATES:
        if random.random() > 0.3:
            signals[key] = random.choice(options)
    return ProcurementSignals(**signals)


def _random_deal_score(stage: SalesStage) -> DealScore | None:
    """Generate a plausible deal score based on sales stage."""
    if stage in (SalesStage.LEAD, SalesStage.LOST):
        return None

    # Progressively higher scores for later stages
    base_scores = {
        "requirement_confirmation": (30, 55),
        "quotation": (45, 70),
        "negotiation": (55, 80),
        "procurement_approval": (65, 85),
        "contract_signing": (75, 95),
        "won": (85, 100),
    }
    min_s, max_s = base_scores.get(stage.value, (30, 60))

    dims = {
        k: random.randint(min_s, max_s)
        for k in ("need_clarity", "budget_fit", "decision_maker_access", "urgency", "engagement")
    }
    risk = _random_risks()
    risk_penalty = sum(
        {"low": 3, "medium": 6, "high": 10, "critical": 15}[r.level.value]
        for r in risk
    )
    raw = int(
        dims["need_clarity"] * 0.30
        + dims["budget_fit"] * 0.25
        + dims["decision_maker_access"] * 0.20
        + dims["urgency"] * 0.15
        + dims["engagement"] * 0.10
        - risk_penalty
    )
    score = max(0, min(100, raw))

    from conversation_agent.sales.models import DealLevel
    return DealScore(
        score=score,
        level=DealLevel.from_score(score),
        risk_penalty=risk_penalty,
        missing_dimensions=random.sample(
            list(dims.keys()),
            random.randint(0, 2),
        ),
        summary=f"综合评分{score}分，{'成交概率较高' if score >= 60 else '需继续跟进'}",
        **dims,
    )


def _random_health_score(days_since_last_interaction: int) -> HealthScore:
    """Generate a plausible health score with time decay applied."""
    # Apply decay to recent_contact based on days since last interaction
    decay = 1.0
    if days_since_last_interaction > 30:
        decay = 0.3
    elif days_since_last_interaction > 14:
        decay = 0.5
    elif days_since_last_interaction > 7:
        decay = 0.7
    elif days_since_last_interaction > 3:
        decay = 0.9

    recent_contact = int(20 * decay)
    dims = {
        "recent_contact": recent_contact,
        "responsiveness": random.randint(5, 20),
        "decision_maker_involvement": random.randint(5, 20),
        "need_clarity": random.randint(5, 20),
        "budget_timeline_clarity": random.randint(5, 20),
    }
    total = sum(dims.values())
    status = HealthStatus.from_score(total)
    return HealthScore(health_score=total, status=status, **dims)


def _random_interactions(
    customer_id: str,
    stage: SalesStage,
    days_ago: int,
) -> list[InteractionRecord]:
    """Generate a chain of interactions leading to the current stage."""
    stage_order = [
        SalesStage.LEAD,
        SalesStage.REQUIREMENT_CONFIRMATION,
        SalesStage.QUOTATION,
        SalesStage.NEGOTIATION,
        SalesStage.PROCUREMENT_APPROVAL,
        SalesStage.CONTRACT_SIGNING,
    ]

    try:
        stage_index = stage_order.index(stage) if stage in stage_order else 0
    except ValueError:
        stage_index = 0

    # One interaction per stage reached, plus a couple extra
    n = max(1, stage_index + random.randint(0, 2))
    records: list[InteractionRecord] = []
    types = list(InteractionType)

    for i in range(n):
        offset_days = days_ago - (n - i) * random.randint(3, 10)
        date = datetime.now(timezone.utc) - timedelta(days=max(0, offset_days))
        summary = _INTERACTION_SUMMARIES[i % len(_INTERACTION_SUMMARIES)]
        int_id = _make_interaction_id(len(records) + 1)

        records.append(
            InteractionRecord(
                interaction_id=int_id,
                customer_id=customer_id,
                date=date,
                type=types[(i + 1) % len(types)],
                raw_text=f"{summary}。客户表达了采购意向。",
                summary=summary,
                procurement_signals=_random_signals() if random.random() > 0.3 else None,
                risks=_random_risks(),
                next_actions=random.sample(_NEXT_ACTIONS, random.randint(1, 3)),
            )
        )

    return records


def generate_seed_data(count: int = 5) -> tuple[list[CustomerProfile], list[InteractionRecord]]:
    """Generate `count` realistic customers with interaction histories.

    Returns (customers, interactions) ready for persistence.
    """
    companies = random.sample(_COMPANY_POOL, min(count, len(_COMPANY_POOL)))
    customers: list[CustomerProfile] = []
    interactions: list[InteractionRecord] = []

    stages = list(SalesStage.active_stages()) + [SalesStage.WON]

    for i, company in enumerate(companies):
        customer_id = _make_customer_id(i + 1)
        stage = random.choice(stages)
        status = (
            CustomerStatus.WON
            if stage == SalesStage.WON
            else CustomerStatus.ACTIVE
        )

        days_ago = random.randint(1, 90)
        created = datetime.now(timezone.utc) - timedelta(days=days_ago)

        profile = CustomerProfile(
            customer_id=customer_id,
            customer_name=company["name"],
            aliases=company["aliases"],
            source=random.choice(["展会", "官网", "推荐", "电话", "邮件"]),
            industry=company["industry"],
            company_size=company["company_size"],
            contacts=_random_contacts(),
            procurement_items=_random_procurement_items(),
            budget_range=company["budget_range"],
            procurement_cycle=company["procurement_cycle"],
            timeline=f"预计{random.randint(1, 6)}个月内完成采购",
            competitors=random.sample(
                ["浪潮", "戴尔", "惠普", "阿里云", "华为云"],
                random.randint(0, 2),
            ),
            decision_makers=[
                c.name
                for c in _random_contacts()
                if c.is_decision_maker
            ] or ["王总"],
            sales_stage=stage,
            status=status,
            risks=_random_risks(),
            next_actions=random.sample(_NEXT_ACTIONS, random.randint(1, 3)),
            tags=random.sample(
                ["VIP", "大客户", "新客户", "战略合作", "紧急"],
                random.randint(1, 2),
            ),
            created_at=created,
            updated_at=created,
        )

        # Generate interactions
        cust_interactions = _random_interactions(customer_id, stage, days_ago)
        last_int_date = (
            cust_interactions[-1].date if cust_interactions else created
        )
        delta = datetime.now(timezone.utc) - last_int_date

        # Attach scores
        profile.deal_score = _random_deal_score(stage)
        profile.health_score = _random_health_score(delta.days)

        customers.append(profile)
        interactions.extend(cust_interactions)

    return customers, interactions


def seed(
    count: int = 5,
    output_dir: Path | None = None,
    clear_first: bool = False,
) -> int:
    """Persist seed data to disk.

    Returns the number of customers created.
    """
    if output_dir:
        cfg = get_config()
        cfg.storage.data_dir = output_dir

    store = CustomerStore()
    int_store = InteractionStore()

    if clear_first:
        for profile in store.list_all():
            store.delete(profile.customer_id)
        logger.info("Cleared existing data")

    customers, interactions = generate_seed_data(count)

    for profile in customers:
        store.save(profile)

    for record in interactions:
        int_store.save(record)

    logger.info(
        "Seeded %d customers with %d interactions",
        len(customers),
        len(interactions),
    )
    return len(customers)
