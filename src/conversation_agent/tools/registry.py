"""Tool registry — register, look up, and list agent tools."""

from __future__ import annotations

from typing import Any

from conversation_agent.tools.base import BaseTool, safe_execute
from conversation_agent.sales.models import ToolResult


class ToolRegistry:
    """A named collection of tools.

    Usage::

        registry = ToolRegistry()
        registry.register(MyTool())
        result = registry.execute("my_tool", param="value")
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Add a tool.  Raises ValueError on duplicate name."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """Look up a tool by name, or None."""
        return self._tools.get(name)

    def execute(self, name: str, **kwargs: Any) -> ToolResult:
        """Execute a registered tool by name.

        Returns a failure ToolResult if the tool is not found.
        """
        tool = self.get(name)
        if tool is None:
            return ToolResult(
                success=False,
                tool_name=name,
                errors=[f"未知工具: {name}"],
                summary=f"工具 '{name}' 未注册",
            )
        return safe_execute(tool, **kwargs)

    def list_tools(self) -> list[str]:
        """Return registered tool names."""
        return sorted(self._tools.keys())

    def to_anthropic_schemas(self) -> list[dict[str, Any]]:
        """Generate Anthropic-compatible tool definitions for all registered tools."""
        return [t.to_anthropic_schema() for t in self._tools.values()]
