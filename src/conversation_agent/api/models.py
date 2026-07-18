"""Public HTTP response contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from conversation_agent.rag.models import RagProvider


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"] = "ok"
    service: str = "conversation-agent"
    version: str = "0.1.0"


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ready", "not_ready"]


class RequestTraceStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component: str
    status: Literal["succeeded", "denied", "failed", "blocked"]
    code: str
    summary: str


class RagDebugPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: RagProvider
    payload: dict[str, Any]


class PrivilegedDebugPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rag_raw_response: RagDebugPayload | None = None


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    trace_id: str
    session_id: str
    result: dict[str, Any]
    trace: tuple[RequestTraceStep, ...] = ()
    debug: PrivilegedDebugPayload | None = None


class APIErrorItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = ""
    message: str
    error_type: str = ""


class APIErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    request_id: str
    trace_id: str
    details: tuple[APIErrorItem, ...] = Field(default_factory=tuple)
    trace: tuple[RequestTraceStep, ...] = ()
