"""Server-side construction of trusted request context."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from conversation_agent.identity.models import Principal
from conversation_agent.authorization.models import AuthorizationDecision, ResourceScope
from conversation_agent.runtime.models import RequestContext, RuntimeVersionSnapshot


class RequestContextBuilder:
    """Build immutable internal context without trusting identity from the body."""

    def __init__(
        self,
        *,
        versions: RuntimeVersionSnapshot,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._versions = versions
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def build(
        self,
        *,
        principal: Principal,
        authorization: AuthorizationDecision,
        received_at: datetime | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> RequestContext:
        return RequestContext(
            request_id=request_id or self._id_factory(),
            trace_id=trace_id or self._id_factory(),
            session_id=session_id or self._id_factory(),
            principal=principal,
            authorization=authorization,
            versions=self._versions,
            received_at=received_at or self._clock(),
            idempotency_key=idempotency_key,
        )


def create_development_context_builder() -> RequestContextBuilder:
    """Create a builder with the current immutable version snapshot."""
    versions = RuntimeVersionSnapshot(
        model_registry_version="qwen3_profiles_v1",
        model_routing_policy_version="not_implemented",
        application_version="0.1.0",
        policy_version="business_rules_v1",
        rag_contract_version="rag_client_v1",
        crm_connector_version="not_configured",
        authorization_policy_version="rbac_abac_v1",
    )
    return RequestContextBuilder(versions=versions)


def development_security_context() -> tuple[Principal, AuthorizationDecision]:
    """Return the explicit M1.3 demo-only identity and authorization snapshot."""

    principal = Principal(
        tenant_id="single_tenant",
        organization_id="default_organization",
        user_id="local_api_user",
        roles=("agent_user",),
        groups=(),
    )
    scope = ResourceScope(
        tenant_id=principal.tenant_id,
        organization_id=principal.organization_id,
        resource_type="organization",
        scope_type="organization",
    )
    decision = AuthorizationDecision(
        allowed=True,
        code="allowed",
        permissions=("chat:invoke", "qa:invoke", "rag:read", "crm:read"),
        resource_scopes=(scope,),
    )
    return principal, decision
