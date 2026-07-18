"""Read-only diagnostics and guarded terminal-idempotency retention.

These operations deliberately use aggregate queries, PostgreSQL database time,
statement timeouts, and bounded batches. They never migrate or repair data.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import IntEnum
from collections.abc import Callable
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from conversation_agent.config import AppConfig, DatabaseConfig, PersistenceMode
from conversation_agent.database.models import Base


BUSINESS_TABLES = (
    "agent_requests",
    "agent_runs",
    "audit_events",
    "idempotency_records",
)
FORBIDDEN_JSON_KEYS = (
    "claims",
    "email",
    "fingerprint",
    "key_hash",
    "owner_request_id",
    "prompt",
    "provider_response",
    "raw_response",
    "token",
)


class DoctorExitCode(IntEnum):
    HEALTHY = 0
    CONFIG_INVALID = 2
    CONNECTION_UNAVAILABLE = 3
    REVISION_MISMATCH = 4
    INTEGRITY_VIOLATION = 5
    PERMISSION_VIOLATION = 6
    TRANSPORT_SECURITY_VIOLATION = 7


@dataclass(frozen=True, slots=True)
class IntegrityIssue:
    code: str
    count: int


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    status: str
    complete: bool
    mode: str
    issues: tuple[IntegrityIssue, ...]
    stale_active_count: int
    expired_terminal_count: int
    maximum_replay_snapshot_bytes: int
    table_counts: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "complete": self.complete,
            "mode": self.mode,
            "issues": [asdict(issue) for issue in self.issues],
            "stale_active_count": self.stale_active_count,
            "expired_terminal_count": self.expired_terminal_count,
            "maximum_replay_snapshot_bytes": self.maximum_replay_snapshot_bytes,
            "table_counts": dict(self.table_counts),
        }


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: str
    value: str | int | float | bool | None = None


@dataclass(frozen=True, slots=True)
class DoctorReport:
    status: str
    exit_code: int
    complete: bool
    mode: str
    checks: tuple[DoctorCheck, ...]
    integrity: IntegrityReport | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "complete": self.complete,
            "mode": self.mode,
            "checks": [asdict(check) for check in self.checks],
            "integrity": None if self.integrity is None else self.integrity.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PruneReport:
    applied: bool
    candidate_count: int
    deleted_count: int
    batches: int
    complete: bool
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PersistenceIntegrityChecker:
    """Check frozen four-table invariants without loading table contents."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        quick_statement_timeout_ms: int = 3_000,
        full_statement_timeout_ms: int = 15_000,
    ) -> None:
        self._engine = engine
        self._quick_timeout = quick_statement_timeout_ms
        self._full_timeout = full_statement_timeout_ms

    async def check(self, *, full: bool = False) -> IntegrityReport:
        timeout = self._full_timeout if full else self._quick_timeout
        mode = "full" if full else "quick"
        try:
            async with self._engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await connection.execute(text("SET TRANSACTION READ ONLY"))
                    await connection.execute(
                        text("SELECT set_config('statement_timeout', :timeout, true)"),
                        {"timeout": f"{timeout}ms"},
                    )
                    report = await self._run_checks(connection, mode=mode)
                    await transaction.rollback()
                    return report
                except BaseException:
                    await transaction.rollback()
                    raise
        except (asyncio.TimeoutError, DBAPIError) as exc:
            if isinstance(exc, DBAPIError):
                sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
                if sqlstate != "57014":
                    raise
            return self._incomplete(mode)

    async def _run_checks(
        self, connection: AsyncConnection, *, mode: str
    ) -> IntegrityReport:
        checks = (
            (
                "actual_request_run_cardinality",
                """
                SELECT count(*) FROM agent_requests r
                LEFT JOIN agent_runs run ON run.original_request_id = r.id
                WHERE r.replayed_from_request_id IS NULL
                  AND r.status IN ('completed', 'failed')
                GROUP BY r.id HAVING count(run.id) <> 1
                """,
                True,
            ),
            (
                "request_run_status_mismatch",
                """
                SELECT count(*) FROM agent_requests r
                JOIN agent_runs run ON run.original_request_id = r.id
                WHERE (r.status = 'failed' AND run.status <> 'failed')
                   OR (r.status = 'completed' AND run.status NOT IN ('completed', 'blocked'))
                """,
                False,
            ),
            (
                "replay_has_run",
                """
                SELECT count(*) FROM agent_requests r
                JOIN agent_runs run ON run.original_request_id = r.id
                WHERE r.replayed_from_request_id IS NOT NULL
                """,
                False,
            ),
            (
                "replay_lineage_invalid",
                """
                SELECT count(*) FROM agent_requests replay
                LEFT JOIN agent_requests original ON original.id = replay.replayed_from_request_id
                WHERE replay.replayed_from_request_id IS NOT NULL
                  AND (original.id IS NULL OR original.replayed_from_request_id IS NOT NULL)
                """,
                False,
            ),
            (
                "active_owner_invalid",
                """
                SELECT count(*) FROM idempotency_records i
                LEFT JOIN agent_requests r ON r.request_id = i.owner_request_id
                WHERE i.status = 'in_progress'
                  AND (i.claim_version < 1 OR r.id IS NULL OR r.status <> 'in_progress')
                """,
                False,
            ),
            (
                "completed_idempotency_invalid",
                """
                SELECT count(*) FROM idempotency_records i
                LEFT JOIN agent_runs run ON run.id = i.completed_run_record_id
                WHERE i.status = 'completed'
                  AND (i.completed_run_record_id IS NULL OR run.id IS NULL
                       OR i.response_snapshot IS NULL
                       OR i.response_snapshot_schema_version IS NULL
                       OR i.expires_at IS NULL)
                """,
                False,
            ),
            (
                "failed_idempotency_invalid",
                """
                SELECT count(*) FROM idempotency_records
                WHERE status = 'failed' AND expires_at IS NULL
                """,
                False,
            ),
            (
                "request_accepted_audit_missing",
                """
                SELECT count(*) FROM agent_requests r
                WHERE r.replayed_from_request_id IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM audit_events a
                    WHERE a.request_id = r.request_id AND a.event_type = 'request_accepted'
                  )
                """,
                False,
            ),
            (
                "terminal_audit_missing",
                """
                SELECT count(*) FROM agent_requests r
                WHERE r.status IN ('completed', 'failed')
                  AND NOT EXISTS (
                    SELECT 1 FROM audit_events a
                    WHERE a.request_id = r.request_id
                      AND a.event_type IN ('request_completed', 'request_failed', 'policy_blocked')
                  )
                """,
                False,
            ),
            (
                "audit_forbidden_json_key",
                self._forbidden_key_sql("audit_events", "details_json"),
                False,
            ),
            (
                "replay_snapshot_forbidden_json_key",
                self._forbidden_key_sql("idempotency_records", "response_snapshot"),
                False,
            ),
        )
        issues: list[IntegrityIssue] = []
        for code, sql, grouped in checks:
            result = await connection.execute(text(sql))
            if grouped:
                count = len(result.all())
            else:
                count = int(result.scalar_one() or 0)
            if count:
                issues.append(IntegrityIssue(code=code, count=count))

        stale_active = int(
            await connection.scalar(
                text(
                    "SELECT count(*) FROM idempotency_records "
                    "WHERE status = 'in_progress' AND lease_expires_at <= clock_timestamp()"
                )
            )
            or 0
        )
        expired_terminal = int(
            await connection.scalar(
                text(
                    "SELECT count(*) FROM idempotency_records "
                    "WHERE status IN ('completed', 'failed') "
                    "AND expires_at <= clock_timestamp()"
                )
            )
            or 0
        )
        maximum_snapshot = int(
            await connection.scalar(
                text(
                    "SELECT coalesce(max(pg_column_size(response_snapshot)), 0) "
                    "FROM idempotency_records WHERE response_snapshot IS NOT NULL"
                )
            )
            or 0
        )
        table_counts = []
        for table in BUSINESS_TABLES:
            count = int(
                await connection.scalar(text(f'SELECT count(*) FROM "{table}"')) or 0
            )
            table_counts.append((table, count))
        return IntegrityReport(
            status="healthy" if not issues else "unhealthy",
            complete=True,
            mode=mode,
            issues=tuple(issues),
            stale_active_count=stale_active,
            expired_terminal_count=expired_terminal,
            maximum_replay_snapshot_bytes=maximum_snapshot,
            table_counts=tuple(table_counts),
        )

    @staticmethod
    def _forbidden_key_sql(table: str, column: str) -> str:
        alternatives = "|".join(FORBIDDEN_JSON_KEYS)
        return (
            f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL "
            f"AND {column}::text ~* '\"({alternatives})\"[[:space:]]*:'"
        )

    @staticmethod
    def _incomplete(mode: str) -> IntegrityReport:
        return IntegrityReport(
            status="incomplete",
            complete=False,
            mode=mode,
            issues=(),
            stale_active_count=0,
            expired_terminal_count=0,
            maximum_replay_snapshot_bytes=0,
            table_counts=(),
        )


