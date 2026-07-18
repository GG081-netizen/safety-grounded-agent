"""Deterministic evaluation for the RAG client adapter boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from conversation_agent.orchestration.coordinator import Coordinator
from conversation_agent.rag.base import RagTimeoutError
from conversation_agent.rag.factory import FallbackRagClient
from conversation_agent.rag.models import RagCallDiagnostic, RagEvidence, RagResult

EvalStatus = Literal["pass", "warning", "fail", "blocked"]


class RagAdapterEvalCase(BaseModel):
    """One fixed scenario in the adapter reliability evaluation."""

    name: str
    description: str


class RagAdapterEvalCaseResult(BaseModel):
    """Observable result for one evaluation case."""

    case_name: str
    provider: str = "none"
    success: bool = False
    blocked: bool = False
    rag_call_count: int | None = None
    has_citation: bool = False
    has_evidence: bool = False
    confidence: float = 0.0
    raw_response_exposed: bool = False
    warnings: list[str] = Field(default_factory=list)
    trace_steps: list[str] = Field(default_factory=list)


class RagAdapterEvalSummary(BaseModel):
    """Aggregated metrics and release gate status."""

    case_count: int
    external_success_rate: float
    fallback_rate: float
    citation_coverage: float
    no_evidence_rate: float
    average_confidence: float
    blocked_no_rag_call_rate: float
    raw_response_exposure_rate: float
    status: EvalStatus
    gate_messages: list[str] = Field(default_factory=list)


class RagAdapterEvalReport(BaseModel):
    """Full report returned by the CLI and tests."""

    summary: RagAdapterEvalSummary
    cases: list[RagAdapterEvalCaseResult]

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def evaluate_rag_adapter(include_raw_response: bool = False) -> RagAdapterEvalReport:
    """Run six deterministic adapter cases without calling a real RAG service."""
    case_results = [
        _run_coordinator_case(
            "external_success_with_citation",
            _StaticRagClient(_external_result(with_citation=True)),
            include_raw_response,
        ),
        _run_coordinator_case(
            "external_success_without_citation",
            _StaticRagClient(_external_result(with_citation=False)),
            include_raw_response,
        ),
        _run_coordinator_case(
            "external_failure_with_local_fallback",
            FallbackRagClient(_FailingRagClient(), _StaticRagClient(_local_result()), fallback_enabled=True),
            include_raw_response,
        ),
        _run_coordinator_case(
            "external_failure_without_fallback",
            FallbackRagClient(_FailingRagClient(), None, fallback_enabled=False),
            include_raw_response,
        ),
        _run_blocked_case(include_raw_response),
        _run_coordinator_case(
            "local_provider_direct",
            _StaticRagClient(_local_result()),
            include_raw_response,
        ),
    ]
    return RagAdapterEvalReport(
        summary=_summarize(case_results),
        cases=case_results,
    )


def _run_coordinator_case(
    case_name: str,
    rag_client,
    include_raw_response: bool,
) -> RagAdapterEvalCaseResult:
    result = Coordinator(rag_client=rag_client).run(
        "笔记本批量采购需要注意什么？",
        session_id=f"eval-{case_name}",
        task_override="qa",
    )
    rag = result.rag_result
    public = result.to_public_dict(include_raw_response=include_raw_response)
    public_rag = public.get("rag_result") or {}
    evidence = rag.evidence if rag else []
    sources = rag.sources if rag else []
    return RagAdapterEvalCaseResult(
        case_name=case_name,
        provider=rag.provider if rag else "none",
        success=bool(rag and rag.provider not in {"none"}),
        blocked=result.policy.is_blocked,
        has_citation=bool(sources),
        has_evidence=bool(evidence),
        confidence=rag.confidence if rag else result.confidence,
        raw_response_exposed=isinstance(public_rag, dict) and "raw_response" in public_rag,
        warnings=rag.warnings if rag else [],
        trace_steps=[step.step_name for step in result.trace],
    )


def _run_blocked_case(include_raw_response: bool) -> RagAdapterEvalCaseResult:
    rag_client = _CountingRagClient(_external_result(with_citation=True))
    result = Coordinator(rag_client=rag_client).run(
        "帮我查采购负责人的私人手机号。",
        session_id="eval-blocked",
        task_override="qa",
    )
    public = result.to_public_dict(include_raw_response=include_raw_response)
    public_rag = public.get("rag_result") or {}
    return RagAdapterEvalCaseResult(
        case_name="blocked_request_no_rag_call",
        provider="none",
        success=result.policy.is_blocked and rag_client.call_count == 0,
        blocked=True,
        rag_call_count=rag_client.call_count,
        confidence=result.confidence,
        raw_response_exposed=isinstance(public_rag, dict) and "raw_response" in public_rag,
        trace_steps=[step.step_name for step in result.trace],
    )


def _summarize(cases: list[RagAdapterEvalCaseResult]) -> RagAdapterEvalSummary:
    case_count = len(cases)
    answer_cases = [case for case in cases if not case.blocked]
    blocked_cases = [case for case in cases if case.blocked]

    external_success_rate = _rate(
        sum(1 for case in cases if case.provider == "external" and case.success),
        case_count,
    )
    fallback_rate = _rate(sum(1 for case in cases if case.provider == "fallback"), case_count)
    citation_coverage = _rate(sum(1 for case in answer_cases if case.has_citation), len(answer_cases))
    no_evidence_rate = _rate(sum(1 for case in answer_cases if not case.has_evidence), len(answer_cases))
    average_confidence = _round(
        sum(case.confidence for case in answer_cases) / len(answer_cases)
        if answer_cases else 0.0
    )
    blocked_no_rag_call_rate = _rate(
        sum(1 for case in blocked_cases if case.rag_call_count == 0),
        len(blocked_cases),
    )
    raw_response_exposure_rate = _rate(
        sum(1 for case in cases if case.raw_response_exposed),
        case_count,
    )

    status, messages = _gate(
        blocked_no_rag_call_rate=blocked_no_rag_call_rate,
        raw_response_exposure_rate=raw_response_exposure_rate,
        citation_coverage=citation_coverage,
        average_confidence=average_confidence,
    )
    return RagAdapterEvalSummary(
        case_count=case_count,
        external_success_rate=external_success_rate,
        fallback_rate=fallback_rate,
        citation_coverage=citation_coverage,
        no_evidence_rate=no_evidence_rate,
        average_confidence=average_confidence,
        blocked_no_rag_call_rate=blocked_no_rag_call_rate,
        raw_response_exposure_rate=raw_response_exposure_rate,
        status=status,
        gate_messages=messages,
    )


def _gate(
    *,
    blocked_no_rag_call_rate: float,
    raw_response_exposure_rate: float,
    citation_coverage: float,
    average_confidence: float,
) -> tuple[EvalStatus, list[str]]:
    messages: list[str] = []
    fail = False
    warning = False
    if blocked_no_rag_call_rate != 1.0:
        fail = True
        messages.append("blocked_no_rag_call_rate must equal 1.0")
    if raw_response_exposure_rate != 0.0:
        fail = True
        messages.append("raw_response_exposure_rate must equal 0.0 by default")
    if citation_coverage < 0.5:
        warning = True
        messages.append("citation_coverage should be >= 0.5")
    if average_confidence < 0.3:
        warning = True
        messages.append("average_confidence should be >= 0.3")
    if fail:
        return "fail", messages
    if warning:
        return "warning", messages
    return "pass", ["all gates satisfied"]


def _external_result(with_citation: bool) -> RagResult:
    evidence = [
        RagEvidence(
            source_id="SLA_2025",
            title="采购 SLA 规则",
            source_path="rag_demo://policies/sla",
            text="批量采购需要明确 SLA、交付周期、验收标准和售后响应。",
            score=0.91,
        )
    ] if with_citation else []
    return RagResult(
        answer="批量采购应先确认 SLA、交付周期、验收标准和售后响应。",
        evidence=evidence,
        sources=[_source_from_evidence(item) for item in evidence],
        confidence=0.82 if with_citation else 0.42,
        provider="external",
        diagnostics=[
            RagCallDiagnostic(
                step_name="external_rag_query",
                provider="external",
                success=True,
                message=f"RAG_demo returned answer with {len(evidence)} citations",
                latency_ms=12.0,
            )
        ],
        raw_response={"debug_trace": {"retrieved": len(evidence), "case": "eval"}},
    )


def _local_result() -> RagResult:
    evidence = [
        RagEvidence(
            source_id="LOCAL_POLICY",
            title="本地采购策略",
            source_path="data/knowledge/policies/sla_procurement.json",
            text="本地 fallback 只提供低置信度建议，应提示用户核验外部知识库。",
            score=0.68,
        )
    ]
    return RagResult(
        answer="本地 fallback 建议核对 SLA、预算和交付风险。",
        evidence=evidence,
        sources=[_source_from_evidence(item) for item in evidence],
        confidence=0.76,
        provider="local",
        diagnostics=[
            RagCallDiagnostic(
                step_name="local_rag_query",
                provider="local",
                success=True,
                message="Local keyword RAG returned 1 evidence items",
                latency_ms=4.0,
            )
        ],
    )


def _source_from_evidence(evidence: RagEvidence) -> dict[str, Any]:
    return {
        "source_id": evidence.source_id,
        "title": evidence.title or evidence.source_id,
        "source_path": evidence.source_path or "",
        "confidence": evidence.score,
    }


class _StaticRagClient:
    def __init__(self, result: RagResult) -> None:
        self.result = result
        self.call_count = 0

    def query(self, question: str, *, trace_id: str | None = None, metadata: dict[str, Any] | None = None) -> RagResult:
        self.call_count += 1
        return self.result.model_copy(deep=True)


class _CountingRagClient(_StaticRagClient):
    pass


class _FailingRagClient:
    def query(self, question: str, *, trace_id: str | None = None, metadata: dict[str, Any] | None = None) -> RagResult:
        raise RagTimeoutError("RAG service timeout after 30s")


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return _round(numerator / denominator)


def _round(value: float) -> float:
    return round(float(value), 2)
