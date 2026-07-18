"""Immutable authorization decision snapshots."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ResourceScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    organization_id: str = Field(min_length=1, max_length=128)
    resource_type: str = Field(min_length=1, max_length=128)
    scope_type: Literal["self", "team", "department", "region", "organization"]
    resource_ids: tuple[str, ...] = ()

    @field_validator("resource_ids", mode="before")
    @classmethod
    def _stable_ids(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)) or any(
            type(item) is not str for item in value
        ):
            raise ValueError("resource_ids must be an array of strings")
        values = {item.strip() for item in value}
        if "" in values:
            raise ValueError("resource_ids cannot contain blank values")
        return tuple(sorted(values))


class AuthorizationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    allowed: bool
    code: str = Field(min_length=1, max_length=128)
    reason: str = ""
    permissions: tuple[str, ...] = ()
    resource_scopes: tuple[ResourceScope, ...] = ()

    @field_validator("permissions", mode="before")
    @classmethod
    def _stable_permissions(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)) or any(
            type(item) is not str for item in value
        ):
            raise ValueError("permissions must be an array of strings")
        values = {item.strip() for item in value}
        if "" in values:
            raise ValueError("permissions cannot contain blank values")
        return tuple(sorted(values))

    @field_validator("resource_scopes", mode="before")
    @classmethod
    def _immutable_scopes(cls, value: object) -> tuple[object, ...]:
        if value is None:
            return ()
        return tuple(value)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def _decision_invariants(self) -> "AuthorizationDecision":
        if self.allowed and self.code != "allowed":
            raise ValueError("allowed decision must use code='allowed'")
        if not self.allowed:
            if self.code == "allowed":
                raise ValueError("denied decision cannot use code='allowed'")
            if not self.reason:
                raise ValueError("denied decision requires a reason")
        return self
