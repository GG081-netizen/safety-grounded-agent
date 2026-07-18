from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Mapping

import pytest

from conversation_agent.application.models import UserRequest
from conversation_agent.application.persistence_mappers import (
    AuditPersistenceMapper,
    FailureCodeMapper,
    RequestPersistenceMapper,
    RunPersistenceMapper,
)
from conversation_agent.application.service import ApplicationResult
from conversation_agent.authorization.models import AuthorizationDecision, ResourceScope
from conversation_agent.identity.models import Principal
from conversation_agent.orchestration.models import OrchestrationResult, TaskRoute
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.runtime.models import RequestContext, RuntimeVersionSnapshot

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 16, 1, 2, 3, tzinfo=timezone.utc)


def _context() -> RequestContext:
    principal = Principal(
        tenant_id="tenant-1",
        organization_id="org-1",
        user_id="user-1",
        display_name="Must Not Persist",
        email="secret@example.com",
        roles=("agent_user",),
    )
    authorization = AuthorizationDecision(
        allowed=True,
        code="allowed",
        permissions=("chat:invoke", "rag:read"),
        resource_scopes=(
            ResourceScope(
                tenant_id="tenant-1",
                organization_id="org-1",
                resource_type="organization",
                scope_type="organization",
            ),
        ),
    )
    return RequestContext(
        request_id="request-1",
        trace_id="trace-1",
        session_id="session-1",
        principal=principal,
        authorization=authorization,
        versions=RuntimeVersionSnapshot(
            model_registry_version="models-v1",
            model_routing_policy_version="not_implemented",
            application_version="0.1.0",
            policy_version="policy-v1",
            rag_contract_version="rag-v1",
            crm_connector_version="not_configured",
            authorization_policy_version="authz-v1",
        ),
        received_at=NOW,
    )


def _result(*, blocked: bool = False) -> ApplicationResult:
    context = _context()
    orchestration = OrchestrationResult(
        session_id=context.session_id,
        user_input="sensitive input",
        policy=PolicyDecision(
            status="BLOCKED" if blocked else "SAFE",
            reason="must not persist",
            matched_rules=["rule-1"] if blocked else [],
        ),
        task_route=None if blocked else TaskRoute(task="qa", confidence=0.9),
        final_response="full answer must not persist",
        citations=[{"title": "secret citation"}],
        confidence=0.7,
    )
    return ApplicationResult(context=context, orchestration=orchestration)


def _flatten(value: object) -> str:
    if isinstance(value, Mapping):
        return " ".join(
            part
            for key, item in value.items()
            for part in (str(key), _flatten(item))
        )
    if isinstance(value, (tuple, list)):
        return " ".join(_flatten(item) for item in value)
    return str(value)


def test_user_text_hash_and_length_use_exact_python_string():
    text = "  客户A🙂  "
    record = RequestPersistenceMapper().map(
        context=_context(),
        operation="POST:/v1/chat",
        user_text=text,
        task_override="qa",
        created_at=NOW,
    )
    assert record.user_text_length == len(text)
    assert record.user_text_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_request_fingerprint_is_stable_and_changes_with_safe_input():
    mapper = RequestPersistenceMapper()
    first = mapper.map(
        context=_context(),
        operation="POST:/v1/chat",
        user_text="same",
        task_override="qa",
        created_at=NOW,
    )
    second = mapper.map(
        context=_context(),
        operation="POST:/v1/chat",
        user_text="same",
        task_override="qa",
        created_at=NOW,
    )
    changed = mapper.map(
        context=_context(),
        operation="POST:/v1/qa",
        user_text="same",
        task_override="qa",
        created_at=NOW,
    )
    assert first.request_fingerprint == second.request_fingerprint
    assert changed.request_fingerprint != first.request_fingerprint


@pytest.mark.parametrize("blocked", [False, True])
def test_run_snapshots_are_versioned_and_exclude_sensitive_payloads(blocked: bool):
    result = _result(blocked=blocked)
    run = RunPersistenceMapper().completed(
        result=result,
        request=type("Ref", (), {"database_id": 1})(),
        run_id="run-1",
        started_at=NOW,
        completed_at=NOW,
    )
    assert run.result_snapshot_schema_version == 1
    assert run.trace_snapshot_schema_version == 1
    serialized = _flatten(
        {"result": run.result_snapshot, "trace": run.trace_snapshot}
    ).lower()
    for forbidden in (
        "full answer must not persist",
        "sensitive input",
        "secret citation",
        "secret@example.com",
        "raw_response",
        "debug",
        "prompt",
        "claims",
        "jwt",
        "jwks",
    ):
        assert forbidden not in serialized


def test_audit_snapshots_exclude_identity_details_and_answer():
    result = _result()
    event = AuditPersistenceMapper().request_completed(
        result=result,
        event_id="event-1",
        run_id="run-1",
        created_at=NOW,
    )
    serialized = _flatten(event.details_json).lower()
    assert "secret@example.com" not in serialized
    assert "full answer" not in serialized
    assert "sensitive input" not in serialized


def test_failure_code_mapper_is_allowlist_only():
    mapper = FailureCodeMapper()
    assert mapper.require("application_service_failed") == "application_service_failed"
    with pytest.raises(ValueError):
        mapper.require("RuntimeError: password=secret")


def test_records_reject_naive_or_non_utc_event_times():
    mapper = RequestPersistenceMapper()
    with pytest.raises(ValueError, match="timezone-aware"):
        mapper.map(
            context=_context(),
            operation="POST:/v1/chat",
            user_text="x",
            task_override=None,
            created_at=datetime(2026, 7, 16),
        )
    with pytest.raises(ValueError, match="must be UTC"):
        mapper.map(
            context=_context(),
            operation="POST:/v1/chat",
            user_text="x",
            task_override=None,
            created_at=datetime(
                2026, 7, 16, tzinfo=timezone(timedelta(hours=8))
            ),
        )
