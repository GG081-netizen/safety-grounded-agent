"""Immutable contracts for the approved LLM model registry."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from conversation_agent.llm.errors import (
    ModelRegistryError,
    ModelRouteValidationError,
    RuntimeModelProfileError,
    UnconfiguredModelProfileError,
)
from conversation_agent.task_types import TaskName


class ModelProfile(str, Enum):
    LIGHTWEIGHT = "lightweight"
    STANDARD = "standard"
    ADVANCED = "advanced"
    EVALUATOR = "evaluator"


class ModelCapability(str, Enum):
    CHAT = "chat"
    TOOL_CALLING = "tool_calling"
    STRUCTURED_OUTPUT = "structured_output"
    THINKING = "thinking"


ModelProvider = Literal[
    "dashscope", "deepseek", "anthropic", "local_openai", "not_configured"
]


class ModelProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    profile: ModelProfile
    provider: ModelProvider
    model: str = Field(min_length=1)
    configured: bool
    runtime_selectable: bool
    capabilities: tuple[ModelCapability, ...] = ()
    enable_thinking: bool = False
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=10)
    read_timeout_max_retries: int = Field(default=1, ge=0, le=10)
    retry_total_budget_seconds: float = Field(default=10.0, ge=0)
    overall_deadline_seconds: float = Field(default=45.0, gt=0)
    max_output_tokens: int = Field(default=4096, ge=1)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _stable_capabilities(cls, value: object) -> tuple[ModelCapability, ...]:
        if value is None:
            return ()
        values = {ModelCapability(item) for item in value}  # type: ignore[union-attr]
        return tuple(capability for capability in ModelCapability if capability in values)

    @model_validator(mode="after")
    def _validate_profile(self) -> "ModelProfileConfig":
        if self.runtime_selectable and not self.configured:
            raise ValueError("runtime_selectable requires configured=true")
        if self.configured:
            if self.provider == "not_configured" or self.model == "not_configured":
                raise ValueError("configured profile requires provider and model")
            if ModelCapability.CHAT not in self.capabilities:
                raise ValueError("configured callable profile requires CHAT capability")
        elif self.provider != "not_configured" or self.model != "not_configured":
            raise ValueError("unconfigured profile must use not_configured sentinels")
        if self.enable_thinking and ModelCapability.THINKING not in self.capabilities:
            raise ValueError("enable_thinking requires THINKING capability")
        if self.overall_deadline_seconds <= 0:
            raise ValueError("overall_deadline_seconds must be positive")
        return self


class ModelRouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    task_name: TaskName
    selected_profile: ModelProfile
    provider: ModelProvider
    model: str = Field(min_length=1)
    enable_thinking: bool
    reason: str = Field(min_length=1)
    fallback_profile: ModelProfile | None = None
    routing_policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _different_fallback(self) -> "ModelRouteDecision":
        if self.fallback_profile == self.selected_profile:
            raise ValueError("fallback_profile must differ from selected_profile")
        return self


class ModelRegistryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    lightweight: ModelProfileConfig
    standard: ModelProfileConfig
    advanced: ModelProfileConfig
    evaluator: ModelProfileConfig
    registry_version: str = Field(min_length=1)
    default_profile: ModelProfile = ModelProfile.STANDARD

    @model_validator(mode="after")
    def _validate_slots(self) -> "ModelRegistryConfig":
        for profile in ModelProfile:
            config = getattr(self, profile.value)
            if config.profile != profile:
                raise ValueError(
                    f"Registry key {profile.value} does not match profile "
                    f"{config.profile.value}"
                )
        if not self.standard.configured or not self.standard.runtime_selectable:
            raise ValueError("standard profile must be configured and runtime selectable")
        self.resolve_runtime(self.default_profile)
        return self

    def _slot(self, profile: ModelProfile) -> ModelProfileConfig:
        config = getattr(self, profile.value, None)
        if not isinstance(config, ModelProfileConfig):
            raise ModelRegistryError(f"Unknown model profile: {profile}")
        return config

    def resolve(self, profile: ModelProfile) -> ModelProfileConfig:
        config = self._slot(profile)
        if not config.configured:
            raise UnconfiguredModelProfileError(profile.value)
        return config

    def resolve_runtime(self, profile: ModelProfile) -> ModelProfileConfig:
        config = self.resolve(profile)
        if not config.runtime_selectable:
            raise RuntimeModelProfileError(profile.value)
        return config

    def validate_route(self, decision: ModelRouteDecision) -> ModelProfileConfig:
        selected = self.resolve_runtime(decision.selected_profile)
        if decision.provider != selected.provider:
            raise ModelRouteValidationError("provider_mismatch")
        if decision.model != selected.model:
            raise ModelRouteValidationError("model_mismatch")
        if decision.enable_thinking != selected.enable_thinking:
            raise ModelRouteValidationError("thinking_mismatch")
        if decision.fallback_profile is not None:
            self.resolve_runtime(decision.fallback_profile)
        return selected


def default_model_registry(
    *,
    base_timeout: float = 30.0,
    max_retries: int = 2,
    read_timeout_max_retries: int = 1,
    retry_total_budget_seconds: float = 10.0,
    overall_deadline_seconds: float = 45.0,
    max_output_tokens: int = 4096,
) -> ModelRegistryConfig:
    common = {
        "timeout_seconds": base_timeout,
        "max_retries": max_retries,
        "read_timeout_max_retries": read_timeout_max_retries,
        "retry_total_budget_seconds": retry_total_budget_seconds,
        "overall_deadline_seconds": overall_deadline_seconds,
        "max_output_tokens": max_output_tokens,
    }
    return ModelRegistryConfig(
        lightweight=ModelProfileConfig(
            profile=ModelProfile.LIGHTWEIGHT,
            provider="not_configured",
            model="not_configured",
            configured=False,
            runtime_selectable=False,
            capabilities=(),
            **common,
        ),
        standard=ModelProfileConfig(
            profile=ModelProfile.STANDARD,
            provider="dashscope",
            model="qwen3-8b",
            configured=True,
            runtime_selectable=True,
            capabilities=(
                ModelCapability.CHAT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.STRUCTURED_OUTPUT,
            ),
            **common,
        ),
        advanced=ModelProfileConfig(
            profile=ModelProfile.ADVANCED,
            provider="dashscope",
            model="qwen3-14b",
            configured=True,
            runtime_selectable=False,
            capabilities=(
                ModelCapability.CHAT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.STRUCTURED_OUTPUT,
            ),
            **common,
        ),
        evaluator=ModelProfileConfig(
            profile=ModelProfile.EVALUATOR,
            provider="dashscope",
            model="qwen3-14b",
            configured=True,
            runtime_selectable=False,
            capabilities=(
                ModelCapability.CHAT,
                ModelCapability.STRUCTURED_OUTPUT,
                ModelCapability.THINKING,
            ),
            enable_thinking=True,
            **common,
        ),
        registry_version="qwen3_profiles_v1",
    )
