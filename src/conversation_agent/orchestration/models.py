"""Structured trace and orchestration models."""

from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field

from conversation_agent.policy.models import PolicyDecision
from conversation_agent.rag.models import RagResult
from conversation_agent.sales.models import IntentResult, Interaction, InteractionMetadata
from conversation_agent.task_types import TaskName


class OrchestrationRequestMetadata(BaseModel):
    """Minimal immutable projection of the trusted request context."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    request_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)


class TaskRoute(BaseModel):
    task: TaskName
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = ""


class AgentStep(BaseModel):
    step_name: str
    input_summary: str = ""
    output_summary: str = ""
    confidence: float | None = None
    latency_ms: int = 0
    tool_calls: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class OrchestrationResult(BaseModel):
    session_id: str
    user_input: str
    policy: PolicyDecision
    intent_result: IntentResult | None = None
    task_route: TaskRoute | None = None
    final_response: str = ""
    rag_result: RagResult | None = None
    citations: list[dict] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    trace: list[AgentStep] = Field(default_factory=list)
    metadata: InteractionMetadata = Field(default_factory=InteractionMetadata)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


    def to_public_dict(self, include_raw_response: bool = False) -> dict:
        """Dump orchestration output while respecting RAG raw response policy."""
        data = self.model_dump(mode="json")
        if self.rag_result is not None:
            data["rag_result"] = self.rag_result.to_public_dict(
                include_raw_response=include_raw_response
            )
        else:
            data["rag_result"] = None
        return data

    def to_interaction(self) -> Interaction:
        """Compatibility bridge for older CLI/tests that expect Interaction."""
        return Interaction(
            session_id=self.session_id,
            user_input=self.user_input,
            intent_result=self.intent_result,
            tools_called=[],
            agent_response=self.final_response,
            metadata=self.metadata,
        )
