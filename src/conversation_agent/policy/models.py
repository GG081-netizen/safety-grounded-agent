"""Policy decision models for the safety firewall."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PolicyStatus = Literal["SAFE", "UNCERTAIN", "BLOCKED"]


class PolicyDecision(BaseModel):
    """A normalized safety decision for one user request."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        hide_input_in_errors=True,
    )

    status: PolicyStatus
    reason: str = ""
    matched_rules: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    classifier_used: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @property
    def is_blocked(self) -> bool:
        return self.status == "BLOCKED"

    @property
    def is_uncertain(self) -> bool:
        return self.status == "UNCERTAIN"


class RiskCategory(str, Enum):
    PRIVACY_OVERREACH = "privacy_overreach"
    SENSITIVE_ATTRIBUTE_INFERENCE = "sensitive_attribute_inference"
    LEGAL_FINANCIAL_FINAL_JUDGMENT = "legal_financial_final_judgment"
    SALES_MISREPRESENTATION = "sales_misrepresentation"
    UNSUPPORTED_BUSINESS_CLAIM = "unsupported_business_claim"
    BUSINESS_UNCERTAIN = "business_uncertain"


class RiskAction(str, Enum):
    REQUEST_PRIVATE_CUSTOMER_DATA = "request_private_customer_data"
    INFER_SENSITIVE_ATTRIBUTE = "infer_sensitive_attribute"
    MAKE_FINAL_LEGAL_JUDGMENT = "make_final_legal_judgment"
    GUARANTEE_FINANCIAL_RETURN = "guarantee_financial_return"
    MAKE_ABSOLUTE_DELIVERY_COMMITMENT = "make_absolute_delivery_commitment"
    GUARANTEE_SALES_OUTCOME = "guarantee_sales_outcome"
    FABRICATE_CUSTOMER_CASE = "fabricate_customer_case"
    FABRICATE_INVENTORY = "fabricate_inventory"
    FABRICATE_CERTIFICATION = "fabricate_certification"
    FABRICATE_DELIVERY_RECORD = "fabricate_delivery_record"
    REQUEST_BUSINESS_CONFIRMATION = "request_business_confirmation"


class RiskSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DetectionSource(str, Enum):
    DETERMINISTIC_RULE = "DETERMINISTIC_RULE"
    REGEX_RULE = "REGEX_RULE"
    CLASSIFIER = "CLASSIFIER"


class UserStance(str, Enum):
    REQUEST = "REQUEST"
    PROHIBIT = "PROHIBIT"
    AUDIT = "AUDIT"
    DISCUSS = "DISCUSS"
    QUOTE = "QUOTE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class TextSpan:
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class RiskCandidate:
    candidate_id: str
    rule_id: str
    category: RiskCategory
    action: RiskAction
    severity: RiskSeverity
    evidence_span: TextSpan
    source: DetectionSource
    confidence: float
    priority: int
    status_hint: PolicyStatus
    reason: str


@dataclass(frozen=True, slots=True)
class RiskStance:
    candidate_id: str
    stance: UserStance
    confidence: float
    reason_code: str
    source: str
