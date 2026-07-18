"""Synchronous application service around the existing Coordinator core."""

from __future__ import annotations

from dataclasses import dataclass

from conversation_agent.application.models import UserRequest
from conversation_agent.orchestration.coordinator import Coordinator
from conversation_agent.orchestration.models import (
    OrchestrationRequestMetadata,
    OrchestrationResult,
)
from conversation_agent.runtime.builder import RequestContextBuilder
from conversation_agent.runtime.models import RequestContext
from conversation_agent.runtime.models import RequestMetadata
from conversation_agent.identity.models import Principal
from conversation_agent.authorization.models import AuthorizationDecision
from conversation_agent.task_types import TaskName


class ApplicationExecutionError(RuntimeError):
    """Raised when the orchestration core cannot complete a request."""


@dataclass(frozen=True)
class ApplicationResult:
    context: RequestContext
    orchestration: OrchestrationResult

    def to_public_dict(self, *, include_raw_response: bool = False) -> dict:
        return {
            "request_id": self.context.request_id,
            "trace_id": self.context.trace_id,
            "session_id": self.context.session_id,
            "result": self.orchestration.to_public_dict(
                include_raw_response=include_raw_response
            ),
        }


class ChatService:
    """Map trusted request context into the current synchronous Coordinator."""

    def __init__(
        self,
        *,
        coordinator: Coordinator,
        context_builder: RequestContextBuilder,
    ) -> None:
        self._coordinator = coordinator
        self._context_builder = context_builder

    def execute(
        self,
        request: UserRequest,
        *,
        metadata: RequestMetadata,
        principal: Principal,
        authorization: AuthorizationDecision,
        idempotency_key: str | None = None,
        forced_task: TaskName | None = None,
    ) -> ApplicationResult:
        context = self._context_builder.build(
            principal=principal,
            authorization=authorization,
            session_id=request.session_id,
            request_id=metadata.request_id,
            trace_id=metadata.trace_id,
            received_at=metadata.received_at,
            idempotency_key=idempotency_key,
        )
        return self.execute_with_context(
            request,
            context=context,
            forced_task=forced_task,
        )

    def execute_with_context(
        self,
        request: UserRequest,
        *,
        context: RequestContext,
        forced_task: TaskName | None = None,
    ) -> ApplicationResult:
        """Execute with a previously built trusted context."""
        task_override = forced_task or request.task_override
        metadata = OrchestrationRequestMetadata(
            request_id=context.request_id,
            trace_id=context.trace_id,
            session_id=context.session_id,
        )
        try:
            result = self._coordinator.run(
                request.text,
                session_id=context.session_id,
                task_override=task_override,
                request_metadata=metadata,
            )
        except Exception as exc:
            raise ApplicationExecutionError(
                "The agent orchestration request could not be completed."
            ) from exc
        return ApplicationResult(context=context, orchestration=result)
