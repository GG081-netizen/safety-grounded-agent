"""SQLAlchemy implementation of the narrow execution repository."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import null, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from conversation_agent.database.errors import (
    DuplicateRequestError,
    InvalidRequestTransitionError,
    PersistenceConflictError,
    PersistenceWriteError,
    RequestNotFoundError,
)
from conversation_agent.database.models import AgentRequest, AgentRun, AuditEvent
from conversation_agent.database.records import (
    JsonValue,
    NewAgentRequest,
    NewAgentRun,
    NewAuditEvent,
    PersistedAgentRequestRef,
    require_utc,
)


def _json_value(value: JsonValue) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _constraint_name(exc: IntegrityError) -> str | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = getattr(current, "constraint_name", None)
        if isinstance(name, str) and name:
            return name
        current = getattr(current, "__cause__", None) or getattr(
            current, "__context__", None
        )
    return None


class SQLAlchemyExecutionRepository:
    """Stage execution records in the caller-owned AsyncSession."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_request(
        self, record: NewAgentRequest
    ) -> PersistedAgentRequestRef:
        model = AgentRequest(
            request_id=record.request_id,
            trace_id=record.trace_id,
            session_id=record.session_id,
            operation=record.operation,
            principal_user_id=record.principal_user_id,
            tenant_id=record.tenant_id,
            organization_id=record.organization_id,
            status=record.status,
            user_text_hash=record.user_text_hash,
            user_text_length=record.user_text_length,
            idempotency_key_hash=record.idempotency_key_hash,
            request_fingerprint=record.request_fingerprint,
            fingerprint_version=record.fingerprint_version,
            authorization_snapshot=_json_value(record.authorization_snapshot),
            authorization_snapshot_schema_version=(
                record.authorization_snapshot_schema_version
            ),
            replayed_from_request_id=record.replayed_from_request_record_id,
            failure_code=record.failure_code,
            created_at=record.created_at,
            completed_at=record.completed_at,
        )
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            if _constraint_name(exc) == "uq_agent_requests_request_id":
                raise DuplicateRequestError(
                    "The request identifier already exists."
                ) from exc
            raise PersistenceConflictError(
                "The request record violates a persistence constraint."
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceWriteError(
                "The request record could not be persisted."
            ) from exc
        return PersistedAgentRequestRef(
            database_id=model.id,
            request_id=model.request_id,
            status=model.status,
            trace_id=model.trace_id,
            session_id=model.session_id,
        )

    async def get_request_for_update(
        self, request_id: str
    ) -> PersistedAgentRequestRef:
        try:
            result = await self._session.execute(
                select(AgentRequest)
                .where(AgentRequest.request_id == request_id)
                .with_for_update()
            )
        except SQLAlchemyError as exc:
            raise PersistenceWriteError(
                "The request record could not be locked."
            ) from exc
        model = result.scalar_one_or_none()
        if model is None:
            raise RequestNotFoundError("The request record does not exist.")
        if model.status != "in_progress":
            raise InvalidRequestTransitionError(
                "The request is not available for finalization."
            )
        return PersistedAgentRequestRef(
            database_id=model.id,
            request_id=model.request_id,
            status=model.status,
            trace_id=model.trace_id,
            session_id=model.session_id,
        )

    async def create_run(self, record: NewAgentRun) -> int:
        model = AgentRun(
            run_id=record.run_id,
            original_request_id=record.original_request_record_id,
            session_id=record.session_id,
            status=record.status,
            routed_task=record.routed_task,
            policy_outcome=record.policy_outcome,
            result_snapshot=(
                null()
                if record.result_snapshot is None
                else _json_value(record.result_snapshot)
            ),
            result_snapshot_schema_version=record.result_snapshot_schema_version,
            confidence=record.confidence,
            trace_snapshot=(
                null()
                if record.trace_snapshot is None
                else _json_value(record.trace_snapshot)
            ),
            trace_snapshot_schema_version=record.trace_snapshot_schema_version,
            rag_provider=record.rag_provider,
            started_at=record.started_at,
            completed_at=record.completed_at,
        )
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise PersistenceConflictError(
                "The run record violates a persistence constraint."
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceWriteError(
                "The run record could not be persisted."
            ) from exc
        return model.id

    async def finalize_request_completed(
        self,
        request: PersistedAgentRequestRef,
        *,
        completed_at: datetime,
    ) -> None:
        await self._finalize(
            request,
            status="completed",
            completed_at=completed_at,
            failure_code=None,
        )

    async def finalize_request_failed(
        self,
        request: PersistedAgentRequestRef,
        *,
        completed_at: datetime,
        failure_code: str,
    ) -> None:
        await self._finalize(
            request,
            status="failed",
            completed_at=completed_at,
            failure_code=failure_code,
        )

    async def _finalize(
        self,
        request: PersistedAgentRequestRef,
        *,
        status: str,
        completed_at: datetime,
        failure_code: str | None,
    ) -> None:
        require_utc(completed_at, "completed_at")
        try:
            result = await self._session.execute(
                update(AgentRequest)
                .where(
                    AgentRequest.id == request.database_id,
                    AgentRequest.request_id == request.request_id,
                    AgentRequest.status == "in_progress",
                )
                .values(
                    status=status,
                    completed_at=completed_at,
                    failure_code=failure_code,
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceWriteError(
                "The request state could not be finalized."
            ) from exc
        if result.rowcount != 1:
            raise InvalidRequestTransitionError(
                "The request state changed before finalization."
            )

    async def create_audit_event(self, record: NewAuditEvent) -> int:
        model = AuditEvent(
            event_id=record.event_id,
            request_id=record.request_id,
            trace_id=record.trace_id,
            tenant_id=record.tenant_id,
            organization_id=record.organization_id,
            event_type=record.event_type,
            principal_user_id=record.principal_user_id,
            outcome=record.outcome,
            details_json=(
                null()
                if record.details_json is None
                else _json_value(record.details_json)
            ),
            created_at=record.created_at,
        )
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise PersistenceConflictError(
                "The audit event violates a persistence constraint."
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceWriteError(
                "The audit event could not be persisted."
            ) from exc
        return model.id
