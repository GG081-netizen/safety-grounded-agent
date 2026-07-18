"""Comprehensive tests for Tool Registry and core tools (Phase 6)."""

import json
from pathlib import Path

import pytest

from conversation_agent.config import reset_config, get_config
from conversation_agent.memory.customer_store import CustomerStore
from conversation_agent.memory.interaction_store import InteractionStore
from conversation_agent.sales.models import (
    CustomerProfile,
    SalesStage,
    ToolResult,
)
from conversation_agent.tools.base import BaseTool, safe_execute
from conversation_agent.tools.registry import ToolRegistry
from conversation_agent.tools.customer_memory import (
    CustomerMemorySearchTool,
    CustomerMemoryUpdateTool,
)
from conversation_agent.tools.sales_score import (
    SalesScoreCalculatorTool,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    reset_config()
    cfg = get_config()
    cfg.storage.data_dir = tmp_path / "data"
    yield
    reset_config()


# ═══════════════════════════════════════════════════════════════════════════════
# BaseTool & safe_execute
# ═══════════════════════════════════════════════════════════════════════════════


class TestBaseTool:
    def test_safe_execute_catches_exception(self):
        class BrokenTool(BaseTool):
            name = "broken"
            description = "always fails"
            parameters_schema = {}
            def execute(self, **kwargs):
                raise RuntimeError("boom")

        result = safe_execute(BrokenTool())
        assert not result.success
        assert "RuntimeError" in result.errors[0]
        assert result.summary

    def test_safe_execute_returns_toolresult(self):
        class OkTool(BaseTool):
            name = "ok"
            description = "works"
            parameters_schema = {}
            def execute(self, **kwargs):
                return ToolResult(success=True, tool_name=self.name, summary="done")

        result = safe_execute(OkTool())
        assert result.success
        assert result.summary == "done"

    def test_to_anthropic_schema(self):
        class SchemaTool(BaseTool):
            name = "test_tool"
            description = "A test tool"
            parameters_schema = {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            }
            def execute(self, **kwargs):
                return ToolResult(success=True, tool_name=self.name, summary="ok")

        t = SchemaTool()
        schema = t.to_anthropic_schema()
        assert schema["name"] == "test_tool"
        assert schema["description"] == "A test tool"
        assert "input_schema" in schema
        assert schema["input_schema"]["required"] == ["x"]


# ═══════════════════════════════════════════════════════════════════════════════
# ToolRegistry
# ═══════════════════════════════════════════════════════════════════════════════


class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        registry.register(CustomerMemorySearchTool())
        tool = registry.get("customer_memory_search")
        assert tool is not None
        assert tool.name == "customer_memory_search"

    def test_get_missing(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_duplicate_register_raises(self):
        registry = ToolRegistry()
        registry.register(CustomerMemorySearchTool())
        with pytest.raises(ValueError):
            registry.register(CustomerMemorySearchTool())

    def test_execute_unknown_tool(self):
        registry = ToolRegistry()
        result = registry.execute("unknown")
        assert not result.success
        assert "unknown" in result.errors[0] or "未知" in result.errors[0]

    def test_list_tools(self):
        registry = ToolRegistry()
        registry.register(CustomerMemorySearchTool())
        registry.register(CustomerMemoryUpdateTool())
        names = registry.list_tools()
        assert "customer_memory_search" in names
        assert "customer_memory_update" in names

    def test_to_anthropic_schemas(self):
        registry = ToolRegistry()
        registry.register(CustomerMemorySearchTool())
        registry.register(SalesScoreCalculatorTool())
        schemas = registry.to_anthropic_schemas()
        assert len(schemas) == 2
        names = {s["name"] for s in schemas}
        assert "customer_memory_search" in names


# ═══════════════════════════════════════════════════════════════════════════════
# CustomerMemorySearchTool
# ═══════════════════════════════════════════════════════════════════════════════


class TestCustomerMemorySearch:
    def test_search_by_name(self):
        store = CustomerStore()
        store.save(CustomerProfile(customer_id="c1", customer_name="联想集团"))
        store.save(CustomerProfile(customer_id="c2", customer_name="华为技术"))

        tool = CustomerMemorySearchTool(store=store)
        result = tool.execute(customer_name="联想")

        assert result.success
        assert len(result.data) == 1
        assert result.data[0]["customer_name"] == "联想集团"

    def test_search_by_industry(self):
        store = CustomerStore()
        store.save(CustomerProfile(customer_id="c1", customer_name="A", industry="IT"))
        store.save(CustomerProfile(customer_id="c2", customer_name="B", industry="Finance"))

        tool = CustomerMemorySearchTool(store=store)
        result = tool.execute(industry="IT")

        assert result.success
        assert len(result.data) == 1

    def test_search_by_sales_stage(self):
        store = CustomerStore()
        store.save(CustomerProfile(customer_id="c1", customer_name="A", sales_stage=SalesStage.WON))
        store.save(CustomerProfile(customer_id="c2", customer_name="B", sales_stage=SalesStage.LEAD))

        tool = CustomerMemorySearchTool(store=store)
        result = tool.execute(sales_stage="won")

        assert result.success
        assert len(result.data) == 1

    def test_search_no_results(self):
        store = CustomerStore()
        tool = CustomerMemorySearchTool(store=store)
        result = tool.execute(customer_name="不存在")
        assert result.success
        assert result.data == []

    def test_search_no_filters_returns_all(self):
        store = CustomerStore()
        store.save(CustomerProfile(customer_id="c1", customer_name="A"))
        store.save(CustomerProfile(customer_id="c2", customer_name="B"))
        tool = CustomerMemorySearchTool(store=store)
        result = tool.execute()
        assert result.success
        assert len(result.data) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# CustomerMemoryUpdateTool
# ═══════════════════════════════════════════════════════════════════════════════


class TestCustomerMemoryUpdate:
    def test_update_name(self):
        store = CustomerStore()
        store.save(CustomerProfile(customer_id="c1", customer_name="旧名称"))
        tool = CustomerMemoryUpdateTool(store=store)
        result = tool.execute(customer_id="c1", customer_name="新名称")
        assert result.success
        assert result.data["changed_fields"] == ["customer_name"]

        updated = store.load("c1")
        assert updated.customer_name == "新名称"

    def test_update_industry(self):
        store = CustomerStore()
        store.save(CustomerProfile(customer_id="c1", customer_name="test"))
        tool = CustomerMemoryUpdateTool(store=store)
        result = tool.execute(customer_id="c1", industry="Finance")
        assert result.success
        assert store.load("c1").industry == "Finance"

    def test_update_sales_stage_valid(self):
        store = CustomerStore()
        store.save(CustomerProfile(customer_id="c1", customer_name="test", sales_stage=SalesStage.LEAD))
        tool = CustomerMemoryUpdateTool(store=store)
        result = tool.execute(customer_id="c1", sales_stage="requirement_confirmation")
        assert result.success
        assert store.load("c1").sales_stage == SalesStage.REQUIREMENT_CONFIRMATION

    def test_update_sales_stage_invalid_transition(self):
        store = CustomerStore()
        store.save(CustomerProfile(customer_id="c1", customer_name="test", sales_stage=SalesStage.LEAD))
        tool = CustomerMemoryUpdateTool(store=store)
        # LEAD → QUOTATION is invalid (skip a stage)
        result = tool.execute(customer_id="c1", sales_stage="quotation")
        assert not result.success
        assert "阶段转换无效" in result.summary

    def test_update_missing_customer_id(self):
        tool = CustomerMemoryUpdateTool()
        result = tool.execute()
        assert not result.success
        assert "customer_id" in result.errors[0]

    def test_update_nonexistent_customer(self):
        tool = CustomerMemoryUpdateTool()
        result = tool.execute(customer_id="nonexistent")
        assert not result.success
        assert "不存在" in result.errors[0] or "未找到" in result.errors[0]

    def test_update_no_changes(self):
        store = CustomerStore()
        store.save(CustomerProfile(customer_id="c1", customer_name="test"))
        tool = CustomerMemoryUpdateTool(store=store)
        result = tool.execute(customer_id="c1", customer_name="test")
        assert result.success
        assert result.warnings


# ═══════════════════════════════════════════════════════════════════════════════
# SalesScoreCalculatorTool
# ═══════════════════════════════════════════════════════════════════════════════


class TestSalesScoreCalculator:
    def test_calculate_deal_score(self):
        store = CustomerStore()
        store.save(CustomerProfile(
            customer_id="c1",
            customer_name="test",
            industry="IT",
            budget_range="100-500万",
            timeline="Q3交付",
            procurement_cycle="3个月",
        ))
        tool = SalesScoreCalculatorTool(customer_store=store)
        result = tool.execute(customer_id="c1", score_type="deal")
        assert result.success
        assert "deal_score" in result.data
        assert 0 <= result.data["deal_score"]["score"] <= 100

    def test_calculate_health_score(self):
        store = CustomerStore()
        store.save(CustomerProfile(customer_id="c1", customer_name="test"))
        tool = SalesScoreCalculatorTool(customer_store=store)
        result = tool.execute(customer_id="c1", score_type="health")
        assert result.success
        assert "health_score" in result.data
        assert 0 <= result.data["health_score"]["score"] <= 100

    def test_calculate_both(self):
        store = CustomerStore()
        store.save(CustomerProfile(customer_id="c1", customer_name="test"))
        tool = SalesScoreCalculatorTool(customer_store=store)
        result = tool.execute(customer_id="c1", score_type="both")
        assert result.success
        assert "deal_score" in result.data
        assert "health_score" in result.data

    def test_missing_customer_id(self):
        tool = SalesScoreCalculatorTool()
        result = tool.execute()
        assert not result.success

    def test_nonexistent_customer(self):
        tool = SalesScoreCalculatorTool()
        result = tool.execute(customer_id="nonexistent")
        assert not result.success

    def test_score_saves_to_profile(self):
        store = CustomerStore()
        store.save(CustomerProfile(customer_id="c1", customer_name="test"))
        tool = SalesScoreCalculatorTool(customer_store=store)
        tool.execute(customer_id="c1", score_type="both")

        profile = store.load("c1")
        assert profile.has_deal_score
        assert profile.has_health_score
