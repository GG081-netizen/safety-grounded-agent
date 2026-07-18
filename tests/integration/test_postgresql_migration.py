"""M1.4-B PostgreSQL migration integration tests.

These tests validate the four-table schema, constraints, indexes,
and migration cycle against a real PostgreSQL database.

Requirements:
  - CONVAGENT_POSTGRES_TEST_URL must be set (never uses CONVAGENT_DATABASE_URL)
  - CONVAGENT_ALLOW_DESTRUCTIVE_DB_TESTS=true for destructive tests
  - Third safety gate: DB name must contain test/testing/ci, or
    CONVAGENT_TEST_DB_CONFIRMED must match the actual database name.

All tests in this module are SERIAL-ONLY — they must not run under
pytest-xdist or any parallel runner.
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import urlparse

from concurrent.futures import ThreadPoolExecutor
import pytest
import pytest_asyncio
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

pytestmark = [pytest.mark.postgres_integration, pytest.mark.enable_socket]

# ═══════════════════════════════════════════════════════════════════════════════
# Safety gates
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_dbname(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path.lstrip("/").split("?")[0]


@pytest.fixture(scope="session")
def postgres_test_url() -> str:
    url = os.getenv("CONVAGENT_POSTGRES_TEST_URL", "").strip()
    if not url:
        pytest.skip("CONVAGENT_POSTGRES_TEST_URL not set")
    # Must differ from application DB URL
    app_url = os.getenv("CONVAGENT_DATABASE_URL", "").strip()
    if app_url and url == app_url:
        pytest.skip(
            "CONVAGENT_POSTGRES_TEST_URL must differ from "
            "CONVAGENT_DATABASE_URL — refusing to use application database"
        )
    return url


@pytest.fixture(scope="session")
def destructive_allowed(postgres_test_url: str) -> bool:
    """Triple-gate check for destructive migration operations."""
    # Gate 1: URL must be from CONVAGENT_POSTGRES_TEST_URL (already satisfied)
    # Gate 2: Explicit opt-in
    if os.getenv("CONVAGENT_ALLOW_DESTRUCTIVE_DB_TESTS", "").strip().lower() != "true":
        return False
    # Gate 3: DB name must contain test/testing/ci, or exact confirmation match
    dbname = _extract_dbname(postgres_test_url)
    confirmed = os.getenv("CONVAGENT_TEST_DB_CONFIRMED", "").strip()
    if confirmed and confirmed == dbname:
        return True
    if any(kw in dbname.lower() for kw in ("test", "testing", "ci")):
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Alembic config (programmatic injection — never mutates os.environ)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="session")
def alembic_cfg(postgres_test_url: str) -> AlembicConfig:
    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", postgres_test_url)
    return cfg


# ═══════════════════════════════════════════════════════════════════════════════
# Async engine for inspection queries (NullPool — no pooling for tests)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="session")
def ensure_upgrade_head(alembic_cfg: AlembicConfig):
    """Session-scoped: ensure database is at head before any test runs."""
    command.upgrade(alembic_cfg, "head")


@pytest_asyncio.fixture
async def db_engine(postgres_test_url: str) -> AsyncIterator[AsyncEngine]:
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(postgres_test_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        await session.execute(
            text(
                "TRUNCATE TABLE idempotency_records, audit_events, "
                "agent_runs, agent_requests RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
        yield session


# ═══════════════════════════════════════════════════════════════════════════════
# Helper — get inspector
# ═══════════════════════════════════════════════════════════════════════════════


async def _get_inspector(db_engine: AsyncEngine):
    async with db_engine.connect() as conn:
        return await conn.run_sync(
            lambda sync_conn: inspect(sync_conn)
        )


async def _get_columns(db_engine: AsyncEngine, table: str) -> dict[str, dict]:
    async with db_engine.connect() as conn:
        cols = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns(table)
        )
    return {c["name"]: c for c in cols}


async def _get_check_constraints(
    db_engine: AsyncEngine, table: str
) -> list[dict]:
    async with db_engine.connect() as conn:
        return await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_check_constraints(table)
        )


async def _get_unique_constraints(
    db_engine: AsyncEngine, table: str
) -> list[dict]:
    async with db_engine.connect() as conn:
        return await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_unique_constraints(table)
        )


async def _get_foreign_keys(
    db_engine: AsyncEngine, table: str
) -> list[dict]:
    async with db_engine.connect() as conn:
        return await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_foreign_keys(table)
        )


async def _get_indexes(
    db_engine: AsyncEngine, table: str
) -> list[dict]:
    async with db_engine.connect() as conn:
        return await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes(table)
        )


async def _get_primary_key(db_engine: AsyncEngine, table: str) -> dict:
    async with db_engine.connect() as conn:
        return await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_pk_constraint(table)
        )


async def _table_names(db_engine: AsyncEngine) -> set[str]:
    async with db_engine.connect() as conn:
        return set(
            await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
        )


def _constraint_name(exc: IntegrityError) -> str:
    """Return asyncpg's structured constraint name or fail the assertion."""
    cause = getattr(exc.orig, "__cause__", None)
    name = getattr(cause, "constraint_name", None)
    assert isinstance(name, str) and name, (
        "asyncpg did not expose a structured constraint_name"
    )
    return name


