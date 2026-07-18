"""Transactional in-memory execution persistence for component tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from copy import deepcopy
from types import TracebackType

from conversation_agent.database.errors import (
    DuplicateRequestError,
    InvalidRequestTransitionError,
    PersistenceConflictError,
    PersistenceWriteError,
    RequestNotFoundError,
    FingerprintVersionError,
    IdempotencyOwnershipLostError,
    InvalidIdempotencyStateError,
)
from conversation_agent.database.idempotency import IdempotencyStateValidator
from conversation_agent.database.records import (
    ClaimOutcome,
    IdempotencyClaimDecision,
    IdempotencyClaimRequest,
    IdempotencyClaimToken,
    IdempotencyScope,
    IdempotencyStatus,
    NewAgentRequest,
    NewAgentRun,
    NewAuditEvent,
    PersistedAgentRequestRef,
    StoredIdempotencyRecord,
)


@dataclass
class FakeExecutionState:
    requests: dict[str, dict[str, object]] = field(default_factory=dict)
    runs: dict[str, dict[str, object]] = field(default_factory=dict)
    audits: list[dict[str, object]] = field(default_factory=list)
    next_request_id: int = 1
    next_run_id: int = 1
    next_audit_id: int = 1
    idempotency: dict[tuple[str, ...], dict[str, object]] = field(
        default_factory=dict
    )
    next_idempotency_id: int = 1


def _clone_state(state: FakeExecutionState) -> FakeExecutionState:
    return FakeExecutionState(
        requests={key: dict(value) for key, value in state.requests.items()},
        runs={key: dict(value) for key, value in state.runs.items()},
        audits=[dict(value) for value in state.audits],
        next_request_id=state.next_request_id,
        next_run_id=state.next_run_id,
        next_audit_id=state.next_audit_id,
        idempotency=deepcopy(state.idempotency),
        next_idempotency_id=state.next_idempotency_id,
    )


class FakeExecutionRepository:
    def __init__(
        self,
        state: FakeExecutionState,
        *,
        operation_log: list[str],
        fail_operations: set[str],
    ) -> None:
        self._state = state
        self._operation_log = operation_log
        self._fail_operations = fail_operations

    def _record(self, operation: str) -> None:
        self._operation_log.append(operation)
        if operation in self._fail_operations:
            raise PersistenceWriteError("Injected fake persistence failure.")

    async def create_request(
        self, record: NewAgentRequest
    ) -> PersistedAgentRequestRef:
        self._record("create_request")
        if record.request_id in self._state.requests:
            raise DuplicateRequestError("The request identifier already exists.")
        database_id = self._state.next_request_id
        self._state.next_request_id += 1
        self._state.requests[record.request_id] = {
            "database_id": database_id,
            "record": record,
            "status": record.status,
            "completed_at": record.completed_at,
            "failure_code": record.failure_code,
        }
        return PersistedAgentRequestRef(
            database_id=database_id,
            request_id=record.request_id,
            status=record.status,
            trace_id=record.trace_id,
            session_id=record.session_id,
        )

    async def get_request_for_update(
        self, request_id: str
    ) -> PersistedAgentRequestRef:
        self._record("get_request_for_update")
        item = self._state.requests.get(request_id)
        if item is None:
            raise RequestNotFoundError("The request record does not exist.")
        status = str(item["status"])
        if status != "in_progress":
            raise InvalidRequestTransitionError(
                "The request is not available for finalization."
            )
        return PersistedAgentRequestRef(
            database_id=int(item["database_id"]),
            request_id=request_id,
            status=status,
            trace_id=str(item["record"].trace_id),
            session_id=item["record"].session_id,
        )

    async def create_run(self, record: NewAgentRun) -> int:
        self._record("create_run")
        if record.run_id in self._state.runs or any(
            item["record"].original_request_record_id
            == record.original_request_record_id
            for item in self._state.runs.values()
        ):
            raise PersistenceConflictError(
                "The run record violates a persistence constraint."
            )
        database_id = self._state.next_run_id
        self._state.next_run_id += 1
        self._state.runs[record.run_id] = {
            "database_id": database_id,
            "record": record,
        }
        return database_id

    async def finalize_request_completed(
        self,
        request: PersistedAgentRequestRef,
        *,
        completed_at: datetime,
    ) -> None:
        self._record("finalize_request_completed")
        self._finalize(request, "completed", completed_at, None)

    async def finalize_request_failed(
        self,
        request: PersistedAgentRequestRef,
        *,
        completed_at: datetime,
        failure_code: str,
    ) -> None:
        self._record("finalize_request_failed")
        self._finalize(request, "failed", completed_at, failure_code)

    def _finalize(
        self,
        request: PersistedAgentRequestRef,
        status: str,
        completed_at: datetime,
        failure_code: str | None,
    ) -> None:
        item = self._state.requests.get(request.request_id)
        if item is None or item["status"] != "in_progress":
            raise InvalidRequestTransitionError(
                "The request state changed before finalization."
            )
        item["status"] = status
        item["completed_at"] = completed_at
        item["failure_code"] = failure_code

    async def create_audit_event(self, record: NewAuditEvent) -> int:
        self._record("create_audit_event")
        if any(item["record"].event_id == record.event_id for item in self._state.audits):
            raise PersistenceConflictError(
                "The audit event violates a persistence constraint."
            )
        database_id = self._state.next_audit_id
        self._state.next_audit_id += 1
        self._state.audits.append({"database_id": database_id, "record": record})
        return database_id


class FakeExecutionUnitOfWorkFactory:
    def __init__(self, state: FakeExecutionState | None = None) -> None:
        self.state = state or FakeExecutionState()
        self.active_uow_count = 0
        self.created_uow_count = 0
        self.committed_uow_count = 0
        self.rolled_back_uow_count = 0
        self.commit_attempt_count = 0
        self.fail_commit_attempts: set[int] = set()
        self.ambiguous_commit_attempts: set[int] = set()
        self.ambiguous_commit_count = 0
        self.fail_operations: set[str] = set()
        self.operation_log: list[str] = []

    def __call__(self) -> "FakeExecutionUnitOfWork":
        self.created_uow_count += 1
        return FakeExecutionUnitOfWork(self)


class FakeExecutionUnitOfWork:
    def __init__(self, factory: FakeExecutionUnitOfWorkFactory) -> None:
        self._factory = factory
        self._staged: FakeExecutionState | None = None
        self._repository: FakeExecutionRepository | None = None
        self._entered = False
        self._finished = False

    @property
    def execution_repository(self) -> FakeExecutionRepository:
        if self._repository is None or not self._entered or self._finished:
            raise RuntimeError("FakeExecutionUnitOfWork is not active")
        return self._repository

    async def __aenter__(self) -> "FakeExecutionUnitOfWork":
        if self._entered:
            raise RuntimeError("FakeExecutionUnitOfWork cannot be entered twice")
        self._entered = True
        self._staged = _clone_state(self._factory.state)
        self._repository = FakeExecutionRepository(
            self._staged,
            operation_log=self._factory.operation_log,
            fail_operations=self._factory.fail_operations,
        )
        self._factory.active_uow_count += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_type, traceback
        if not self._finished:
            self._factory.rolled_back_uow_count += 1
            self._finished = True
        self._factory.active_uow_count -= 1
        return False

    async def commit(self) -> None:
        if not self._entered or self._finished or self._staged is None:
            raise RuntimeError("FakeExecutionUnitOfWork is not active")
        self._factory.commit_attempt_count += 1
        if self._factory.commit_attempt_count in self._factory.fail_commit_attempts:
            self._factory.rolled_back_uow_count += 1
            self._finished = True
            raise PersistenceWriteError("Injected fake commit failure.")
        if self._factory.commit_attempt_count in self._factory.ambiguous_commit_attempts:
            # Model a server-side COMMIT followed by a lost acknowledgement.
            self._factory.state = _clone_state(self._staged)
            self._factory.ambiguous_commit_count += 1
            self._finished = True
            raise PersistenceWriteError("Injected ambiguous commit result.")
        self._factory.state = _clone_state(self._staged)
        self._factory.committed_uow_count += 1
        self._finished = True

    async def rollback(self) -> None:
        if not self._entered or self._finished:
            raise RuntimeError("FakeExecutionUnitOfWork is not active")
        self._factory.rolled_back_uow_count += 1
        self._finished = True


class FakeIdempotencyRepository:
    def __init__(self, state: FakeExecutionState, *, database_clock) -> None:
        self._state = state
        self._database_clock = database_clock
        self._validator = IdempotencyStateValidator()

    async def claim(
        self, request: IdempotencyClaimRequest
    ) -> IdempotencyClaimDecision:
        from datetime import timedelta

        now = self._database_clock()
        lease = now + timedelta(seconds=request.lease_duration_seconds)
        key = self._key(request.scope)
        item = self._state.idempotency.get(key)
        if item is None:
            database_id = self._state.next_idempotency_id
            self._state.next_idempotency_id += 1
            item = self._new_item(database_id, request, now, lease)
            self._state.idempotency[key] = item
            return IdempotencyClaimDecision(
                outcome=ClaimOutcome.ACQUIRED,
                token=self._token(item, request.scope),
            )

        stored = self._stored(item, request.scope)
        status = self._validator.validate(stored)
        terminal_expired = (
            status in (IdempotencyStatus.COMPLETED, IdempotencyStatus.FAILED)
            and stored.expires_at is not None
            and stored.expires_at <= now
        )
        if terminal_expired:
            self._replace(item, request, now, lease)
            return IdempotencyClaimDecision(
                outcome=ClaimOutcome.ACQUIRED,
                token=self._token(item, request.scope),
                expired_reuse=True,
            )
        if stored.fingerprint_version != request.fingerprint_version:
            raise FingerprintVersionError(
                "The persisted fingerprint version is not compatible."
            )
        if stored.request_fingerprint != request.request_fingerprint:
            return IdempotencyClaimDecision(outcome=ClaimOutcome.CONFLICT)
        if status is IdempotencyStatus.ACTIVE:
            assert stored.lease_expires_at is not None
            if stored.lease_expires_at > now:
                return IdempotencyClaimDecision(
                    outcome=ClaimOutcome.IN_PROGRESS,
                    token=self._token(item, request.scope),
                )
            prior = self._request_ref(str(item["owner_request_id"]))
            if prior.status != "in_progress":
                raise InvalidIdempotencyStateError(
                    "The active claim owner request is not in progress."
                )
            self._replace(item, request, now, lease)
            return IdempotencyClaimDecision(
                outcome=ClaimOutcome.RECLAIMED,
                token=self._token(item, request.scope),
                reclaimed_request=prior,
            )
        if status is IdempotencyStatus.COMPLETED:
            run_id = int(item["completed_run_record_id"])
            run = next(
                (
                    value
                    for value in self._state.runs.values()
                    if value["database_id"] == run_id
                ),
                None,
            )
            if run is None:
                raise InvalidIdempotencyStateError(
                    "The completed claim has no canonical replay source."
                )
            original_db_id = run["record"].original_request_record_id
            original = next(
                (
                    (request_id, value)
                    for request_id, value in self._state.requests.items()
                    if value["database_id"] == original_db_id
                ),
                None,
            )
            if original is None:
                raise InvalidIdempotencyStateError(
                    "The completed claim has no original request."
                )
            return IdempotencyClaimDecision(
                outcome=ClaimOutcome.REPLAY,
                original_request_record_id=original_db_id,
                original_request_id=original[0],
                replay_snapshot=deepcopy(item["response_snapshot"]),
                replay_snapshot_version=int(item["response_snapshot_version"]),
            )
        prior = self._state.requests.get(str(item["owner_request_id"]))
        if prior is None or prior["status"] != "failed" or not prior["failure_code"]:
            raise InvalidIdempotencyStateError(
                "The failed claim has no safe failure state."
            )
        return IdempotencyClaimDecision(
            outcome=ClaimOutcome.PREVIOUS_FAILURE,
            previous_failure_code=str(prior["failure_code"]),
        )

    async def assert_current_owner(self, token: IdempotencyClaimToken) -> None:
        item = self._state.idempotency.get(self._key(token.scope))
        if (
            item is None
            or item["status"] != IdempotencyStatus.ACTIVE.value
            or item["owner_request_id"] != token.owner_request_id
            or item["claim_version"] != token.claim_version
        ):
            raise IdempotencyOwnershipLostError(
                "The idempotency claim is no longer owned by this execution."
            )

    async def complete_fenced(
        self,
        token: IdempotencyClaimToken,
        *,
        completed_run_record_id: int,
        response_snapshot: dict[str, object],
        response_snapshot_version: int,
        replay_ttl_seconds: int,
    ) -> None:
        from datetime import timedelta

        await self.assert_current_owner(token)
        item = self._state.idempotency[self._key(token.scope)]
        now = self._database_clock()
        item.update(
            status=IdempotencyStatus.COMPLETED.value,
            completed_run_record_id=completed_run_record_id,
            response_snapshot=deepcopy(response_snapshot),
            response_snapshot_version=response_snapshot_version,
            updated_at=now,
            expires_at=now + timedelta(seconds=replay_ttl_seconds),
        )

    async def fail_fenced(
        self,
        token: IdempotencyClaimToken,
        *,
        failure_ttl_seconds: int,
    ) -> None:
        from datetime import timedelta

        await self.assert_current_owner(token)
        item = self._state.idempotency[self._key(token.scope)]
        now = self._database_clock()
        item.update(
            status=IdempotencyStatus.FAILED.value,
            completed_run_record_id=None,
            response_snapshot=None,
            response_snapshot_version=None,
            updated_at=now,
            expires_at=now + timedelta(seconds=failure_ttl_seconds),
        )

    def _replace(self, item, request, now, lease) -> None:
        item.update(
            request_fingerprint=request.request_fingerprint,
            fingerprint_version=request.fingerprint_version,
            status=IdempotencyStatus.ACTIVE.value,
            claim_version=int(item["claim_version"]) + 1,
            owner_request_id=request.owner_request_id,
            claimed_at=now,
            lease_expires_at=lease,
            completed_run_record_id=None,
            response_snapshot=None,
            response_snapshot_version=None,
            updated_at=now,
            expires_at=lease,
        )

    def _request_ref(self, request_id: str) -> PersistedAgentRequestRef:
        item = self._state.requests.get(request_id)
        if item is None:
            raise InvalidIdempotencyStateError(
                "The idempotency owner request does not exist."
            )
        record = item["record"]
        return PersistedAgentRequestRef(
            database_id=int(item["database_id"]),
            request_id=request_id,
            status=str(item["status"]),
            trace_id=record.trace_id,
            session_id=record.session_id,
        )

    @staticmethod
    def _key(scope: IdempotencyScope) -> tuple[str, ...]:
        return (
            scope.tenant_id,
            scope.organization_id,
            scope.principal_user_id,
            scope.operation,
            scope.key_hash,
        )

    @staticmethod
    def _new_item(database_id, request, now, lease):
        return {
            "database_id": database_id,
            "scope": request.scope,
            "request_fingerprint": request.request_fingerprint,
            "fingerprint_version": request.fingerprint_version,
            "status": IdempotencyStatus.ACTIVE.value,
            "claim_version": 1,
            "owner_request_id": request.owner_request_id,
            "claimed_at": now,
            "lease_expires_at": lease,
            "completed_run_record_id": None,
            "response_snapshot": None,
            "response_snapshot_version": None,
            "created_at": now,
            "updated_at": now,
            "expires_at": lease,
        }

    @staticmethod
    def _stored(item, scope) -> StoredIdempotencyRecord:
        return StoredIdempotencyRecord(
            database_id=int(item["database_id"]),
            scope=scope,
            request_fingerprint=str(item["request_fingerprint"]),
            fingerprint_version=int(item["fingerprint_version"]),
            status=str(item["status"]),
            claim_version=item["claim_version"],
            owner_request_id=item["owner_request_id"],
            claimed_at=item["claimed_at"],
            lease_expires_at=item["lease_expires_at"],
            completed_run_record_id=item["completed_run_record_id"],
            response_snapshot=item["response_snapshot"],
            response_snapshot_version=item["response_snapshot_version"],
            expires_at=item["expires_at"],
        )

    @staticmethod
    def _token(item, scope) -> IdempotencyClaimToken:
        return IdempotencyClaimToken(
            idempotency_record_id=int(item["database_id"]),
            scope=scope,
            owner_request_id=str(item["owner_request_id"]),
            claim_version=int(item["claim_version"]),
            claimed_at=item["claimed_at"],
            lease_expires_at=item["lease_expires_at"],
        )


class FakeIdempotentUnitOfWorkFactory(FakeExecutionUnitOfWorkFactory):
    def __init__(self, state=None, *, database_clock) -> None:
        super().__init__(state)
        self.database_clock = database_clock

    def __call__(self) -> "FakeIdempotentExecutionUnitOfWork":
        self.created_uow_count += 1
        return FakeIdempotentExecutionUnitOfWork(self)


class FakeIdempotentExecutionUnitOfWork(FakeExecutionUnitOfWork):
    def __init__(self, factory: FakeIdempotentUnitOfWorkFactory) -> None:
        super().__init__(factory)
        self._idempotency_repository = None

    @property
    def idempotency_repository(self) -> FakeIdempotencyRepository:
        if self._idempotency_repository is None or not self._entered or self._finished:
            raise RuntimeError("FakeIdempotentExecutionUnitOfWork is not active")
        return self._idempotency_repository

    async def __aenter__(self) -> "FakeIdempotentExecutionUnitOfWork":
        await super().__aenter__()
        assert self._staged is not None
        self._idempotency_repository = FakeIdempotencyRepository(
            self._staged,
            database_clock=self._factory.database_clock,
        )
        return self
