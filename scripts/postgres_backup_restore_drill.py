"""Bounded local logical backup/restore drill using PostgreSQL client tools.

This is a local validation tool, not a production backup system. Credentials
are passed only through the child-process environment and are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from conversation_agent.database.models import (
    AgentRequest,
    AgentRun,
    AuditEvent,
    IdempotencyRecord,
)
from conversation_agent.application.idempotency_mappers import ReplaySnapshotMapper
from conversation_agent.authorization.models import AuthorizationDecision, ResourceScope
from conversation_agent.identity.models import Principal
from conversation_agent.operations import PersistenceIntegrityChecker
from conversation_agent.runtime.models import RequestContext, RuntimeVersionSnapshot


REQUIRED_TOOLS = ("pg_dump", "pg_restore", "createdb", "dropdb")

_OPERATION_FAILURE_TYPES: dict[str, str] = {
    "create_source_database": "source_database_create_failed",
    "pg_dump": "pg_dump_failed",
    "create_restore_database": "restore_database_create_failed",
    "pg_restore": "pg_restore_failed",
    "drop_restore_database": "cleanup_failed",
    "drop_source_database": "cleanup_failed",
}


# ── Structured Exception ──────────────────────────────────────────────────────


class PostgreSQLOperationError(RuntimeError):
    """Structured diagnostic for a PostgreSQL client tool failure.

    Never stores full command strings, passwords, PGPASSWORD, or connection URLs.
    """

    def __init__(
        self,
        *,
        operation: str,
        executable: str,
        return_code: int,
        stderr_summary: str,
        failure_type: str | None = None,
    ) -> None:
        self.operation = operation
        self.executable = executable
        self.return_code = return_code
        self.stderr_summary = stderr_summary
        self.failure_type = failure_type or _OPERATION_FAILURE_TYPES.get(
            operation, "unexpected_error"
        )
        super().__init__(stderr_summary)


# ── Credential Sanitizer ──────────────────────────────────────────────────────


def sanitize_diagnostic(
    text: str,
    *,
    password: str | None,
    connection_urls: tuple[str, ...],
) -> str:
    """Remove credentials from diagnostic text. Never log raw secrets."""
    if password:
        text = text.replace(password, "***REDACTED***")
    for url in connection_urls:
        if url:
            text = text.replace(url, "***REDACTED_URL***")
    text = re.sub(r"PGPASSWORD=\S+", "PGPASSWORD=***REDACTED***", text)
    text = re.sub(r"(postgresql(\+\w+)?://)[^@]*@", r"\1***REDACTED***@", text)
    text = " ".join(text.split())
    if len(text) > 1000:
        text = text[:997] + "..."
    return text


# ── Helpers ────────────────────────────────────────────────────────────────────


def _stable_hex(value: str) -> str:
    """Return a deterministic 64-char lowercase hex hash for seed data."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _replay_context(now: datetime) -> RequestContext:
    principal = Principal(
        tenant_id="backup-tenant",
        organization_id="backup-org",
        user_id="backup-user",
        roles=("agent_user",),
    )
    return RequestContext(
        request_id="backup-restore-replay",
        trace_id="backup-restore-replay-trace",
        session_id="backup-restore-replay-session",
        principal=principal,
        authorization=AuthorizationDecision(
            allowed=True,
            code="allowed",
            permissions=("chat:invoke", "crm:read", "rag:read"),
            resource_scopes=(
                ResourceScope(
                    tenant_id=principal.tenant_id,
                    organization_id=principal.organization_id,
                    resource_type="organization",
                    scope_type="organization",
                ),
            ),
        ),
        versions=RuntimeVersionSnapshot(
            model_registry_version="models-v1",
            model_routing_policy_version="not_implemented",
            application_version="0.1.0",
            policy_version="policy-v1",
            rag_contract_version="rag-v1",
            crm_connector_version="not_configured",
            authorization_policy_version="authz-v1",
        ),
        received_at=now,
    )


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise PostgreSQLOperationError(
            operation="tool_discovery",
            executable=name,
            return_code=-1,
            stderr_summary=f"required PostgreSQL client tool is unavailable: {name}",
            failure_type="client_tools_unavailable",
        )
    return path


