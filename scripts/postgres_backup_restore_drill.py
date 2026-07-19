"""Bounded local logical backup/restore drill using PostgreSQL client tools.

This is a local validation tool, not a production backup system. Credentials
are passed only through the child-process environment and are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import stat
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
        raise RuntimeError(f"required PostgreSQL client tool is unavailable: {name}")
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


def _run(command: list[str], *, environment: dict[str, str], timeout: float) -> None:
    completed = subprocess.run(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        stderr_text = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"PostgreSQL client operation failed: {' '.join(command)} — "
            f"{stderr_text if stderr_text else 'no stderr output'}"
        )


async def _seed_source(source_url: str, prefix: str) -> None:
    engine = create_async_engine(source_url)
    now = datetime.now(timezone.utc)
    created = now - timedelta(hours=2)
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
                    user_text_hash="a" * 64,
                    user_text_length=9,
                    idempotency_key_hash="b" * 64,
                    request_fingerprint="c" * 64,
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
                    user_text_hash="d" * 64,
                    user_text_length=7,
                    request_fingerprint="e" * 64,
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
                    user_text_hash="f" * 64,
                    user_text_length=6,
                    idempotency_key_hash="1" * 64,
                    request_fingerprint="2" * 64,
                    fingerprint_version=2,
                    authorization_snapshot={"allowed": True},
                    authorization_snapshot_schema_version=1,
                    failure_code="application_service_failed",
                    created_at=created,
                    completed_at=created + timedelta(seconds=1),
                ).returning(AgentRequest.id)
            )
            await connection.execute(
                AgentRun.__table__.insert().values(
                    run_id=f"{prefix}-failed-run",
                    original_request_id=failed_request,
                    status="failed",
                    trace_snapshot={"failure_code": "application_service_failed"},
                    trace_snapshot_schema_version=1,
                    started_at=created,
                    completed_at=created + timedelta(seconds=1),
                )
            )
            old_request = await connection.scalar(
                AgentRequest.__table__.insert().values(
                    request_id=f"{prefix}-reclaimed-old",
                    trace_id=f"{prefix}-reclaimed-old-trace",
                    operation="v1.chat",
                    principal_user_id="backup-user",
                    tenant_id="backup-tenant",
                    organization_id="backup-org",
                    status="failed",
                    user_text_hash="3" * 64,
                    user_text_length=5,
                    idempotency_key_hash="4" * 64,
                    request_fingerprint="5" * 64,
                    fingerprint_version=2,
                    authorization_snapshot={"allowed": True},
                    authorization_snapshot_schema_version=1,
                    failure_code="idempotency_lease_reclaimed",
                    created_at=created,
                    completed_at=created + timedelta(minutes=5),
                ).returning(AgentRequest.id)
            )
            await connection.execute(
                AgentRun.__table__.insert().values(
                    run_id=f"{prefix}-reclaimed-old-run",
                    original_request_id=old_request,
                    status="failed",
                    trace_snapshot={"failure_code": "idempotency_lease_reclaimed"},
                    trace_snapshot_schema_version=1,
                    started_at=created + timedelta(minutes=5),
                    completed_at=created + timedelta(minutes=5),
                )
            )
            await connection.execute(
                AgentRequest.__table__.insert().values(
                    request_id=f"{prefix}-active",
                    trace_id=f"{prefix}-active-trace",
                    operation="v1.chat",
                    principal_user_id="backup-user",
                    tenant_id="backup-tenant",
                    organization_id="backup-org",
                    status="in_progress",
                    user_text_hash="6" * 64,
                    user_text_length=5,
                    idempotency_key_hash="4" * 64,
                    request_fingerprint="5" * 64,
                    fingerprint_version=2,
                    authorization_snapshot={"allowed": True},
                    authorization_snapshot_schema_version=1,
                    created_at=now,
                )
            )
            await connection.execute(
                AgentRequest.__table__.insert().values(
                    request_id=f"{prefix}-replay",
                    trace_id=f"{prefix}-replay-trace",
                    operation="v1.chat",
                    principal_user_id="backup-user",
                    tenant_id="backup-tenant",
                    organization_id="backup-org",
                    status="completed",
                    user_text_hash="a" * 64,
                    user_text_length=9,
                    idempotency_key_hash="b" * 64,
                    request_fingerprint="c" * 64,
                    fingerprint_version=2,
                    replayed_from_request_id=completed_request,
                    authorization_snapshot={"allowed": True},
                    authorization_snapshot_schema_version=1,
                    created_at=now,
                    completed_at=now,
                )
            )
            audit_rows = []
            for request_name, terminal_type, outcome in (
                ("completed", "request_completed", "completed"),
                ("blocked", "policy_blocked", "blocked"),
                ("failed", "request_failed", "failed"),
                ("reclaimed-old", "request_failed", "failed"),
                ("active", None, None),
            ):
                audit_rows.append({
                    "event_id": f"{prefix}-{request_name}-accepted",
                    "request_id": f"{prefix}-{request_name}",
                    "tenant_id": "backup-tenant",
                    "organization_id": "backup-org",
                    "event_type": "request_accepted",
                    "outcome": "accepted",
                    "details_json": {"audit_payload_version": 1},
                    "created_at": created,
                })
                if terminal_type:
                    audit_rows.append({
                        "event_id": f"{prefix}-{request_name}-terminal",
                        "request_id": f"{prefix}-{request_name}",
                        "tenant_id": "backup-tenant",
                        "organization_id": "backup-org",
                        "event_type": terminal_type,
                        "outcome": outcome,
                        "details_json": {"audit_payload_version": 1},
                        "created_at": now,
                    })
            audit_rows.append({
                "event_id": f"{prefix}-replay-terminal",
                "request_id": f"{prefix}-replay",
                "tenant_id": "backup-tenant",
                "organization_id": "backup-org",
                "event_type": "request_completed",
                "outcome": "replayed",
                "details_json": {"audit_payload_version": 1, "replayed": True},
                "created_at": now,
            })
            await connection.execute(AuditEvent.__table__.insert(), audit_rows)
            idempotency_rows = (
                    {
                        "tenant_id": "backup-tenant",
                        "organization_id": "backup-org",
                        "principal_user_id": "backup-user",
                        "operation": "v1.chat",
                        "idempotency_key_hash": "b" * 64,
                        "request_fingerprint": "c" * 64,
                        "fingerprint_version": 2,
                        "status": "completed",
                        "claim_version": 1,
                        "owner_request_id": f"{prefix}-completed",
                        "claimed_at": created,
                        "lease_expires_at": created + timedelta(minutes=5),
                        "completed_run_record_id": completed_run,
                        "response_snapshot": {
                            "snapshot_schema_version": 1,
                            "policy": {
                                "status": "SAFE",
                                "reason": "",
                                "matched_rules": [],
                                "warnings": [],
                                "classifier_used": False,
                                "confidence": 1.0,
                            },
                            "intent_result": None,
                            "task_route": None,
                            "final_response": "synthetic backup result",
                            "rag_result": None,
                            "citations": [],
                            "confidence": 0.8,
                        },
                        "response_snapshot_schema_version": 1,
                        "created_at": created,
                        "updated_at": now,
                        "expires_at": now + timedelta(hours=1),
                    },
                    {
                        "tenant_id": "backup-tenant",
                        "organization_id": "backup-org",
                        "principal_user_id": "backup-user",
                        "operation": "v1.qa",
                        "idempotency_key_hash": "1" * 64,
                        "request_fingerprint": "2" * 64,
                        "fingerprint_version": 2,
                        "status": "failed",
                        "claim_version": 1,
                        "owner_request_id": f"{prefix}-failed",
                        "claimed_at": created,
                        "lease_expires_at": created + timedelta(minutes=5),
                        "created_at": created,
                        "updated_at": now,
                        "expires_at": now + timedelta(hours=1),
                    },
                    {
                        "tenant_id": "backup-tenant",
                        "organization_id": "backup-org",
                        "principal_user_id": "backup-user",
                        "operation": "v1.reclaim",
                        "idempotency_key_hash": "4" * 64,
                        "request_fingerprint": "5" * 64,
                        "fingerprint_version": 2,
                        "status": "in_progress",
                        "claim_version": 2,
                        "owner_request_id": f"{prefix}-active",
                        "claimed_at": now,
                        "lease_expires_at": now + timedelta(minutes=5),
                        "created_at": created,
                        "updated_at": now,
                        "expires_at": now + timedelta(minutes=5),
                    },
            )
            for idempotency_row in idempotency_rows:
                await connection.execute(
                    IdempotencyRecord.__table__.insert(), idempotency_row
                )
    finally:
        await engine.dispose()


async def _verify(source_url: str, restore_url: str) -> None:
    source = create_async_engine(source_url)
    restore = create_async_engine(restore_url)
    try:
        async with source.connect() as source_connection, restore.connect() as restored:
            assert await restored.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
            source_counts = []
            restore_counts = []
            for table in (
                "agent_requests",
                "agent_runs",
                "audit_events",
                "idempotency_records",
            ):
                source_counts.append(
                    int(await source_connection.scalar(text(f'SELECT count(*) FROM "{table}"')) or 0)
                )
                restore_counts.append(
                    int(await restored.scalar(text(f'SELECT count(*) FROM "{table}"')) or 0)
                )
            if restore_counts != source_counts:
                raise RuntimeError("restored table counts do not match source")
            snapshot_row = (
                await restored.execute(
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
                raise RuntimeError("restored replay snapshot did not reproduce the result")
        integrity = await PersistenceIntegrityChecker(restore).check(full=True)
        if integrity.status != "healthy" or not integrity.complete:
            raise RuntimeError("restored persistence integrity is not healthy")
    finally:
        await source.dispose()
        await restore.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    source_url = os.getenv("CONVAGENT_POSTGRES_TEST_URL", "").strip()
    if not source_url:
        print("backup_restore_status=unavailable")
        return 2
    try:
        tools = {name: _tool(name) for name in REQUIRED_TOOLS}
    except RuntimeError:
        print("backup_restore_status=client_tools_unavailable")
        return 2

    parsed = make_url(source_url)
    if not parsed.database or "test" not in parsed.database.lower():
        print("backup_restore_status=unsafe_source_database")
        return 2
    suffix = uuid.uuid4().hex[:12]
    source_database = f"convagent_m14f_source_{suffix}"
    restore_database = f"convagent_m14f_restore_{suffix}"
    drill_source_url = parsed.set(database=source_database).render_as_string(
        hide_password=False
    )
    restore_url = parsed.set(database=restore_database).render_as_string(
        hide_password=False
    )
    environment = _connection_environment(parsed.password)
    backup_path: Path | None = None
    database_created = False
    source_database_created = False
    started = time.monotonic()
    try:
        descriptor, raw_path = tempfile.mkstemp(prefix="convagent-m14f-", suffix=".dump")
        os.close(descriptor)
        backup_path = Path(raw_path)
        backup_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        _run(
            [tools["createdb"], *_common_args(parsed), source_database],
            environment=environment,
            timeout=args.timeout,
        )
        source_database_created = True
        alembic = AlembicConfig("alembic.ini")
        alembic.set_main_option("sqlalchemy.url", drill_source_url)
        alembic_command.upgrade(alembic, "head")
        asyncio.run(_seed_source(drill_source_url, f"backup-{suffix}"))
        dump_started = time.monotonic()
        _run(
            [
                tools["pg_dump"],
                *_common_args(parsed),
                "--format=custom",
                "--file",
                str(backup_path),
                source_database,
            ],
            environment=environment,
            timeout=args.timeout,
        )
        dump_seconds = time.monotonic() - dump_started
        _run(
            [tools["createdb"], *_common_args(parsed), restore_database],
            environment=environment,
            timeout=args.timeout,
        )
        database_created = True
        restore_started = time.monotonic()
        _run(
            [
                tools["pg_restore"],
                *_common_args(parsed),
                "--exit-on-error",
                "--dbname",
                restore_database,
                str(backup_path),
            ],
            environment=environment,
            timeout=args.timeout,
        )
        asyncio.run(_verify(drill_source_url, restore_url))
        restore_seconds = time.monotonic() - restore_started
        print("backup_restore_status=passed")
        print(f"backup_bytes={backup_path.stat().st_size}")
        print(f"backup_seconds={dump_seconds:.3f}")
        print(f"restore_seconds={restore_seconds:.3f}")
        print(f"total_seconds={time.monotonic() - started:.3f}")
        return 0
    except Exception as exc:
        print("backup_restore_status=failed")
        print(f"backup_restore_failure_type={type(exc).__name__}")
        print(f"backup_restore_failure_message={exc}")
        if isinstance(exc, IntegrityError):
            cause = getattr(exc.orig, "__cause__", None)
            constraint_name = getattr(cause, "constraint_name", None)
            print(
                "backup_restore_failure_constraint="
                f"{constraint_name if isinstance(constraint_name, str) else 'unavailable'}"
            )
        return 1
    finally:
        if database_created:
            try:
                _run(
                    [tools["dropdb"], *_common_args(parsed), "--if-exists", restore_database],
                    environment=environment,
                    timeout=min(args.timeout, 30.0),
                )
            except (RuntimeError, subprocess.TimeoutExpired):
                print("restore_database_cleanup=failed")
        if source_database_created:
            try:
                _run(
                    [tools["dropdb"], *_common_args(parsed), "--if-exists", source_database],
                    environment=environment,
                    timeout=min(args.timeout, 30.0),
                )
            except (RuntimeError, subprocess.TimeoutExpired):
                print("source_database_cleanup=failed")
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
