"""PostgreSQL-backed atomic claim, replay, and fencing operations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any

from sqlalchemy import func, null, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from conversation_agent.database.errors import (
    FingerprintVersionError,
    IdempotencyOwnershipLostError,
    InvalidIdempotencyStateError,
    PersistenceWriteError,
)
from conversation_agent.database.idempotency import IdempotencyStateValidator
from conversation_agent.database.models import AgentRequest, AgentRun, IdempotencyRecord
from conversation_agent.database.records import (
    ClaimOutcome,
    IdempotencyClaimDecision,
    IdempotencyClaimRequest,
    IdempotencyClaimToken,
    IdempotencyScope,
    IdempotencyStatus,
    JsonObject,
    JsonValue,
    PersistedAgentRequestRef,
    StoredIdempotencyRecord,
)


def _freeze_json(value: Any) -> JsonValue:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise InvalidIdempotencyStateError(
        "The persisted replay snapshot contains an unsupported value."
    )


class SQLAlchemyIdempotencyRepository:
    """Stage idempotency changes in a caller-owned AsyncSession."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        state_validator: IdempotencyStateValidator | None = None,
    ) -> None:
        self._session = session
        self._validator = state_validator or IdempotencyStateValidator()

    async def claim(
        self, request: IdempotencyClaimRequest
    ) -> IdempotencyClaimDecision:
        db_now = await self._database_now()
        lease_expires_at = db_now + timedelta(
            seconds=request.lease_duration_seconds
        )
        values = {
            "tenant_id": request.scope.tenant_id,
            "organization_id": request.scope.organization_id,
            "principal_user_id": request.scope.principal_user_id,
            "operation": request.scope.operation,
            "idempotency_key_hash": request.scope.key_hash,
            "request_fingerprint": request.request_fingerprint,
            "fingerprint_version": request.fingerprint_version,
            "status": IdempotencyStatus.ACTIVE.value,
            "claim_version": 1,
            "owner_request_id": request.owner_request_id,
            "claimed_at": db_now,
            "lease_expires_at": lease_expires_at,
            "completed_run_record_id": None,
            "response_snapshot": null(),
            "response_snapshot_schema_version": None,
            "created_at": db_now,
            "updated_at": db_now,
            "expires_at": lease_expires_at,
        }
        try:
            inserted = await self._session.execute(
                pg_insert(IdempotencyRecord)
                .values(**values)
                .on_conflict_do_nothing(
                    constraint="uq_idempotency_records_scope"
                )
                .returning(IdempotencyRecord.id)
            )
        except SQLAlchemyError as exc:
            raise PersistenceWriteError(
                "The idempotency claim could not be created."
            ) from exc
        inserted_id = inserted.scalar_one_or_none()
        if inserted_id is not None:
            return IdempotencyClaimDecision(
                outcome=ClaimOutcome.ACQUIRED,
                token=self._token(
                    database_id=inserted_id,
                    scope=request.scope,
                    owner_request_id=request.owner_request_id,
                    claim_version=1,
                    claimed_at=db_now,
                    lease_expires_at=lease_expires_at,
                ),
            )

        model = await self._load_scope_for_update(request.scope)
        stored = self._stored(model)
        status = self._validator.validate(stored)
        terminal_expired = (
            status in (IdempotencyStatus.COMPLETED, IdempotencyStatus.FAILED)
            and stored.expires_at is not None
            and stored.expires_at <= db_now
        )
        if terminal_expired:
            return await self._reacquire(
                model,
                request=request,
                db_now=db_now,
                lease_expires_at=lease_expires_at,
            )
        if stored.fingerprint_version != request.fingerprint_version:
            raise FingerprintVersionError(
                "The persisted fingerprint version is not compatible."
            )
        if stored.request_fingerprint != request.request_fingerprint:
            return IdempotencyClaimDecision(outcome=ClaimOutcome.CONFLICT)

        if status is IdempotencyStatus.ACTIVE:
            assert stored.lease_expires_at is not None
            if stored.lease_expires_at > db_now:
                return IdempotencyClaimDecision(
                    outcome=ClaimOutcome.IN_PROGRESS,
                    token=self._token_from(stored),
                )
            return await self._reclaim(
                model,
                request=request,
                db_now=db_now,
                lease_expires_at=lease_expires_at,
            )
        if status is IdempotencyStatus.COMPLETED:
            return await self._replay_decision(stored)
        return await self._previous_failure_decision(stored)

    async def assert_current_owner(self, token: IdempotencyClaimToken) -> None:
        model = await self._load_id_for_update(token.idempotency_record_id)
        stored = self._stored(model)
        status = self._validator.validate(stored)
        if (
            status is not IdempotencyStatus.ACTIVE
            or stored.owner_request_id != token.owner_request_id
            or stored.claim_version != token.claim_version
        ):
            raise IdempotencyOwnershipLostError(
                "The idempotency claim is no longer owned by this execution."
            )

    async def complete_fenced(
        self,
        token: IdempotencyClaimToken,
        *,
        completed_run_record_id: int,
        response_snapshot: dict[str, Any],
        response_snapshot_version: int,
        replay_ttl_seconds: int,
    ) -> None:
        db_now = await self._database_now()
        try:
            result = await self._session.execute(
                update(IdempotencyRecord)
                .where(
                    IdempotencyRecord.id == token.idempotency_record_id,
                    IdempotencyRecord.status == IdempotencyStatus.ACTIVE.value,
                    IdempotencyRecord.owner_request_id == token.owner_request_id,
                    IdempotencyRecord.claim_version == token.claim_version,
                )
                .values(
                    status=IdempotencyStatus.COMPLETED.value,
                    completed_run_record_id=completed_run_record_id,
                    response_snapshot=response_snapshot,
                    response_snapshot_schema_version=response_snapshot_version,
                    updated_at=db_now,
                    expires_at=db_now + timedelta(seconds=replay_ttl_seconds),
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceWriteError(
                "The idempotency completion could not be persisted."
            ) from exc
        if result.rowcount != 1:
            raise IdempotencyOwnershipLostError(
                "The idempotency completion lost claim ownership."
            )

    async def fail_fenced(
        self,
        token: IdempotencyClaimToken,
        *,
        failure_ttl_seconds: int,
    ) -> None:
        db_now = await self._database_now()
        try:
            result = await self._session.execute(
                update(IdempotencyRecord)
                .where(
                    IdempotencyRecord.id == token.idempotency_record_id,
                    IdempotencyRecord.status == IdempotencyStatus.ACTIVE.value,
                    IdempotencyRecord.owner_request_id == token.owner_request_id,
                    IdempotencyRecord.claim_version == token.claim_version,
                )
                .values(
                    status=IdempotencyStatus.FAILED.value,
                    completed_run_record_id=null(),
                    response_snapshot=null(),
                    response_snapshot_schema_version=None,
                    updated_at=db_now,
                    expires_at=db_now + timedelta(seconds=failure_ttl_seconds),
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceWriteError(
                "The idempotency failure could not be persisted."
            ) from exc
        if result.rowcount != 1:
            raise IdempotencyOwnershipLostError(
                "The idempotency failure lost claim ownership."
            )

    async def _reclaim(
        self,
        model: IdempotencyRecord,
        *,
        request: IdempotencyClaimRequest,
        db_now: datetime,
        lease_expires_at: datetime,
    ) -> IdempotencyClaimDecision:
        prior = await self._load_owner_request_for_update(model.owner_request_id)
        if prior.status != "in_progress":
            raise InvalidIdempotencyStateError(
                "The active claim owner request is not in progress."
            )
        claim_version = model.claim_version + 1
        await self._replace_active_owner(
            model,
            request=request,
            claim_version=claim_version,
            db_now=db_now,
            lease_expires_at=lease_expires_at,
        )
        return IdempotencyClaimDecision(
            outcome=ClaimOutcome.RECLAIMED,
            token=self._token(
                database_id=model.id,
                scope=request.scope,
                owner_request_id=request.owner_request_id,
                claim_version=claim_version,
                claimed_at=db_now,
                lease_expires_at=lease_expires_at,
            ),
            reclaimed_request=prior,
        )

    async def _reacquire(
        self,
        model: IdempotencyRecord,
        *,
        request: IdempotencyClaimRequest,
        db_now: datetime,
        lease_expires_at: datetime,
    ) -> IdempotencyClaimDecision:
        claim_version = model.claim_version + 1
        await self._replace_active_owner(
            model,
            request=request,
            claim_version=claim_version,
            db_now=db_now,
            lease_expires_at=lease_expires_at,
        )
        return IdempotencyClaimDecision(
            outcome=ClaimOutcome.ACQUIRED,
            token=self._token(
                database_id=model.id,
                scope=request.scope,
                owner_request_id=request.owner_request_id,
                claim_version=claim_version,
                claimed_at=db_now,
                lease_expires_at=lease_expires_at,
            ),
            expired_reuse=True,
        )

    async def _replace_active_owner(
        self,
        model: IdempotencyRecord,
        *,
        request: IdempotencyClaimRequest,
        claim_version: int,
        db_now: datetime,
        lease_expires_at: datetime,
    ) -> None:
        model.status = IdempotencyStatus.ACTIVE.value
        model.request_fingerprint = request.request_fingerprint
        model.fingerprint_version = request.fingerprint_version
        model.claim_version = claim_version
        model.owner_request_id = request.owner_request_id
        model.claimed_at = db_now
        model.lease_expires_at = lease_expires_at
        model.completed_run_record_id = None
        model.response_snapshot = null()
        model.response_snapshot_schema_version = None
        model.updated_at = db_now
        model.expires_at = lease_expires_at
        try:
            await self._session.flush()
        except SQLAlchemyError as exc:
            raise PersistenceWriteError(
                "The idempotency claim could not be replaced."
            ) from exc

    async def _replay_decision(
        self, stored: StoredIdempotencyRecord
    ) -> IdempotencyClaimDecision:
        assert stored.completed_run_record_id is not None
        try:
            row = (
                await self._session.execute(
                    select(AgentRequest.id, AgentRequest.request_id)
                    .join(AgentRun, AgentRun.original_request_id == AgentRequest.id)
                    .where(AgentRun.id == stored.completed_run_record_id)
                )
            ).one_or_none()
        except SQLAlchemyError as exc:
            raise PersistenceWriteError(
                "The canonical replay source could not be loaded."
            ) from exc
        if row is None:
            raise InvalidIdempotencyStateError(
                "The completed claim has no canonical replay source."
            )
        assert stored.response_snapshot is not None
        return IdempotencyClaimDecision(
            outcome=ClaimOutcome.REPLAY,
            original_request_record_id=row.id,
            original_request_id=row.request_id,
            replay_snapshot=stored.response_snapshot,
            replay_snapshot_version=stored.response_snapshot_version,
        )

    async def _previous_failure_decision(
        self, stored: StoredIdempotencyRecord
    ) -> IdempotencyClaimDecision:
        prior = await self._load_owner_request_for_update(stored.owner_request_id or "")
        if prior.status != "failed":
            raise InvalidIdempotencyStateError(
                "The failed claim owner request is not failed."
            )
        model = await self._session.get(AgentRequest, prior.database_id)
        if model is None or not model.failure_code:
            raise InvalidIdempotencyStateError(
                "The failed claim has no safe failure code."
            )
        return IdempotencyClaimDecision(
            outcome=ClaimOutcome.PREVIOUS_FAILURE,
            previous_failure_code=model.failure_code,
        )

    async def _load_scope_for_update(
        self, scope: IdempotencyScope
    ) -> IdempotencyRecord:
        try:
            model = (
                await self._session.execute(
                    select(IdempotencyRecord)
                    .where(*self._scope_conditions(scope))
                    .with_for_update()
                )
            ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise PersistenceWriteError(
                "The idempotency claim could not be locked."
            ) from exc
        if model is None:
            raise InvalidIdempotencyStateError(
                "The conflicting idempotency record does not exist."
            )
        return model

    async def _load_id_for_update(self, database_id: int) -> IdempotencyRecord:
        try:
            model = (
                await self._session.execute(
                    select(IdempotencyRecord)
                    .where(IdempotencyRecord.id == database_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise PersistenceWriteError(
                "The idempotency owner could not be locked."
            ) from exc
        if model is None:
            raise IdempotencyOwnershipLostError(
                "The idempotency claim no longer exists."
            )
        return model

    async def _load_owner_request_for_update(
        self, request_id: str
    ) -> PersistedAgentRequestRef:
        try:
            model = (
                await self._session.execute(
                    select(AgentRequest)
                    .where(AgentRequest.request_id == request_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise PersistenceWriteError(
                "The idempotency owner request could not be locked."
            ) from exc
        if model is None:
            raise InvalidIdempotencyStateError(
                "The idempotency owner request does not exist."
            )
        return PersistedAgentRequestRef(
            database_id=model.id,
            request_id=model.request_id,
            status=model.status,
            trace_id=model.trace_id,
            session_id=model.session_id,
        )

    async def _database_now(self) -> datetime:
        try:
            value = (
                await self._session.execute(select(func.clock_timestamp()))
            ).scalar_one()
        except SQLAlchemyError as exc:
            raise PersistenceWriteError(
                "The database time could not be read."
            ) from exc
        return value.astimezone(timezone.utc)

    @staticmethod
    def _scope_conditions(scope: IdempotencyScope) -> tuple[Any, ...]:
        return (
            IdempotencyRecord.tenant_id == scope.tenant_id,
            IdempotencyRecord.organization_id == scope.organization_id,
            IdempotencyRecord.principal_user_id == scope.principal_user_id,
            IdempotencyRecord.operation == scope.operation,
            IdempotencyRecord.idempotency_key_hash == scope.key_hash,
        )

    @staticmethod
    def _stored(model: IdempotencyRecord) -> StoredIdempotencyRecord:
        snapshot = None
        if model.response_snapshot is not None:
            frozen = _freeze_json(model.response_snapshot)
            if not isinstance(frozen, Mapping):
                raise InvalidIdempotencyStateError(
                    "The replay snapshot is not a JSON object."
                )
            snapshot = frozen
        return StoredIdempotencyRecord(
            database_id=model.id,
            scope=IdempotencyScope(
                tenant_id=model.tenant_id,
                organization_id=model.organization_id,
                principal_user_id=model.principal_user_id,
                operation=model.operation,
                key_hash=model.idempotency_key_hash,
            ),
            request_fingerprint=model.request_fingerprint,
            fingerprint_version=model.fingerprint_version,
            status=model.status,
            claim_version=model.claim_version,
            owner_request_id=model.owner_request_id,
            claimed_at=model.claimed_at,
            lease_expires_at=model.lease_expires_at,
            completed_run_record_id=model.completed_run_record_id,
            response_snapshot=snapshot,
            response_snapshot_version=model.response_snapshot_schema_version,
            expires_at=model.expires_at,
        )

    @staticmethod
    def _token(
        *,
        database_id: int,
        scope: IdempotencyScope,
        owner_request_id: str,
        claim_version: int,
        claimed_at: datetime,
        lease_expires_at: datetime,
    ) -> IdempotencyClaimToken:
        return IdempotencyClaimToken(
            idempotency_record_id=database_id,
            scope=scope,
            owner_request_id=owner_request_id,
            claim_version=claim_version,
            claimed_at=claimed_at,
            lease_expires_at=lease_expires_at,
        )

    @classmethod
    def _token_from(
        cls, stored: StoredIdempotencyRecord
    ) -> IdempotencyClaimToken:
        assert stored.owner_request_id is not None
        assert stored.claim_version is not None
        assert stored.claimed_at is not None
        assert stored.lease_expires_at is not None
        return cls._token(
            database_id=stored.database_id,
            scope=stored.scope,
            owner_request_id=stored.owner_request_id,
            claim_version=stored.claim_version,
            claimed_at=stored.claimed_at,
            lease_expires_at=stored.lease_expires_at,
        )
