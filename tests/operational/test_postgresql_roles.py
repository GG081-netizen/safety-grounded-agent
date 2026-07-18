"""Ephemeral-database least-privilege role drill for PostgreSQL 17."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine


pytestmark = [
    pytest.mark.operational_integration,
    pytest.mark.postgres_integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(40),
]


async def _expect_denied(engine, statement: str) -> None:
    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(text(statement))


async def test_ephemeral_database_role_ownership_and_runtime_permissions():
    source_url = os.getenv("CONVAGENT_POSTGRES_TEST_URL", "").strip()
    if not source_url:
        pytest.skip("CONVAGENT_POSTGRES_TEST_URL not set")
    suffix = uuid.uuid4().hex[:12]
    database = f"convagent_m14f_roles_{suffix}"
    migration_role = f"convagent_migration_{suffix}"
    app_role = f"convagent_app_{suffix}"
    maintenance_role = f"convagent_maintenance_{suffix}"
    migration_password = f"m{uuid.uuid4().hex}"
    app_password = f"a{uuid.uuid4().hex}"
    maintenance_password = f"x{uuid.uuid4().hex}"
    parsed = make_url(source_url)
    admin_engine = create_async_engine(source_url, isolation_level="AUTOCOMMIT")
    app_engine = None
    maintenance_engine = None
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE ROLE "{migration_role}" LOGIN PASSWORD \'{migration_password}\''))
            await connection.execute(text(f'CREATE ROLE "{app_role}" LOGIN PASSWORD \'{app_password}\''))
            await connection.execute(text(f'CREATE ROLE "{maintenance_role}" LOGIN PASSWORD \'{maintenance_password}\''))
            await connection.execute(text(f'CREATE DATABASE "{database}" OWNER "{migration_role}"'))

        migration_url = parsed.set(
            username=migration_role,
            password=migration_password,
            database=database,
        ).render_as_string(hide_password=False)
        alembic = AlembicConfig("alembic.ini")
        alembic.set_main_option("sqlalchemy.url", migration_url)
        await asyncio.to_thread(command.upgrade, alembic, "head")

        database_admin_url = parsed.set(database=database).render_as_string(
            hide_password=False
        )
        database_admin = create_async_engine(database_admin_url)
        async with database_admin.begin() as connection:
            await connection.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
            await connection.execute(text(f'GRANT CONNECT ON DATABASE "{database}" TO "{app_role}", "{maintenance_role}"'))
            await connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{app_role}", "{maintenance_role}"'))
            await connection.execute(text(f'GRANT SELECT ON alembic_version TO "{app_role}"'))
            await connection.execute(text(f'GRANT SELECT, INSERT, UPDATE ON agent_requests TO "{app_role}"'))
            await connection.execute(text(f'GRANT SELECT, INSERT ON agent_runs TO "{app_role}"'))
            await connection.execute(text(f'GRANT SELECT, INSERT ON audit_events TO "{app_role}"'))
            await connection.execute(text(f'GRANT SELECT, INSERT, UPDATE ON idempotency_records TO "{app_role}"'))
            await connection.execute(text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{app_role}"'))
            await connection.execute(text(f'GRANT SELECT, DELETE ON idempotency_records TO "{maintenance_role}"'))
            await connection.execute(text(f'ALTER DEFAULT PRIVILEGES FOR ROLE "{migration_role}" IN SCHEMA public GRANT SELECT, INSERT ON TABLES TO "{app_role}"'))
            await connection.execute(text(f'ALTER DEFAULT PRIVILEGES FOR ROLE "{migration_role}" IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO "{app_role}"'))

            owners = set(
                (
                    await connection.execute(
                        text(
                            "SELECT tableowner FROM pg_tables WHERE schemaname='public' "
                            "AND tablename IN ('agent_requests','agent_runs','audit_events','idempotency_records')"
                        )
                    )
                ).scalars()
            )
            assert owners == {migration_role}
            assert await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_namespace n, "
                    "LATERAL aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) acl "
                    "WHERE n.nspname='public' AND acl.grantee=0 "
                    "AND acl.privilege_type='CREATE')"
                )
            ) is False
        await database_admin.dispose()

        app_url = parsed.set(
            username=app_role,
            password=app_password,
            database=database,
        ).render_as_string(hide_password=False)
        app_engine = create_async_engine(app_url)
        now = datetime.now(timezone.utc)
        async with app_engine.begin() as connection:
            request_record_id = await connection.scalar(
                text(
                    "INSERT INTO agent_requests "
                    "(request_id, trace_id, operation, principal_user_id, tenant_id, organization_id, status, "
                    "user_text_hash, user_text_length, request_fingerprint, fingerprint_version, "
                    "authorization_snapshot, authorization_snapshot_schema_version, created_at) "
                    "VALUES (:request_id, :trace_id, 'v1.chat', 'user', 'tenant', 'org', 'in_progress', "
                    ":hash, 4, :fingerprint, 2, CAST(:authorization AS jsonb), 1, :created_at) RETURNING id"
                ),
                {
                    "request_id": f"role-request-{suffix}",
                    "trace_id": f"role-trace-{suffix}",
                    "hash": "a" * 64,
                    "fingerprint": "b" * 64,
                    "authorization": '{"allowed": true}',
                    "created_at": now,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO audit_events "
                    "(event_id, request_id, tenant_id, organization_id, event_type, outcome, created_at) "
                    "VALUES (:event_id, :request_id, 'tenant', 'org', 'request_accepted', 'accepted', :created_at)"
                ),
                {
                    "event_id": f"role-audit-{suffix}",
                    "request_id": f"role-request-{suffix}",
                    "created_at": now,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO agent_runs "
                    "(run_id, original_request_id, status, started_at, completed_at) "
                    "VALUES (:run_id, :request_id, 'failed', :started_at, :completed_at)"
                ),
                {
                    "run_id": f"role-run-{suffix}",
                    "request_id": request_record_id,
                    "started_at": now,
                    "completed_at": now,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO idempotency_records "
                    "(tenant_id, organization_id, principal_user_id, operation, idempotency_key_hash, "
                    "request_fingerprint, fingerprint_version, status, claim_version, owner_request_id, "
                    "claimed_at, lease_expires_at, created_at, updated_at, expires_at) "
                    "VALUES ('tenant', 'org', 'user', 'v1.chat', :hash, :fingerprint, 2, 'failed', 1, "
                    ":owner, :claimed, :lease, :created, :updated, :expires)"
                ),
                {
                    "hash": "c" * 64,
                    "fingerprint": "b" * 64,
                    "owner": f"role-request-{suffix}",
                    "claimed": now,
                    "lease": now + timedelta(minutes=5),
                    "created": now,
                    "updated": now,
                    "expires": now + timedelta(hours=1),
                },
            )
            await connection.execute(
                text("UPDATE agent_requests SET status='failed', completed_at=:now, failure_code='role_test' WHERE id=:id"),
                {"now": now, "id": request_record_id},
            )

        await _expect_denied(app_engine, "CREATE TABLE forbidden_table(id integer)")
        await _expect_denied(app_engine, "ALTER TABLE agent_requests ADD COLUMN forbidden integer")
        await _expect_denied(app_engine, "DROP TABLE audit_events")
        await _expect_denied(app_engine, "TRUNCATE audit_events")
        await _expect_denied(app_engine, "UPDATE alembic_version SET version_num='bad'")
        await _expect_denied(app_engine, "UPDATE audit_events SET outcome='bad'")
        await _expect_denied(app_engine, "DELETE FROM audit_events")
        await _expect_denied(app_engine, "DELETE FROM agent_requests")

        maintenance_url = parsed.set(
            username=maintenance_role,
            password=maintenance_password,
            database=database,
        ).render_as_string(hide_password=False)
        maintenance_engine = create_async_engine(maintenance_url)
        async with maintenance_engine.begin() as connection:
            deleted = await connection.execute(
                text("DELETE FROM idempotency_records WHERE tenant_id='tenant' RETURNING id")
            )
            assert len(deleted.all()) == 1
    finally:
        if app_engine is not None:
            await app_engine.dispose()
        if maintenance_engine is not None:
            await maintenance_engine.dispose()
        try:
            async with admin_engine.connect() as connection:
                await connection.execute(
                    text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:database AND pid <> pg_backend_pid()"),
                    {"database": database},
                )
                await connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
                await connection.execute(text(f'DROP ROLE IF EXISTS "{app_role}"'))
                await connection.execute(text(f'DROP ROLE IF EXISTS "{maintenance_role}"'))
                await connection.execute(text(f'DROP ROLE IF EXISTS "{migration_role}"'))
        finally:
            await admin_engine.dispose()
