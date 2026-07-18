from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from conversation_agent.application.durable_service import DurableApplicationService
from conversation_agent.application.models import UserRequest
from conversation_agent.application.service import ApplicationExecutionError, ApplicationResult
from conversation_agent.authorization.models import AuthorizationDecision, ResourceScope
from conversation_agent.database.errors import (
    DurableApplicationExecutionError,
    PersistenceFinalizationError,
    RequestInitializationError,
)
from conversation_agent.database.fake_execution import FakeExecutionUnitOfWorkFactory
from conversation_agent.identity.models import Principal
from conversation_agent.orchestration.models import OrchestrationResult, TaskRoute
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.runtime.models import RequestContext, RuntimeVersionSnapshot

pytestmark = pytest.mark.unit

BASE_TIME = datetime(2026, 7, 16, 2, 0, tzinfo=timezone.utc)


def _context(request_id: str = "request-1") -> RequestContext:
    principal = Principal(
        tenant_id="tenant-1",
        organization_id="org-1",
        user_id="user-1",
        roles=("agent_user",),
    )
    authorization = AuthorizationDecision(
        allowed=True,
        code="allowed",
        permissions=("chat:invoke", "rag:read", "crm:read"),
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
        request_id=request_id,
        trace_id=f"trace-{request_id}",
        session_id=f"session-{request_id}",
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
        received_at=BASE_TIME,
    )


class RecordingChatService:
    def __init__(
        self,
        factory: FakeExecutionUnitOfWorkFactory,
        *,
        blocked: bool = False,
        raises: bool = False,
    ) -> None:
        self.factory = factory
        self.blocked = blocked
        self.raises = raises
        self.call_count = 0
        self.thread_id: int | None = None
        self.active_uow_during_call: int | None = None

    def execute_with_context(self, request, *, context, forced_task=None):
        self.call_count += 1
        self.thread_id = threading.get_ident()
        self.active_uow_during_call = self.factory.active_uow_count
        if self.raises:
            raise ApplicationExecutionError("internal secret failure")
        return ApplicationResult(
            context=context,
            orchestration=OrchestrationResult(
                session_id=context.session_id,
                user_input=request.text,
                policy=PolicyDecision(
                    status="BLOCKED" if self.blocked else "SAFE",
                    matched_rules=["blocked-rule"] if self.blocked else [],
                ),
                task_route=None if self.blocked else TaskRoute(task="qa"),
                final_response="blocked response" if self.blocked else "answer",
                confidence=0.8,
            ),
        )


def _clock():
    values = iter(BASE_TIME + timedelta(seconds=index) for index in range(20))
    return lambda: next(values)


def _service(factory, chat):
    ids = iter(f"event-{index}" for index in range(20))
    return DurableApplicationService(
        chat_service=chat,
        uow_factory=factory,
        clock=_clock(),
        run_id_factory=lambda: "run-1",
        event_id_factory=lambda: next(ids),
    )


@pytest.mark.asyncio
async def test_completed_path_uses_two_transactions_and_returns_original_result():
    factory = FakeExecutionUnitOfWorkFactory()
    chat = RecordingChatService(factory)
    result = await _service(factory, chat).execute(
        UserRequest(text="客户🙂"),
        context=_context(),
        operation="POST:/v1/chat",
    )
    assert result.orchestration.final_response == "answer"
    assert factory.created_uow_count == 2
    assert factory.committed_uow_count == 2
    assert factory.active_uow_count == 0
    assert chat.active_uow_during_call == 0
    assert chat.thread_id != threading.get_ident()
    assert factory.state.requests["request-1"]["status"] == "completed"
    assert factory.state.runs["run-1"]["record"].status == "completed"
    assert [item["record"].event_type for item in factory.state.audits] == [
        "request_accepted",
        "request_completed",
    ]


@pytest.mark.asyncio
async def test_policy_blocked_uses_structured_status_and_completes_request():
    factory = FakeExecutionUnitOfWorkFactory()
    chat = RecordingChatService(factory, blocked=True)
    result = await _service(factory, chat).execute(
        UserRequest(text="text without refusal keywords"),
        context=_context(),
        operation="POST:/v1/chat",
    )
    assert result.orchestration.policy.is_blocked
    assert factory.state.requests["request-1"]["status"] == "completed"
    assert factory.state.runs["run-1"]["record"].status == "blocked"
    assert factory.state.audits[-1]["record"].event_type == "policy_blocked"


