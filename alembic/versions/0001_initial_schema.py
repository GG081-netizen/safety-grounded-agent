"""Initial schema — M1.4-B four persistence tables.

Revision ID: 0001
Revises: (none)
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HEX64_RE = r"^[a-f0-9]{64}$"
_TIMESTAMPTZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    # ── agent_requests ──────────────────────────────────────────────────

    op.create_table(
        "agent_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=True),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("principal_user_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("user_text_hash", sa.String(64), nullable=False),
        sa.Column("user_text_length", sa.Integer(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("fingerprint_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "replayed_from_request_id",
            sa.Integer(),
            sa.ForeignKey("agent_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("authorization_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "authorization_snapshot_schema_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            _TIMESTAMPTZ,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", _TIMESTAMPTZ, nullable=True),
        # PK
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_requests")),
        # UNIQUE
        sa.UniqueConstraint("request_id", name=op.f("uq_agent_requests_request_id")),
        # CHECKs
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed', 'failed')",
            name=op.f("ck_agent_requests_status_values"),
        ),
        sa.CheckConstraint(
            "user_text_length >= 0",
            name=op.f("ck_agent_requests_user_text_length_nonneg"),
        ),
        sa.CheckConstraint(
            f"request_fingerprint ~ '{_HEX64_RE}'",
            name=op.f("ck_agent_requests_fingerprint_hex"),
        ),
        sa.CheckConstraint(
            f"user_text_hash ~ '{_HEX64_RE}'",
            name=op.f("ck_agent_requests_user_text_hash_hex"),
        ),
        sa.CheckConstraint(
            f"idempotency_key_hash IS NULL OR idempotency_key_hash ~ '{_HEX64_RE}'",
            name=op.f("ck_agent_requests_idempotency_key_hash_hex"),
        ),
        sa.CheckConstraint(
            "fingerprint_version >= 1",
            name=op.f("ck_agent_requests_fingerprint_version_min"),
        ),
        sa.CheckConstraint(
            "authorization_snapshot_schema_version >= 1",
            name=op.f("ck_agent_requests_auth_snapshot_version_min"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name=op.f("ck_agent_requests_completed_after_created"),
        ),
    )
    # Composite indexes
    op.create_index(
        op.f("ix_agent_requests_trace_id"), "agent_requests", ["trace_id"]
    )
    op.create_index(
        op.f("ix_agent_requests_tenant_org_time"),
        "agent_requests",
        ["tenant_id", "organization_id", "created_at"],
    )
    op.create_index(
        op.f("ix_agent_requests_principal_time"),
        "agent_requests",
        ["principal_user_id", "created_at"],
    )
    op.create_index(
        op.f("ix_agent_requests_status_time"),
        "agent_requests",
        ["status", "created_at"],
    )
    op.create_index(
        op.f("ix_agent_requests_replayed_from_request_id"),
        "agent_requests",
        ["replayed_from_request_id"],
    )

    # ── agent_runs ──────────────────────────────────────────────────────

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column(
            "original_request_id",
            sa.Integer(),
            sa.ForeignKey("agent_requests.id"),
            nullable=False,
        ),
        sa.Column("session_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("routed_task", sa.String(64), nullable=True),
        sa.Column("policy_outcome", sa.String(32), nullable=True),
        sa.Column("result_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("result_snapshot_schema_version", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("trace_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("trace_snapshot_schema_version", sa.Integer(), nullable=True),
        sa.Column("rag_provider", sa.String(32), nullable=True),
        sa.Column(
            "started_at",
            _TIMESTAMPTZ,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", _TIMESTAMPTZ, nullable=True),
        # PK
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
        # UNIQUE
        sa.UniqueConstraint("run_id", name=op.f("uq_agent_runs_run_id")),
        sa.UniqueConstraint(
            "original_request_id", name=op.f("uq_agent_runs_request_id")
        ),
        # CHECKs
        sa.CheckConstraint(
            "status IN ('completed', 'blocked', 'failed')",
            name=op.f("ck_agent_runs_status_values"),
        ),
        sa.CheckConstraint(
            "result_snapshot IS NULL OR result_snapshot_schema_version IS NOT NULL",
            name=op.f("ck_agent_runs_result_snapshot_version"),
        ),
        sa.CheckConstraint(
            "trace_snapshot IS NULL OR trace_snapshot_schema_version IS NOT NULL",
            name=op.f("ck_agent_runs_trace_snapshot_version"),
        ),
        sa.CheckConstraint(
            "result_snapshot_schema_version IS NULL OR result_snapshot_schema_version >= 1",
            name=op.f("ck_agent_runs_result_snapshot_version_min"),
        ),
        sa.CheckConstraint(
            "trace_snapshot_schema_version IS NULL OR trace_snapshot_schema_version >= 1",
            name=op.f("ck_agent_runs_trace_snapshot_version_min"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name=op.f("ck_agent_runs_confidence_range"),
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name=op.f("ck_agent_runs_completed_after_started"),
        ),
    )
    op.create_index(
        op.f("ix_agent_runs_status_time"),
        "agent_runs",
        ["status", "completed_at"],
    )

    # ── audit_events ────────────────────────────────────────────────────

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("trace_id", sa.String(128), nullable=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("principal_user_id", sa.String(128), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("details_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            _TIMESTAMPTZ,
            nullable=False,
            server_default=sa.func.now(),
        ),
        # PK
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
        # UNIQUE
        sa.UniqueConstraint("event_id", name=op.f("uq_audit_events_event_id")),
    )
    op.create_index(
        op.f("ix_audit_events_request_id"), "audit_events", ["request_id"]
    )
    op.create_index(
        op.f("ix_audit_events_type_time"),
        "audit_events",
        ["event_type", "created_at"],
    )
    op.create_index(
        op.f("ix_audit_events_tenant_org_time"),
        "audit_events",
        ["tenant_id", "organization_id", "created_at"],
    )

    # ── idempotency_records ─────────────────────────────────────────────

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("principal_user_id", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("fingerprint_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("claim_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("owner_request_id", sa.String(128), nullable=False),
        sa.Column(
            "claimed_at",
            _TIMESTAMPTZ,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("lease_expires_at", _TIMESTAMPTZ, nullable=False),
        sa.Column(
            "completed_run_record_id",
            sa.Integer(),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("response_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("response_snapshot_schema_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            _TIMESTAMPTZ,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            _TIMESTAMPTZ,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", _TIMESTAMPTZ, nullable=False),
        # PK
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_records")),
        # UNIQUE — scoped idempotency: 5-field composite
        sa.UniqueConstraint(
            "tenant_id",
            "organization_id",
            "principal_user_id",
            "operation",
            "idempotency_key_hash",
            name=op.f("uq_idempotency_records_scope"),
        ),
        # CHECKs
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed', 'failed')",
            name=op.f("ck_idempotency_records_status_values"),
        ),
        sa.CheckConstraint(
            "claim_version >= 1",
            name=op.f("ck_idempotency_records_claim_version_min"),
        ),
        sa.CheckConstraint(
            f"idempotency_key_hash ~ '{_HEX64_RE}'",
            name=op.f("ck_idempotency_records_key_hash_hex"),
        ),
        sa.CheckConstraint(
            f"request_fingerprint ~ '{_HEX64_RE}'",
            name=op.f("ck_idempotency_records_fingerprint_hex"),
        ),
        sa.CheckConstraint(
            "fingerprint_version >= 1",
            name=op.f("ck_idempotency_records_fingerprint_version_min"),
        ),
        sa.CheckConstraint(
            "response_snapshot IS NULL OR response_snapshot_schema_version IS NOT NULL",
            name=op.f("ck_idempotency_records_response_snapshot_version"),
        ),
        sa.CheckConstraint(
            "response_snapshot_schema_version IS NULL OR response_snapshot_schema_version >= 1",
            name=op.f("ck_idempotency_records_response_snapshot_version_min"),
        ),
        sa.CheckConstraint(
            "lease_expires_at >= claimed_at",
            name=op.f("ck_idempotency_records_lease_after_claimed"),
        ),
        sa.CheckConstraint(
            "expires_at >= created_at",
            name=op.f("ck_idempotency_records_expires_after_created"),
        ),
    )
    op.create_index(
        op.f("ix_idempotency_status_expires"),
        "idempotency_records",
        ["status", "expires_at"],
    )
    op.create_index(
        op.f("ix_idempotency_lease"),
        "idempotency_records",
        ["status", "lease_expires_at"],
    )


def downgrade() -> None:
    """Drop tables in reverse-FK order (no CASCADE)."""
    op.drop_table("idempotency_records")
    op.drop_table("audit_events")
    op.drop_table("agent_runs")
    op.drop_table("agent_requests")