@asynccontextmanager
async def _expect_constraint(
    session: AsyncSession, expected_name: str
) -> AsyncIterator[None]:
    try:
        with pytest.raises(IntegrityError) as exc_info:
            yield
        assert _constraint_name(exc_info.value) == expected_name
    finally:
        await session.rollback()


def _canonical_default(value: object | None) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip().lower().replace(" ", "")
    while rendered.startswith("(") and rendered.endswith(")"):
        rendered = rendered[1:-1]
    rendered = re.sub(r"::(?:integer|bigint|smallint)$", "", rendered)
    if rendered in {"1", "'1'"}:
        return "1"
    if rendered in {"now()", "current_timestamp"}:
        return "now()"
    return rendered


def _expected_column_default(table, column) -> object | None:
    if column.server_default is not None:
        return column.server_default.arg
    if column.primary_key and column.autoincrement in {True, "auto"}:
        return f"nextval('{table.name}_{column.name}_seq'::regclass)"
    return None


def _compare_server_default(
    context,
    inspected_column,
    metadata_column,
    inspected_default,
    metadata_default,
    rendered_metadata_default,
):
    del context, inspected_column, metadata_column, metadata_default
    inspected = _canonical_default(inspected_default)
    metadata = _canonical_default(rendered_metadata_default)
    known = {None, "1", "now()"}
    if inspected in known and metadata in known:
        return inspected != metadata
    return None


def _include_business_schema(
    obj, name: str | None, type_: str, reflected: bool, compare_to
) -> bool:
    del obj, reflected, compare_to
    return not (type_ == "table" and name == "alembic_version")


def _type_signature(type_: object) -> tuple[object, ...]:
    if isinstance(type_, JSONB):
        return ("jsonb",)
    if isinstance(type_, JSON):
        return ("json",)
    if isinstance(type_, String):
        return ("varchar", type_.length)
    if isinstance(type_, Integer):
        return ("integer",)
    if isinstance(type_, DateTime):
        return ("timestamp", bool(type_.timezone))
    if isinstance(type_, Float):
        return ("float",)
    raise AssertionError(f"Unsupported PostgreSQL type in schema signature: {type_!r}")


def _metadata_unique_constraints(table) -> dict[str, tuple[str, ...]]:
    constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    column_sets = [tuple(column.name for column in item.columns) for item in constraints]
    assert len(column_sets) == len(set(column_sets)), (
        f"Duplicate ORM unique constraint columns in {table.name}: {column_sets}"
    )
    return {
        str(item.name): tuple(column.name for column in item.columns)
        for item in constraints
    }


def _metadata_foreign_keys(table) -> dict[tuple[str, ...], tuple[object, ...]]:
    result: dict[tuple[str, ...], tuple[object, ...]] = {}
    for constraint in table.constraints:
        if not isinstance(constraint, ForeignKeyConstraint):
            continue
        local = tuple(column.name for column in constraint.columns)
        elements = tuple(constraint.elements)
        result[local] = (
            elements[0].column.table.name,
            tuple(element.column.name for element in elements),
            constraint.ondelete,
        )
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Schema tests (require ensure_upgrade_head fixture)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.usefixtures("ensure_upgrade_head")
@pytest.mark.asyncio
class TestUpgradeCreatesTables:
    async def test_four_tables_exist(self, db_engine: AsyncEngine):
        names = await _table_names(db_engine)
        for t in ("agent_requests", "agent_runs", "audit_events", "idempotency_records"):
            assert t in names, f"Table {t} missing"


