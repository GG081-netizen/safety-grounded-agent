"""Tests for TaskRouter boundary with IntentRouter."""

from conversation_agent.orchestration.task_router import TaskRouter
from conversation_agent.sales.intent_router import IntentRouter
from conversation_agent.sales.models import Intent


def test_query_sales_progress_routes_to_sales_analysis():
    intent = IntentRouter().route("查一下联想销售进展")
    task = TaskRouter().route("查一下联想销售进展", intent)
    assert intent.intent == Intent.QUERY
    assert task.task == "sales_analysis"


def test_weekly_report_boundary():
    intent = IntentRouter().route("生成本周销售周报")
    task = TaskRouter().route("生成本周销售周报", intent)
    assert intent.intent == Intent.REPORT
    assert task.task == "weekly_report"


def test_email_boundary():
    intent = IntentRouter().route("写一封跟进邮件")
    task = TaskRouter().route("写一封跟进邮件", intent)
    assert intent.intent == Intent.EMAIL_DRAFT
    assert task.task == "email_draft"