class PersistenceDoctor:
    """Bounded read-only diagnosis for the active PostgreSQL contract."""

    def __init__(
        self,
        *,
        config: AppConfig,
        engine: AsyncEngine,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._engine = engine
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def run(self, *, full: bool = False) -> DoctorReport:
        overall_timeout = (
            self._config.database.doctor_full_overall_timeout_seconds
            if full
            else self._config.database.doctor_quick_overall_timeout_seconds
        )
        try:
            async with asyncio.timeout(overall_timeout):
                return await self._run_bounded(full=full)
        except asyncio.TimeoutError:
            return DoctorReport(
                status="incomplete",
                exit_code=DoctorExitCode.INTEGRITY_VIOLATION,
                complete=False,
                mode="full" if full else "quick",
                checks=(DoctorCheck("overall_deadline", "failed", False),),
                integrity=None,
            )

    async def _run_bounded(self, *, full: bool) -> DoctorReport:
        if self._config.database.effective_persistence_mode is not PersistenceMode.POSTGRES:
            return DoctorReport(
                status="unhealthy",
                exit_code=DoctorExitCode.CONFIG_INVALID,
                complete=True,
                mode="full" if full else "quick",
                checks=(DoctorCheck("persistence_mode", "failed", "not_postgres"),),
                integrity=None,
            )
        checks: list[DoctorCheck] = []
        timeout = self._config.database.doctor_statement_timeout_ms
        try:
            async with self._engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await connection.execute(text("SET TRANSACTION READ ONLY"))
                    await connection.execute(
                        text("SELECT set_config('statement_timeout', :timeout, true)"),
                        {"timeout": f"{timeout}ms"},
                    )
                    row = (
                        await connection.execute(
                            text(
                                "SELECT current_database(), current_schema(), "
                                "current_setting('search_path'), current_setting('TimeZone'), "
                                "clock_timestamp(), current_user, version()"
                            )
                        )
                    ).one()
                    db_time = row[4]
                    app_time = self._clock()
                    drift = abs((db_time - app_time).total_seconds())
                    checks.extend(
                        (
                            DoctorCheck("connectivity", "passed", True),
                            DoctorCheck("database_name", "passed", row[0]),
                            DoctorCheck("current_schema", "passed", row[1]),
                            DoctorCheck("search_path", "passed", row[2]),
                            DoctorCheck(
                                "timezone",
                                "passed" if row[3] in {"UTC", "Etc/UTC"} else "warning",
                                row[3],
                            ),
                            DoctorCheck("clock_drift_seconds", "passed" if drift <= self._config.database.max_clock_drift_seconds else "failed", round(drift, 3)),
                            DoctorCheck("current_role", "passed", row[5]),
                            DoctorCheck("postgresql_version", "passed", str(row[6]).split(" ")[1]),
                        )
                    )
                    revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                    checks.append(
                        DoctorCheck(
                            "revision",
                            "passed" if revision == self._config.database.expected_revision else "failed",
                            revision,
                        )
                    )
                    tables = tuple(
                        (
                            await connection.execute(
                                text(
                                    "SELECT table_name FROM information_schema.tables "
                                    "WHERE table_schema = current_schema() "
                                    "AND table_name = ANY(CAST(:tables AS text[])) ORDER BY table_name"
                                ),
                                {"tables": list(BUSINESS_TABLES)},
                            )
                        ).scalars()
                    )
                    checks.append(
                        DoctorCheck(
                            "business_tables",
                            "passed" if set(tables) == set(BUSINESS_TABLES) else "failed",
                            len(tables),
                        )
                    )
                    expected_timestamptz = {
                        (table.name, column.name)
                        for table in Base.metadata.sorted_tables
                        if table.name in BUSINESS_TABLES
                        for column in table.columns
                        if getattr(column.type, "timezone", False)
                    }
                    reflected_timestamptz = set(
                        (
                            await connection.execute(
                                text(
                                    "SELECT table_name, column_name "
                                    "FROM information_schema.columns "
                                    "WHERE table_schema = current_schema() "
                                    "AND table_name = ANY(CAST(:tables AS text[])) "
                                    "AND data_type = 'timestamp with time zone'"
                                ),
                                {"tables": list(BUSINESS_TABLES)},
                            )
                        ).tuples()
                    )
                    checks.append(
                        DoctorCheck(
                            "timestamptz_columns",
                            "passed"
                            if reflected_timestamptz == expected_timestamptz
                            else "failed",
                            len(reflected_timestamptz),
                        )
                    )
                    diff_count = await connection.run_sync(self._metadata_diff_count)
                    checks.append(
                        DoctorCheck("metadata_diff_count", "passed" if diff_count == 0 else "failed", diff_count)
                    )
                    tls = await connection.scalar(
                        text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
                    )
                    tls_ok = bool(tls) or self._config.database.tls_mode == "disable"
                    checks.append(DoctorCheck("tls", "passed" if tls_ok else "failed", bool(tls)))
                    await transaction.rollback()
                except BaseException:
                    await transaction.rollback()
                    raise
        except Exception:
            return DoctorReport(
                status="unhealthy",
                exit_code=DoctorExitCode.CONNECTION_UNAVAILABLE,
                complete=True,
                mode="full" if full else "quick",
                checks=(DoctorCheck("connectivity", "failed", False),),
                integrity=None,
            )

        integrity = await PersistenceIntegrityChecker(
            self._engine,
            quick_statement_timeout_ms=timeout,
            full_statement_timeout_ms=self._config.database.doctor_full_statement_timeout_ms,
        ).check(full=full)
        failed = {check.name for check in checks if check.status == "failed"}
        if "revision" in failed:
            code = DoctorExitCode.REVISION_MISMATCH
        elif "tls" in failed:
            code = DoctorExitCode.TRANSPORT_SECURITY_VIOLATION
        elif failed or integrity.status == "unhealthy" or not integrity.complete:
            code = DoctorExitCode.INTEGRITY_VIOLATION
        else:
            code = DoctorExitCode.HEALTHY
        return DoctorReport(
            status="healthy" if code == DoctorExitCode.HEALTHY else "unhealthy",
            exit_code=int(code),
            complete=integrity.complete,
            mode="full" if full else "quick",
            checks=tuple(checks),
            integrity=integrity,
        )

    @staticmethod
    def _metadata_diff_count(sync_connection) -> int:
        context = MigrationContext.configure(
            sync_connection,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_object": lambda obj, name, type_, reflected, compare_to: not (
                    type_ == "table" and name == "alembic_version"
                ),
            },
        )
        return len(compare_metadata(context, Base.metadata))


