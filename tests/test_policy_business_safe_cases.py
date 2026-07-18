import pytest

from conversation_agent.policy.engine import PolicyEngine

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "text",
    (
        "整理服务器采购需求。",
        "分析办公电脑供应商报价。",
        "比较工业传感器采购参数。",
        "根据真实互动记录生成跟进建议。",
        "根据已有资料总结真实客户案例。",
        "生成客户会议纪要。",
        "汇总本周销售机会。",
        "根据确认的库存编写交付说明。",
    ),
)
def test_normal_enterprise_requests_are_safe(text):
    assert PolicyEngine().decide(text).status == "SAFE"
