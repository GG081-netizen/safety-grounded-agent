from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from conversation_agent.application.models import UserRequest
from conversation_agent.authorization.models import AuthorizationDecision, ResourceScope
from conversation_agent.identity.models import Principal
from conversation_agent.runtime.models import RequestContext, RuntimeVersionSnapshot


pytestmark = pytest.mark.unit


def _principal() -> Principal:
    return Principal(
        tenant_id="t1",
        organization_id="o1",
        user_id="u1",
        roles=("sales", "sales"),
        groups=("east", "alpha"),
    )


def _versions() -> RuntimeVersionSnapshot:
    return RuntimeVersionSnapshot(
        model_registry_version="qwen3_profiles_v1",
        model_routing_policy_version="not_implemented",
        application_version="0.1.0",
        policy_version="business_rules_v1",
        rag_contract_version="rag_client_v1",
        crm_connector_version="not_configured",
        authorization_policy_version="not_configured",
    )


def _authorization() -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=True,
        code="allowed",
        permissions=("chat:invoke",),
        resource_scopes=(
            ResourceScope(
                tenant_id="t1",
                organization_id="o1",
                resource_type="organization",
                scope_type="organization",
            ),
        ),
    )


def test_user_request_forbids_trusted_fields():
    with pytest.raises(ValidationError):
        UserRequest(text="query customer", roles=["system_admin"])  # type: ignore[call-arg]


def test_principal_values_are_stable_and_disabled_is_representable():
    principal = _principal().model_copy(update={"enabled": False})
    assert principal.roles == ("sales",)
    assert principal.groups == ("alpha", "east")
    assert principal.enabled is False


@pytest.mark.parametrize(
    ("allowed", "code", "reason"),
    [
        (True, "denied_scope", "wrong"),
        (False, "allowed", "wrong"),
        (False, "denied_scope", ""),
    ],
)
def test_authorization_decision_invariants(allowed, code, reason):
    with pytest.raises(ValidationError):
        AuthorizationDecision(allowed=allowed, code=code, reason=reason)


def test_authorization_snapshot_is_deterministic_and_immutable():
    scope = ResourceScope(
        tenant_id="t1",
        organization_id="o1",
        resource_type="customer",
        scope_type="department",
        resource_ids=("c2", "c1", "c1"),
    )
    decision = AuthorizationDecision(
        allowed=True,
        code="allowed",
        permissions=("crm.read", "rag.query", "crm.read"),
        resource_scopes=[scope],
    )
    assert decision.permissions == ("crm.read", "rag.query")
    assert decision.resource_scopes == (scope,)
    assert scope.resource_ids == ("c1", "c2")


def test_request_context_normalizes_aware_datetime_to_utc():
    received = datetime(2026, 7, 14, 9, 30, tzinfo=timezone(timedelta(hours=8)))
    context = RequestContext(
        request_id="r1",
        trace_id="tr1",
        session_id="s1",
        principal=_principal(),
        authorization=_authorization(),
        versions=_versions(),
        received_at=received,
    )
    assert context.received_at.utcoffset() == timedelta(0)
    assert context.received_at.hour == 1


def test_request_context_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        RequestContext(
            request_id="r1",
            trace_id="tr1",
            session_id="s1",
            principal=_principal(),
            authorization=_authorization(),
            versions=_versions(),
            received_at=datetime(2026, 7, 14, 1, 30),
        )
