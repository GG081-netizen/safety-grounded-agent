"""Null / fake persistence implementations (M1.4-A).

These no-op / in-memory implementations satisfy the UnitOfWork Protocol
and DatabaseRepository interface without touching a network or database.

Use cases:
- NullUnitOfWork  / NullRepository  — demo / test modes with no DB
- FakeUnitOfWork  / FakeRepository  — unit tests that need observable state
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from conversation_agent.database.repository import (
    DatabaseRepository,
    IdempotencyClaimResult,
)
from conversation_agent.database.unit_of_work import UnitOfWork


# ═══════════════════════════════════════════════════════════════════════════════
# Null — always succeeds, no side-effects
# ═══════════════════════════════════════════════════════════════════════════════


class NullRepository(DatabaseRepository):
    """Repository that returns safe defaults and never fails.

    All claim / update operations report success.  All find operations
    return None.  Useful for demo mode where persistence is disabled.
    """

    async def claim_idempotency(
        self,
        session: Any,
        scope: str,
        idempotency_key: str,
        request_fingerprint: str,
        fingerprint_version: int,
        owner_request_id: str,
        lease_duration_seconds: int,
    ) -> IdempotencyClaimResult:
        return IdempotencyClaimResult(claimed=True)

    async def re_claim_idempotency(
        self,
        session: Any,
        scope: str,
        idempotency_key: str,
        new_fingerprint: str,
        new_owner_request_id: str,
        lease_duration_seconds: int,
    ) -> tuple[bool, int | None]:
        return (True, 1)

    async def complete_idempotency_fenced(
        self,
        session: Any,
        scope: str,
        idempotency_key: str,
        owner_request_id: str,
        claim_version: int,
        completed_run_id: str,
        response_snapshot: dict[str, Any],
    ) -> bool:
        return True

    async def fail_idempotency_fenced(
        self,
        session: Any,
        scope: str,
        idempotency_key: str,
        owner_request_id: str,
        claim_version: int,
    ) -> bool:
        return True

    async def find_idempotency_by_scope_key(
        self,
        session: Any,
        scope: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        return None

    async def insert_agent_request(
        self, session: Any, record: dict[str, Any]
    ) -> dict[str, Any]:
        return {**record, "id": 0}

    async def update_agent_request_status(
        self, session: Any, request_id: str, status: str, **fields: Any
    ) -> bool:
        return True

    async def insert_agent_run(
        self, session: Any, record: dict[str, Any]
    ) -> dict[str, Any]:
        return {**record, "id": 0}

    async def insert_audit_event(
        self, session: Any, record: dict[str, Any]
    ) -> dict[str, Any]:
        return {**record, "id": 0}


class NullUnitOfWork:
    """Unit of work that satisfies the Protocol without a database.

    Implements the UnitOfWork Protocol implicitly (duck-typed).  Every
    call is a no-op and session() yields a sentinel object — callers
    must not attempt real SQLAlchemy operations through it.
    """

    async def begin(self) -> Any:
        return _NULL_SESSION_SENTINEL

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


_NULL_SESSION_SENTINEL = object()


# ═══════════════════════════════════════════════════════════════════════════════
# Fake — in-memory, observable, suitable for unit tests
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class FakeRepositoryState:
    """Mutable state container for FakeRepository."""

    idempotency_records: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict
    )
    agent_requests: dict[str, dict[str, Any]] = field(default_factory=dict)
    agent_runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    claim_attempts: list[dict[str, Any]] = field(default_factory=list)


class FakeRepository(DatabaseRepository):
    """In-memory repository for unit tests.

    Stores records in FakeRepositoryState so tests can assert on
    what was persisted.  Idempotency claims are deterministic based
    on the state contents.
    """

    def __init__(self, state: FakeRepositoryState | None = None) -> None:
        self._state = state or FakeRepositoryState()

    @property
    def state(self) -> FakeRepositoryState:
        return self._state

    async def claim_idempotency(
        self,
        session: Any,
        scope: str,
        idempotency_key: str,
        request_fingerprint: str,
        fingerprint_version: int,
        owner_request_id: str,
        lease_duration_seconds: int,
    ) -> IdempotencyClaimResult:
        self._state.claim_attempts.append(
            {
                "scope": scope,
                "idempotency_key": idempotency_key,
                "request_fingerprint": request_fingerprint,
                "owner_request_id": owner_request_id,
            }
        )
        key = (scope, idempotency_key)
        if key in self._state.idempotency_records:
            return IdempotencyClaimResult(
                claimed=False,
                existing_record=self._state.idempotency_records[key],
            )
        record = {
            "scope": scope,
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "fingerprint_version": fingerprint_version,
            "status": "in_progress",
            "claim_version": 1,
            "owner_request_id": owner_request_id,
        }
        self._state.idempotency_records[key] = record
        return IdempotencyClaimResult(claimed=True)

    async def re_claim_idempotency(
        self,
        session: Any,
        scope: str,
        idempotency_key: str,
        new_fingerprint: str,
        new_owner_request_id: str,
        lease_duration_seconds: int,
    ) -> tuple[bool, int | None]:
        key = (scope, idempotency_key)
        record = self._state.idempotency_records.get(key)
        if record is None:
            return (False, None)
        new_version = record["claim_version"] + 1
        record["claim_version"] = new_version
        record["owner_request_id"] = new_owner_request_id
        record["request_fingerprint"] = new_fingerprint
        record["status"] = "in_progress"
        return (True, new_version)

    async def complete_idempotency_fenced(
        self,
        session: Any,
        scope: str,
        idempotency_key: str,
        owner_request_id: str,
        claim_version: int,
        completed_run_id: str,
        response_snapshot: dict[str, Any],
    ) -> bool:
        key = (scope, idempotency_key)
        record = self._state.idempotency_records.get(key)
        if (
            record is None
            or record["owner_request_id"] != owner_request_id
            or record["claim_version"] != claim_version
            or record["status"] != "in_progress"
        ):
            return False
        record["status"] = "completed"
        record["completed_run_id"] = completed_run_id
        record["response_snapshot"] = response_snapshot
        return True

    async def fail_idempotency_fenced(
        self,
        session: Any,
        scope: str,
        idempotency_key: str,
        owner_request_id: str,
        claim_version: int,
    ) -> bool:
        key = (scope, idempotency_key)
        record = self._state.idempotency_records.get(key)
        if (
            record is None
            or record["owner_request_id"] != owner_request_id
            or record["claim_version"] != claim_version
            or record["status"] != "in_progress"
        ):
            return False
        record["status"] = "failed"
        return True

    async def find_idempotency_by_scope_key(
        self,
        session: Any,
        scope: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        return self._state.idempotency_records.get((scope, idempotency_key))

    async def insert_agent_request(
        self, session: Any, record: dict[str, Any]
    ) -> dict[str, Any]:
        self._state.agent_requests[record["request_id"]] = record
        return record

    async def update_agent_request_status(
        self, session: Any, request_id: str, status: str, **fields: Any
    ) -> bool:
        rec = self._state.agent_requests.get(request_id)
        if rec is None:
            return False
        rec["status"] = status
        rec.update(fields)
        return True

    async def insert_agent_run(
        self, session: Any, record: dict[str, Any]
    ) -> dict[str, Any]:
        self._state.agent_runs[record["run_id"]] = record
        return record

    async def insert_audit_event(
        self, session: Any, record: dict[str, Any]
    ) -> dict[str, Any]:
        self._state.audit_events.append(record)
        return record


class FakeUnitOfWork:
    """In-memory unit of work for unit tests.

    Uses FakeRepository + FakeRepositoryState under the hood.
    begin/commit/rollback are no-ops; the state is preserved in-memory.
    """

    def __init__(
        self,
        repository: FakeRepository | None = None,
        state: FakeRepositoryState | None = None,
    ) -> None:
        self._state = state or FakeRepositoryState()
        self._repository = repository or FakeRepository(self._state)
        self.committed = False
        self.rolled_back = False

    @property
    def repository(self) -> FakeRepository:
        return self._repository

    @property
    def state(self) -> FakeRepositoryState:
        return self._state

    async def begin(self) -> Any:
        self.committed = False
        self.rolled_back = False
        return _FAKE_SESSION_SENTINEL

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


_FAKE_SESSION_SENTINEL = object()
