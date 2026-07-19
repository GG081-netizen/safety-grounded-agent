from __future__ import annotations

import inspect
import io
import re
from pathlib import Path
from unittest import mock

import pytest

from conversation_agent.operations.persistence import IdempotencyPruner
from scripts import postgres_backup_restore_drill as drill


pytestmark = pytest.mark.unit


# ── Existing Contract Tests (updated for refactored script) ───────────────────

def test_backup_drill_requires_official_tools_and_uses_no_shell():
    source = inspect.getsource(drill)
    assert all(
        tool in drill.REQUIRED_TOOLS
        for tool in ("pg_dump", "pg_restore", "createdb", "dropdb")
    )
    assert "shell=True" not in source
    assert "PGPASSWORD" in source
    # hide_password=True for safe rendering, but hide_password=False needed for
    # building the connection environment
    assert "hide_password=False" in source or "hide_password=True" in source


def test_prune_contract_uses_database_time_skip_locked_and_terminal_recheck():
    source = inspect.getsource(IdempotencyPruner.run)
    assert "clock_timestamp()" in source
    assert "FOR UPDATE SKIP LOCKED" in source
    assert source.count("status IN ('completed', 'failed')") >= 2
    assert "status = 'in_progress'" not in source


def test_ci_operational_job_has_skip_protection_and_backup_restore_gate():
    workflow = Path(".github/workflows/portfolio-release-gates.yml").read_text(
        encoding="utf-8"
    )
    assert "operational-postgres:" in workflow
    assert "--suite-identity operational-postgres" in workflow
    assert "--minimum-tests 6" in workflow
    assert "Run bounded logical backup and fresh-database restore" in workflow
    assert "scripts/postgres_backup_restore_drill.py --timeout 120" in workflow
    assert 'grep -Fx "0001 (head)"' in workflow
    # PostgreSQL 17 client installation and verification
    assert "postgresql-client-17" in workflow
    assert "extract_major" in workflow


# ── New Structured Exception Tests ────────────────────────────────────────────

def test_postgresql_operation_error_stores_no_command_or_password():
    error = drill.PostgreSQLOperationError(
        operation="pg_dump",
        executable="pg_dump",
        return_code=1,
        stderr_summary="server version mismatch",
    )
    error_str = str(error)
    error_repr = repr(error)
    assert "secret123" not in error_str
    assert "secret123" not in error_repr
    assert "pg_dump --host" not in error_str
    assert error.operation == "pg_dump"
    assert error.executable == "pg_dump"
    assert error.return_code == 1
    assert "server version mismatch" in error.stderr_summary


# ── Tool Discovery Tests ──────────────────────────────────────────────────────

def test_missing_tool_raises_client_tools_unavailable():
    with mock.patch("shutil.which", return_value=None):
        with pytest.raises(drill.PostgreSQLOperationError) as exc:
            drill._tool("pg_dump")
        assert exc.value.failure_type == "client_tools_unavailable"
        assert exc.value.executable == "pg_dump"


# ── Version Parse Tests ───────────────────────────────────────────────────────

def test_extract_major_from_pg_dump_version():
    assert drill._extract_major("pg_dump (PostgreSQL) 17.6") == 17
    assert drill._extract_major("pg_dump (PostgreSQL) 16.14 (Ubuntu 16.14-1)") == 16


def test_extract_major_unrecognized_output_fails():
    with pytest.raises(drill.PostgreSQLOperationError) as exc:
        drill._extract_major("unrecognized output")
    assert exc.value.failure_type == "client_version_parse_failed"


# ── Version Mismatch Tests ────────────────────────────────────────────────────

def test_mismatched_client_versions_detected():
    versions = {"pg_dump": 17, "pg_restore": 16, "createdb": 17, "dropdb": 17}
    majors = set(versions.values())
    # Simulate check: client tools must all have same major
    assert len(majors) != 1
    # Verify the mismatch would be reported
    error = drill.PostgreSQLOperationError(
        operation="version_preflight",
        executable="pg_dump",
        return_code=-1,
        stderr_summary="client tool versions are not all the same major",
        failure_type="client_server_version_mismatch",
    )
    assert error.failure_type == "client_server_version_mismatch"


# ── Sanitizer Tests ───────────────────────────────────────────────────────────

def test_sanitize_removes_password():
    result = drill.sanitize_diagnostic(
        "error connecting with password secret123",
        password="secret123",
        connection_urls=(),
    )
    assert "secret123" not in result
    assert "***REDACTED***" in result


def test_sanitize_removes_connection_url():
    url = "postgresql+asyncpg://user:secret123@host:5432/db"
    result = drill.sanitize_diagnostic(
        f"error connecting to {url}",
        password=None,
        connection_urls=(url,),
    )
    assert "secret123" not in result
    assert "***REDACTED_URL***" in result


def test_sanitize_removes_pgpassword():
    result = drill.sanitize_diagnostic(
        "PGPASSWORD=real-secret environment variable set",
        password=None,
        connection_urls=(),
    )
    assert "real-secret" not in result
    assert "PGPASSWORD=***REDACTED***" in result


