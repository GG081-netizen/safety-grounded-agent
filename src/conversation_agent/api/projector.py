"""Project internal application results into the validated public API contract."""

from __future__ import annotations

from conversation_agent.api.models import (
    AgentResponse,
    PrivilegedDebugPayload,
    RagDebugPayload,
    RequestTraceStep,
)
from conversation_agent.application.service import ApplicationResult


class ResponseProjector:
    def project(
        self,
        result: ApplicationResult,
        *,
        security_trace: tuple[RequestTraceStep, ...],
        include_raw_response: bool,
    ) -> AgentResponse:
        public_result = result.orchestration.to_public_dict(include_raw_response=False)
        orchestration_trace = tuple(
            RequestTraceStep(
                component=step.step_name,
                status=(
                    "blocked"
                    if step.step_name == "policy_engine" and result.orchestration.policy.is_blocked
                    else "succeeded"
                ),
                code=(
                    "policy_blocked"
                    if step.step_name == "policy_engine" and result.orchestration.policy.is_blocked
                    else step.step_name
                ),
                summary=step.output_summary or "Orchestration step completed.",
            )
            for step in result.orchestration.trace
        )
        debug = None
        rag = result.orchestration.rag_result
        if (
            include_raw_response
            and "raw_response:view" in result.context.authorization.permissions
            and rag is not None
            and rag.raw_response is not None
        ):
            debug = PrivilegedDebugPayload(
                rag_raw_response=RagDebugPayload(
                    provider=rag.provider,
                    payload=rag.raw_response,
                )
            )
        return AgentResponse(
            request_id=result.context.request_id,
            trace_id=result.context.trace_id,
            session_id=result.context.session_id,
            result=public_result,
            trace=security_trace + orchestration_trace,
            debug=debug,
        )
