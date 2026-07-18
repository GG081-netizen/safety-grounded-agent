"""M1.4-F bounded operations against the configured test PostgreSQL."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from conversation_agent.config import AppConfig, DatabaseConfig, PersistenceMode
from conversation_agent.database.models import (
    AgentRequest,
    AgentRun,
    AuditEvent,
    IdempotencyRecord,
)
from conversation_agent.operations import (
    IdempotencyPruner,
    PersistenceDoctor,
    PersistenceIntegrityChecker,
)


pytestmark = [
    pytest.mark.postgres_integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
]


@pytest.fixture(scope="session")
def operations_postgres_url() -> str:
    url = os.getenv("CONVAGENT_POSTGRES_TEST_URL", "").strip()
    if not url:
        pytest.skip("CONVAGENT_POSTGRES_TEST_URL not set")
    return url


@pytest.fixture(scope="session")
def operations_schema(operations_postgres_url: str) -> None:
    config = AlembicConfig("alembic.ini")
    config.set_main_option("sqlalchemy.url", operations_postgres_url)
    command.upgrade(config, "head")


@pytest_asyncio.fixture
async def operations_engine(
    operations_postgres_url: str,
    operations_schema: None,
) -> AsyncEngine:
    del operations_schema
    engine = create_async_engine(operations_postgres_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


async def _insert_failed_case(engine: AsyncEngine, prefix: str) -> None:
    now = datetime.now(timezone.utc)
    created = now - timedelta(hours=2)
    completed = created + timedelta(seconds=1)
    async with engine.begin() as connection:
        request_id = await connection.scalar(
            AgentRequest.__table__.insert()
            .values(
                request_id=f"{prefix}-request",
                trace_id=f"{prefix}-trace",
                operation="v1.chat",
                principal_user_id=f"{prefix}-user",
                tenant_id=f"{prefix}-tenant",
                organization_id=f"{prefix}-org",
                status="failed",
                user_text_hash="a" * 64,
                user_text_length=4,
                idempotency_key_hash="b" * 64,
                request_fingerprint="c" * 64,
                fingerprint_version=2,
                authorization_snapshot={"allowed": True},
                authorization_snapshot_schema_version=1,
                failure_code="application_service_failed",
                created_at=created,
                completed_at=completed,
            )
            .returning(AgentRequest.id)
        )
        await connection.execute(
            AgentRun.__table__.insert().values(
                run_id=f"{prefix}-run",
                original_request_id=request_id,
                status="failed",
                trace_snapshot={"failure_code": "application_service_failed"},
                trace_snapshot_schema_version=1,
                started_at=created,
                completed_at=completed,
            )
        )
        await connection.execute(
            AuditEvent.__table__.insert(),
            (
                {
                    "event_id": f"{prefix}-accepted",
                    "request_id": f"{prefix}-request",
                    "trace_id": f"{prefix}-trace",
                    "tenant_id": f"{prefix}-tenant",
                    "organization_id": f"{prefix}-org",
                    "event_type": "request_accepted",
                    "outcome": "accepted",
                    "details_json": {"audit_payload_version": 1},
                    "created_at": created,
                },
                {
                    "event_id": f"{prefix}-failed",
                    "request_id": f"{prefix}-request",
                    "trace_id": f"{prefix}-trace",
                    "tenant_id": f"{prefix}-tenant",
                    "organization_id": f"{prefix}-org",
                    "event_type": "request_failed",
                    "outcome": "failed",
                    "details_json": {"audit_payload_version": 1},
                    "created_at": completed,
                },
            ),
        )
        await connection.execute(
            IdempotencyRecord.__table__.insert().values(
                tenant_id=f"{prefix}-tenant",
                organization_id=f"{prefix}-org",
                principal_user_id=f"{prefix}-user",
                operation="v1.chat",
                idempotency_key_hash="b" * 64,
                request_fingerprint="c" * 64,
                fingerprint_version=2,
                status="failed",
                claim_version=1,
                owner_request_id=f"{prefix}-request",
                claimed_at=created,
                lease_expires_at=created + timedelta(minutes=5),
                created_at=created,
                updated_at=completed,
                expires_at=now - timedelta(minutes=30),
            )
        )


async def _cleanup(engine: AsyncEngine, prefix: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.tenant_id == f"{prefix}-tenant"
            )
        )
        await connection.execute(
            delete(AuditEvent).where(AuditEvent.event_id.like(f"{prefix}%"))
        )
        await connection.execute(
            delete(AgentRun).where(AgentRun.run_id.like(f"{prefix}%"))
        )
        await connection.execute(
            delete(AgentRequest).where(AgentRequest.request_id.like(f"{prefix}%"))
        )


async def test_integrity_checker_and_doctor_are_read_only_on_real_postgres(
    operations_engine: AsyncEngine,
    operations_postgres_url: str,
):
    prefix = f"m14f-doctor-{uuid.uuid4()}"
    await _insert_failed_case(operations_engine, prefix)
    try:
        before = {}
        async with operations_engine.connect() as connection:
            for table in (AgentRequest, AgentRun, AuditEvent, IdempotencyRecord):
                before[table.__tablename__] = int(
                    await connection.scalar(select(__import__("sqlalchemy").func.count()).select_from(table))
                    or 0
                )
        integrity = await PersistenceIntegrityChecker(operations_engine).check()
        assert integrity.status == "healthy"
        assert integrity.complete is True
        assert integrity.expired_terminal_count >= 1

        config = AppConfig(
            runtime_mode="test",
            database=DatabaseConfig(
                url=operations_postgres_url,
                persistence_mode=PersistenceMode.POSTGRES,
                expected_revision="0001",
            ),
        )
        doctor = await PersistenceDoctor(
            config=config,
            engine=operations_engine,
        ).run()
        assert doctor.exit_code == 0, doctor.to_dict()
        assert doctor.complete is True
        after = {}
        async with operations_engine.connect() as connection:
            for table in (AgentRequest, AgentRun, AuditEvent, IdempotencyRecord):
                after[table.__tablename__] = int(
                    await connection.scalar(select(__import__("sqlalchemy").func.count()).select_from(table))
                    or 0
                )
        assert after == before
    finally:
        await _cleanup(operations_engine, prefix)


async def test_prune_uses_database_time_and_never_deletes_active_or_history(
    operations_engine: AsyncEngine,
):
    prefix = f"m14f-prune-{uuid.uuid4()}"
    await _insert_failed_case(operations_engine, prefix)
    now = datetime.now(timezone.utc)
    async with operations_engine.begin() as connection:
        await connection.execute(
            IdempotencyRecord.__table__.insert().values(
                tenant_id=f"{prefix}-active-tenant",
                organization_id=f"{prefix}-org",
                principal_user_id=f"{prefix}-user",
                operation="v1.chat",
                idempotency_key_hash="d" * 64,
                request_fingerprint="e" * 64,
                fingerprint_version=2,
                status="in_progress",
                claim_version=1,
                owner_request_id=f"{prefix}-active-request",
                claimed_at=now - timedelta(hours=2),
                lease_expires_at=now - timedelta(hours=1),
                created_at=now - timedelta(hours=2),
                updated_at=now - timedelta(hours=2),
                expires_at=now - timedelta(hours=1),
            )
        )
    try:
        pruner = IdempotencyPruner(
            operations_engine,
            batch_size=1,
            safety_margin_seconds=1,
        )
        dry_run = await pruner.run()
        assert dry_run.applied is False
        assert dry_run.candidate_count >= 1
        applied = await pruner.run(apply=True)
        assert applied.deleted_count >= 1
        async with operations_engine.connect() as connection:
            failed_exists = await connection.scalar(
                select(IdempotencyRecord.id).where(
                    IdempotencyRecord.tenant_id == f"{prefix}-tenant"
                )
            )
            active_exists = await connection.scalar(
                select(IdempotencyRecord.id).where(
                    IdempotencyRecord.tenant_id == f"{prefix}-active-tenant"
                )
            )
            request_exists = await connection.scalar(
                select(AgentRequest.id).where(
                    AgentRequest.request_id == f"{prefix}-request"
                )
            )
        assert failed_exists is None
        assert active_exists is not None
        assert request_exists is not None
    finally:
        async with operations_engine.begin() as connection:
            await connection.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.tenant_id == f"{prefix}-active-tenant"
                )
            )
        await _cleanup(operations_engine, prefix)
