import pytest
from pydantic import ValidationError

from conversation_agent.llm.errors import (
    ModelRouteValidationError,
    RuntimeModelProfileError,
    UnconfiguredModelProfileError,
)
from conversation_agent.llm.models import (
    ModelCapability,
    ModelProfile,
    ModelProfileConfig,
    ModelRouteDecision,
    default_model_registry,
)


pytestmark = pytest.mark.unit


def test_default_registry_approved_capabilities():
    registry = default_model_registry()
    assert registry.lightweight.capabilities == ()
    assert registry.standard.capabilities == (
        ModelCapability.CHAT,
        ModelCapability.TOOL_CALLING,
        ModelCapability.STRUCTURED_OUTPUT,
    )
    assert registry.advanced.capabilities == registry.standard.capabilities
    assert registry.evaluator.capabilities == (
        ModelCapability.CHAT,
        ModelCapability.STRUCTURED_OUTPUT,
        ModelCapability.THINKING,
    )


def test_profile_configuration_and_runtime_selection_are_distinct():
    registry = default_model_registry()
    assert registry.resolve(ModelProfile.ADVANCED).model == "qwen3-14b"
    assert registry.resolve(ModelProfile.EVALUATOR).enable_thinking is True
    with pytest.raises(RuntimeModelProfileError):
        registry.resolve_runtime(ModelProfile.ADVANCED)
    with pytest.raises(UnconfiguredModelProfileError):
        registry.resolve(ModelProfile.LIGHTWEIGHT)


def test_registry_is_deeply_immutable():
    registry = default_model_registry()
    with pytest.raises(ValidationError):
        registry.standard.model = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        registry.standard.capabilities[0] = ModelCapability.THINKING  # type: ignore[index]


def test_route_snapshot_must_match_registry():
    registry = default_model_registry()
    decision = ModelRouteDecision(
        task_name="qa",
        selected_profile=ModelProfile.STANDARD,
        provider="dashscope",
        model="qwen3-14b",
        enable_thinking=False,
        reason="test",
        routing_policy_version="model_router_v1",
    )
    with pytest.raises(ModelRouteValidationError, match="model_mismatch"):
        registry.validate_route(decision)


def test_thinking_requires_approved_capability():
    with pytest.raises(ValidationError):
        ModelProfileConfig(
            profile=ModelProfile.STANDARD,
            provider="dashscope",
            model="qwen3-8b",
            configured=True,
            runtime_selectable=True,
            capabilities=(ModelCapability.CHAT,),
            enable_thinking=True,
        )
