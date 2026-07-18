"""DatabaseRepository — abstract base class (M1.4-A Contract only).

Defines the method signatures that every repository must implement.
The concrete SQLAlchemy implementation is deferred to M1.4-C.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from conversation_agent.database.records import (
    IdempotencyClaimDecision,
    IdempotencyClaimRequest,
    IdempotencyClaimToken,
    NewAgentRequest,
    NewAgentRun,
    NewAuditEvent,
    PersistedAgentRequestRef,
)


@dataclass(frozen=True)
class IdempotencyClaimResult:
    """Returned by claim_idempotency."""

    claimed: bool
    existing_record: dict[str, Any] | None = None


@runtime_checkable
class ExecutionRepository(Protocol):
    """Narrow M1.4-C contract for request, run, and audit persistence."""

    async def create_request(
        self, record: NewAgentRequest
    ) -> PersistedAgentRequestRef: ...

    async def get_request_for_update(
        self, request_id: str
    ) -> PersistedAgentRequestRef: ...

    async def create_run(self, record: NewAgentRun) -> int: ...

    async def finalize_request_completed(
        self, request: PersistedAgentRequestRef, *, completed_at: Any
    ) -> None: ...

    async def finalize_request_failed(
        self,
        request: PersistedAgentRequestRef,
        *,
        completed_at: Any,
        failure_code: str,
    ) -> None: ...

    async def create_audit_event(self, record: NewAuditEvent) -> int: ...


@runtime_checkable
class IdempotencyRepository(Protocol):
    """Narrow M1.4-D contract for claim, replay, and fencing state."""

    async def claim(
        self, request: IdempotencyClaimRequest
    ) -> IdempotencyClaimDecision: ...

    async def assert_current_owner(self, token: IdempotencyClaimToken) -> None: ...

    async def complete_fenced(
        self,
        token: IdempotencyClaimToken,
        *,
        completed_run_record_id: int,
        response_snapshot: dict[str, Any],
        response_snapshot_version: int,
        replay_ttl_seconds: int,
    ) -> None: ...

    async def fail_fenced(
        self,
        token: IdempotencyClaimToken,
        *,
        failure_ttl_seconds: int,
    ) -> None: ...


class DatabaseRepository:
    """Abstract data-access layer for the four M1.4 persistence tables.

    Every method receives an explicit AsyncSession so the caller
    controls transactional boundaries.  Default implementations
    raise NotImplementedError — subclasses must override them.
    """

    # ── idempotency ─────────────────────────────────────────────────

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
        raise NotImplementedError

    async def re_claim_idempotency(
        self,
        session: Any,
        scope: str,
        idempotency_key: str,
        new_fingerprint: str,
        new_owner_request_id: str,
        lease_duration_seconds: int,
    ) -> tuple[bool, int | None]:
        """Returns (re_claimed: bool, new_claim_version: int | None)."""
        raise NotImplementedError

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
        """Returns True if the fenced UPDATE affected a row."""
        raise NotImplementedError

    async def fail_idempotency_fenced(
        self,
        session: Any,
        scope: str,
        idempotency_key: str,
        owner_request_id: str,
        claim_version: int,
    ) -> bool:
        """Returns True if the fenced UPDATE affected a row."""
        raise NotImplementedError

    async def find_idempotency_by_scope_key(
        self,
        session: Any,
        scope: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    # ── agent_requests ──────────────────────────────────────────────

    async def insert_agent_request(
        self, session: Any, record: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def update_agent_request_status(
        self, session: Any, request_id: str, status: str, **fields: Any
    ) -> bool:
        """Returns True if a row was updated."""
        raise NotImplementedError

    # ── agent_runs ──────────────────────────────────────────────────

    async def insert_agent_run(
        self, session: Any, record: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError

    # ── audit_events ────────────────────────────────────────────────

    async def insert_audit_event(
        self, session: Any, record: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError
