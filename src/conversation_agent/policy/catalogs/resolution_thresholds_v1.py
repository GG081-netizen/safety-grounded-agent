"""Resolution thresholds for policy matrix version 1."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolicyResolutionThresholds:
    """Rule certainty thresholds, not statistically calibrated probabilities."""

    discuss_safe_min_confidence: float = 0.90
    quote_safe_min_confidence: float = 0.90
    unknown_requires_uncertain: bool = True


RESOLUTION_THRESHOLDS_V1 = PolicyResolutionThresholds()