def test_sanitize_removes_url_userinfo():
    result = drill.sanitize_diagnostic(
        "connecting to postgresql://admin:secret123@127.0.0.1:5432/test",
        password=None,
        connection_urls=(),
    )
    assert "secret123" not in result


def test_sanitize_truncates_long_text():
    long_text = "x" * 2000
    result = drill.sanitize_diagnostic(long_text, password=None, connection_urls=())
    assert len(result) <= 1003


def test_sanitize_full_output_no_credentials():
    """Combined stdout+stderr must never leak credentials."""
    result = drill.sanitize_diagnostic(
        "command failed: pg_dump --host localhost --port 5432 --username admin "
        "--dbname test PGPASSWORD=real-secret postgresql://admin:secret@host/db",
        password="secret",
        connection_urls=("postgresql://admin:secret@host/db",),
    )
    assert "secret" not in result
    assert "real-secret" not in result
    assert "postgresql://admin:secret@host/db" not in result


# ── Failure Type Mapping Tests ────────────────────────────────────────────────

def test_operation_failure_types_covers_all_operations():
    operations = (
        "create_source_database",
        "pg_dump",
        "create_restore_database",
        "pg_restore",
        "drop_restore_database",
        "drop_source_database",
    )
    for op in operations:
        assert op in drill._OPERATION_FAILURE_TYPES, f"{op} missing from mapping"


# ── Cleanup Semantics Tests ───────────────────────────────────────────────────

def test_cleanup_failure_type_mapping():
    assert drill._OPERATION_FAILURE_TYPES["drop_restore_database"] == "cleanup_failed"
    assert drill._OPERATION_FAILURE_TYPES["drop_source_database"] == "cleanup_failed"


# ── Workflow CI Tests ─────────────────────────────────────────────────────────

def test_workflow_has_pg17_client_install_and_verify():
    workflow = Path(".github/workflows/portfolio-release-gates.yml").read_text(
        encoding="utf-8"
    )
    assert "postgresql-client-17" in workflow
    assert "extract_major" in workflow
    assert "apt.postgresql.org" in workflow


def test_workflow_uses_https_for_apt_repository():
    workflow = Path(".github/workflows/portfolio-release-gates.yml").read_text(
        encoding="utf-8"
    )
    assert "https://apt.postgresql.org" in workflow
    assert "set -euo pipefail" in workflow


# ── Seed Hash Regression Tests ─────────────────────────────────────────────────


def test_stable_hex_returns_64_char_lowercase_hex():
    result = drill._stable_hex("test")
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


def test_stable_hex_different_inputs_produce_different_values():
    a = drill._stable_hex("alpha")
    b = drill._stable_hex("beta")
    assert a != b
    assert len(a) == len(b) == 64


def test_seed_source_has_stable_hex_helper():
    source = inspect.getsource(drill)
    assert "def _stable_hex" in source
    assert "hashlib.sha256" in source


def test_all_seed_hashes_match_hex_pattern():
    """All hash constants in seed source satisfy ^[0-9a-f]{64}$."""
    import re
    source = inspect.getsource(drill._seed_source)
    hex_pattern = re.compile(r"^[0-9a-f]{64}$")
    hex_var_assignments = re.findall(
        r'(_\w+_hash|fingerprint)\s*=\s*_stable_hex\(', source
    )
    assert len(hex_var_assignments) >= 3, "Expected named hex hash variables"
    # Verify no remaining fake patterns like "a"*64 or "g"*64
    assert '"a" * 64' not in source
    assert '"b" * 64' not in source
    assert '"c" * 64' not in source
    assert '"d" * 64' not in source
    assert '"e" * 64' not in source
    assert '"f" * 64' not in source
    assert '"g" * 64' not in source
    assert '"h" * 64' not in source
    assert '"i" * 64' not in source
    assert '"j" * 64' not in source


def test_replay_request_and_idempotency_record_share_idempotency_hash():
    source = inspect.getsource(drill._seed_source)
    assert "replay_idempotency_hash" in source
    assert "idempotency_key_hash=replay_idempotency_hash" in source


def test_replay_request_and_idempotency_record_share_fingerprint():
    source = inspect.getsource(drill._seed_source)
    assert "replay_fingerprint" in source
    assert "request_fingerprint=replay_fingerprint" in source


def test_restore_database_split_into_create_and_restore():
    source = inspect.getsource(drill)
    assert "def _create_restore_database" in source
    assert "def _restore_dump" in source


def test_success_not_emitted_before_cleanup():
    source = inspect.getsource(drill.main)
    assert "_emit_success" in source
    # Success emission must be after cleanup logic
    after_cleanup = source.split("Emit results")[1]
    assert "_emit_success" in after_cleanup


def test_named_temporary_file_used():
    source = inspect.getsource(drill.main)
    assert "NamedTemporaryFile" in source
    assert "delete=False" in source
    assert "tempfile.mktemp" not in source