@pytest.mark.usefixtures("ensure_upgrade_head")
@pytest.mark.asyncio
class TestAgentRequestsSchema:
    async def test_all_columns_present(self, db_engine: AsyncEngine):
        cols = await _get_columns(db_engine, "agent_requests")
        expected = {
            "id", "request_id", "trace_id", "session_id", "operation",
            "principal_user_id", "tenant_id", "organization_id", "status",
            "user_text_hash", "user_text_length", "idempotency_key_hash",
            "request_fingerprint", "fingerprint_version",
            "replayed_from_request_id", "authorization_snapshot",
            "authorization_snapshot_schema_version", "failure_code",
            "created_at", "completed_at",
        }
        actual = set(cols.keys())
        missing = expected - actual
        extra = actual - expected
        assert not missing, f"Missing columns: {missing}"
        assert not extra, f"Extra columns: {extra}"

    async def test_required_columns_not_nullable(self, db_engine: AsyncEngine):
        cols = await _get_columns(db_engine, "agent_requests")
        required = {"request_id", "trace_id", "operation", "principal_user_id",
                     "tenant_id", "organization_id", "status", "user_text_hash",
                     "user_text_length", "request_fingerprint", "fingerprint_version",
                     "authorization_snapshot", "authorization_snapshot_schema_version"}
        for name in required:
            assert not cols[name]["nullable"], f"{name} should be NOT NULL"

    async def test_nullable_columns(self, db_engine: AsyncEngine):
        cols = await _get_columns(db_engine, "agent_requests")
        nullable = {"session_id", "idempotency_key_hash", "replayed_from_request_id",
                     "failure_code", "completed_at"}
        for name in nullable:
            assert cols[name]["nullable"], f"{name} should be nullable"

    async def test_user_text_length_check_rejects_negative(self, db_session: AsyncSession):
        async with _expect_constraint(
            db_session, "ck_agent_requests_user_text_length_nonneg"
        ):
            await db_session.execute(
                text(
                    "INSERT INTO agent_requests "
                    "(request_id, trace_id, operation, principal_user_id, "
                    "tenant_id, organization_id, status, user_text_hash, "
                    "user_text_length, request_fingerprint, "
                    "authorization_snapshot) "
                    "VALUES "
                    "('r1', 't1', 'POST:/v1/chat', 'u1', "
                    "'t', 'o', 'in_progress', "
                    "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                    "-1, "
                    "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                    "'{}'::jsonb)"
                )
            )
            await db_session.commit()

    async def test_fingerprint_hex_check_rejects_non_hex(self, db_session: AsyncSession):
        async with _expect_constraint(
            db_session, "ck_agent_requests_fingerprint_hex"
        ):
            await db_session.execute(
                text(
                    "INSERT INTO agent_requests "
                    "(request_id, trace_id, operation, principal_user_id, "
                    "tenant_id, organization_id, status, user_text_hash, "
                    "user_text_length, request_fingerprint, "
                    "authorization_snapshot) "
                    "VALUES "
                    "('r2', 't2', 'POST:/v1/chat', 'u1', "
                    "'t', 'o', 'in_progress', "
                    "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                    "0, "
                    "'NOT_HEX_ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ', "
                    "'{}'::jsonb)"
                )
            )
            await db_session.commit()

    async def test_user_text_hash_hex_check(self, db_session: AsyncSession):
        async with _expect_constraint(
            db_session, "ck_agent_requests_user_text_hash_hex"
        ):
            await db_session.execute(
                text(
                    "INSERT INTO agent_requests "
                    "(request_id, trace_id, operation, principal_user_id, "
                    "tenant_id, organization_id, status, user_text_hash, "
                    "user_text_length, request_fingerprint, "
                    "authorization_snapshot) "
                    "VALUES "
                    "('r3', 't3', 'POST:/v1/chat', 'u1', "
                    "'t', 'o', 'in_progress', "
                    "'NOT_HEX_ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ', "
                    "0, "
                    "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                    "'{}'::jsonb)"
                )
            )
            await db_session.commit()

    async def test_idempotency_key_hash_nullable_or_hex(
        self, db_session: AsyncSession
    ):
        # NULL should be allowed
        await db_session.execute(
            text(
                "INSERT INTO agent_requests "
                "(request_id, trace_id, operation, principal_user_id, "
                "tenant_id, organization_id, status, user_text_hash, "
                "user_text_length, request_fingerprint, idempotency_key_hash, "
                "authorization_snapshot) "
                "VALUES "
                "('r4', 't4', 'POST:/v1/chat', 'u1', "
                "'t', 'o', 'in_progress', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                "0, "
                "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                "NULL, "
                "'{}'::jsonb)"
            )
        )
        await db_session.rollback()
        # Non-hex non-null should be rejected
        async with _expect_constraint(
            db_session, "ck_agent_requests_idempotency_key_hash_hex"
        ):
            await db_session.execute(
                text(
                    "INSERT INTO agent_requests "
                    "(request_id, trace_id, operation, principal_user_id, "
                    "tenant_id, organization_id, status, user_text_hash, "
                    "user_text_length, request_fingerprint, idempotency_key_hash, "
                    "authorization_snapshot) "
                    "VALUES "
                    "('r5', 't5', 'POST:/v1/chat', 'u1', "
                    "'t', 'o', 'in_progress', "
                    "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                    "0, "
                    "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                    "'NOT_HEX', "
                    "'{}'::jsonb)"
                )
            )
            await db_session.commit()

    async def test_version_fields_minimum(self, db_session: AsyncSession):
        # fingerprint_version < 1 should be rejected
        async with _expect_constraint(
            db_session, "ck_agent_requests_fingerprint_version_min"
        ):
            await db_session.execute(
                text(
                    "INSERT INTO agent_requests "
                    "(request_id, trace_id, operation, principal_user_id, "
                    "tenant_id, organization_id, status, user_text_hash, "
                    "user_text_length, request_fingerprint, "
                    "fingerprint_version, authorization_snapshot) "
                    "VALUES "
                    "('r6', 't6', 'POST:/v1/chat', 'u1', "
                    "'t', 'o', 'in_progress', "
                    "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                    "0, "
                    "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                    "0, '{}'::jsonb)"
                )
            )
            await db_session.commit()

    async def test_completed_after_created_constraint(
        self, db_session: AsyncSession
    ):
        await db_session.execute(
            text(
                "INSERT INTO agent_requests "
                "(request_id, trace_id, operation, principal_user_id, "
                "tenant_id, organization_id, status, user_text_hash, "
                "user_text_length, request_fingerprint, "
                "authorization_snapshot) "
                "VALUES "
                "('r7', 't7', 'POST:/v1/chat', 'u1', "
                "'t', 'o', 'in_progress', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                "0, "
                "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                "'{}'::jsonb)"
            )
        )
        await db_session.commit()
        async with _expect_constraint(
            db_session, "ck_agent_requests_completed_after_created"
        ):
            await db_session.execute(
                text(
                    "UPDATE agent_requests "
                    "SET completed_at = created_at - INTERVAL '1 second' "
                    "WHERE request_id = 'r7'"
                )
            )
            await db_session.commit()

    async def test_unique_request_id(self, db_session: AsyncSession):
        await db_session.execute(
            text(
                "INSERT INTO agent_requests "
                "(request_id, trace_id, operation, principal_user_id, "
                "tenant_id, organization_id, status, user_text_hash, "
                "user_text_length, request_fingerprint, "
                "authorization_snapshot) "
                "VALUES "
                "('r-uniq', 't1', 'POST:/v1/chat', 'u1', "
                "'t', 'o', 'in_progress', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                "0, "
                "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                "'{}'::jsonb)"
            )
        )
        await db_session.commit()
        async with _expect_constraint(db_session, "uq_agent_requests_request_id"):
            await db_session.execute(
                text(
                    "INSERT INTO agent_requests "
                    "(request_id, trace_id, operation, principal_user_id, "
                    "tenant_id, organization_id, status, user_text_hash, "
                    "user_text_length, request_fingerprint, "
                    "authorization_snapshot) "
                    "VALUES "
                    "('r-uniq', 't2', 'POST:/v1/chat', 'u1', "
                    "'t', 'o', 'in_progress', "
                    "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                    "0, "
                    "'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', "
                    "'{}'::jsonb)"
                )
            )
            await db_session.commit()

    async def test_replay_self_fk(self, db_session: AsyncSession):
        # Insert original
        result = await db_session.execute(
            text(
                "INSERT INTO agent_requests "
                "(request_id, trace_id, operation, principal_user_id, "
                "tenant_id, organization_id, status, user_text_hash, "
                "user_text_length, request_fingerprint, "
                "authorization_snapshot) "
                "VALUES "
                "('r-orig', 't1', 'POST:/v1/chat', 'u1', "
                "'t', 'o', 'completed', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                "0, "
                "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                "'{}'::jsonb) "
                "RETURNING id"
            )
        )
        orig_id = result.scalar_one()
        await db_session.commit()
        # Insert replayed referencing original
        await db_session.execute(
            text(
                "INSERT INTO agent_requests "
                "(request_id, trace_id, operation, principal_user_id, "
                "tenant_id, organization_id, status, user_text_hash, "
                "user_text_length, request_fingerprint, "
                "replayed_from_request_id, authorization_snapshot) "
                "VALUES "
                "('r-replay', 't2', 'POST:/v1/chat', 'u1', "
                "'t', 'o', 'completed', "
                "'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', "
                "0, "
                "'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd', "
                f"{orig_id}, '{{}}'::jsonb)"
            )
        )
        await db_session.commit()
        async with _expect_constraint(
            db_session,
            "fk_agent_requests_replayed_from_request_id_agent_requests",
        ):
            await db_session.execute(
                text(
                    "INSERT INTO agent_requests "
                    "(request_id, trace_id, operation, principal_user_id, "
                    "tenant_id, organization_id, status, user_text_hash, "
                    "user_text_length, request_fingerprint, "
                    "replayed_from_request_id, authorization_snapshot) "
                    "VALUES "
                    "('r-replay-bad', 't3', 'POST:/v1/chat', 'u1', "
                    "'t', 'o', 'completed', "
                    "'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', "
                    "0, "
                    "'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', "
                    "999999999, '{}'::jsonb)"
                )
            )
            await db_session.commit()


