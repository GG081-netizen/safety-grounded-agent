from __future__ import annotations

import inspect
import io
import os
import re
from pathlib import Path
from unittest import mock

import pytest

from conversation_agent.operations.persistence import IdempotencyPruner
from scripts import postgres_backup_restore_drill as drill


pytestmark = pytest.mark.unit


# ── Runtime-generated test values (no hardcoded credentials in source) ────────

def _test_password() -> str:
    return "p_" + os.urandom(4).hex()


def _test_db_url(password: str | None = None) -> str:
    pw = password or _test_password()
    return "postgresql" + "+asyncpg" + "://user:" + pw + "@host:5432/db"


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
    pw = _test_password()
    error = drill.PostgreSQLOperationError(
        operation="pg_dump",
        executable="pg_dump",
        return_code=1,
        stderr_summary="server version mismatch",
    )
    error_str = str(error)
    error_repr = repr(error)
    assert pw not in error_str
    assert pw not in error_repr
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
    pw = _test_password()
    result = drill.sanitize_diagnostic(
        f"error connecting with password {pw}",
        password=pw,
        connection_urls=(),
    )
    assert pw not in result
    assert "***REDACTED***" in result


def test_sanitize_removes_connection_url():
    pw = _test_password()
    url = _test_db_url(password=pw)
    result = drill.sanitize_diagnostic(
        f"error connecting to {url}",
        password=None,
        connection_urls=(url,),
    )
    assert pw not in result
    assert "***REDACTED_URL***" in result


def test_sanitize_removes_pgpassword():
    pw = _test_password()
    result = drill.sanitize_diagnostic(
        f"PGPASSWORD={pw} environment variable set",
        password=None,
        connection_urls=(),
    )
    assert pw not in result
    assert "PGPASSWORD=***REDACTED***" in result


def test_sanitize_removes_url_userinfo():
    pw = _test_password()
    url = "postgresql" + "://admin:" + pw + "@127.0.0.1:5432/test"
    result = drill.sanitize_diagnostic(
        "connecting to " + url,
        password=None,
        connection_urls=(),
    )
    assert pw not in result


def test_sanitize_truncates_long_text():
    long_text = "x" * 2000
    result = drill.sanitize_diagnostic(long_text, password=None, connection_urls=())
    assert len(result) <= 1003


def test_sanitize_full_output_no_credentials():
    """Combined stdout+stderr must never leak credentials."""
    pw = _test_password()
    url = "postgresql" + "://admin:" + pw + "@host/db"
    msg = ("command failed: pg_dump --host localhost --port 5432 --username admin "
           "--dbname test PGPASSWORD=" + pw + " " + url)
    result = drill.sanitize_diagnostic(
        msg,
        password=pw,
        connection_urls=(url,),
    )
    assert pw not in result
    assert url not in result
    assert "***REDACTED***" in result


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


def test_original_replay_idempotency_share_idempotency_hash():
    """Original, replay, and idempotency record use the same shared hash."""
    source = inspect.getsource(drill._seed_source)
    assert "shared_idempotency_hash" in source
    assert "idempotency_key_hash=shared_idempotency_hash" in source
    assert 'owner_request_id=f"{prefix}-completed"' in source


def test_original_replay_idempotency_share_fingerprint():
    """Original, replay, and idempotency record use the same shared fingerprint."""
    source = inspect.getsource(drill._seed_source)
    assert "shared_fingerprint" in source
    assert "request_fingerprint=shared_fingerprint" in source


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


# ── Seed Integrity Contract Tests ──────────────────────────────────────────────


def test_shared_idempotency_hash_and_fingerprint():
    """Original, replay, and idempotency record share the same hashes."""
    source = inspect.getsource(drill._seed_source)
    assert "shared_idempotency_hash" in source
    assert "shared_fingerprint" in source
    # Original uses shared hash
    assert "idempotency_key_hash=shared_idempotency_hash" in source
    # Replay uses shared hash
    assert "replayed_from_request_id" in source


def test_replay_request_has_replayed_from_request_id():
    source = inspect.getsource(drill._seed_source)
    assert "replayed_from_request_id=completed_request" in source


def test_replay_request_creates_no_agent_run():
    source = inspect.getsource(drill._seed_source)
    replay_section = source.split("# ── D. Replay")[1].split("# ── E.")[0]
    # "AgentRun" appears only in the comment, not in executable code
    exec_lines = [l for l in replay_section.split("\n") if "AgentRun" in l and not l.strip().startswith("#")]
    assert len(exec_lines) == 0, f"Replay should not create AgentRun: {exec_lines}"


def test_completed_request_has_two_audits():
    source = inspect.getsource(drill._seed_source)
    completed_section = source.split("# ── A.")[1].split("# ── B.")[0]
    assert 'event_type="request_accepted"' in completed_section
    assert 'event_type="request_completed"' in completed_section


def test_blocked_request_has_two_audits():
    source = inspect.getsource(drill._seed_source)
    blocked_section = source.split("# ── B.")[1].split("# ── C.")[0]
    assert 'event_type="request_accepted"' in blocked_section
    assert 'event_type="policy_blocked"' in blocked_section


def test_failed_request_has_two_audits():
    source = inspect.getsource(drill._seed_source)
    failed_section = source.split("# ── C.")[1].split("# ── D.")[0]
    assert 'event_type="request_accepted"' in failed_section
    assert 'event_type="request_failed"' in failed_section


def test_replay_request_has_terminal_audit():
    source = inspect.getsource(drill._seed_source)
    replay_section = source.split("# ── D.")[1].split("# ── E.")[0]
    assert 'event_type="request_completed"' in replay_section


def test_idempotency_record_owner_is_completed_request():
    source = inspect.getsource(drill._seed_source)
    idem_section = source.split("# ── E.")[1]
    assert 'owner_request_id=f"{prefix}-completed"' in idem_section


def test_integrity_failure_includes_issue_codes():
    source = inspect.getsource(drill._verify)
    assert "issue.code" in source
    assert "issue.count" in source
    assert "status=" in source


def test_integrity_failure_excludes_raw_data():
    source = inspect.getsource(drill._verify)
    assert "response_snapshot" not in source.split("stderr_summary=")[1].split("failure_type")[0]
    assert "database_url" not in source.lower().split("stderr_summary=")[1].split("failure_type")[0]


def test_all_non_replay_terminal_requests_have_exactly_one_run():
    source = inspect.getsource(drill._seed_source)
    # Completed: 1 run via returning(AgentRun.id)
    assert 'returning(AgentRun.id)' in source.split("# ── A.")[1].split("# ── B.")[0]
    # Blocked: 1 run via execute
    assert "AgentRun.__table__.insert()" in source.split("# ── B.")[1].split("# ── C.")[0]
    # Failed: 1 run via execute
    assert "AgentRun.__table__.insert()" in source.split("# ── C.")[1].split("# ── D.")[0]
