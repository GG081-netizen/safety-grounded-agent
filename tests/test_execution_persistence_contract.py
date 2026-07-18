from __future__ import annotations

import inspect

import pytest

from conversation_agent.application.durable_service import DurableApplicationService
from conversation_agent.database.repository import ExecutionRepository
from conversation_agent.database.sqlalchemy_repository import (
    SQLAlchemyExecutionRepository,
)

pytestmark = pytest.mark.unit


def test_sqlalchemy_repository_satisfies_only_execution_contract():
    methods = {
        name
        for name, member in inspect.getmembers(
            SQLAlchemyExecutionRepository, predicate=inspect.isfunction
        )
        if not name.startswith("_")
    }
    assert methods == {
        "create_audit_event",
        "create_request",
        "create_run",
        "finalize_request_completed",
        "finalize_request_failed",
        "get_request_for_update",
    }
    assert "claim_idempotency" not in methods
    assert "complete_idempotency_fenced" not in methods


def test_repository_does_not_commit_or_rollback():
    source = inspect.getsource(SQLAlchemyExecutionRepository)
    assert ".commit(" not in source
    assert ".rollback(" not in source


def test_durable_service_depends_on_execution_uow_not_idempotency_repository():
    source = inspect.getsource(DurableApplicationService)
    assert "IdempotencyRepository" not in source
    assert "idempotency_records" not in source


def test_execution_repository_protocol_is_narrow():
    protocol_members = {
        name
        for name, member in inspect.getmembers(
            ExecutionRepository, predicate=inspect.isfunction
        )
        if not name.startswith("_")
    }
    assert "create_request" in protocol_members
    assert "claim_idempotency" not in protocol_members
