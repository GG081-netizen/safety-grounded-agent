"""Agent tools — deterministic computation and storage.

Core tools (Phase 6):
  - customer_memory_search   — search customer profiles
  - customer_memory_update   — update customer profiles
  - sales_score_calculator   — compute deal + health scores
"""

from conversation_agent.tools.base import BaseTool, safe_execute
from conversation_agent.tools.registry import ToolRegistry

__all__ = ["BaseTool", "safe_execute", "ToolRegistry"]
