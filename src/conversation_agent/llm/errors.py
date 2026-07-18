"""LLM configuration and model-registry errors."""


class LLMConfigurationError(RuntimeError):
    """Raised when the configured LLM provider cannot be used."""


class ModelRegistryError(ValueError):
    """Base error for invalid model registry operations."""


class UnconfiguredModelProfileError(ModelRegistryError):
    """Raised when a profile has no usable provider/model configuration."""


class RuntimeModelProfileError(ModelRegistryError):
    """Raised when a configured profile is not selectable by runtime code."""


class ModelRouteValidationError(ModelRegistryError):
    """Raised when a route decision disagrees with its registry snapshot."""
