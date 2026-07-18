"""RAG client protocol and error taxonomy."""

from __future__ import annotations

from typing import Any, Protocol

from conversation_agent.rag.models import RagResult


class RagClient(Protocol):
    def query(
        self,
        question: str,
        *,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RagResult:
        ...


class RagClientError(Exception):
    error_type = "rag_client_error"

    def __init__(self, message: str, *, error_type: str | None = None) -> None:
        super().__init__(message)
        if error_type is not None:
            self.error_type = error_type


class RagTimeoutError(RagClientError):
    error_type = "timeout"


class RagConnectionError(RagClientError):
    error_type = "connection_error"


class RagHttpError(RagClientError):
    error_type = "http_error"


class RagBadResponseError(RagClientError):
    error_type = "schema_error"