@pytest.mark.asyncio
async def test_application_failure_is_durably_failed_without_exception_text():
    factory = FakeExecutionUnitOfWorkFactory()
    chat = RecordingChatService(factory, raises=True)
    with pytest.raises(DurableApplicationExecutionError) as exc_info:
        await _service(factory, chat).execute(
            UserRequest(text="query"),
            context=_context(),
            operation="POST:/v1/chat",
        )
    request = factory.state.requests["request-1"]
    run = factory.state.runs["run-1"]["record"]
    audit = factory.state.audits[-1]["record"]
    assert request["status"] == "failed"
    assert request["failure_code"] == "application_service_failed"
    assert run.status == "failed"
    assert audit.event_type == "request_failed"
    persisted = repr(factory.state)
    assert "internal secret failure" not in persisted
    assert "Traceback" not in persisted
    assert isinstance(exc_info.value.__cause__, ApplicationExecutionError)


@pytest.mark.asyncio
async def test_transaction_a_failure_prevents_chat_and_rolls_back_request():
    factory = FakeExecutionUnitOfWorkFactory()
    factory.fail_operations.add("create_audit_event")
    chat = RecordingChatService(factory)
    with pytest.raises(RequestInitializationError):
        await _service(factory, chat).execute(
            UserRequest(text="query"),
            context=_context(),
            operation="POST:/v1/chat",
        )
    assert chat.call_count == 0
    assert factory.state.requests == {}
    assert factory.state.audits == []


@pytest.mark.asyncio
async def test_duplicate_request_id_never_executes_chat():
    factory = FakeExecutionUnitOfWorkFactory()
    first_chat = RecordingChatService(factory)
    await _service(factory, first_chat).execute(
        UserRequest(text="first"),
        context=_context(),
        operation="POST:/v1/chat",
    )
    second_chat = RecordingChatService(factory)
    with pytest.raises(RequestInitializationError):
        await _service(factory, second_chat).execute(
            UserRequest(text="second"),
            context=_context(),
            operation="POST:/v1/chat",
        )
    assert second_chat.call_count == 0


@pytest.mark.asyncio
async def test_completed_finalization_commit_failure_never_returns_success():
    factory = FakeExecutionUnitOfWorkFactory()
    factory.fail_commit_attempts.add(2)
    chat = RecordingChatService(factory)
    with pytest.raises(PersistenceFinalizationError):
        await _service(factory, chat).execute(
            UserRequest(text="query"),
            context=_context(),
            operation="POST:/v1/chat",
        )
    assert factory.state.requests["request-1"]["status"] == "in_progress"
    assert factory.state.runs == {}
    assert len(factory.state.audits) == 1


@pytest.mark.asyncio
async def test_blocked_finalization_commit_failure_never_returns_blocked_result():
    factory = FakeExecutionUnitOfWorkFactory()
    factory.fail_commit_attempts.add(2)
    chat = RecordingChatService(factory, blocked=True)
    with pytest.raises(PersistenceFinalizationError):
        await _service(factory, chat).execute(
            UserRequest(text="query"),
            context=_context(),
            operation="POST:/v1/chat",
        )
    assert factory.state.requests["request-1"]["status"] == "in_progress"
    assert factory.state.runs == {}


@pytest.mark.asyncio
async def test_failed_finalization_failure_raises_persistence_finalization_error():
    factory = FakeExecutionUnitOfWorkFactory()
    factory.fail_commit_attempts.add(2)
    chat = RecordingChatService(factory, raises=True)
    with pytest.raises(PersistenceFinalizationError):
        await _service(factory, chat).execute(
            UserRequest(text="query"),
            context=_context(),
            operation="POST:/v1/chat",
        )
    assert factory.state.requests["request-1"]["status"] == "in_progress"
    assert factory.state.runs == {}


@pytest.mark.asyncio
async def test_mapper_and_service_never_touch_idempotency_runtime():
    factory = FakeExecutionUnitOfWorkFactory()
    chat = RecordingChatService(factory)
    await _service(factory, chat).execute(
        UserRequest(text="query"),
        context=_context(),
        operation="POST:/v1/chat",
    )
    assert all("idempotency" not in operation for operation in factory.operation_log)
