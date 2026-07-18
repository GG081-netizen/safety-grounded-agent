"""Local keyword RAG client used as direct provider or fallback."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from conversation_agent.rag.models import RagCallDiagnostic, RagResult
from conversation_agent.rag.module import KnowledgeStore, generate_with_citations, rank_and_filter, retrieve


class LocalKeywordRagClient:
    provider = "local"

    def __init__(self, store: KnowledgeStore | None = None) -> None:
        self._store = store or KnowledgeStore()

    def query(
        self,
        question: str,
        *,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RagResult:
        start = datetime.now(timezone.utc)
        candidates = retrieve(question, store=self._store)
        evidence = rank_and_filter(question, candidates)
        result = generate_with_citations(question, evidence)
        result.provider = "local"
        result.diagnostics.append(RagCallDiagnostic(
            step_name="local_rag_query",
            provider="local",
            success=True,
            message=f"Local keyword RAG returned {len(result.evidence)} evidence items",
            latency_ms=_elapsed(start),
        ))
        return result


def _elapsed(start: datetime) -> float:
    return round((datetime.now(timezone.utc) - start).total_seconds() * 1000, 2)