class IdempotencyPruner:
    """Delete only expired terminal idempotency rows in bounded transactions."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        batch_size: int = 100,
        safety_margin_seconds: int = 300,
        max_batches: int = 100,
        overall_timeout_seconds: float = 30.0,
    ) -> None:
        if not 1 <= batch_size <= 1_000:
            raise ValueError("batch_size must be between 1 and 1000")
        if safety_margin_seconds <= 0:
            raise ValueError("safety_margin_seconds must be positive")
        if max_batches <= 0 or overall_timeout_seconds <= 0:
            raise ValueError("prune bounds must be positive")
        self._engine = engine
        self._batch_size = batch_size
        self._margin = safety_margin_seconds
        self._max_batches = max_batches
        self._timeout = overall_timeout_seconds

    async def run(self, *, apply: bool = False) -> PruneReport:
        started = time.monotonic()
        if not apply:
            async with self._engine.connect() as connection:
                candidate_count = int(
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM idempotency_records "
                            "WHERE status IN ('completed', 'failed') "
                            "AND expires_at <= clock_timestamp() - (:margin * interval '1 second')"
                        ),
                        {"margin": self._margin},
                    )
                    or 0
                )
            return PruneReport(False, candidate_count, 0, 0, True, time.monotonic() - started)

        deleted = 0
        batches = 0
        complete = True
        while batches < self._max_batches:
            if time.monotonic() - started >= self._timeout:
                complete = False
                break
            async with self._engine.begin() as connection:
                result = await connection.execute(
                    text(
                        "WITH candidates AS ("
                        " SELECT id FROM idempotency_records"
                        " WHERE status IN ('completed', 'failed')"
                        " AND expires_at <= clock_timestamp() - (:margin * interval '1 second')"
                        " ORDER BY expires_at, id FOR UPDATE SKIP LOCKED LIMIT :batch"
                        ") DELETE FROM idempotency_records target USING candidates"
                        " WHERE target.id = candidates.id"
                        " AND target.status IN ('completed', 'failed')"
                        " AND target.expires_at <= clock_timestamp() - (:margin * interval '1 second')"
                        " RETURNING target.id"
                    ),
                    {"margin": self._margin, "batch": self._batch_size},
                )
                batch_deleted = len(result.all())
            batches += 1
            deleted += batch_deleted
            if batch_deleted < self._batch_size:
                break
        else:
            complete = False
        return PruneReport(True, deleted, deleted, batches, complete, time.monotonic() - started)


@dataclass(frozen=True, slots=True)
class ConfigurationAuditReport:
    status: str
    violations: tuple[str, ...]
    warnings: tuple[str, ...]
    maximum_app_connections_per_worker: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def audit_production_config(config: AppConfig) -> ConfigurationAuditReport:
    """Evaluate production persistence invariants without exposing secrets."""

    database = config.database
    violations: list[str] = []
    warnings: list[str] = []
    if config.runtime_mode != "production":
        warnings.append("environment_is_not_production")
    if database.effective_persistence_mode is not PersistenceMode.POSTGRES:
        violations.append("persistence_mode_must_be_postgres")
    if not database.url_value:
        violations.append("database_url_missing")
    if database.idempotency_header_mode is None:
        violations.append("idempotency_header_mode_not_explicit")
    if database.auto_migrate:
        violations.append("automatic_migration_forbidden")
    if database.echo:
        violations.append("sql_echo_forbidden")
    for name, value in (
        ("connect_timeout", database.connect_timeout_seconds),
        ("pool_timeout", database.pool_timeout_seconds),
        ("statement_timeout", database.statement_timeout_ms),
        ("lock_timeout", database.lock_timeout_ms),
        ("idle_transaction_timeout", database.idle_in_transaction_session_timeout_ms),
        ("readiness_timeout", database.readiness_timeout_seconds),
        ("shutdown_timeout", database.graceful_shutdown_timeout_seconds),
    ):
        if value <= 0:
            violations.append(f"{name}_must_be_positive")
    if database.stale_in_progress_timeout_seconds <= 60:
        warnings.append("lease_duration_has_limited_execution_margin")
    if database.schema_name != "public":
        warnings.append("non_default_schema_requires_role_review")
    return ConfigurationAuditReport(
        status="healthy" if not violations else "unhealthy",
        violations=tuple(sorted(violations)),
        warnings=tuple(sorted(warnings)),
        maximum_app_connections_per_worker=(
            database.pool_size + database.max_overflow
        ),
    )