@pytest.mark.usefixtures("ensure_upgrade_head")
@pytest.mark.asyncio
class TestAgentRunsSchema:
    async def test_all_columns_present(self, db_engine: AsyncEngine):
        cols = await _get_columns(db_engine, "agent_runs")
        expected = {
            "id", "run_id", "original_request_id", "session_id", "status",
            "routed_task", "policy_outcome", "result_snapshot",
            "result_snapshot_schema_version", "confidence", "trace_snapshot",
            "trace_snapshot_schema_version", "rag_provider",
            "started_at", "completed_at",
        }
        actual = set(cols.keys())
        missing = expected - actual
        assert not missing, f"Missing columns: {missing}"

    async def test_unique_run_id(self, db_session: AsyncSession):
        # Need a request first
        await db_session.execute(
            text(
                "INSERT INTO agent_requests "
                "(request_id, trace_id, operation, principal_user_id, "
                "tenant_id, organization_id, status, user_text_hash, "
                "user_text_length, request_fingerprint, "
                "authorization_snapshot) "
                "VALUES "
                "('r-run1', 't1', 'POST:/v1/chat', 'u1', "
                "'t', 'o', 'in_progress', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                "0, "
                "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                "'{}'::jsonb)"
            )
        )
        await db_session.commit()
        # Insert run
        await db_session.execute(
            text(
                "INSERT INTO agent_runs "
                "(run_id, original_request_id, status) "
                "VALUES "
                "('run-uniq', "
                "(SELECT id FROM agent_requests WHERE request_id = 'r-run1'), "
                "'completed')"
            )
        )
        await db_session.commit()
        # Duplicate run_id should fail
        async with _expect_constraint(db_session, "uq_agent_runs_run_id"):
            await db_session.execute(
                text(
                    "INSERT INTO agent_runs "
                    "(run_id, original_request_id, status) "
                    "VALUES "
                    "('run-uniq', "
                    "(SELECT id FROM agent_requests WHERE request_id = 'r-run1'), "
                    "'completed')"
                )
            )
            await db_session.commit()

    async def test_1to1_original_request_id(self, db_session: AsyncSession):
        # Insert a second request
        await db_session.execute(
            text(
                "INSERT INTO agent_requests "
                "(request_id, trace_id, operation, principal_user_id, "
                "tenant_id, organization_id, status, user_text_hash, "
                "user_text_length, request_fingerprint, "
                "authorization_snapshot) "
                "VALUES "
                "('r-1to1', 't1', 'POST:/v1/chat', 'u1', "
                "'t', 'o', 'in_progress', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                "0, "
                "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                "'{}'::jsonb)"
            )
        )
        await db_session.commit()
        # First run
        await db_session.execute(
            text(
                "INSERT INTO agent_runs "
                "(run_id, original_request_id, status) "
                "VALUES "
                "('run-1to1-1', "
                "(SELECT id FROM agent_requests WHERE request_id = 'r-1to1'), "
                "'completed')"
            )
        )
        await db_session.commit()
        # Second run for same request should fail
        async with _expect_constraint(db_session, "uq_agent_runs_request_id"):
            await db_session.execute(
                text(
                    "INSERT INTO agent_runs "
                    "(run_id, original_request_id, status) "
                    "VALUES "
                    "('run-1to1-2', "
                    "(SELECT id FROM agent_requests WHERE request_id = 'r-1to1'), "
                    "'completed')"
                )
            )
            await db_session.commit()

    async def test_status_check(self, db_session: AsyncSession):
        await db_session.execute(
            text(
                "INSERT INTO agent_requests "
                "(request_id, trace_id, operation, principal_user_id, "
                "tenant_id, organization_id, status, user_text_hash, "
                "user_text_length, request_fingerprint, "
                "authorization_snapshot) "
                "VALUES "
                "('r-status', 't1', 'POST:/v1/chat', 'u1', "
                "'t', 'o', 'in_progress', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                "0, "
                "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                "'{}'::jsonb)"
            )
        )
        await db_session.commit()
        async with _expect_constraint(db_session, "ck_agent_runs_status_values"):
            await db_session.execute(
                text(
                    "INSERT INTO agent_runs "
                    "(run_id, original_request_id, status) "
                    "VALUES "
                    "('run-bad-status', "
                    "(SELECT id FROM agent_requests WHERE request_id = 'r-status'), "
                    "'nonexistent_status')"
                )
            )
            await db_session.commit()

    async def test_confidence_range(self, db_session: AsyncSession):
        # confidence < 0 should fail
        await db_session.execute(
            text(
                "INSERT INTO agent_requests "
                "(request_id, trace_id, operation, principal_user_id, "
                "tenant_id, organization_id, status, user_text_hash, "
                "user_text_length, request_fingerprint, "
                "authorization_snapshot) "
                "VALUES "
                "('r-conf', 't1', 'POST:/v1/chat', 'u1', "
                "'t', 'o', 'in_progress', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                "0, "
                "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                "'{}'::jsonb)"
            )
        )
        await db_session.commit()
        async with _expect_constraint(db_session, "ck_agent_runs_confidence_range"):
            await db_session.execute(
                text(
                    "INSERT INTO agent_runs "
                    "(run_id, original_request_id, status, confidence) "
                    "VALUES "
                    "('run-conf-neg', "
                    "(SELECT id FROM agent_requests WHERE request_id = 'r-conf'), "
                    "'completed', -0.1)"
                )
            )
            await db_session.commit()

    async def test_snapshot_version_pairing(self, db_session: AsyncSession):
        # Setup: insert a valid agent_requests row
        await db_session.execute(
            text(
                "INSERT INTO agent_requests "
                "(request_id, trace_id, operation, principal_user_id, "
                "tenant_id, organization_id, status, user_text_hash, "
                "user_text_length, request_fingerprint, "
                "authorization_snapshot) "
                "VALUES "
                "('r-snap', 't1', 'POST:/v1/chat', 'u1', "
                "'t', 'o', 'in_progress', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                "0, "
                "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                "'{}'::jsonb)"
            )
        )
        await db_session.commit()

        # Get the ID of a valid request
        result = await db_session.execute(
            text("SELECT id FROM agent_requests WHERE request_id = 'r-snap'")
        )
        req_id = result.scalar_one()
        await db_session.commit()

        async with _expect_constraint(
            db_session, "ck_agent_runs_result_snapshot_version"
        ):
            await db_session.execute(
                text(
                    "INSERT INTO agent_runs "
                    "(run_id, original_request_id, status, "
                    "result_snapshot, result_snapshot_schema_version) "
                    "VALUES "
                    "('run-snap-bad', "
                    f"{req_id}, "
                    "'completed', '{\"a\": 1}'::jsonb, NULL)"
                )
            )
            await db_session.commit()


