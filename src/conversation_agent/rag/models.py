"""RAG evidence, diagnostics, and answer models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RagProvider = Literal["external", "local", "fallback", "none"]


class RagCallDiagnostic(BaseModel):
    """Diagnostic record for one RAG provider call."""

    step_name: str
    provider: str
    success: bool
    error_type: str | None = None
    message: str | None = None
    latency_ms: float | None = None


class RagEvidence(BaseModel):
    """A citation/evidence item normalized from local or external RAG."""

    source_id: str
    title: str | None = None
    source_path: str | None = None
    text: str = ""
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# Backward-compatible alias used by the original local keyword module/tests.
class Evidence(RagEvidence):
    title: str = ""
    source_path: str = ""
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    score: float = Field(default=0.0, ge=0.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class RagResult(BaseModel):
    """Unified result returned by all RAG clients."""

    answer: str
    evidence: list[RagEvidence] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    provider: RagProvider = "none"
    diagnostics: list[RagCallDiagnostic] = Field(default_factory=list)
    raw_response: dict[str, Any] | None = None

    def to_public_dict(self, include_raw_response: bool = False) -> dict[str, Any]:
        """Dump for API/CLI output, hiding raw external debug data by default."""
        data = self.model_dump(mode="json")
        if not include_raw_response:
            data.pop("raw_response", None)
        return data
