"""RAG client factory and fallback wrapper."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from conversation_agent.config import RagServiceConfig
from conversation_agent.rag.base import RagClient, RagClientError
from conversation_agent.rag.external_client import ExternalRagClient
from conversation_agent.rag.local_client import LocalKeywordRagClient
from conversation_agent.rag.models import RagCallDiagnostic, RagResult

_FALLBACK_WARNING = "External RAG unavailable; used local keyword fallback."


class FallbackRagClient:
    def __init__(self, primary: RagClient, fallback: RagClient | None = None, fallback_enabled: bool = True) -> None:
        self._primary = primary
        self._fallback = fallback
        self._fallback_enabled = fallback_enabled

    def query(
        self,
        question: str,
        *,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RagResult:
        start = datetime.now(timezone.utc)
        try:
            return self._primary.query(question, trace_id=trace_id, metadata=metadata)
        except RagClientError as exc:
            failure = RagCallDiagnostic(
                step_name="external_rag_query",
                provider="external",
                success=False,
                error_type=exc.error_type,
                message=str(exc),
                latency_ms=_elapsed(start),
            )
            if self._fallback_enabled and self._fallback is not None:
                result = self._fallback.query(question, trace_id=trace_id, metadata=metadata)
                result.provider = "fallback"
                result.confidence = min(result.confidence, 0.55)
                result.warnings.insert(0, _FALLBACK_WARNING)
                local_diagnostics = [
                    d.model_copy(update={"step_name": "local_rag_fallback"})
                    for d in result.diagnostics
                ]
                result.diagnostics = [failure, *local_diagnostics]
                return result
            return RagResult(
                answer="外部 RAG 服务暂不可用，且未启用本地 fallback。",
                confidence=0.15,
                warnings=[str(exc)],
                provider="none",
                diagnostics=[failure],
            )


def create_rag_client(config: RagServiceConfig) -> RagClient:
    local = LocalKeywordRagClient()
    if config.provider == "local":
        return local
    external = ExternalRagClient(config.base_url, timeout=config.timeout_seconds)
    return FallbackRagClient(
        primary=external,
        fallback=local if config.fallback_to_local else None,
        fallback_enabled=config.fallback_to_local,
    )


def _elapsed(start: datetime) -> float:
    return round((datetime.now(timezone.utc) - start).total_seconds() * 1000, 2)
