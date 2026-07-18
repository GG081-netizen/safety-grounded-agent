"""Server-created identity and organization contracts."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    organization_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=255, strict=True)
    email: str | None = Field(default=None, max_length=320, strict=True)
    department_id: str | None = Field(default=None, max_length=128)
    team_id: str | None = Field(default=None, max_length=128)
    region_id: str | None = Field(default=None, max_length=128)
    roles: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    enabled: bool = Field(default=True, strict=True)

    @field_validator("department_id", "team_id", "region_id")
    @classmethod
    def _non_empty_optional_id(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("optional organization IDs cannot be blank")
        return value

    @field_validator("roles", "groups", mode="before")
    @classmethod
    def _stable_values(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)) or any(
            type(item) is not str for item in value
        ):
            raise ValueError("roles and groups must be arrays of strings")
        values = {item.strip() for item in value}
        if "" in values:
            raise ValueError("roles and groups cannot contain blank values")
        return tuple(sorted(values))
