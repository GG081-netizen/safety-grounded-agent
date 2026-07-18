"""Immutable server-created request and version snapshots."""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from conversation_agent.identity.models import Principal
from conversation_agent.authorization.models import AuthorizationDecision
from conversation_agent.llm.models import ModelProfile


class RuntimeVersionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    default_model_profile: ModelProfile = ModelProfile.STANDARD
    model_registry_version: str = Field(min_length=1)
    model_routing_policy_version: str = Field(min_length=1)
    application_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    rag_contract_version: str = Field(min_length=1)
    crm_connector_version: str = Field(min_length=1)
    authorization_policy_version: str = Field(min_length=1)


class RequestMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    request_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    received_at: datetime

    @field_validator("received_at")
    @classmethod
    def _metadata_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        return value.astimezone(timezone.utc)


class RequestContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    request_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    principal: Principal
    authorization: AuthorizationDecision
    versions: RuntimeVersionSnapshot
    received_at: datetime
    idempotency_key: str | None = Field(default=None, max_length=255)

    @field_validator("received_at")
    @classmethod
    def _normalize_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("idempotency_key")
    @classmethod
    def _non_empty_idempotency_key(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("idempotency_key cannot be blank")
        return value

    @model_validator(mode="after")
    def _scope_matches_principal(self) -> "RequestContext":
        for scope in self.authorization.resource_scopes:
            if (
                scope.tenant_id != self.principal.tenant_id
                or scope.organization_id != self.principal.organization_id
            ):
                raise ValueError("authorization scope must match the principal boundary")
        return self