@pytest.mark.usefixtures("ensure_upgrade_head")
@pytest.mark.asyncio
class TestIdempotencyRecordsSchema:
    async def test_scoped_unique_constraint(self, db_session: AsyncSession):
        base = (
            "INSERT INTO idempotency_records "
            "(tenant_id, organization_id, principal_user_id, operation, "
            "idempotency_key_hash, request_fingerprint, status, "
            "claim_version, owner_request_id, lease_expires_at, expires_at) "
            "VALUES "
            "('t1', 'o1', 'u1', 'POST:/v1/chat', "
            "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
            "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
            "'in_progress', 1, 'req-1', NOW() + INTERVAL '5 minutes', "
            "NOW() + INTERVAL '1 hour')"
        )
        await db_session.execute(text(base))
        await db_session.commit()
        # Same 5-field key → should fail
        async with _expect_constraint(db_session, "uq_idempotency_records_scope"):
            await db_session.execute(text(base))
            await db_session.commit()

    async def test_different_principal_same_key_hash_coexist(
        self, db_session: AsyncSession
    ):
        insert = (
            "INSERT INTO idempotency_records "
            "(tenant_id, organization_id, principal_user_id, operation, "
            "idempotency_key_hash, request_fingerprint, status, "
            "claim_version, owner_request_id, lease_expires_at, expires_at) "
            "VALUES "
            "('t1', 'o1', '{principal}', 'POST:/v1/chat', "
            "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
            "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
            "'in_progress', 1, 'req-a', NOW() + INTERVAL '5 minutes', "
            "NOW() + INTERVAL '1 hour')"
        )
        await db_session.execute(text(insert.format(principal="u-alpha")))
        await db_session.commit()
        await db_session.execute(text(insert.format(principal="u-beta")))
        await db_session.commit()

    async def test_claim_version_min_check(self, db_session: AsyncSession):
        async with _expect_constraint(
            db_session, "ck_idempotency_records_claim_version_min"
        ):
            await db_session.execute(
                text(
                    "INSERT INTO idempotency_records "
                    "(tenant_id, organization_id, principal_user_id, operation, "
                    "idempotency_key_hash, request_fingerprint, status, "
                    "claim_version, owner_request_id, lease_expires_at, expires_at) "
                    "VALUES "
                    "('t1', 'o1', 'u-cv', 'POST:/v1/chat', "
                    "'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', "
                    "'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd', "
                    "'in_progress', 0, 'req-cv', NOW() + INTERVAL '5 minutes', "
                    "NOW() + INTERVAL '1 hour')"
                )
            )
            await db_session.commit()

    async def test_key_hash_hex_check(self, db_session: AsyncSession):
        async with _expect_constraint(
            db_session, "ck_idempotency_records_key_hash_hex"
        ):
            await db_session.execute(
                text(
                    "INSERT INTO idempotency_records "
                    "(tenant_id, organization_id, principal_user_id, operation, "
                    "idempotency_key_hash, request_fingerprint, status, "
                    "claim_version, owner_request_id, lease_expires_at, expires_at) "
                    "VALUES "
                    "('t1', 'o1', 'u1', 'POST:/v1/chat', "
                    "'NOT_A_HEX_STRING_ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ', "
                    "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                    "'in_progress', 1, 'req-hex', NOW() + INTERVAL '5 minutes', "
                    "NOW() + INTERVAL '1 hour')"
                )
            )
            await db_session.commit()

    async def test_fingerprint_hex_check(self, db_session: AsyncSession):
        async with _expect_constraint(
            db_session, "ck_idempotency_records_fingerprint_hex"
        ):
            await db_session.execute(
                text(
                    "INSERT INTO idempotency_records "
                    "(tenant_id, organization_id, principal_user_id, operation, "
                    "idempotency_key_hash, request_fingerprint, status, "
                    "claim_version, owner_request_id, lease_expires_at, expires_at) "
                    "VALUES "
                    "('t1', 'o1', 'u1', 'POST:/v1/chat', "
                    "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                    "'NOT_HEX_ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ', "
                    "'in_progress', 1, 'req-fp', NOW() + INTERVAL '5 minutes', "
                    "NOW() + INTERVAL '1 hour')"
                )
            )
            await db_session.commit()

    async def test_lease_after_claimed_check(self, db_session: AsyncSession):
        async with _expect_constraint(
            db_session, "ck_idempotency_records_lease_after_claimed"
        ):
            await db_session.execute(
                text(
                    "INSERT INTO idempotency_records "
                    "(tenant_id, organization_id, principal_user_id, operation, "
                    "idempotency_key_hash, request_fingerprint, status, "
                    "claim_version, owner_request_id, claimed_at, "
                    "lease_expires_at, expires_at) "
                    "VALUES "
                    "('t1', 'o1', 'u1', 'POST:/v1/chat', "
                    "'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', "
                    "'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff', "
                    "'in_progress', 1, 'req-la', NOW() + INTERVAL '10 minutes', "
                    "NOW() + INTERVAL '5 minutes', "
                    "NOW() + INTERVAL '1 hour')"
                )
            )
            await db_session.commit()

    async def test_expires_after_created_check(self, db_session: AsyncSession):
        async with _expect_constraint(
            db_session, "ck_idempotency_records_expires_after_created"
        ):
            await db_session.execute(
                text(
                    "INSERT INTO idempotency_records "
                    "(tenant_id, organization_id, principal_user_id, operation, "
                    "idempotency_key_hash, request_fingerprint, status, "
                    "claim_version, owner_request_id, created_at, "
                    "lease_expires_at, expires_at) "
                    "VALUES "
                    "('t1', 'o1', 'u1', 'POST:/v1/chat', "
                    "'1111111111111111111111111111111111111111111111111111111111111111', "
                    "'2222222222222222222222222222222222222222222222222222222222222222', "
                    "'in_progress', 1, 'req-ea', NOW() + INTERVAL '1 hour', "
                    "NOW() + INTERVAL '2 hours', "
                    "NOW())"
                )
            )
            await db_session.commit()

    async def test_response_snapshot_version_pairing(self, db_session: AsyncSession):
        async with _expect_constraint(
            db_session, "ck_idempotency_records_response_snapshot_version"
        ):
            await db_session.execute(
                text(
                    "INSERT INTO idempotency_records "
                    "(tenant_id, organization_id, principal_user_id, operation, "
                    "idempotency_key_hash, request_fingerprint, status, "
                    "claim_version, owner_request_id, "
                    "response_snapshot, response_snapshot_schema_version, "
                    "lease_expires_at, expires_at) "
                    "VALUES "
                    "('t1', 'o1', 'u1', 'POST:/v1/chat', "
                    "'3333333333333333333333333333333333333333333333333333333333333333', "
                    "'4444444444444444444444444444444444444444444444444444444444444444', "
                    "'in_progress', 1, 'req-rs', "
                    "'{\"result\": 1}'::jsonb, NULL, "
                    "NOW() + INTERVAL '5 minutes', "
                    "NOW() + INTERVAL '1 hour')"
                )
            )
            await db_session.commit()


