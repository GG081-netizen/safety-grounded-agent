"""Immutable persistence records decoupled from SQLAlchemy ORM models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
JsonObject: TypeAlias = Mapping[str, JsonValue]


def require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must be UTC")


@dataclass(frozen=True, slots=True)
class NewAgentRequest:
    request_id: str
    trace_id: str
    session_id: str | None
    operation: str
    principal_user_id: str
    tenant_id: str
    organization_id: str
    user_text_hash: str
    user_text_length: int
    request_fingerprint: str
    fingerprint_version: int
    authorization_snapshot: JsonObject
    authorization_snapshot_schema_version: int
    created_at: datetime
    status: str = "in_progress"
    idempotency_key_hash: str | None = None
    replayed_from_request_record_id: int | None = None
    completed_at: datetime | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        require_utc(self.created_at, "created_at")
        if self.completed_at is not None:
            require_utc(self.completed_at, "completed_at")


@dataclass(frozen=True, slots=True)
class PersistedAgentRequestRef:
    database_id: int
    request_id: str
    status: str
    trace_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class NewAgentRun:
    run_id: str
    original_request_record_id: int
    session_id: str | None
    status: str
    routed_task: str | None
    policy_outcome: str | None
    result_snapshot: JsonObject | None
    result_snapshot_schema_version: int | None
    confidence: float | None
    trace_snapshot: JsonObject | None
    trace_snapshot_schema_version: int | None
    rag_provider: str | None
    started_at: datetime
    completed_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.started_at, "started_at")
        require_utc(self.completed_at, "completed_at")
        if (self.result_snapshot is None) != (
            self.result_snapshot_schema_version is None
        ):
            raise ValueError("result snapshot and version must be paired")
        if (self.trace_snapshot is None) != (
            self.trace_snapshot_schema_version is None
        ):
            raise ValueError("trace snapshot and version must be paired")


@dataclass(frozen=True, slots=True)
class NewAuditEvent:
    event_id: str
    request_id: str | None
    trace_id: str | None
    tenant_id: str
    organization_id: str
    event_type: str
    principal_user_id: str | None
    outcome: str
    details_json: JsonObject | None
    created_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.created_at, "created_at")


class IdempotencyStatus(str, Enum):
    ACTIVE = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ClaimOutcome(str, Enum):
    ACQUIRED = "acquired"
    RECLAIMED = "reclaimed"
    REPLAY = "replay"
    IN_PROGRESS = "in_progress"
    CONFLICT = "conflict"
    PREVIOUS_FAILURE = "previous_failure"


@dataclass(frozen=True, slots=True)
class IdempotencyPolicy:
    lease_duration_seconds: int = 60
    replay_ttl_seconds: int = 3600
    failure_ttl_seconds: int = 300
    max_replay_snapshot_bytes: int = 262_144

    def __post_init__(self) -> None:
        for name in (
            "lease_duration_seconds",
            "replay_ttl_seconds",
            "failure_ttl_seconds",
            "max_replay_snapshot_bytes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class IdempotencyScope:
    tenant_id: str
    organization_id: str
    principal_user_id: str
    operation: str
    key_hash: str


@dataclass(frozen=True, slots=True)
class IdempotencyClaimRequest:
    scope: IdempotencyScope
    request_fingerprint: str
    fingerprint_version: int
    owner_request_id: str
    lease_duration_seconds: int


@dataclass(frozen=True, slots=True)
class IdempotencyClaimToken:
    idempotency_record_id: int
    scope: IdempotencyScope
    owner_request_id: str
    claim_version: int
    claimed_at: datetime
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.claimed_at, "claimed_at")
        require_utc(self.lease_expires_at, "lease_expires_at")


@dataclass(frozen=True, slots=True)
class StoredIdempotencyRecord:
    database_id: int
    scope: IdempotencyScope
    request_fingerprint: str
    fingerprint_version: int
    status: str
    claim_version: int | None
    owner_request_id: str | None
    claimed_at: datetime | None
    lease_expires_at: datetime | None
    completed_run_record_id: int | None
    response_snapshot: JsonObject | None
    response_snapshot_version: int | None
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class IdempotencyClaimDecision:
    outcome: ClaimOutcome
    token: IdempotencyClaimToken | None = None
    original_request_record_id: int | None = None
    original_request_id: str | None = None
    replay_snapshot: JsonObject | None = None
    replay_snapshot_version: int | None = None
    previous_failure_code: str | None = None
    reclaimed_request: PersistedAgentRequestRef | None = None
    expired_reuse: bool = False


class IdempotentResultOutcome(str, Enum):
    EXECUTED = "executed"
    REPLAYED = "replayed"
    IN_PROGRESS = "in_progress"
    CONFLICT = "conflict"
    PREVIOUS_FAILURE = "previous_failure"


@dataclass(frozen=True, slots=True)
class IdempotentApplicationResult:
    outcome: IdempotentResultOutcome
    request_id: str
    application_result: object | None = None
    original_request_id: str | None = None
    claim_version: int | None = None
    safe_failure_code: str | None = None
