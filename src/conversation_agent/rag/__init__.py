"""RAG client abstraction with external service support and local fallback."""

from conversation_agent.rag.base import RagClient, RagClientError
from conversation_agent.rag.external_client import ExternalRagClient
from conversation_agent.rag.factory import FallbackRagClient, create_rag_client
from conversation_agent.rag.local_client import LocalKeywordRagClient
from conversation_agent.rag.models import Evidence, RagCallDiagnostic, RagEvidence, RagResult
from conversation_agent.rag.module import KnowledgeStore, generate_with_citations, rank_and_filter, retrieve

__all__ = [
    "RagClient",
    "RagClientError",
    "ExternalRagClient",
    "FallbackRagClient",
    "LocalKeywordRagClient",
    "create_rag_client",
    "Evidence",
    "RagEvidence",
    "RagCallDiagnostic",
    "RagResult",
    "KnowledgeStore",
    "retrieve",
    "rank_and_filter",
    "generate_with_citations",
]
