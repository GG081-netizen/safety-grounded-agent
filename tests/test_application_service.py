from datetime import datetime, timezone

import pytest

from conversation_agent.application.models import UserRequest
from conversation_agent.application.service import ApplicationExecutionError, ChatService
from conversation_agent.authorization.models import AuthorizationDecision, ResourceScope
from conversation_agent.identity.models import Principal
from conversation_agent.orchestration.models import OrchestrationResult
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.runtime.builder import RequestContextBuilder
from conversation_agent.runtime.models import RequestMetadata, RuntimeVersionSnapshot


pytestmark = pytest.mark.unit


def _versions() -> RuntimeVersionSnapshot:
    return RuntimeVersionSnapshot(
        model_registry_version="qwen3_profiles_v1",
        model_routing_policy_version="not_implemented",
        application_version="0.1.0",
        policy_version="business_rules_v1",
        rag_contract_version="rag_client_v1",
        crm_connector_version="not_configured",
        authorization_policy_version="rbac_abac_v1",
    )


def _builder() -> RequestContextBuilder:
    ids = iter(("request-generated", "trace-generated", "session-generated"))
    return RequestContextBuilder(
        versions=_versions(),
        id_factory=lambda: next(ids),
        clock=lambda: datetime(2026, 7, 14, 1, 30, tzinfo=timezone.utc),
    )


def _security_args() -> tuple[RequestMetadata, Principal, AuthorizationDecision]:
    principal = Principal(
        tenant_id="tenant-server",
        organization_id="org-server",
        user_id="user-server",
        roles=("sales_user",),
    )
    authorization = AuthorizationDecision(
        allowed=True,
        code="allowed",
        permissions=("chat:invoke", "rag:read", "crm:read"),
        resource_scopes=(
            ResourceScope(
                tenant_id="tenant-server",
                organization_id="org-server",
                resource_type="organization",
                scope_type="organization",
            ),
        ),
    )
    metadata = RequestMetadata(
        request_id="request-api",
        trace_id="trace-api",
        received_at=datetime(2026, 7, 14, 1, 30, tzinfo=timezone.utc),
    )
    return metadata, principal, authorization


class RecordingCoordinator:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict] = []
        self.raises = raises

    def run(
        self,
        user_input,
        session_id=None,
        task_override=None,
        *,
        request_metadata=None,
    ):
        self.calls.append(
            {
                "user_input": user_input,
                "session_id": session_id,
                "task_override": task_override,
                "request_metadata": request_metadata,
            }
        )
        if self.raises:
            raise RuntimeError("internal detail must stay behind service boundary")
        return OrchestrationResult(
            session_id=session_id,
            user_input=user_input,
            policy=PolicyDecision(status="SAFE", confidence=1.0),
            final_response="ok",
            confidence=0.8,
        )


def test_context_builder_generates_server_owned_context():
    _, principal, authorization = _security_args()
    context = _builder().build(principal=principal, authorization=authorization)

    assert context.request_id == "request-generated"
    assert context.trace_id == "trace-generated"
    assert context.session_id == "session-generated"
    assert context.principal.tenant_id == "tenant-server"
    assert context.principal.roles == ("sales_user",)
    assert context.received_at == datetime(2026, 7, 14, 1, 30, tzinfo=timezone.utc)


def test_chat_service_maps_request_and_context_into_coordinator():
    coordinator = RecordingCoordinator()
    service = ChatService(coordinator=coordinator, context_builder=_builder())  # type: ignore[arg-type]
    metadata, principal, authorization = _security_args()
    request = UserRequest(
        text="Generate this week's sales report",
        session_id="client-session",
        task_override="weekly_report",
    )

    result = service.execute(
        request,
        metadata=metadata,
        principal=principal,
        authorization=authorization,
        idempotency_key="idem-1",
    )

    assert result.context.request_id == "request-api"
    assert result.context.trace_id == "trace-api"
    assert result.context.session_id == "client-session"
    assert result.context.idempotency_key == "idem-1"
    assert coordinator.calls[0]["task_override"] == "weekly_report"
    assert coordinator.calls[0]["request_metadata"].request_id == "request-api"
    assert coordinator.calls[0]["request_metadata"].trace_id == "trace-api"


def test_forced_task_takes_precedence_over_request_override():
    coordinator = RecordingCoordinator()
    service = ChatService(coordinator=coordinator, context_builder=_builder())  # type: ignore[arg-type]
    metadata, principal, authorization = _security_args()
    service.execute(
        UserRequest(text="query knowledge", task_override="email_draft"),
        metadata=metadata,
        principal=principal,
        authorization=authorization,
        forced_task="qa",
    )
    assert coordinator.calls[0]["task_override"] == "qa"


def test_application_service_wraps_internal_coordinator_error():
    coordinator = RecordingCoordinator(raises=True)
    service = ChatService(coordinator=coordinator, context_builder=_builder())  # type: ignore[arg-type]
    metadata, principal, authorization = _security_args()
    with pytest.raises(ApplicationExecutionError) as exc_info:
        service.execute(
            UserRequest(text="query"),
            metadata=metadata,
            principal=principal,
            authorization=authorization,
        )
    assert "internal detail" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)