@pytest.mark.usefixtures("ensure_upgrade_head")
@pytest.mark.asyncio
class TestCommonSchema:
    async def test_timestamptz_columns(self, db_engine: AsyncEngine):
        """All _at columns should use TIMESTAMP WITH TIME ZONE."""
        for table in await _table_names(db_engine):
            cols = await _get_columns(db_engine, table)
            for name, info in cols.items():
                if name.endswith("_at"):
                    assert getattr(info["type"], "timezone", False), (
                        f"{table}.{name} is {info['type']}, expected TIMESTAMPTZ"
                    )

    async def test_jsonb_columns(self, db_engine: AsyncEngine):
        from conversation_agent.database.models import Base

        jsonb_cols = {
            ("agent_requests", "authorization_snapshot"),
            ("agent_runs", "result_snapshot"),
            ("agent_runs", "trace_snapshot"),
            ("audit_events", "details_json"),
            ("idempotency_records", "response_snapshot"),
        }
        for table, col in jsonb_cols:
            cols = await _get_columns(db_engine, table)
            assert isinstance(cols[col]["type"], JSONB), (
                f"{table}.{col} is {cols[col]['type']!r}, expected JSONB"
            )
            assert isinstance(Base.metadata.tables[table].columns[col].type, JSONB)

    async def test_no_forbidden_columns(self, db_engine: AsyncEngine):
        forbidden = {
            "raw_response", "jwt", "token", "claims", "email",
            "jwks", "key_material", "debug", "stack_trace",
            "provider_sdk_response", "idempotency_key",
        }
        for table in await _table_names(db_engine):
            cols = await _get_columns(db_engine, table)
            found = forbidden & set(cols.keys())
            assert not found, f"Table {table} has forbidden columns: {found}"

    async def test_orm_matches_alembic_schema(self, db_engine: AsyncEngine):
        """Verify Alembic reports no ORM/database metadata drift."""
        from conversation_agent.database.models import Base

        def _compare(sync_conn):
            context = MigrationContext.configure(
                sync_conn,
                opts={
                    "compare_type": True,
                    "compare_server_default": _compare_server_default,
                    "include_object": _include_business_schema,
                },
            )
            return compare_metadata(context, Base.metadata)

        async with db_engine.connect() as conn:
            differences = await conn.run_sync(_compare)
        assert differences == []

    async def test_exact_schema_signature(self, db_engine: AsyncEngine):
        """Compare exact columns, constraints, foreign keys, and indexes."""
        from conversation_agent.database.models import Base

        expected_tables = {
            "agent_requests",
            "agent_runs",
            "audit_events",
            "idempotency_records",
        }
        actual_tables = await _table_names(db_engine)
        assert actual_tables - {"alembic_version"} == expected_tables
        assert set(Base.metadata.tables) == expected_tables

        for table_name in sorted(expected_tables):
            orm_table = Base.metadata.tables[table_name]
            actual_columns = await _get_columns(db_engine, table_name)
            assert set(actual_columns) == {
                column.name for column in orm_table.columns
            }

            for column in orm_table.columns:
                reflected = actual_columns[column.name]
                assert _type_signature(reflected["type"]) == _type_signature(column.type)
                assert reflected["nullable"] is column.nullable
                orm_default = _expected_column_default(orm_table, column)
                assert _canonical_default(reflected["default"]) == _canonical_default(
                    orm_default
                )

            actual_pk = await _get_primary_key(db_engine, table_name)
            assert actual_pk["name"] == orm_table.primary_key.name
            assert tuple(actual_pk["constrained_columns"]) == tuple(
                column.name for column in orm_table.primary_key.columns
            )

            actual_unique_items = await _get_unique_constraints(db_engine, table_name)
            actual_unique = {
                str(item["name"]): tuple(item["column_names"])
                for item in actual_unique_items
            }
            actual_column_sets = list(actual_unique.values())
            assert len(actual_column_sets) == len(set(actual_column_sets))
            assert actual_unique == _metadata_unique_constraints(orm_table)

            actual_foreign_keys = {
                tuple(item["constrained_columns"]): (
                    item["referred_table"],
                    tuple(item["referred_columns"]),
                    item.get("options", {}).get("ondelete"),
                )
                for item in await _get_foreign_keys(db_engine, table_name)
            }
            assert actual_foreign_keys == _metadata_foreign_keys(orm_table)

            actual_checks = {
                str(item["name"])
                for item in await _get_check_constraints(db_engine, table_name)
            }
            metadata_checks = {
                str(item.name)
                for item in orm_table.constraints
                if isinstance(item, CheckConstraint)
            }
            assert actual_checks == metadata_checks

            actual_indexes = {
                str(item["name"]): (
                    tuple(item["column_names"]),
                    bool(item["unique"]),
                )
                for item in await _get_indexes(db_engine, table_name)
                if not item.get("duplicates_constraint")
            }
            metadata_indexes = {
                str(item.name): (
                    tuple(column.name for column in item.columns),
                    bool(item.unique),
                )
                for item in orm_table.indexes
            }
            assert actual_indexes == metadata_indexes


