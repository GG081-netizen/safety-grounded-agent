"""Pydantic data models for the Procurement Sales Copilot Agent.

Phase 2 additions:
  - IntentResult (typed contract for Phase 5 IntentRouter)
  - Field validators for data integrity (score ranges, non-empty IDs, etc.)
  - Computed properties on DealScore, HealthScore, CustomerProfile
  - SalesStage state-machine helpers
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ═══════════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════════


class Intent(str, Enum):
    CUSTOMER_INTAKE = "customer_intake"
    MEETING_NOTE = "meeting_note"
    QUERY = "query"
    EMAIL_DRAFT = "email_draft"
    REPORT = "report"
    PODCAST_SCRIPT = "podcast_script"


class SalesStage(str, Enum):
    LEAD = "lead"
    REQUIREMENT_CONFIRMATION = "requirement_confirmation"
    QUOTATION = "quotation"
    NEGOTIATION = "negotiation"
    PROCUREMENT_APPROVAL = "procurement_approval"
    CONTRACT_SIGNING = "contract_signing"
    WON = "won"
    LOST = "lost"

# -- State-machine data (module-level to avoid Enum metaclass capture) --

_SALES_STAGE_ORDER = [
    SalesStage.LEAD,
    SalesStage.REQUIREMENT_CONFIRMATION,
    SalesStage.QUOTATION,
    SalesStage.NEGOTIATION,
    SalesStage.PROCUREMENT_APPROVAL,
    SalesStage.CONTRACT_SIGNING,
]
_SALES_TERMINAL: set[SalesStage] = {SalesStage.WON, SalesStage.LOST}

# Monkey-patch helpers onto SalesStage so they're available as methods.
# (Defined here rather than inside the class body because the Enum metaclass
#  converts underscore-prefixed class attributes to enum members.)


def _sales_is_terminal(self: SalesStage) -> bool:
    return self in _SALES_TERMINAL


def _sales_is_won(self: SalesStage) -> bool:
    return self == SalesStage.WON


def _sales_is_lost(self: SalesStage) -> bool:
    return self == SalesStage.LOST


def _sales_next_stages(self: SalesStage) -> list[SalesStage]:
    if self in _SALES_TERMINAL:
        return []
    try:
        idx = _SALES_STAGE_ORDER.index(self)
    except ValueError:
        return []
    if idx + 1 >= len(_SALES_STAGE_ORDER):
        return []  # last active stage → only WON/LOST (handled by can_transition_to)
    return [_SALES_STAGE_ORDER[idx + 1]]


def _sales_can_transition_to(self: SalesStage, target: SalesStage) -> bool:
    if self in _SALES_TERMINAL:
        return False
    if target == SalesStage.LEAD:  # re-qualification
        return True
    if target in _SALES_TERMINAL:  # close as won/lost
        return True
    return target in _sales_next_stages(self)


def _sales_active_stages() -> list[SalesStage]:
    return list(_SALES_STAGE_ORDER)


def _sales_from_string(value: str) -> SalesStage | None:
    try:
        return SalesStage(value.lower().strip())
    except ValueError:
        return None


SalesStage.is_terminal = _sales_is_terminal           # type: ignore[attr-defined]
SalesStage.is_won = _sales_is_won                      # type: ignore[attr-defined]
SalesStage.is_lost = _sales_is_lost                    # type: ignore[attr-defined]
SalesStage.next_stages = _sales_next_stages             # type: ignore[attr-defined]
SalesStage.can_transition_to = _sales_can_transition_to # type: ignore[attr-defined]
SalesStage.active_stages = staticmethod(_sales_active_stages)  # type: ignore[attr-defined]
SalesStage.from_string = staticmethod(_sales_from_string)      # type: ignore[attr-defined]


class CustomerStatus(str, Enum):
    NEW = "new"
    ACTIVE = "active"
    DORMANT = "dormant"
    CHURN_RISK = "churn_risk"
    WON = "won"
    LOST = "lost"


class ProductCategory(str, Enum):
    OFFICE_EQUIPMENT = "office_equipment"
    IT_EQUIPMENT = "it_equipment"
    MEETING_DEVICES = "meeting_devices"
    OFFICE_FURNITURE = "office_furniture"
    EMPLOYEE_BENEFITS = "employee_benefits"
    ENTERPRISE_SOFTWARE = "enterprise_software"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class InteractionType(str, Enum):
    CALL = "call"
    MEETING = "meeting"
    EMAIL = "email"
    WECHAT = "wechat"
    NOTE = "note"
    OTHER = "other"


class DealLevel(str, Enum):
    C = "C"
    B = "B"
    A = "A"
    S = "S"

    @classmethod
    def from_score(cls, score: int) -> DealLevel:
        """Resolve a raw score (0-100) to its grade."""
        if score <= 40:
            return cls.C
        if score <= 60:
            return cls.B
        if score <= 80:
            return cls.A
        return cls.S


class HealthStatus(str, Enum):
    COLD = "cold"
    WARM = "warm"
    HEALTHY = "healthy"

    @classmethod
    def from_score(cls, score: int) -> HealthStatus:
        """Resolve a health score (0-100) to its status label."""
        if score <= 40:
            return cls.COLD
        if score <= 70:
            return cls.WARM
        return cls.HEALTHY


# ═══════════════════════════════════════════════════════════════════════════════
# Components
# ═══════════════════════════════════════════════════════════════════════════════


class Contact(BaseModel):
    """A person associated with the customer account."""

    name: str = Field(min_length=1, max_length=128)
    title: str | None = None
    department: str | None = None
    influence_level: Literal["low", "medium", "high", "unknown"] = "unknown"
    email: str | None = None
    phone: str | None = None

    @property
    def is_decision_maker(self) -> bool:
        return self.influence_level == "high"


class ProcurementItem(BaseModel):
    """A single product or service being procured."""

    product_name: str = Field(min_length=1, max_length=256)
    category: ProductCategory | None = None
    quantity: int | None = Field(default=None, ge=1)
    unit_budget: float | None = Field(default=None, ge=0)
    total_budget: float | None = Field(default=None, ge=0)
    requirements: list[str] = Field(default_factory=list)

    @property
    def has_budget(self) -> bool:
        return self.unit_budget is not None or self.total_budget is not None

    @property
    def has_category(self) -> bool:
        return self.category is not None


class RiskItem(BaseModel):
    """A single identified risk for the deal."""

    level: RiskLevel
    reason: str = Field(min_length=1, max_length=1024)

    @property
    def is_critical(self) -> bool:
        return self.level == RiskLevel.CRITICAL

    @property
    def is_high_priority(self) -> bool:
        return self.level in (RiskLevel.HIGH, RiskLevel.CRITICAL)


class ProcurementSignals(BaseModel):
    """Qualitative signals extracted from customer interactions."""

    urgency_signal: str | None = None
    budget_signal: str | None = None
    decision_signal: str | None = None
    competition_signal: str | None = None
    engagement_signal: str | None = None

    @property
    def filled_signal_count(self) -> int:
        """How many of the 5 signal slots are populated."""
        return sum(
            1
            for s in (
                self.urgency_signal,
                self.budget_signal,
                self.decision_signal,
                self.competition_signal,
                self.engagement_signal,
            )
            if s
        )

    @property
    def has_competition(self) -> bool:
        return bool(self.competition_signal)


class DealScore(BaseModel):
    """Deal win-probability score with explainability fields.

    All dimension scores are 0-100. The final `score` is derived from
    weighted dimensions minus `risk_penalty`, clamped to 0-100.
    Confidence reflects how many dimensions were available for scoring.
    """

    score: int = Field(ge=0, le=100)
    level: DealLevel
    need_clarity: int = Field(default=0, ge=0, le=100)
    budget_fit: int = Field(default=0, ge=0, le=100)
    decision_maker_access: int = Field(default=0, ge=0, le=100)
    urgency: int = Field(default=0, ge=0, le=100)
    engagement: int = Field(default=0, ge=0, le=100)
    risk_penalty: int = Field(default=0, ge=0)

    confidence: Literal["high", "medium", "low"] = "medium"
    filled_dimensions: int = Field(default=5, ge=0, le=5)
    total_dimensions: int = Field(default=5, ge=1, le=5)
    missing_dimensions: list[str] = Field(default_factory=list)
    reasoning: dict[str, str] = Field(default_factory=dict)
    summary: str = ""

    @model_validator(mode="after")
    def _check_dimension_consistency(self) -> DealScore:
        """Ensure filled/missing counts match reality."""
        actual_filled = 5 - len(self.missing_dimensions)
        if actual_filled != self.filled_dimensions:
            # Auto-correct — trust missing_dimensions as source of truth
            object.__setattr__(self, "filled_dimensions", actual_filled)
        if self.filled_dimensions > self.total_dimensions:
            raise ValueError(
                f"filled_dimensions ({self.filled_dimensions}) cannot exceed "
                f"total_dimensions ({self.total_dimensions})"
            )
        return self

    @model_validator(mode="after")
    def _derive_confidence(self) -> DealScore:
        """Set confidence from dimension coverage if not explicitly overridden."""
        ratio = self.filled_dimensions / max(self.total_dimensions, 1)
        if ratio >= 0.8:
            derived = "high"
        elif ratio >= 0.5:
            derived = "medium"
        else:
            derived = "low"
        # Only override if the default "medium" is still in place and missing_dimensions
        # suggest otherwise (or user set it explicitly). We use a simple heuristic:
        # if confidence is medium but ratio < 0.5, fix it up.
        if self.confidence == "medium" and derived != "medium":
            object.__setattr__(self, "confidence", derived)
        return self

    @property
    def dimension_values(self) -> dict[str, int]:
        """Return the five dimension scores as a dict."""
        return {
            "need_clarity": self.need_clarity,
            "budget_fit": self.budget_fit,
            "decision_maker_access": self.decision_maker_access,
            "urgency": self.urgency,
            "engagement": self.engagement,
        }

    @property
    def coverage_ratio(self) -> float:
        """How many dimensions are filled (0.0–1.0)."""
        return self.filled_dimensions / max(self.total_dimensions, 1)

    @property
    def high_confidence(self) -> bool:
        return self.confidence == "high"

    @property
    def low_confidence(self) -> bool:
        return self.confidence == "low"


class HealthScore(BaseModel):
    """Customer relationship health score (0–100) with dimension breakdown."""

    health_score: int = Field(ge=0, le=100)
    status: HealthStatus
    recent_contact: int = Field(default=0, ge=0, le=20)
    responsiveness: int = Field(default=0, ge=0, le=20)
    decision_maker_involvement: int = Field(default=0, ge=0, le=20)
    need_clarity: int = Field(default=0, ge=0, le=20)
    budget_timeline_clarity: int = Field(default=0, ge=0, le=20)
    summary: str = ""

    @model_validator(mode="after")
    def _check_sum(self) -> HealthScore:
        """Warn if dimension scores sum beyond health_score (non-fatal)."""
        dim_sum = (
            self.recent_contact
            + self.responsiveness
            + self.decision_maker_involvement
            + self.need_clarity
            + self.budget_timeline_clarity
        )
        if dim_sum > 100:
            raise ValueError(
                f"Health dimensions sum to {dim_sum}, cannot exceed 100"
            )
        return self

    @property
    def dimension_values(self) -> dict[str, int]:
        return {
            "recent_contact": self.recent_contact,
            "responsiveness": self.responsiveness,
            "decision_maker_involvement": self.decision_maker_involvement,
            "need_clarity": self.need_clarity,
            "budget_timeline_clarity": self.budget_timeline_clarity,
        }

    @property
    def is_healthy(self) -> bool:
        return self.status == HealthStatus.HEALTHY

    @property
    def is_cold(self) -> bool:
        return self.status == HealthStatus.COLD


class FollowUpSuggestion(BaseModel):
    """A recommended next action with priority and rationale."""

    follow_up_priority: Literal["high", "medium", "low"]
    recommended_date: str | None = None
    recommended_action: str = Field(min_length=1, max_length=2048)
    reason: str = Field(min_length=1, max_length=2048)

    @property
    def is_urgent(self) -> bool:
        return self.follow_up_priority == "high"

    @property
    def has_date(self) -> bool:
        return self.recommended_date is not None


# ═══════════════════════════════════════════════════════════════════════════════
# Top-level business records
# ═══════════════════════════════════════════════════════════════════════════════


class CustomerProfile(BaseModel):
    """Complete customer profile — the single source of truth for one customer."""

    customer_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    customer_name: str = Field(min_length=1, max_length=256)
    aliases: list[str] = Field(default_factory=list)
    schema_version: int = Field(default=1, ge=1)
    version: int = Field(default=1, ge=0)

    source: str | None = None
    industry: str | None = None
    company_size: str | None = None

    procurement_department: str | None = None
    contacts: list[Contact] = Field(default_factory=list)

    procurement_items: list[ProcurementItem] = Field(default_factory=list)
    budget_range: str | None = None
    procurement_cycle: str | None = None
    timeline: str | None = None

    competitors: list[str] = Field(default_factory=list)
    decision_makers: list[str] = Field(default_factory=list)

    sales_stage: SalesStage = SalesStage.LEAD
    status: CustomerStatus = CustomerStatus.ACTIVE

    deal_score: DealScore | None = None
    health_score: HealthScore | None = None

    risks: list[RiskItem] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # -- Computed properties --

    @property
    def contact_count(self) -> int:
        return len(self.contacts)

    @property
    def has_contacts(self) -> bool:
        return len(self.contacts) > 0

    @property
    def primary_contact(self) -> Contact | None:
        """First contact, or None."""
        return self.contacts[0] if self.contacts else None

    @property
    def decision_maker_contacts(self) -> list[Contact]:
        """Contacts with high influence."""
        return [c for c in self.contacts if c.is_decision_maker]

    @property
    def has_decision_maker_contact(self) -> bool:
        return any(c.is_decision_maker for c in self.contacts)

    @property
    def item_count(self) -> int:
        return len(self.procurement_items)

    @property
    def has_procurement_items(self) -> bool:
        return len(self.procurement_items) > 0

    @property
    def high_priority_risk_count(self) -> int:
        return sum(1 for r in self.risks if r.is_high_priority)

    @property
    def has_risks(self) -> bool:
        return len(self.risks) > 0

    @property
    def has_deal_score(self) -> bool:
        return self.deal_score is not None

    @property
    def has_health_score(self) -> bool:
        return self.health_score is not None

    @property
    def is_terminal_stage(self) -> bool:
        return self.sales_stage.is_terminal()

    @property
    def is_won(self) -> bool:
        return self.sales_stage.is_won()

    @property
    def is_lost(self) -> bool:
        return self.sales_stage.is_lost()

    @property
    def days_since_update(self) -> int:
        """Days since the profile was last updated (UTC)."""
        delta = datetime.now(timezone.utc) - self.updated_at
        return max(0, delta.days)

    # -- Helper methods --

    def bump_version(self) -> None:
        """Increment version and touch updated_at. Call before save."""
        self.version += 1
        self.updated_at = datetime.now(timezone.utc)

    def transition_to(self, stage: SalesStage) -> bool:
        """Attempt a stage transition. Returns True if valid."""
        if not self.sales_stage.can_transition_to(stage):
            return False
        self.sales_stage = stage
        self.updated_at = datetime.now(timezone.utc)
        return True

    def add_contact(self, contact: Contact) -> None:
        self.contacts.append(contact)
        self.updated_at = datetime.now(timezone.utc)

    def add_risk(self, risk: RiskItem) -> None:
        self.risks.append(risk)
        self.updated_at = datetime.now(timezone.utc)

    def set_deal_score(self, score: DealScore) -> None:
        self.deal_score = score
        self.updated_at = datetime.now(timezone.utc)

    def set_health_score(self, score: HealthScore) -> None:
        self.health_score = score
        self.updated_at = datetime.now(timezone.utc)


class InteractionRecord(BaseModel):
    """A single customer interaction — meeting, call, email, etc."""

    interaction_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    customer_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: InteractionType = InteractionType.NOTE

    raw_text: str = ""
    summary: str = ""
    key_quotes: list[str] = Field(default_factory=list)

    extracted_facts: dict = Field(default_factory=dict)
    procurement_signals: ProcurementSignals | None = None
    risks: list[RiskItem] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def has_signals(self) -> bool:
        return self.procurement_signals is not None

    @property
    def has_risks(self) -> bool:
        return len(self.risks) > 0

    @property
    def has_raw_text(self) -> bool:
        return bool(self.raw_text)

    @property
    def word_count(self) -> int:
        """Approximate word count of raw_text, or 0."""
        return len(self.raw_text.split()) if self.raw_text else 0


class IntentResult(BaseModel):
    """Result of intent routing — produced by IntentRouter (Phase 5).

    Defines the contract between the router and the rest of the system.
    The `intent` field uses the Intent enum; `confidence` is 0.0–1.0.
    """

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    alternative_intents: list[Intent] = Field(default_factory=list)

    @property
    def high_confidence(self) -> bool:
        return self.confidence >= 0.8

    @property
    def medium_confidence(self) -> bool:
        return 0.5 <= self.confidence < 0.8

    @property
    def low_confidence(self) -> bool:
        return self.confidence < 0.5

    @property
    def confidence_label(self) -> Literal["high", "medium", "low"]:
        if self.confidence >= 0.8:
            return "high"
        if self.confidence >= 0.5:
            return "medium"
        return "low"

    @property
    def is_ambiguous(self) -> bool:
        """True if there are alternative intents with meaningful confidence."""
        return len(self.alternative_intents) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Agent Runtime Protocol (Phase 1)
# ═══════════════════════════════════════════════════════════════════════════════


class InteractionMetadata(BaseModel):
    """Runtime metadata for one agent interaction turn.

    Captures token usage, cost, latency, and tool-call summary for
    observability without coupling to the LLM client.
    """

    session_id: str = ""
    intent: str | None = None
    intent_confidence: float | None = None
    tools_called: list[str] = Field(default_factory=list)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ToolResult(BaseModel):
    """Unified return type for every tool execution.

    Every tool MUST return this structure so the agent can reason about
    success / partial-success / failure uniformly.
    """

    success: bool
    tool_name: str = ""
    data: dict | list | str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: str = ""

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def is_partial(self) -> bool:
        """Success with warnings — partial / degraded result."""
        return self.success and len(self.warnings) > 0


class Interaction(BaseModel):
    """Complete input/output protocol for one agent turn.

    This is the *runtime* protocol — distinct from InteractionRecord which
    is the *business* record persisted to disk.
    """

    session_id: str = Field(min_length=1, max_length=64)
    user_input: str
    intent_result: IntentResult | None = None
    tools_called: list[ToolResult] = Field(default_factory=list)
    agent_response: str = ""
    metadata: InteractionMetadata = Field(default_factory=InteractionMetadata)

    @property
    def tool_success_count(self) -> int:
        return sum(1 for t in self.tools_called if t.success)

    @property
    def tool_failure_count(self) -> int:
        return sum(1 for t in self.tools_called if not t.success)

    @property
    def all_tools_succeeded(self) -> bool:
        return self.tool_failure_count == 0 and len(self.tools_called) > 0
