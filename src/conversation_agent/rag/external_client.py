"""External RAG client adapter for RAG_demo /query."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from conversation_agent.rag.base import (
    RagBadResponseError,
    RagConnectionError,
    RagHttpError,
    RagTimeoutError,
)
from conversation_agent.rag.models import RagCallDiagnostic, RagEvidence, RagResult


class ExternalRagClient:
    provider = "external"

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def query(
        self,
        question: str,
        *,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RagResult:
        start = datetime.now(timezone.utc)
        try:
            response = httpx.post(
                f"{self.base_url}/query",
                json={"question": question},
                timeout=self.timeout,
            )
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise RagBadResponseError(f"RAG service returned invalid JSON: {exc}", error_type="invalid_json") from exc
            result = self._to_rag_result(data)
            result.provider = "external"
            result.diagnostics.append(RagCallDiagnostic(
                step_name="external_rag_query",
                provider="external",
                success=True,
                message=f"RAG_demo returned answer with {len(result.evidence)} citations",
                latency_ms=_elapsed(start),
            ))
            return result
        except httpx.TimeoutException as exc:
            raise RagTimeoutError(f"RAG service timeout after {self.timeout}s") from exc
        except httpx.ConnectError as exc:
            raise RagConnectionError(f"RAG service connection error: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise RagHttpError(f"RAG service HTTP error: {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise RagConnectionError(f"RAG service request error: {exc}") from exc

    def _to_rag_result(self, data: Any) -> RagResult:
        if not isinstance(data, dict):
            raise RagBadResponseError("RAG service response must be a JSON object", error_type="schema_error")
        answer = data.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise RagBadResponseError("RAG service response missing non-empty answer", error_type="missing_answer")

        citations = data.get("citations") or data.get("sources") or []
        if not isinstance(citations, list):
            citations = [citations]
        evidence = [_citation_to_evidence(c, idx) for idx, c in enumerate(citations, start=1)]
        evidence = [e for e in evidence if e is not None]
        sources = [_evidence_to_source(e) for e in evidence]

        confidence = data.get("confidence")
        if not isinstance(confidence, (int, float)):
            confidence = 0.75 if evidence else 0.35
        confidence = max(0.0, min(1.0, float(confidence)))

        return RagResult(
            answer=answer,
            evidence=evidence,
            sources=sources,
            confidence=confidence,
            raw_response=data,
        )


def _citation_to_evidence(citation: Any, idx: int) -> RagEvidence | None:
    if isinstance(citation, str):
        text = citation
        return RagEvidence(source_id=citation or f"citation_{idx}", title=citation, text=text, metadata={"raw": citation})
    if not isinstance(citation, dict):
        return None

    source_id = _first(citation, "source_id", "id", "source", "title") or f"citation_{idx}"
    title = _first(citation, "title", "source", "source_id", "id")
    text = _first(citation, "text", "snippet", "content", "quote") or ""
    source_path = _first(citation, "source_path", "path", "file", "url")
    score = _first(citation, "score", "rerank_score", "relevance_score")
    try:
        score_val = float(score) if score is not None else None
    except (TypeError, ValueError):
        score_val = None
    return RagEvidence(
        source_id=str(source_id),
        title=str(title) if title is not None else None,
        source_path=str(source_path) if source_path is not None else None,
        text=str(text),
        score=score_val,
        metadata={k: v for k, v in citation.items() if k not in {"text", "snippet", "content", "quote"}},
    )


def _evidence_to_source(evidence: RagEvidence) -> dict[str, Any]:
    return {
        "source_id": evidence.source_id,
        "title": evidence.title or evidence.source_id,
        "source_path": evidence.source_path or "",
        "confidence": evidence.score,
    }


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _elapsed(start: datetime) -> float:
    return round((datetime.now(timezone.utc) - start).total_seconds() * 1000, 2)
