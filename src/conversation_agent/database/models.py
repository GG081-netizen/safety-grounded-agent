"""SQLAlchemy 2.0 ORM models — M1.4-B.

Four tables: agent_requests, agent_runs, audit_events, idempotency_records.
All constraints follow the M1.4 plan §4.1–4.4 with M1.4-B amendments.

Design invariants:
- No circular FKs.
- Internal FKs use INTEGER surrogate keys; external IDs are VARCHAR UNIQUE.
- Raw Idempotency-Key is never persisted — only SHA-256 hash.
- No raw_response, JWT, key material, email, or stack traces.
- All time columns are TIMESTAMPTZ.
- JSONB only for snapshots and audit details.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import func

# ── Naming convention (stable, deterministic names) ──────────────────────

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    __abstract__ = True
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ── Shared helpers ────────────────────────────────────────────────────────

_HEX64_RE = r"""^[a-f0-9]{64}$"""
_TIMESTAMPTZ = DateTime(timezone=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Table 1: agent_requests
# ═══════════════════════════════════════════════════════════════════════════════

class AgentRequest(Base):
    __tablename__ = "agent_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(128), nullable=False)
    trace_id = Column(String(128), nullable=False)
    session_id = Column(String(128), nullable=True)
    operation = Column(String(64), nullable=False)
    principal_user_id = Column(String(128), nullable=False)
    tenant_id = Column(String(128), nullable=False)
    organization_id = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False)
    user_text_hash = Column(String(64), nullable=False)
    user_text_length = Column(Integer, nullable=False)
    idempotency_key_hash = Column(String(64), nullable=True)
    request_fingerprint = Column(String(64), nullable=False)
    fingerprint_version = Column(Integer, nullable=False, server_default=text("1"))
    replayed_from_request_id = Column(
        Integer,
        ForeignKey("agent_requests.id", ondelete="SET NULL"),
        nullable=True,
    )
    authorization_snapshot = Column(JSONB, nullable=False)
    authorization_snapshot_schema_version = Column(
        Integer, nullable=False, server_default=text("1")
    )
    failure_code = Column(String(64), nullable=True)
    created_at = Column(_TIMESTAMPTZ, nullable=False, server_default=func.now())
    completed_at = Column(_TIMESTAMPTZ, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress', 'completed', 'failed')",
            name="status_values",
        ),
        CheckConstraint(
            "user_text_length >= 0",
            name="user_text_length_nonneg",
        ),
        CheckConstraint(
            f"request_fingerprint ~ '{_HEX64_RE}'",
            name="fingerprint_hex",
        ),
        CheckConstraint(
            f"user_text_hash ~ '{_HEX64_RE}'",
            name="user_text_hash_hex",
        ),
        CheckConstraint(
            f"idempotency_key_hash IS NULL OR idempotency_key_hash ~ '{_HEX64_RE}'",
            name="idempotency_key_hash_hex",
        ),
        CheckConstraint(
            "fingerprint_version >= 1",
            name="fingerprint_version_min",
        ),
        CheckConstraint(
            "authorization_snapshot_schema_version >= 1",
            name="auth_snapshot_version_min",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name="completed_after_created",
        ),
        UniqueConstraint("request_id", name="uq_agent_requests_request_id"),
        Index("ix_agent_requests_trace_id", "trace_id"),
        Index(
            "ix_agent_requests_tenant_org_time",
            "tenant_id",
            "organization_id",
            "created_at",
        ),
        Index(
            "ix_agent_requests_principal_time", "principal_user_id", "created_at"
        ),
        Index("ix_agent_requests_status_time", "status", "created_at"),
        Index(
            "ix_agent_requests_replayed_from_request_id", "replayed_from_request_id"
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Table 2: agent_runs
# ═══════════════════════════════════════════════════════════════════════════════

class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(128), nullable=False)
    original_request_id = Column(
        Integer,
        ForeignKey("agent_requests.id"),
        nullable=False,
    )
    session_id = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False)
    routed_task = Column(String(64), nullable=True)
    policy_outcome = Column(String(32), nullable=True)
    result_snapshot = Column(JSONB, nullable=True)
    result_snapshot_schema_version = Column(Integer, nullable=True)
    confidence = Column(Float, nullable=True)
    trace_snapshot = Column(JSONB, nullable=True)
    trace_snapshot_schema_version = Column(Integer, nullable=True)
    rag_provider = Column(String(32), nullable=True)
    started_at = Column(_TIMESTAMPTZ, nullable=False, server_default=func.now())
    completed_at = Column(_TIMESTAMPTZ, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('completed', 'blocked', 'failed')",
            name="status_values",
        ),
        CheckConstraint(
            "result_snapshot IS NULL OR result_snapshot_schema_version IS NOT NULL",
            name="result_snapshot_version",
        ),
        CheckConstraint(
            "trace_snapshot IS NULL OR trace_snapshot_schema_version IS NOT NULL",
            name="trace_snapshot_version",
        ),
        CheckConstraint(
            "result_snapshot_schema_version IS NULL OR result_snapshot_schema_version >= 1",
            name="result_snapshot_version_min",
        ),
        CheckConstraint(
            "trace_snapshot_schema_version IS NULL OR trace_snapshot_schema_version >= 1",
            name="trace_snapshot_version_min",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="confidence_range",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="completed_after_started",
        ),
        UniqueConstraint("run_id", name="uq_agent_runs_run_id"),
        UniqueConstraint(
            "original_request_id", name="uq_agent_runs_request_id"
        ),
        Index("ix_agent_runs_status_time", "status", "completed_at"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Table 3: audit_events
# ═══════════════════════════════════════════════════════════════════════════════

class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(128), nullable=False)
    request_id = Column(String(128), nullable=True)
    trace_id = Column(String(128), nullable=True)
    tenant_id = Column(String(128), nullable=False)
    organization_id = Column(String(128), nullable=False)
    event_type = Column(String(64), nullable=False)
    principal_user_id = Column(String(128), nullable=True)
    outcome = Column(String(32), nullable=False)
    details_json = Column(JSONB, nullable=True)
    created_at = Column(_TIMESTAMPTZ, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("event_id", name="uq_audit_events_event_id"),
        Index("ix_audit_events_request_id", "request_id"),
        Index("ix_audit_events_type_time", "event_type", "created_at"),
        Index(
            "ix_audit_events_tenant_org_time",
            "tenant_id",
            "organization_id",
            "created_at",
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Table 4: idempotency_records
# ═══════════════════════════════════════════════════════════════════════════════

class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(128), nullable=False)
    organization_id = Column(String(128), nullable=False)
    principal_user_id = Column(String(128), nullable=False)
    operation = Column(String(64), nullable=False)
    idempotency_key_hash = Column(String(64), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    fingerprint_version = Column(Integer, nullable=False, server_default=text("1"))
    status = Column(String(32), nullable=False)
    claim_version = Column(Integer, nullable=False, server_default=text("1"))
    owner_request_id = Column(String(128), nullable=False)
    claimed_at = Column(_TIMESTAMPTZ, nullable=False, server_default=func.now())
    lease_expires_at = Column(_TIMESTAMPTZ, nullable=False)
    completed_run_record_id = Column(
        Integer,
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    response_snapshot = Column(JSONB, nullable=True)
    response_snapshot_schema_version = Column(Integer, nullable=True)
    created_at = Column(_TIMESTAMPTZ, nullable=False, server_default=func.now())
    updated_at = Column(_TIMESTAMPTZ, nullable=False, server_default=func.now())
    expires_at = Column(_TIMESTAMPTZ, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "organization_id",
            "principal_user_id",
            "operation",
            "idempotency_key_hash",
            name="uq_idempotency_records_scope",
        ),
        CheckConstraint(
            "status IN ('in_progress', 'completed', 'failed')",
            name="status_values",
        ),
        CheckConstraint(
            "claim_version >= 1",
            name="claim_version_min",
        ),
        CheckConstraint(
            f"idempotency_key_hash ~ '{_HEX64_RE}'",
            name="key_hash_hex",
        ),
        CheckConstraint(
            f"request_fingerprint ~ '{_HEX64_RE}'",
            name="fingerprint_hex",
        ),
        CheckConstraint(
            "fingerprint_version >= 1",
            name="fingerprint_version_min",
        ),
        CheckConstraint(
            "response_snapshot IS NULL OR response_snapshot_schema_version IS NOT NULL",
            name="response_snapshot_version",
        ),
        CheckConstraint(
            "response_snapshot_schema_version IS NULL OR response_snapshot_schema_version >= 1",
            name="response_snapshot_version_min",
        ),
        CheckConstraint(
            "lease_expires_at >= claimed_at",
            name="lease_after_claimed",
        ),
        CheckConstraint(
            "expires_at >= created_at",
            name="expires_after_created",
        ),
        Index("ix_idempotency_status_expires", "status", "expires_at"),
        Index("ix_idempotency_lease", "status", "lease_expires_at"),
    )
