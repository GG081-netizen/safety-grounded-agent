"""Base tool interface — every tool must implement this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from conversation_agent.sales.models import ToolResult


class BaseTool(ABC):
    """Abstract base for an agent tool.

    Every tool MUST:
      - Define name / description / parameters_schema
      - Implement execute() returning ToolResult
      - Catch internal exceptions and return a failure ToolResult
    """

    name: str = ""
    description: str = ""
    parameters_schema: dict[str, Any] = {}

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with the given parameters.

        Must always return a ToolResult — never raise.
        """
        ...

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Generate an Anthropic-compatible tool definition."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters_schema,
        }


def safe_execute(tool: BaseTool, **kwargs: Any) -> ToolResult:
    """Call tool.execute() and catch any unexpected exception into ToolResult."""
    try:
        return tool.execute(**kwargs)
    except Exception as exc:
        return ToolResult(
            success=False,
            tool_name=tool.name,
            errors=[f"{type(exc).__name__}: {exc}"],
            summary=f"工具 {tool.name} 执行异常",
        )
