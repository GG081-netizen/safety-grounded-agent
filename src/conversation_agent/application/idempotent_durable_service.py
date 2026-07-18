"""M1.4-D persistent idempotency component; not wired into FastAPI."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from functools import partial

import anyio

from conversation_agent.application.idempotency_mappers import ReplaySnapshotMapper
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
    IdempotencyError,
    PersistenceFinalizationError,
    RequestInitializationError,
)
from conversation_agent.database.idempotency import scope_from_values
from conversation_agent.database.records import (
    ClaimOutcome,
    IdempotencyClaimDecision,
    IdempotencyClaimRequest,
    IdempotencyClaimToken,
    IdempotencyPolicy,
    IdempotentApplicationResult,
    IdempotentResultOutcome,
    PersistedAgentRequestRef,
)
from conversation_agent.database.unit_of_work import IdempotentExecutionUnitOfWork
from conversation_agent.runtime.models import RequestContext
from conversation_agent.task_types import TaskName

IdempotentUnitOfWorkFactory = Callable[[], IdempotentExecutionUnitOfWork]
Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


class IdempotentDurableApplicationService:
    """Suppress duplicate component executions using persistent claim ownership."""

    def __init__(
        self,
        *,
        chat_service: ChatService,
        uow_factory: IdempotentUnitOfWorkFactory,
        policy: IdempotencyPolicy | None = None,
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
        self._policy = policy or IdempotencyPolicy()
        self._request_mapper = request_mapper or RequestPersistenceMapper()
        self._run_mapper = run_mapper or RunPersistenceMapper()
        self._audit_mapper = audit_mapper or AuditPersistenceMapper()
        self._failure_code_mapper = failure_code_mapper or FailureCodeMapper()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._run_id_factory = run_id_factory or (lambda: str(uuid.uuid4()))
        self._event_id_factory = event_id_factory or (lambda: str(uuid.uuid4()))
        self._replay_mapper = ReplaySnapshotMapper(
            max_bytes=self._policy.max_replay_snapshot_bytes
        )

    async def execute(
        self,
        request: UserRequest,
        *,
        context: RequestContext,
        operation: str,
        idempotency_key: str,
        forced_task: TaskName | None = None,
    ) -> IdempotentApplicationResult:
        if not context.authorization.allowed:
            raise RequestInitializationError(
                "Current authorization is required before idempotency processing."
            )
        if not idempotency_key:
            raise RequestInitializationError(
                "An idempotency key is required for this component."
            )

        accepted_at = self._now()
        task_override = forced_task or request.task_override
        task_value = None if task_override is None else str(task_override)
        scope = scope_from_values(
            tenant_id=context.principal.tenant_id,
            organization_id=context.principal.organization_id,
            principal_user_id=context.principal.user_id,
            operation=operation,
            raw_key=idempotency_key,
        )
        request_record = self._request_mapper.map(
            context=context,
            operation=operation,
            user_text=request.text,
            task_override=task_value,
            request_session_id=request.session_id,
            created_at=accepted_at,
            idempotency_key_hash=scope.key_hash,
        )
        claim_request = IdempotencyClaimRequest(
            scope=scope,
            request_fingerprint=request_record.request_fingerprint,
            fingerprint_version=request_record.fingerprint_version,
            owner_request_id=context.request_id,
            lease_duration_seconds=self._policy.lease_duration_seconds,
        )

        try:
            accepted = await self._claim_and_accept(
                request=request,
                context=context,
                operation=operation,
                request_record=request_record,
                claim_request=claim_request,
                accepted_at=accepted_at,
            )
        except IdempotencyError:
            raise
        except Exception as exc:
            raise RequestInitializationError(
                "The idempotent request could not be durably accepted."
            ) from exc
        if isinstance(accepted, IdempotentApplicationResult):
            return accepted
        token, persisted_request = accepted

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
                    token=token,
                    run_id=run_id,
                    failure_code=failure_code,
                    started_at=run_started_at,
                    completed_at=run_completed_at,
                )
            except Exception as finalization_error:
                raise PersistenceFinalizationError(
                    "The failed idempotent request could not be finalized."
                ) from finalization_error
            raise DurableApplicationExecutionError(
                "The idempotent application request could not be completed."
            ) from exc

        run_completed_at = self._now()
        try:
            await self._finalize_result(
                result=result,
                request=persisted_request,
                token=token,
                run_id=run_id,
                started_at=run_started_at,
                completed_at=run_completed_at,
            )
        except Exception as exc:
            raise PersistenceFinalizationError(
                "The idempotent application result could not be finalized."
            ) from exc
        return IdempotentApplicationResult(
            outcome=IdempotentResultOutcome.EXECUTED,
            request_id=context.request_id,
            application_result=result,
            claim_version=token.claim_version,
        )

    async def _claim_and_accept(
        self,
        *,
        request: UserRequest,
        context: RequestContext,
        operation: str,
        request_record,
        claim_request: IdempotencyClaimRequest,
        accepted_at: datetime,
    ) -> tuple[IdempotencyClaimToken, PersistedAgentRequestRef] | IdempotentApplicationResult:
        async with self._uow_factory() as uow:
            decision = await uow.idempotency_repository.claim(claim_request)
            if decision.outcome in (ClaimOutcome.ACQUIRED, ClaimOutcome.RECLAIMED):
                assert decision.token is not None
                if decision.outcome is ClaimOutcome.RECLAIMED:
                    await self._finalize_reclaimed_owner(
                        uow=uow,
                        context=context,
                        decision=decision,
                        event_time=accepted_at,
                    )
                request_ref = await uow.execution_repository.create_request(
                    request_record
                )
                await uow.execution_repository.create_audit_event(
                    self._audit_mapper.request_accepted(
                        context=context,
                        event_id=self._event_id_factory(),
                        operation=operation,
                        created_at=accepted_at,
                        idempotency_outcome=decision.outcome.value,
                        claim_version=decision.token.claim_version,
                        reclaimed=decision.outcome is ClaimOutcome.RECLAIMED,
                        expired_reuse=decision.expired_reuse,
                    )
                )
                await uow.commit()
                return decision.token, request_ref

            if decision.outcome is ClaimOutcome.REPLAY:
                result = self._restore_replay(
                    request=request,
                    context=context,
                    decision=decision,
                    replayed_at=accepted_at,
                )
                replay_record = self._request_mapper.map(
                    context=context,
                    operation=operation,
                    user_text=request.text,
                    task_override=(
                        None
                        if request.task_override is None
                        else str(request.task_override)
                    ),
                    request_session_id=request.session_id,
                    created_at=accepted_at,
                    idempotency_key_hash=claim_request.scope.key_hash,
                    status="completed",
                    replayed_from_request_record_id=(
                        decision.original_request_record_id
                    ),
                    completed_at=accepted_at,
                )
                await uow.execution_repository.create_request(replay_record)
                await uow.execution_repository.create_audit_event(
                    self._audit_mapper.request_replayed(
                        context=context,
                        event_id=self._event_id_factory(),
                        operation=operation,
                        created_at=accepted_at,
                    )
                )
                await uow.commit()
                return IdempotentApplicationResult(
                    outcome=IdempotentResultOutcome.REPLAYED,
                    request_id=context.request_id,
                    original_request_id=decision.original_request_id,
                    application_result=result,
                )

            await uow.rollback()
            if decision.outcome is ClaimOutcome.IN_PROGRESS:
                return IdempotentApplicationResult(
                    outcome=IdempotentResultOutcome.IN_PROGRESS,
                    request_id=context.request_id,
                    claim_version=(
                        None if decision.token is None else decision.token.claim_version
                    ),
                )
            if decision.outcome is ClaimOutcome.CONFLICT:
                return IdempotentApplicationResult(
                    outcome=IdempotentResultOutcome.CONFLICT,
                    request_id=context.request_id,
                )
            return IdempotentApplicationResult(
                outcome=IdempotentResultOutcome.PREVIOUS_FAILURE,
                request_id=context.request_id,
                safe_failure_code=decision.previous_failure_code,
            )

    async def _finalize_reclaimed_owner(
        self,
        *,
        uow: IdempotentExecutionUnitOfWork,
        context: RequestContext,
        decision: IdempotencyClaimDecision,
        event_time: datetime,
    ) -> None:
        old_request = decision.reclaimed_request
        token = decision.token
        assert old_request is not None
        assert token is not None
        run_id = self._run_id_factory()
        run = self._run_mapper.lease_reclaimed(
            request=old_request,
            run_id=run_id,
            event_time=event_time,
        )
        await uow.execution_repository.create_run(run)
        await uow.execution_repository.finalize_request_failed(
            old_request,
            completed_at=event_time,
            failure_code=self._failure_code_mapper.require(
                "idempotency_lease_reclaimed"
            ),
        )
        await uow.execution_repository.create_audit_event(
            self._audit_mapper.lease_reclaimed(
                context=context,
                old_request=old_request,
                event_id=self._event_id_factory(),
                run_id=run_id,
                claim_version=token.claim_version,
                created_at=event_time,
            )
        )

    async def _finalize_result(
        self,
        *,
        result: ApplicationResult,
        request: PersistedAgentRequestRef,
        token: IdempotencyClaimToken,
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
            await uow.idempotency_repository.assert_current_owner(token)
            locked = await uow.execution_repository.get_request_for_update(
                request.request_id
            )
            replay_snapshot = self._replay_mapper.map(result)
            run_record_id = await uow.execution_repository.create_run(run)
            await uow.execution_repository.finalize_request_completed(
                locked, completed_at=completed_at
            )
            await uow.execution_repository.create_audit_event(audit)
            await uow.idempotency_repository.complete_fenced(
                token,
                completed_run_record_id=run_record_id,
                response_snapshot=replay_snapshot,
                response_snapshot_version=1,
                replay_ttl_seconds=self._policy.replay_ttl_seconds,
            )
            await uow.commit()

    async def _finalize_failed(
        self,
        *,
        context: RequestContext,
        request: PersistedAgentRequestRef,
        token: IdempotencyClaimToken,
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
            await uow.idempotency_repository.assert_current_owner(token)
            locked = await uow.execution_repository.get_request_for_update(
                request.request_id
            )
            await uow.execution_repository.create_run(run)
            await uow.execution_repository.finalize_request_failed(
                locked,
                completed_at=completed_at,
                failure_code=failure_code,
            )
            await uow.execution_repository.create_audit_event(audit)
            await uow.idempotency_repository.fail_fenced(
                token,
                failure_ttl_seconds=self._policy.failure_ttl_seconds,
            )
            await uow.commit()

    def _restore_replay(
        self,
        *,
        request: UserRequest,
        context: RequestContext,
        decision: IdempotencyClaimDecision,
        replayed_at: datetime,
    ) -> ApplicationResult:
        if (
            decision.replay_snapshot is None
            or decision.replay_snapshot_version is None
        ):
            raise RequestInitializationError(
                "The replay decision has no approved snapshot."
            )
        return self._replay_mapper.restore(
            dict(decision.replay_snapshot),
            snapshot_version=decision.replay_snapshot_version,
            context=context,
            user_text=request.text,
            replayed_at=replayed_at,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Clock must return a timezone-aware UTC datetime")
        if value.utcoffset() != timedelta(0):
            raise ValueError("Clock must return UTC")
        return value.astimezone(timezone.utc)
