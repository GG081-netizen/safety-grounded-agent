"""Customer memory tools — search and update customer profiles."""

from __future__ import annotations

from typing import Any

from conversation_agent.memory.customer_store import CustomerStore
from conversation_agent.memory.interaction_store import InteractionStore
from conversation_agent.sales.models import (
    CustomerProfile,
    CustomerStatus,
    SalesStage,
    ToolResult,
)
from conversation_agent.tools.base import BaseTool


class CustomerMemorySearchTool(BaseTool):
    """Search for customers by name, industry, and/or sales stage."""

    name = "customer_memory_search"
    description = (
        "搜索客户档案。支持按客户名称（模糊匹配）、行业、销售阶段、"
        "客户状态进行组合查询。至少需要提供一个筛选条件。"
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "customer_name": {
                "type": "string",
                "description": "客户名称或别名（模糊匹配）",
            },
            "industry": {
                "type": "string",
                "description": "行业名称（精确匹配）",
            },
            "sales_stage": {
                "type": "string",
                "description": "销售阶段，如 lead, quotation, negotiation, won",
                "enum": [
                    "lead",
                    "requirement_confirmation",
                    "quotation",
                    "negotiation",
                    "procurement_approval",
                    "contract_signing",
                    "won",
                    "lost",
                ],
            },
            "status": {
                "type": "string",
                "description": "客户状态，如 active, dormant, churn_risk",
                "enum": ["new", "active", "dormant", "churn_risk", "won", "lost"],
            },
        },
        "required": [],
    }

    def __init__(self, store: CustomerStore | None = None) -> None:
        self._store = store or CustomerStore()

    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute customer search with the given filters."""
        customer_name = kwargs.get("customer_name")
        industry = kwargs.get("industry")
        sales_stage = kwargs.get("sales_stage")
        status = kwargs.get("status")

        results = self._store.search(
            customer_name=customer_name,
            industry=industry,
            sales_stage=sales_stage,
            status=status,
        )

        if not results:
            return ToolResult(
                success=True,
                tool_name=self.name,
                data=[],
                warnings=[],
                summary="未找到匹配的客户",
            )

        data = [
            {
                "customer_id": p.customer_id,
                "customer_name": p.customer_name,
                "industry": p.industry,
                "sales_stage": p.sales_stage.value,
                "status": p.status.value,
                "deal_score": p.deal_score.score if p.deal_score else None,
                "deal_level": p.deal_score.level.value if p.deal_score else None,
                "health_score": p.health_score.health_score if p.health_score else None,
                "health_status": p.health_score.status.value if p.health_score else None,
                "contact_count": p.contact_count,
            }
            for p in results
        ]

        filters = []
        if customer_name:
            filters.append(f"名称包含'{customer_name}'")
        if industry:
            filters.append(f"行业={industry}")
        if sales_stage:
            filters.append(f"阶段={sales_stage}")
        if status:
            filters.append(f"状态={status}")

        return ToolResult(
            success=True,
            tool_name=self.name,
            data=data,
            summary=f"找到 {len(results)} 个客户"
            + (f"（{'，'.join(filters)}）" if filters else "（全部）"),
        )


class CustomerMemoryUpdateTool(BaseTool):
    """Update a customer profile with new information."""

    name = "customer_memory_update"
    description = (
        "更新客户档案。可以更新客户基本信息、销售阶段、联系人、"
        "采购项、风险、跟进动作等。需要提供 customer_id 和要更新的字段。"
    )

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "customer_id": {
                "type": "string",
                "description": "要更新的客户ID",
            },
            "customer_name": {
                "type": "string",
                "description": "新的客户名称（可选）",
            },
            "industry": {
                "type": "string",
                "description": "行业（可选）",
            },
            "sales_stage": {
                "type": "string",
                "description": "销售阶段（可选）",
                "enum": [
                    "lead",
                    "requirement_confirmation",
                    "quotation",
                    "negotiation",
                    "procurement_approval",
                    "contract_signing",
                    "won",
                    "lost",
                ],
            },
            "status": {
                "type": "string",
                "description": "客户状态（可选）",
                "enum": ["new", "active", "dormant", "churn_risk", "won", "lost"],
            },
            "aliases": {
                "type": "array",
                "items": {"type": "string"},
                "description": "别名列表（可选）",
            },
        },
        "required": ["customer_id"],
    }

    def __init__(self, store: CustomerStore | None = None) -> None:
        self._store = store or CustomerStore()

    def execute(self, **kwargs: Any) -> ToolResult:
        """Update a customer profile."""
        customer_id = kwargs.get("customer_id")
        if not customer_id:
            return ToolResult(
                success=False,
                tool_name=self.name,
                errors=["缺少必填参数: customer_id"],
                summary="更新失败：未指定 customer_id",
            )

        profile = self._store.load(customer_id)
        if profile is None:
            return ToolResult(
                success=False,
                tool_name=self.name,
                errors=[f"客户 '{customer_id}' 不存在"],
                summary=f"更新失败：客户 {customer_id} 未找到",
            )

        changed: list[str] = []

        name = kwargs.get("customer_name")
        if name and name != profile.customer_name:
            profile.customer_name = name
            changed.append("customer_name")

        industry = kwargs.get("industry")
        if industry is not None and industry != profile.industry:
            profile.industry = industry
            changed.append("industry")

        stage_val = kwargs.get("sales_stage")
        if stage_val:
            new_stage = SalesStage.from_string(stage_val)
            if new_stage and new_stage != profile.sales_stage:
                if profile.transition_to(new_stage):
                    changed.append(f"sales_stage→{stage_val}")
                else:
                    return ToolResult(
                        success=False,
                        tool_name=self.name,
                        errors=[
                            f"无效的阶段转换: {profile.sales_stage.value} → {stage_val}"
                        ],
                        summary="更新失败：销售阶段转换无效",
                    )

        status_val = kwargs.get("status")
        if status_val:
            try:
                new_status = CustomerStatus(status_val)
                if new_status != profile.status:
                    profile.status = new_status
                    changed.append(f"status→{status_val}")
            except ValueError:
                return ToolResult(
                    success=False,
                    tool_name=self.name,
                    errors=[f"无效的客户状态: {status_val}"],
                    summary="更新失败：无效的客户状态",
                )

        aliases = kwargs.get("aliases")
        if aliases is not None and isinstance(aliases, list):
            profile.aliases = aliases
            changed.append("aliases")

        if not changed:
            return ToolResult(
                success=True,
                tool_name=self.name,
                data={"customer_id": customer_id},
                warnings=["没有字段被修改"],
                summary=f"客户 {customer_id} 无需更新",
            )

        self._store.save(profile)
        return ToolResult(
            success=True,
            tool_name=self.name,
            data={
                "customer_id": customer_id,
                "changed_fields": changed,
            },
            summary=f"已更新客户 {customer_id}: {', '.join(changed)}",
        )
