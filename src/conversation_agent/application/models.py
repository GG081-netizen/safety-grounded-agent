"""Untrusted external request DTOs for the future application layer."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from conversation_agent.task_types import TaskName


class UserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=20_000)
    task_override: TaskName | None = None
    session_id: str | None = Field(default=None, max_length=128)

    @field_validator("session_id")
    @classmethod
    def _non_empty_session(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("session_id cannot be blank")
        return value
