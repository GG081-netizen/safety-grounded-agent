"""M1.4-C durable application component with two short transactions."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from functools import partial

import anyio

from conversation_agent.application.models import UserRequest
from conversation_agent.application.persistence_mappers import (
    AuditPersistenceMapper,
    FailureCodeMapper,
    RequestPersistenceMapper,
    RunPersistenceMapper,
)
from conversation_agent.application.service import (
    ApplicationExecutionError,
    ApplicationResult,
    ChatService,
)
from conversation_agent.database.errors import (
    DurableApplicationExecutionError,
    PersistenceFinalizationError,
    RequestInitializationError,
)
from conversation_agent.database.records import PersistedAgentRequestRef
from conversation_agent.database.unit_of_work import ExecutionUnitOfWork
from conversation_agent.runtime.models import RequestContext
from conversation_agent.task_types import TaskName

ExecutionUnitOfWorkFactory = Callable[[], ExecutionUnitOfWork]
Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


class DurableApplicationService:
    """Persist request/run/audit state without wiring it into FastAPI."""

    def __init__(
        self,
        *,
        chat_service: ChatService,
        uow_factory: ExecutionUnitOfWorkFactory,
        request_mapper: RequestPersistenceMapper | None = None,
        run_mapper: RunPersistenceMapper | None = None,
        audit_mapper: AuditPersistenceMapper | None = None,
        failure_code_mapper: FailureCodeMapper | None = None,
        clock: Clock | None = None,
        run_id_factory: IdFactory | None = None,
        event_id_factory: IdFactory | None = None,
    ) -> None:
        self._chat_service = chat_service
        self._uow_factory = uow_factory
        self._request_mapper = request_mapper or RequestPersistenceMapper()
        self._run_mapper = run_mapper or RunPersistenceMapper()
        self._audit_mapper = audit_mapper or AuditPersistenceMapper()
        self._failure_code_mapper = failure_code_mapper or FailureCodeMapper()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._run_id_factory = run_id_factory or (lambda: str(uuid.uuid4()))
        self._event_id_factory = event_id_factory or (lambda: str(uuid.uuid4()))

    async def execute(
        self,
        request: UserRequest,
        *,
        context: RequestContext,
        operation: str,
        forced_task: TaskName | None = None,
    ) -> ApplicationResult:
        accepted_at = self._now()
        task_override = forced_task or request.task_override
        task_value = None if task_override is None else str(task_override)
        request_record = self._request_mapper.map(
            context=context,
            operation=operation,
            user_text=request.text,
            task_override=task_value,
            request_session_id=request.session_id,
            created_at=accepted_at,
        )
        try:
            persisted_request = await self._accept_request(
                context=context,
                operation=operation,
                record=request_record,
                event_time=accepted_at,
            )
        except Exception as exc:
            raise RequestInitializationError(
                "The request could not be durably accepted."
            ) from exc

        run_id = self._run_id_factory()
        run_started_at = self._now()
        execute = partial(
            self._chat_service.execute_with_context,
            request,
            context=context,
            forced_task=forced_task,
        )
        try:
            result = await anyio.to_thread.run_sync(
                execute,
                abandon_on_cancel=True,
            )
        except Exception as exc:
            run_completed_at = self._now()
            failure_code = self._failure_code_mapper.require(
                "application_service_failed"
                if isinstance(exc, ApplicationExecutionError)
                else "coordinator_execution_failed"
            )
            try:
                await self._finalize_failed(
                    context=context,
                    request=persisted_request,
                    run_id=run_id,
                    failure_code=failure_code,
                    started_at=run_started_at,
                    completed_at=run_completed_at,
                )
            except Exception as finalization_error:
                raise PersistenceFinalizationError(
                    "The failed request could not be durably finalized."
                ) from finalization_error
            raise DurableApplicationExecutionError(
                "The durable application request could not be completed."
            ) from exc

        run_completed_at = self._now()
        try:
            await self._finalize_result(
                result=result,
                request=persisted_request,
                run_id=run_id,
                started_at=run_started_at,
                completed_at=run_completed_at,
            )
        except Exception as exc:
            raise PersistenceFinalizationError(
                "The application result could not be durably finalized."
            ) from exc
        return result

    async def _accept_request(
        self,
        *,
        context: RequestContext,
        operation: str,
        record,
        event_time: datetime,
    ) -> PersistedAgentRequestRef:
        async with self._uow_factory() as uow:
            repository = uow.execution_repository
            request_ref = await repository.create_request(record)
            await repository.create_audit_event(
                self._audit_mapper.request_accepted(
                    context=context,
                    event_id=self._event_id_factory(),
                    operation=operation,
                    created_at=event_time,
                )
            )
            await uow.commit()
            return request_ref

    async def _finalize_result(
        self,
        *,
        result: ApplicationResult,
        request: PersistedAgentRequestRef,
        run_id: str,
        started_at: datetime,
        completed_at: datetime,
    ) -> None:
        blocked = result.orchestration.policy.is_blocked
        run = self._run_mapper.completed(
            result=result,
            request=request,
            run_id=run_id,
            started_at=started_at,
            completed_at=completed_at,
        )
        audit = (
            self._audit_mapper.policy_blocked(
                result=result,
                event_id=self._event_id_factory(),
                run_id=run_id,
                created_at=completed_at,
            )
            if blocked
            else self._audit_mapper.request_completed(
                result=result,
                event_id=self._event_id_factory(),
                run_id=run_id,
                created_at=completed_at,
            )
        )
        async with self._uow_factory() as uow:
            repository = uow.execution_repository
            locked = await repository.get_request_for_update(request.request_id)
            await repository.create_run(run)
            await repository.finalize_request_completed(
                locked, completed_at=completed_at
            )
            await repository.create_audit_event(audit)
            await uow.commit()

    async def _finalize_failed(
        self,
        *,
        context: RequestContext,
        request: PersistedAgentRequestRef,
        run_id: str,
        failure_code: str,
        started_at: datetime,
        completed_at: datetime,
    ) -> None:
        run = self._run_mapper.failed(
            context=context,
            request=request,
            run_id=run_id,
            failure_code=failure_code,
            started_at=started_at,
            completed_at=completed_at,
        )
        audit = self._audit_mapper.request_failed(
            context=context,
            event_id=self._event_id_factory(),
            run_id=run_id,
            failure_code=failure_code,
            created_at=completed_at,
        )
        async with self._uow_factory() as uow:
            repository = uow.execution_repository
            locked = await repository.get_request_for_update(request.request_id)
            await repository.create_run(run)
            await repository.finalize_request_failed(
                locked,
                completed_at=completed_at,
                failure_code=failure_code,
            )
            await repository.create_audit_event(audit)
            await uow.commit()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Clock must return a timezone-aware UTC datetime")
        if value.utcoffset() != timedelta(0):
            raise ValueError("Clock must return UTC")
        return value.astimezone(timezone.utc)
