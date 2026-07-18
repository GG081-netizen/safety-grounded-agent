"""Deterministic versioned authorization policy for M1.3."""

from __future__ import annotations

from collections.abc import Iterable

from conversation_agent.authorization.models import AuthorizationDecision, ResourceScope
from conversation_agent.identity.models import Principal


ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "agent_user": frozenset({"chat:invoke", "qa:invoke", "rag:read", "crm:read"}),
    "knowledge_user": frozenset({"qa:invoke", "rag:read"}),
    "sales_user": frozenset({"chat:invoke", "rag:read", "crm:read"}),
    "debug_viewer": frozenset({"raw_response:view"}),
}
AUTHORIZATION_POLICY_VERSION = "rbac_abac_v1"


class AuthorizationService:
    def effective_permissions(self, principal: Principal) -> tuple[str, ...]:
        permissions: set[str] = set()
        for role in principal.roles:
            permissions.update(ROLE_PERMISSIONS.get(role, ()))
        return tuple(sorted(permissions))

    def authorize(
        self,
        principal: Principal,
        required_permissions: Iterable[str],
    ) -> AuthorizationDecision:
        permissions = self.effective_permissions(principal)
        required = frozenset(required_permissions)
        scope = ResourceScope(
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            resource_type="organization",
            scope_type="organization",
        )
        if not principal.enabled:
            return AuthorizationDecision(
                allowed=False,
                code="denied_disabled_principal",
                reason="The authenticated principal is disabled.",
                permissions=permissions,
                resource_scopes=(scope,),
            )
        missing = required.difference(permissions)
        if missing:
            return AuthorizationDecision(
                allowed=False,
                code="denied_missing_permission",
                reason="The principal lacks permissions required by this route.",
                permissions=permissions,
                resource_scopes=(scope,),
            )
        return AuthorizationDecision(
            allowed=True,
            code="allowed",
            permissions=permissions,
            resource_scopes=(scope,),
        )