# ═══════════════════════════════════════════════════════════════════════════════
# Destructive tests (triple-gate protected)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.usefixtures("ensure_upgrade_head")
@pytest.mark.asyncio
class TestDestructiveMigrations:
    """Tests requiring CONVAGENT_ALLOW_DESTRUCTIVE_DB_TESTS=true + test DB name."""

    @pytest.fixture(autouse=True)
    def _require_destructive(self, destructive_allowed: bool):
        if not destructive_allowed:
            pytest.skip(
                "Destructive migration tests require: "
                "CONVAGENT_ALLOW_DESTRUCTIVE_DB_TESTS=true AND "
                "database name containing 'test'/'testing'/'ci' OR "
                "CONVAGENT_TEST_DB_CONFIRMED matching the database name"
            )

    async def test_downgrade_removes_four_business_tables(
        self, alembic_cfg: AlembicConfig, postgres_test_url: str
    ):
        from sqlalchemy.pool import NullPool

        def _downgrade():
            command.downgrade(alembic_cfg, "base")

        def _upgrade():
            command.upgrade(alembic_cfg, "head")

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(_downgrade).result()
            engine = create_async_engine(postgres_test_url, poolclass=NullPool)
            try:
                async with engine.connect() as conn:
                    result = await conn.execute(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables "
                            "WHERE schemaname NOT IN "
                            "('pg_catalog', 'information_schema')"
                        )
                    )
                    remaining = {row[0] for row in result.fetchall()}
                business = {"agent_requests", "agent_runs", "audit_events", "idempotency_records"}
                assert business & remaining == set(), (
                    f"Business tables still present after downgrade: {business & remaining}"
                )
            finally:
                await engine.dispose()
        finally:
            # Restore head regardless of outcome
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(_upgrade).result()

    async def test_upgrade_downgrade_upgrade_cycle(
        self, alembic_cfg: AlembicConfig
    ):
        def _downgrade():
            command.downgrade(alembic_cfg, "base")

        def _upgrade():
            command.upgrade(alembic_cfg, "head")

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(_downgrade).result()
                pool.submit(_upgrade).result()
        finally:
            # Ensure head is restored
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(_upgrade).result()
