from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from conversation_agent.operations.persistence import IdempotencyPruner
from scripts import postgres_backup_restore_drill


pytestmark = pytest.mark.unit


def test_backup_drill_requires_official_tools_and_uses_no_shell():
    source = inspect.getsource(postgres_backup_restore_drill)
    assert all(tool in postgres_backup_restore_drill.REQUIRED_TOOLS for tool in (
        "pg_dump",
        "pg_restore",
        "createdb",
        "dropdb",
    ))
    assert "shell=True" not in source
    assert "PGPASSWORD" in source
    assert "hide_password=False" in source


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