def _connection_environment(password: str | None) -> dict[str, str]:
    environment = os.environ.copy()
    if password:
        environment["PGPASSWORD"] = password
    return environment


def _common_args(url) -> list[str]:
    result = ["--host", url.host or "localhost", "--port", str(url.port or 5432)]
    if url.username:
        result.extend(("--username", url.username))
    return result


def _extract_major(version_output: str) -> int:
    """Extract major version from 'pg_dump (PostgreSQL) 17.6' format."""
    m = re.match(r"^[^0-9]*([0-9]+)", version_output.strip())
    if not m:
        raise PostgreSQLOperationError(
            operation="version_parse",
            executable="unknown",
            return_code=-1,
            stderr_summary=f"cannot parse version from: {version_output.strip()[:80]}",
            failure_type="client_version_parse_failed",
        )
    return int(m.group(1))


# ── Subprocess Runner ─────────────────────────────────────────────────────────


def _run(
    *,
    operation: str,
    command: list[str],
    environment: dict[str, str],
    timeout: float,
    failure_type: str | None = None,
    password: str | None = None,
    connection_urls: tuple[str, ...] = (),
) -> None:
    """Run a PostgreSQL client tool with structured error reporting."""
    effective_type = failure_type or _OPERATION_FAILURE_TYPES.get(
        operation, "unexpected_error"
    )
    try:
        completed = subprocess.run(
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise PostgreSQLOperationError(
            operation=operation,
            executable=Path(command[0]).name,
            return_code=-1,
            stderr_summary=f"timed out after {timeout}s",
            failure_type="timeout",
        ) from None

    if completed.returncode != 0:
        raw_stderr = completed.stderr.decode("utf-8", errors="replace")
        sanitized = sanitize_diagnostic(
            raw_stderr, password=password, connection_urls=connection_urls
        )
        raise PostgreSQLOperationError(
            operation=operation,
            executable=Path(command[0]).name,
            return_code=completed.returncode,
            stderr_summary=sanitized,
            failure_type=effective_type,
        )


# ── Version Preflight ─────────────────────────────────────────────────────────


async def _get_server_major(server_url: str) -> int:
    """Query PostgreSQL server major version."""
    engine = create_async_engine(server_url)
    try:
        async with engine.connect() as conn:
            version_str = await conn.scalar(
                text("SELECT current_setting('server_version_num')")
            )
            if version_str is None:
                raise PostgreSQLOperationError(
                    operation="server_version_query",
                    executable="postgresql_server",
                    return_code=-1,
                    stderr_summary="server_version_num returned NULL",
                    failure_type="server_version_query_failed",
                )
            return int(str(version_str)) // 10000
    except PostgreSQLOperationError:
        raise
    except Exception as exc:
        raise PostgreSQLOperationError(
            operation="server_version_query",
            executable="postgresql_server",
            return_code=-1,
            stderr_summary=f"server version query failed: {type(exc).__name__}",
            failure_type="server_version_query_failed",
        ) from exc
    finally:
        await engine.dispose()


def _check_client_versions(tools: dict[str, str]) -> dict[str, int]:
    """Check all client tool versions and return {tool_name: major_version}."""
    versions: dict[str, int] = {}
    for name in ("pg_dump", "pg_restore", "createdb", "dropdb"):
        completed = subprocess.run(
            [tools[name], "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10.0,
            check=False,
        )
        if completed.returncode != 0:
            raise PostgreSQLOperationError(
                operation="version_parse",
                executable=name,
                return_code=completed.returncode,
                stderr_summary=f"{name} --version failed",
                failure_type="client_version_parse_failed",
            )
        versions[name] = _extract_major(
            completed.stdout.decode("utf-8", errors="replace")
        )
    return versions


# ── Seed Data ──────────────────────────────────────────────────────────────────


async def _seed_source(source_url: str, prefix: str) -> None:
    engine = create_async_engine(source_url)
    now = datetime.now(timezone.utc)
    created = now - timedelta(hours=2)

    # Deterministic hex hashes for seed data (all satisfy ^[0-9a-f]{64}$)
    completed_user_text_hash = _stable_hex(f"{prefix}-completed-text")
    completed_idempotency_hash = _stable_hex(f"{prefix}-completed-idem")
    completed_fingerprint = _stable_hex(f"{prefix}-completed-fp")
    blocked_user_text_hash = _stable_hex(f"{prefix}-blocked-text")
    blocked_fingerprint = _stable_hex(f"{prefix}-blocked-fp")
    failed_user_text_hash = _stable_hex(f"{prefix}-failed-text")
    failed_fingerprint = _stable_hex(f"{prefix}-failed-fp")
    replay_user_text_hash = _stable_hex(f"{prefix}-replay-text")
    replay_idempotency_hash = _stable_hex(f"{prefix}-replay-idem")
    replay_fingerprint = _stable_hex(f"{prefix}-replay-fp")

    try:
        async with engine.begin() as connection:
            completed_request = await connection.scalar(
                AgentRequest.__table__.insert().values(
                    request_id=f"{prefix}-completed",
                    trace_id=f"{prefix}-completed-trace",
                    operation="v1.chat",
                    principal_user_id="backup-user",
                    tenant_id="backup-tenant",
                    organization_id="backup-org",
                    status="completed",
                    user_text_hash=completed_user_text_hash,
                    user_text_length=9,
                    idempotency_key_hash=completed_idempotency_hash,
                    request_fingerprint=completed_fingerprint,
                    fingerprint_version=2,
                    authorization_snapshot={"allowed": True},
                    authorization_snapshot_schema_version=1,
                    created_at=created,
                    completed_at=created + timedelta(seconds=2),
                ).returning(AgentRequest.id)
            )
            completed_run = await connection.scalar(
                AgentRun.__table__.insert().values(
                    run_id=f"{prefix}-completed-run",
                    original_request_id=completed_request,
                    status="completed",
                    result_snapshot={"answer": "synthetic backup result"},
                    result_snapshot_schema_version=1,
                    trace_snapshot={"stages": []},
                    trace_snapshot_schema_version=1,
                    started_at=created,
                    completed_at=created + timedelta(seconds=2),
                ).returning(AgentRun.id)
            )
            blocked_request = await connection.scalar(
                AgentRequest.__table__.insert().values(
                    request_id=f"{prefix}-blocked",
                    trace_id=f"{prefix}-blocked-trace",
                    operation="v1.qa",
                    principal_user_id="backup-user",
                    tenant_id="backup-tenant",
                    organization_id="backup-org",
                    status="completed",
                    user_text_hash=blocked_user_text_hash,
                    user_text_length=7,
                    request_fingerprint=blocked_fingerprint,
                    fingerprint_version=2,
                    authorization_snapshot={"allowed": True},
                    authorization_snapshot_schema_version=1,
                    created_at=created,
                    completed_at=created + timedelta(seconds=1),
                ).returning(AgentRequest.id)
            )
            await connection.execute(
                AgentRun.__table__.insert().values(
                    run_id=f"{prefix}-blocked-run",
                    original_request_id=blocked_request,
                    status="blocked",
                    policy_outcome="BLOCKED",
                    result_snapshot={"answer": "blocked"},
                    result_snapshot_schema_version=1,
                    trace_snapshot={"stages": ["policy_engine"]},
                    trace_snapshot_schema_version=1,
                    started_at=created,
                    completed_at=created + timedelta(seconds=1),
                )
            )
            failed_request = await connection.scalar(
                AgentRequest.__table__.insert().values(
                    request_id=f"{prefix}-failed",
                    trace_id=f"{prefix}-failed-trace",
                    operation="v1.chat",
                    principal_user_id="backup-user",
                    tenant_id="backup-tenant",
                    organization_id="backup-org",
                    status="failed",
                    failure_code="application_execution_error",
                    user_text_hash=failed_user_text_hash,
                    user_text_length=5,
                    request_fingerprint=failed_fingerprint,
                    fingerprint_version=2,
                    authorization_snapshot={"allowed": True},
                    authorization_snapshot_schema_version=1,
                    created_at=created,
                    completed_at=created + timedelta(seconds=1),
                ).returning(AgentRequest.id)
            )
            await connection.execute(
                AgentRun.__table__.insert().values(
                    run_id=f"{prefix}-failed-run",
                    original_request_id=failed_request,
                    status="failed",
                    result_snapshot={"error": "synthetic failure"},
                    result_snapshot_schema_version=1,
                    trace_snapshot={"stages": []},
                    trace_snapshot_schema_version=1,
                    started_at=created,
                    completed_at=created + timedelta(seconds=1),
                )
            )
            replay_request = await connection.scalar(
                AgentRequest.__table__.insert().values(
                    request_id=f"{prefix}-replay",
                    trace_id=f"{prefix}-replay-trace",
                    operation="v1.chat",
                    principal_user_id="backup-user",
                    tenant_id="backup-tenant",
                    organization_id="backup-org",
                    status="completed",
                    user_text_hash=replay_user_text_hash,
                    user_text_length=8,
                    idempotency_key_hash=replay_idempotency_hash,
                    request_fingerprint=replay_fingerprint,
                    fingerprint_version=2,
                    authorization_snapshot={"allowed": True},
                    authorization_snapshot_schema_version=1,
                    created_at=created,
                    completed_at=created + timedelta(seconds=3),
                ).returning(AgentRequest.id)
            )
            await connection.execute(
                IdempotencyRecord.__table__.insert().values(
                    tenant_id="backup-tenant",
                    organization_id="backup-org",
                    principal_user_id="backup-user",
                    idempotency_key_hash=replay_idempotency_hash,
                    operation="v1.chat",
                    status="completed",
                    claim_version=1,
                    owner_request_id=str(replay_request),
                    request_fingerprint=replay_fingerprint,
                    fingerprint_version=2,
                    completed_run_record_id=completed_run,
                    response_snapshot={
                        "session_id": "backup-session",
                        "user_input": "synthetic backup request",
                        "policy": {"status": "SAFE", "confidence": 1.0},
                        "final_response": "synthetic backup result",
                        "confidence": 0.82,
                        "trace": [],
                    },
                    response_snapshot_schema_version=1,
                    created_at=created,
                    claimed_at=created,
                    lease_expires_at=created + timedelta(hours=1),
                    expires_at=created + timedelta(days=7),
                )
            )
            await connection.execute(
                AuditEvent.__table__.insert().values(
                    event_id=f"{prefix}-audit-1",
                    request_id=f"{prefix}-completed",
                    trace_id=f"{prefix}-completed-trace",
                    tenant_id="backup-tenant",
                    organization_id="backup-org",
                    event_type="request_accepted",
                    principal_user_id="backup-user",
                    outcome="success",
                    details_json={"source": "backup_drill"},
                    created_at=created,
                )
            )
    except Exception as exc:
        # Sanitize: never include connection URLs or passwords in diagnostics
        detail = str(exc)
        if source_url:
            detail = detail.replace(source_url, "***REDACTED_URL***")
        raise PostgreSQLOperationError(
            operation="seed_source",
            executable="sqlalchemy",
            return_code=-1,
            stderr_summary=f"seed source failed: {type(exc).__name__} — {detail[:200]}",
            failure_type="seed_source_failed",
        ) from exc
    finally:
        await engine.dispose()


# ── Dump / Restore ─────────────────────────────────────────────────────────────


def _dump(
    *,
    tools: dict[str, str],
    source_url,
    dump_path: Path,
    environment: dict[str, str],
    password: str | None,
    connection_urls: tuple[str, ...],
    timeout: float,
) -> None:
    command = [
        tools["pg_dump"],
        *_common_args(source_url),
        "--format=custom",
        "--file",
        str(dump_path),
        source_url.database,
    ]
    _run(
        operation="pg_dump",
        command=command,
        environment=environment,
        timeout=timeout,
        password=password,
        connection_urls=connection_urls,
    )


def _create_restore_database(
    *,
    tools: dict[str, str],
    restore_url,
    environment: dict[str, str],
    password: str | None,
    connection_urls: tuple[str, ...],
    timeout: float,
) -> None:
    _run(
        operation="create_restore_database",
        command=[
            tools["createdb"],
            *_common_args(restore_url),
            restore_url.database,
        ],
        environment=environment,
        timeout=timeout,
        password=password,
        connection_urls=connection_urls,
    )


def _restore_dump(
    *,
    tools: dict[str, str],
    restore_url,
    dump_path: Path,
    environment: dict[str, str],
    password: str | None,
    connection_urls: tuple[str, ...],
    timeout: float,
) -> None:
    _run(
        operation="pg_restore",
        command=[
            tools["pg_restore"],
            *_common_args(restore_url),
            "--dbname",
            restore_url.database,
            "--no-owner",
            "--no-privileges",
            str(dump_path),
        ],
        environment=environment,
        timeout=timeout,
        password=password,
        connection_urls=connection_urls,
    )


# ── Verification ──────────────────────────────────────────────────────────────


async def _verify(
    source_url: str,
    restore_url: str,
) -> None:
    source = create_async_engine(source_url)
    restore_engine = create_async_engine(restore_url)
    try:
        async with restore_engine.begin() as restore:
            source_counts: list[int] = []
            restore_counts: list[int] = []
            for table in (
                "agent_requests",
                "agent_runs",
                "audit_events",
                "idempotency_records",
            ):
                async with source.connect() as src:
                    source_counts.append(
                        int(await src.scalar(text(f'SELECT count(*) FROM "{table}"')) or 0)
                    )
                restore_counts.append(
                    int(await restore.scalar(text(f'SELECT count(*) FROM "{table}"')) or 0)
                )
            if restore_counts != source_counts:
                raise PostgreSQLOperationError(
                    operation="verify_restored_data",
                    executable="sqlalchemy",
                    return_code=-1,
                    stderr_summary="restored table counts do not match source",
                    failure_type="restored_data_verification_failed",
                )
            snapshot_row = (
                await restore.execute(
                    text(
                        "SELECT response_snapshot, response_snapshot_schema_version "
                        "FROM idempotency_records WHERE status = 'completed'"
                    )
                )
            ).one()
            restored_result = ReplaySnapshotMapper(max_bytes=262144).restore(
                snapshot_row[0],
                snapshot_version=snapshot_row[1],
                context=_replay_context(datetime.now(timezone.utc)),
                user_text="synthetic backup request",
                replayed_at=datetime.now(timezone.utc),
            )
            if restored_result.orchestration.final_response != "synthetic backup result":
                raise PostgreSQLOperationError(
                    operation="verify_restored_data",
                    executable="sqlalchemy",
                    return_code=-1,
                    stderr_summary="restored replay snapshot did not reproduce the result",
                    failure_type="restored_data_verification_failed",
                )
        integrity = await PersistenceIntegrityChecker(restore_engine).check(full=True)
        if integrity.status != "healthy" or not integrity.complete:
            raise PostgreSQLOperationError(
                operation="integrity_check",
                executable="sqlalchemy",
                return_code=-1,
                stderr_summary="restored persistence integrity is not healthy",
                failure_type="persistence_integrity_failed",
            )
    finally:
        await source.dispose()
        await restore_engine.dispose()


# ── Main ──────────────────────────────────────────────────────────────────────


def _emit_success(
    server_major: int,
    client_versions: dict[str, int],
    backup_bytes: int,
    dump_seconds: float,
    restore_seconds: float,
    total_seconds: float,
) -> None:
    print("backup_restore_status=passed")
    print(f"postgres_server_major={server_major}")
    for name in ("pg_dump", "pg_restore", "createdb", "dropdb"):
        print(f"{name}_major={client_versions[name]}")
    print(f"backup_bytes={backup_bytes}")
    print(f"backup_seconds={dump_seconds:.3f}")
    print(f"restore_seconds={restore_seconds:.3f}")
    print(f"total_seconds={total_seconds:.3f}")
    print("cleanup_status=passed")
    print("database_revision=0001")


def _emit_failure(error: PostgreSQLOperationError) -> None:
    print("backup_restore_status=failed")
    print(f"backup_restore_failure_type={error.failure_type}")
    print(f"backup_restore_failure_operation={error.operation}")
    print(f"backup_restore_failure_tool={error.executable}")
    print(f"backup_restore_failure_return_code={error.return_code}")
    print(f"backup_restore_failure_message={error.stderr_summary}")


def _emit_cleanup_warnings(failures: list[str]) -> None:
    for i, f in enumerate(failures, start=1):
        print(f"cleanup_failure_{i}={f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")

    source_url_str = os.getenv("CONVAGENT_POSTGRES_TEST_URL", "").strip()
    if not source_url_str:
        print("backup_restore_status=unavailable")
        return 2

    source_url = make_url(source_url_str)
    password = source_url.password
    # Build connection URLs for sanitization (never printed)
    source_conn = source_url.render_as_string(hide_password=False)
    source_conn_safe = source_url.render_as_string(hide_password=True)

    try:
        tools = {name: _tool(name) for name in REQUIRED_TOOLS}
    except PostgreSQLOperationError as exc:
        _emit_failure(exc)
        return 1

    # Version preflight
    try:
        client_versions = _check_client_versions(tools)
    except PostgreSQLOperationError as exc:
        _emit_failure(exc)
        return 1

    client_majors = set(client_versions.values())
    if len(client_majors) != 1:
        _emit_failure(
            PostgreSQLOperationError(
                operation="version_preflight",
                executable="pg_dump",
                return_code=-1,
                stderr_summary="client tool versions are not all the same major",
                failure_type="client_server_version_mismatch",
            )
        )
        for name in ("pg_dump", "pg_restore", "createdb", "dropdb"):
            print(f"{name}_major={client_versions[name]}")
        return 1

    # Create temp source database, get server major
    suffix = uuid.uuid4().hex[:12]
    source_database = f"convagent_m14f_source_{suffix}"
    source_url_with_db = source_url.set(database=source_database)
    source_db_url_str = source_url_with_db.render_as_string(hide_password=False)
    # Build restore URL from source URL (same host/port/user/password)
    restore_database = f"convagent_m14f_restore_{suffix}"
    restore_url = source_url.set(database=restore_database)
    restore_url_str = restore_url.render_as_string(hide_password=False)
    connection_urls = (source_db_url_str, restore_url_str)

    # State variables for cleanup control flow
    exit_code = 1
    primary_failure: PostgreSQLOperationError | None = None
    cleanup_failures: list[str] = []
    source_database_created = False
    restore_database_created = False
    backup_path: Path | None = None
    started = time.monotonic()

    try:
        # Preflight: server version
        try:
            environment = _connection_environment(password)
            _run(
                operation="create_source_database",
                command=[
                    tools["createdb"],
                    *_common_args(source_url_with_db),
                    source_database,
                ],
                environment=environment,
                timeout=min(args.timeout, 30.0),
                password=password,
                connection_urls=connection_urls,
            )
            source_database_created = True
            server_major = asyncio.run(
                _get_server_major(source_url_with_db.render_as_string(hide_password=False))
            )
        except PostgreSQLOperationError as exc:
            primary_failure = exc
            raise

        client_major = next(iter(client_majors))
        if client_major != server_major:
            primary_failure = PostgreSQLOperationError(
                operation="version_preflight",
                executable="pg_dump",
                return_code=-1,
                stderr_summary=(
                    f"client major {client_major} != server major {server_major}"
                ),
                failure_type="client_server_version_mismatch",
            )
            print(f"postgres_server_major={server_major}")
            for name in ("pg_dump", "pg_restore", "createdb", "dropdb"):
                print(f"{name}_major={client_versions[name]}")
            raise primary_failure

        # Migration
        try:
            alembic_cfg = AlembicConfig()
            alembic_cfg.set_main_option(
                "sqlalchemy.url",
                source_url_with_db.render_as_string(hide_password=False),
            )
            alembic_cfg.set_main_option("script_location", "alembic")
            alembic_command.upgrade(alembic_cfg, "head")
        except Exception as exc:
            primary_failure = PostgreSQLOperationError(
                operation="migration",
                executable="alembic",
                return_code=-1,
                stderr_summary=f"migration upgrade failed: {type(exc).__name__}",
                failure_type="migration_upgrade_failed",
            )
            raise primary_failure from exc

        # Seed
        try:
            asyncio.run(
                _seed_source(
                    source_url_with_db.render_as_string(hide_password=False),
                    prefix=f"m14f_{suffix}",
                )
            )
        except PostgreSQLOperationError as exc:
            primary_failure = exc
            raise

        # Dump
        dump_started = time.monotonic()
        with tempfile.NamedTemporaryFile(
            suffix=".dump", prefix="convagent-m14f-", delete=False
        ) as tmpf:
            backup_path = Path(tmpf.name)
        try:
            _dump(
                tools=tools,
                source_url=source_url_with_db,
                dump_path=backup_path,
                environment=environment,
                password=password,
                connection_urls=connection_urls,
                timeout=args.timeout,
            )
        except PostgreSQLOperationError as exc:
            primary_failure = exc
            raise
        dump_seconds = time.monotonic() - dump_started

        # Restore: create database first, mark state immediately
        restore_started = time.monotonic()
        try:
            _create_restore_database(
                tools=tools,
                restore_url=restore_url,
                environment=environment,
                password=password,
                connection_urls=connection_urls,
                timeout=args.timeout,
            )
            restore_database_created = True
            _restore_dump(
                tools=tools,
                restore_url=restore_url,
                dump_path=backup_path,
                environment=environment,
                password=password,
                connection_urls=connection_urls,
                timeout=args.timeout,
            )
        except PostgreSQLOperationError as exc:
            primary_failure = exc
            raise
        restore_seconds = time.monotonic() - restore_started

        # Verify
        try:
            asyncio.run(
                _verify(
                    source_url_with_db.render_as_string(hide_password=False),
                    restore_url_str,
                )
            )
        except PostgreSQLOperationError as exc:
            primary_failure = exc
            raise

        # Success — save values for emission after cleanup
        total_seconds = time.monotonic() - started
        backup_bytes = backup_path.stat().st_size
        exit_code = 0

    except PostgreSQLOperationError:
        pass  # primary_failure already set
    except Exception as exc:
        primary_failure = PostgreSQLOperationError(
            operation="unknown",
            executable="python",
            return_code=-1,
            stderr_summary=f"unexpected error: {type(exc).__name__}",
            failure_type="unexpected_error",
        )
    finally:
        # Cleanup - never overrides primary failure
        if restore_database_created:
            try:
                _run(
                    operation="drop_restore_database",
                    command=[
                        tools["dropdb"],
                        *_common_args(restore_url),
                        "--if-exists",
                        restore_database,
                    ],
                    environment=environment,
                    timeout=min(args.timeout, 30.0),
                    password=password,
                    connection_urls=connection_urls,
                )
            except PostgreSQLOperationError:
                cleanup_failures.append("drop_restore_database")
        if source_database_created:
            try:
                _run(
                    operation="drop_source_database",
                    command=[
                        tools["dropdb"],
                        *_common_args(source_url_with_db),
                        "--if-exists",
                        source_database,
                    ],
                    environment=environment,
                    timeout=min(args.timeout, 30.0),
                    password=password,
                    connection_urls=connection_urls,
                )
            except PostgreSQLOperationError:
                cleanup_failures.append("drop_source_database")
        if backup_path is not None and backup_path.exists():
            try:
                backup_path.unlink()
            except OSError:
                cleanup_failures.append("unlink_dump_file")

    # Emit results — success only after cleanup passes
    if primary_failure is not None:
        _emit_failure(primary_failure)
        if cleanup_failures:
            _emit_cleanup_warnings(cleanup_failures)
        return 1

    if cleanup_failures:
        # Main succeeded but cleanup failed
        print("backup_restore_status=failed")
        print("backup_restore_failure_type=cleanup_failed")
        _emit_cleanup_warnings(cleanup_failures)
        return 1

    # All good — emit success last
    _emit_success(
        server_major=server_major,
        client_versions=client_versions,
        backup_bytes=backup_bytes,
        dump_seconds=dump_seconds,
        restore_seconds=restore_seconds,
        total_seconds=total_seconds,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
