"""Select the approved M1.4-E execution path without HTTP concerns."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial

import anyio

from conversation_agent.application.durable_service import DurableApplicationService
from conversation_agent.application.idempotent_durable_service import (
    IdempotentDurableApplicationService,
)
from conversation_agent.application.models import UserRequest
from conversation_agent.application.service import ApplicationResult, ChatService
from conversation_agent.config import IdempotencyHeaderMode, PersistenceMode
from conversation_agent.database.records import IdempotentResultOutcome
from conversation_agent.runtime.models import RequestContext
from conversation_agent.task_types import TaskName


class ExecutionGatewayError(RuntimeError):
    def __init__(self, code: str, *, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class GatewayExecutionResult:
    application_result: ApplicationResult
    idempotency_status: str | None = None


class RequestExecutionGateway:
    def __init__(
        self,
        *,
        persistence_mode: PersistenceMode,
        header_mode: IdempotencyHeaderMode,
        chat_service: ChatService,
        durable_service: DurableApplicationService | None = None,
        idempotent_service: IdempotentDurableApplicationService | None = None,
    ) -> None:
        self._persistence_mode = persistence_mode
        self._header_mode = header_mode
        self._chat_service = chat_service
        self._durable = durable_service
        self._idempotent = idempotent_service

    async def execute(
        self,
        request: UserRequest,
        *,
        context: RequestContext,
        operation: str,
        idempotency_key: str | None,
        replay_compatible: bool,
        forced_task: TaskName | None = None,
    ) -> GatewayExecutionResult:
        if idempotency_key is None:
            if self._header_mode is IdempotencyHeaderMode.REQUIRED:
                raise ExecutionGatewayError(
                    "missing_idempotency_key", status_code=400
                )
            if self._persistence_mode is PersistenceMode.NULL:
                execute = partial(
                    self._chat_service.execute_with_context,
                    request,
                    context=context,
                    forced_task=forced_task,
                )
                result = await anyio.to_thread.run_sync(
                    execute,
                    abandon_on_cancel=True,
                )
                return GatewayExecutionResult(application_result=result)
            if self._durable is None:
                raise ExecutionGatewayError(
                    "persistence_unavailable", status_code=503
                )
            result = await self._durable.execute(
                request,
                context=context,
                operation=operation,
                forced_task=forced_task,
            )
            return GatewayExecutionResult(application_result=result)

        if not replay_compatible:
            raise ExecutionGatewayError(
                "idempotency_not_supported_for_raw_response",
                status_code=400,
            )
        if self._persistence_mode is PersistenceMode.NULL:
            raise ExecutionGatewayError(
                "idempotency_unavailable", status_code=503
            )
        if self._idempotent is None:
            raise ExecutionGatewayError(
                "persistence_unavailable", status_code=503
            )
        outcome = await self._idempotent.execute(
            request,
            context=context,
            operation=operation,
            idempotency_key=idempotency_key,
            forced_task=forced_task,
        )
        if outcome.outcome is IdempotentResultOutcome.IN_PROGRESS:
            raise ExecutionGatewayError(
                "idempotency_request_in_progress", status_code=409
            )
        if outcome.outcome is IdempotentResultOutcome.CONFLICT:
            raise ExecutionGatewayError(
                "idempotency_key_conflict", status_code=409
            )
        if outcome.outcome is IdempotentResultOutcome.PREVIOUS_FAILURE:
            raise ExecutionGatewayError(
                "idempotency_previous_failure", status_code=409
            )
        if not isinstance(outcome.application_result, ApplicationResult):
            raise ExecutionGatewayError(
                "invalid_idempotency_state", status_code=500
            )
        status = (
            "replayed"
            if outcome.outcome is IdempotentResultOutcome.REPLAYED
            else "executed"
        )
        return GatewayExecutionResult(
            application_result=outcome.application_result,
            idempotency_status=status,
        )
