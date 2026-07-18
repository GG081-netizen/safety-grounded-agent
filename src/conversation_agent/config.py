"""Central configuration for the Procurement Sales Copilot Agent.

All config lives in a Pydantic AppConfig model with validation.
Module-level accessors resolve from a lazy singleton for backward compatibility
via module __getattr__ — they return plain Python types, not proxies.
"""

from __future__ import annotations

import json
import os
import re
from enum import Enum
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from conversation_agent.llm.models import (
    ModelProfile,
    ModelProfileConfig,
    ModelRegistryConfig,
    default_model_registry,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Sub-config models
# ═══════════════════════════════════════════════════════════════════════════════


class ScoringWeights(BaseModel):
    """Per-dimension weights for deal scoring. Must sum to 1.0."""

    need_clarity: float = 0.30
    budget_fit: float = 0.25
    decision_maker_access: float = 0.20
    urgency: float = 0.15
    engagement: float = 0.10

    @model_validator(mode="after")
    def _check_sum(self) -> ScoringWeights:
        total = (
            self.need_clarity
            + self.budget_fit
            + self.decision_maker_access
            + self.urgency
            + self.engagement
        )
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Scoring weights must sum to 1.0, got {total:.2f}")
        return self

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class RiskPenaltyConfig(BaseModel):
    """Risk-level → penalty points."""

    low: int = 3
    medium: int = 6
    high: int = 10
    critical: int = 15

    def as_dict(self) -> dict[str, int]:
        return self.model_dump()


class ScoreLevel(BaseModel):
    label: str  # C, B, A, S
    min_score: int
    max_score: int


class ScoreLevelConfig(BaseModel):
    """Score → grade mapping."""

    levels: list[ScoreLevel] = Field(default_factory=lambda: [
        ScoreLevel(label="C", min_score=0, max_score=40),
        ScoreLevel(label="B", min_score=41, max_score=60),
        ScoreLevel(label="A", min_score=61, max_score=80),
        ScoreLevel(label="S", min_score=81, max_score=100),
    ])

    def resolve(self, score: int) -> str:
        for level in self.levels:
            if level.min_score <= score <= level.max_score:
                return level.label
        return "C"


class HealthWeights(BaseModel):
    """Per-dimension max score for health assessment."""

    recent_contact: int = 20
    responsiveness: int = 20
    decision_maker_involvement: int = 20
    need_clarity: int = 20
    budget_timeline_clarity: int = 20

    @property
    def max_total(self) -> int:
        return (
            self.recent_contact
            + self.responsiveness
            + self.decision_maker_involvement
            + self.need_clarity
            + self.budget_timeline_clarity
        )

    def as_dict(self) -> dict[str, int]:
        return self.model_dump()


class HealthTimeDecayThreshold(BaseModel):
    days: int
    multiplier: float


class HealthTimeDecay(BaseModel):
    """Days-since-contact → multiplier for recent_contact score."""

    thresholds: list[HealthTimeDecayThreshold] = Field(default_factory=lambda: [
        HealthTimeDecayThreshold(days=1, multiplier=1.0),
        HealthTimeDecayThreshold(days=3, multiplier=0.9),
        HealthTimeDecayThreshold(days=7, multiplier=0.7),
        HealthTimeDecayThreshold(days=14, multiplier=0.5),
        HealthTimeDecayThreshold(days=30, multiplier=0.3),
    ])

    def resolve(self, days_since_contact: int) -> float:
        """Return the decay multiplier for a given number of days."""
        multiplier = 0.1
        for t in sorted(self.thresholds, key=lambda x: x.days):
            if days_since_contact <= t.days:
                return t.multiplier
            multiplier = t.multiplier
        return multiplier


class HealthStatusConfig(BaseModel):
    """Health score → label mapping."""

    cold_max: int = 40
    warm_max: int = 70
    healthy_max: int = 100

    def resolve(self, score: int) -> str:
        if score <= self.cold_max:
            return "cold"
        if score <= self.warm_max:
            return "warm"
        return "healthy"


class LLMConfig(BaseModel):
    """LLM client configuration."""

    model_config = ConfigDict(
        hide_input_in_errors=True,
        validate_assignment=True,
    )

    provider: Literal["dashscope", "deepseek", "anthropic"] = "dashscope"
    default_profile: ModelProfile = ModelProfile.STANDARD
    model_registry: ModelRegistryConfig = Field(default_factory=default_model_registry)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    auth_token: SecretStr = Field(default_factory=lambda: SecretStr(""))
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen3-8b"
    max_tool_rounds: int = Field(default=5, ge=1, le=20)
    max_retries: int = Field(default=2, ge=0, le=10)
    read_timeout_max_retries: int = Field(default=1, ge=0, le=10)
    retry_total_budget_seconds: float = Field(default=10.0, ge=0)
    overall_deadline_seconds: float = Field(default=45.0, gt=0)
    retry_base_delay: float = Field(default=1.0, ge=0.1)
    request_timeout: float = Field(default=30.0, ge=1.0)
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)
    max_tokens: int = Field(default=4096, ge=1, le=32000)

    def api_key_value(self) -> str:
        return self.api_key.get_secret_value()

    def auth_token_value(self) -> str:
        return self.auth_token.get_secret_value()


class RagServiceConfig(BaseModel):
    """External/local RAG service configuration."""

    provider: Literal["external", "local"] = "external"
    base_url: str = "http://127.0.0.1:8001"
    timeout_seconds: float = Field(default=30.0, ge=0.1)
    fallback_to_local: bool = True
    include_raw_response: bool = False


class OIDCConfig(BaseModel):
    """OIDC-compatible JWT resource-server configuration."""

    issuer: str = ""
    audience: str = ""
    jwks_url: str = ""
    tenant_id: str = "single_tenant"
    expected_organization_id: str = "default_organization"
    algorithm: Literal["RS256"] = "RS256"
    clock_skew_seconds: int = Field(default=60, ge=0, le=600)
    max_token_lifetime_seconds: int = Field(default=3600, gt=0)
    max_token_bytes: int = Field(default=8192, ge=1024)
    min_rsa_key_size_bits: int = Field(default=2048, ge=2048)
    max_kid_bytes: int = Field(default=128, ge=1, le=1024)
    jwks_cache_ttl_seconds: float = Field(default=300.0, gt=0)
    negative_kid_ttl_seconds: float = Field(default=10.0, gt=0)
    jwks_max_response_bytes: int = Field(default=262_144, ge=1024)
    jwks_max_keys: int = Field(default=20, ge=1, le=1000)
    jwks_timeout_seconds: float = Field(default=5.0, gt=0)
    required_token_use_claim: str | None = "token_use"
    required_token_use_value: str | None = "access"
    required_typ_header: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.issuer and self.audience and self.jwks_url)


class APIConfig(BaseModel):
    """Public HTTP surface configuration."""

    docs_enabled: bool = True


class StorageConfig(BaseModel):
    """Filesystem storage configuration."""

    data_dir: Path = Path("./data")
    customers_dir_name: str = "customers"
    interactions_dir_name: str = "interactions"
    backups_dir_name: str = "backups"
    aliases_file_name: str = "aliases.json"
    backup_enabled: bool = True
    backup_max_keep: int = Field(default=50, ge=1)

    @property
    def customers_dir(self) -> Path:
        return self.data_dir / self.customers_dir_name

    @property
    def interactions_dir(self) -> Path:
        return self.data_dir / self.interactions_dir_name

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / self.backups_dir_name

    @property
    def aliases_file(self) -> Path:
        return self.data_dir / self.aliases_file_name


class FollowUpRule(BaseModel):
    condition: str
    priority: Literal["high", "medium", "low"]
    action: str


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    jsonl_enabled: bool = True
    jsonl_file: str = "logs/agent.jsonl"


class PersistenceMode(str, Enum):
    POSTGRES = "postgres"
    NULL = "null"
    FAKE = "fake"


class IdempotencyHeaderMode(str, Enum):
    OPTIONAL = "optional"
    REQUIRED = "required"


class DatabaseTlsMode(str, Enum):
    DISABLE = "disable"
    REQUIRE = "require"
    VERIFY_CA = "verify_ca"
    VERIFY_FULL = "verify_full"


class DatabaseConfig(BaseModel):
    """PostgreSQL / SQLAlchemy async persistence configuration.

    Establishes the configuration Contract for M1.4 without wiring it into
    the HTTP request chain.  Production fail-closed semantics are enforced
    by the AppConfig validator, not by this model alone.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    persistence_mode: PersistenceMode | None = None
    idempotency_header_mode: IdempotencyHeaderMode | None = None
    enabled: bool = True
    required: bool = False
    url: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    pool_size: int = Field(default=5, ge=1, le=50)
    max_overflow: int = Field(default=10, ge=0, le=100)
    pool_timeout_seconds: float = Field(default=30.0, ge=1.0)
    pool_recycle_seconds: float = Field(default=3600.0, ge=1.0)
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    statement_timeout_ms: int = Field(default=45_000, gt=0, le=600_000)
    lock_timeout_ms: int = Field(default=5_000, gt=0, le=120_000)
    idle_in_transaction_session_timeout_ms: int = Field(
        default=30_000, gt=0, le=600_000
    )
    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    graceful_shutdown_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    tls_mode: DatabaseTlsMode = DatabaseTlsMode.DISABLE
    tls_ca_file: str = Field(default="", repr=False)
    tls_client_cert_file: str = Field(default="", repr=False)
    tls_client_key_file: str = Field(default="", repr=False)
    schema_name: str = Field(default="public", min_length=1, max_length=63)
    max_clock_drift_seconds: float = Field(default=5.0, ge=0, le=300)
    doctor_statement_timeout_ms: int = Field(default=3_000, gt=0, le=60_000)
    doctor_full_statement_timeout_ms: int = Field(
        default=15_000, gt=0, le=300_000
    )
    doctor_quick_overall_timeout_seconds: float = Field(
        default=10.0, gt=0, le=120
    )
    doctor_full_overall_timeout_seconds: float = Field(
        default=60.0, gt=0, le=900
    )
    echo: bool = False
    auto_migrate: bool = False
    store_user_text: bool = False
    idempotency_ttl_seconds: int = Field(default=3600, ge=60)
    stale_in_progress_timeout_seconds: int = Field(default=300, ge=60)
    max_idempotency_key_bytes: int = Field(default=255, ge=1, le=4096)
    max_replay_snapshot_bytes: int = Field(default=262_144, ge=1024)
    expected_revision: str = Field(default="0001", min_length=1, max_length=64)

    @model_validator(mode="after")
    def _enabled_required_consistency(self) -> "DatabaseConfig":
        if not self.enabled and self.required:
            raise ValueError(
                "database cannot be required when disabled "
                "(enabled=false, required=true)"
            )
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.schema_name):
            raise ValueError("database schema name is invalid")
        if self.tls_mode in (
            DatabaseTlsMode.VERIFY_CA,
            DatabaseTlsMode.VERIFY_FULL,
        ) and not self.tls_ca_file:
            raise ValueError("verified database TLS requires a CA file")
        if bool(self.tls_client_cert_file) != bool(self.tls_client_key_file):
            raise ValueError("database client certificate and key must be paired")
        return self

    @property
    def is_configured(self) -> bool:
        """A database URL has been supplied (does not guarantee connectivity)."""
        return bool(self.url_value)

    @property
    def url_value(self) -> str:
        return self.url.get_secret_value()

    @property
    def effective_persistence_mode(self) -> PersistenceMode:
        if self.persistence_mode is not None:
            return self.persistence_mode
        return (
            PersistenceMode.POSTGRES
            if self.enabled and self.is_configured
            else PersistenceMode.NULL
        )

    @property
    def effective_idempotency_header_mode(self) -> IdempotencyHeaderMode:
        return self.idempotency_header_mode or IdempotencyHeaderMode.OPTIONAL


# ═══════════════════════════════════════════════════════════════════════════════
# Top-level AppConfig
# ═══════════════════════════════════════════════════════════════════════════════


class AppConfig(BaseModel):
    """Master configuration for the Procurement Sales Copilot Agent."""

    model_config = ConfigDict(hide_input_in_errors=True)

    runtime_mode: Literal["demo", "test", "production"] = "demo"
    scoring: ScoringWeights = Field(default_factory=ScoringWeights)
    risk_penalty: RiskPenaltyConfig = Field(default_factory=RiskPenaltyConfig)
    score_levels: ScoreLevelConfig = Field(default_factory=ScoreLevelConfig)
    health_weights: HealthWeights = Field(default_factory=HealthWeights)
    health_time_decay: HealthTimeDecay = Field(default_factory=HealthTimeDecay)
    health_status: HealthStatusConfig = Field(default_factory=HealthStatusConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    rag_service: RagServiceConfig = Field(default_factory=RagServiceConfig)
    oidc: OIDCConfig = Field(default_factory=OIDCConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    follow_up_rules: list[FollowUpRule] = Field(default_factory=list)
    schema_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _validate_production_security(self) -> "AppConfig":
        persistence_mode = self.database.effective_persistence_mode
        header_mode = self.database.effective_idempotency_header_mode
        if (
            persistence_mode is PersistenceMode.NULL
            and header_mode is IdempotencyHeaderMode.REQUIRED
        ):
            raise ValueError(
                "required idempotency headers need persistent storage"
            )
        if persistence_mode is PersistenceMode.POSTGRES:
            if not self.database.url_value:
                raise ValueError("postgres persistence requires database URL")
            if not self.database.url_value.startswith("postgresql+asyncpg://"):
                raise ValueError(
                    "postgres persistence requires the asyncpg PostgreSQL driver"
                )
        if persistence_mode is PersistenceMode.FAKE and self.runtime_mode != "test":
            raise ValueError("fake persistence is only allowed in test mode")
        if self.runtime_mode == "production":
            if not self.oidc.is_configured:
                raise ValueError("production mode requires complete OIDC configuration")
            if not (
                self.oidc.required_typ_header
                or (
                    self.oidc.required_token_use_claim
                    and self.oidc.required_token_use_value
                )
            ):
                raise ValueError("production mode requires an access-token type rule")
            if not self.oidc.jwks_url.startswith("https://"):
                raise ValueError("production JWKS URL must use HTTPS")
            # --- M1.4-A: production database Contract ---
            if self.database.persistence_mode is None:
                raise ValueError("production mode requires explicit persistence mode")
            if self.database.idempotency_header_mode is None:
                raise ValueError(
                    "production mode requires explicit idempotency header mode"
                )
            if persistence_mode is not PersistenceMode.POSTGRES:
                raise ValueError("production mode requires postgres persistence")
            if self.database.auto_migrate:
                raise ValueError("auto_migrate must be false in production")
            host = urlsplit(self.database.url_value).hostname
            if (
                host not in {None, "localhost", "127.0.0.1", "::1"}
                and self.database.tls_mode is DatabaseTlsMode.DISABLE
            ):
                raise ValueError("production remote PostgreSQL requires TLS")
        return self


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton + factory
# ═══════════════════════════════════════════════════════════════════════════════

_config: AppConfig | None = None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _first_non_empty_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _build_model_registry() -> ModelRegistryConfig:
    timeout = float(os.getenv("CONVAGENT_LLM_TIMEOUT_SECONDS", "30"))
    max_retries = int(os.getenv("CONVAGENT_LLM_MAX_RETRIES", "2"))
    read_retries = int(os.getenv("CONVAGENT_LLM_READ_TIMEOUT_MAX_RETRIES", "1"))
    retry_budget = float(
        os.getenv("CONVAGENT_LLM_RETRY_TOTAL_BUDGET_SECONDS", "10")
    )
    deadline = float(os.getenv("CONVAGENT_LLM_OVERALL_DEADLINE_SECONDS", "45"))
    max_tokens = int(os.getenv("CONVAGENT_LLM_MAX_OUTPUT_TOKENS", "4096"))
    registry = default_model_registry(
        base_timeout=timeout,
        max_retries=max_retries,
        read_timeout_max_retries=read_retries,
        retry_total_budget_seconds=retry_budget,
        overall_deadline_seconds=deadline,
        max_output_tokens=max_tokens,
    )

    provider = os.getenv("CONVAGENT_LLM_PROVIDER", "dashscope").strip() or "dashscope"
    if provider == "deepseek":
        standard_provider = "deepseek"
        standard_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip() or "deepseek-chat"
    elif provider == "anthropic":
        standard_provider = "anthropic"
        standard_model = _first_non_empty_env(
            "ANTHROPIC_MODEL", "CONVERSATION_AGENT_MODEL", default="claude-sonnet-4-6"
        )
    else:
        standard_provider = "dashscope"
        standard_model = _first_non_empty_env(
            "CONVAGENT_LLM_STANDARD_MODEL", default="qwen3-8b"
        )

    standard = ModelProfileConfig.model_validate(
        {
            **registry.standard.model_dump(),
            "provider": standard_provider,
            "model": standard_model,
            "configured": _env_bool("CONVAGENT_LLM_STANDARD_ENABLED", True),
            "runtime_selectable": True,
            "enable_thinking": _env_bool(
                "CONVAGENT_LLM_STANDARD_THINKING", False
            ),
        }
    )
    advanced = ModelProfileConfig.model_validate(
        {
            **registry.advanced.model_dump(),
            "model": _first_non_empty_env(
                "CONVAGENT_LLM_ADVANCED_MODEL", default="qwen3-14b"
            ),
            "configured": _env_bool("CONVAGENT_LLM_ADVANCED_ENABLED", True),
            "enable_thinking": _env_bool(
                "CONVAGENT_LLM_ADVANCED_THINKING", False
            ),
        }
    )
    evaluator = ModelProfileConfig.model_validate(
        {
            **registry.evaluator.model_dump(),
            "model": _first_non_empty_env(
                "CONVAGENT_LLM_EVALUATOR_MODEL", default="qwen3-14b"
            ),
            "configured": _env_bool("CONVAGENT_LLM_EVALUATOR_ENABLED", True),
            "enable_thinking": _env_bool(
                "CONVAGENT_LLM_EVALUATOR_THINKING", True
            ),
        }
    )
    return ModelRegistryConfig(
        lightweight=registry.lightweight,
        standard=standard,
        advanced=advanced,
        evaluator=evaluator,
        registry_version=registry.registry_version,
        default_profile=ModelProfile(
            os.getenv("CONVAGENT_LLM_DEFAULT_PROFILE", "standard")
        ),
    )


def _build_default_config() -> AppConfig:
    """Build AppConfig from environment variables with sensible defaults."""
    provider = os.getenv("CONVAGENT_LLM_PROVIDER", "dashscope").strip() or "dashscope"
    registry = _build_model_registry()
    if provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        auth_token = ""
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
    elif provider == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN", "").strip()
        base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    else:
        api_key = _first_non_empty_env(
            "CONVAGENT_DASHSCOPE_API_KEY", "DASHSCOPE_API_KEY"
        )
        auth_token = ""
        base_url = _first_non_empty_env(
            "CONVAGENT_DASHSCOPE_BASE_URL",
            default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    runtime_mode = os.getenv("CONVAGENT_RUNTIME_MODE", "demo").strip() or "demo"
    return AppConfig(
        runtime_mode=runtime_mode,  # type: ignore[arg-type]
        database=DatabaseConfig(
            url=SecretStr(os.getenv("CONVAGENT_DATABASE_URL", "").strip()),
            persistence_mode=(
                PersistenceMode(value)
                if (value := os.getenv("CONVAGENT_PERSISTENCE_MODE", "").strip())
                else None
            ),
            idempotency_header_mode=(
                IdempotencyHeaderMode(value)
                if (
                    value := os.getenv(
                        "CONVAGENT_IDEMPOTENCY_HEADER_MODE", ""
                    ).strip()
                )
                else None
            ),
            enabled=_env_bool("CONVAGENT_DATABASE_ENABLED", True),
            required=_env_bool("CONVAGENT_DATABASE_REQUIRED", False),
            pool_size=int(os.getenv("CONVAGENT_DATABASE_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("CONVAGENT_DATABASE_MAX_OVERFLOW", "10")),
            pool_timeout_seconds=float(
                os.getenv("CONVAGENT_DATABASE_POOL_TIMEOUT", "30")
            ),
            pool_recycle_seconds=float(
                os.getenv("CONVAGENT_DATABASE_POOL_RECYCLE", "3600")
            ),
            connect_timeout_seconds=float(
                os.getenv("CONVAGENT_DATABASE_CONNECT_TIMEOUT", "5")
            ),
            statement_timeout_ms=int(
                os.getenv("CONVAGENT_DATABASE_STATEMENT_TIMEOUT_MS", "45000")
            ),
            lock_timeout_ms=int(
                os.getenv("CONVAGENT_DATABASE_LOCK_TIMEOUT_MS", "5000")
            ),
            idle_in_transaction_session_timeout_ms=int(
                os.getenv(
                    "CONVAGENT_DATABASE_IDLE_TRANSACTION_TIMEOUT_MS", "30000"
                )
            ),
            readiness_timeout_seconds=float(
                os.getenv("CONVAGENT_DATABASE_READINESS_TIMEOUT", "3")
            ),
            graceful_shutdown_timeout_seconds=float(
                os.getenv("CONVAGENT_DATABASE_SHUTDOWN_TIMEOUT", "30")
            ),
            tls_mode=DatabaseTlsMode(
                os.getenv("CONVAGENT_DATABASE_TLS_MODE", "disable").strip()
                or "disable"
            ),
            tls_ca_file=os.getenv("CONVAGENT_DATABASE_TLS_CA_FILE", "").strip(),
            tls_client_cert_file=os.getenv(
                "CONVAGENT_DATABASE_TLS_CLIENT_CERT_FILE", ""
            ).strip(),
            tls_client_key_file=os.getenv(
                "CONVAGENT_DATABASE_TLS_CLIENT_KEY_FILE", ""
            ).strip(),
            schema_name=os.getenv("CONVAGENT_DATABASE_SCHEMA", "public").strip()
            or "public",
            max_clock_drift_seconds=float(
                os.getenv("CONVAGENT_DATABASE_MAX_CLOCK_DRIFT", "5")
            ),
            doctor_statement_timeout_ms=int(
                os.getenv("CONVAGENT_DATABASE_DOCTOR_TIMEOUT_MS", "3000")
            ),
            doctor_full_statement_timeout_ms=int(
                os.getenv("CONVAGENT_DATABASE_DOCTOR_FULL_TIMEOUT_MS", "15000")
            ),
            doctor_quick_overall_timeout_seconds=float(
                os.getenv("CONVAGENT_DATABASE_DOCTOR_QUICK_DEADLINE", "10")
            ),
            doctor_full_overall_timeout_seconds=float(
                os.getenv("CONVAGENT_DATABASE_DOCTOR_FULL_DEADLINE", "60")
            ),
            echo=_env_bool("CONVAGENT_DATABASE_ECHO", False),
            auto_migrate=_env_bool("CONVAGENT_DATABASE_AUTO_MIGRATE", False),
            store_user_text=_env_bool("CONVAGENT_DATABASE_STORE_USER_TEXT", False),
            idempotency_ttl_seconds=int(
                os.getenv("CONVAGENT_IDEMPOTENCY_TTL", "3600")
            ),
            stale_in_progress_timeout_seconds=int(
                os.getenv("CONVAGENT_IDEMPOTENCY_STALE_TIMEOUT", "300")
            ),
            max_idempotency_key_bytes=int(
                os.getenv("CONVAGENT_IDEMPOTENCY_MAX_KEY_BYTES", "255")
            ),
            max_replay_snapshot_bytes=int(
                os.getenv("CONVAGENT_IDEMPOTENCY_MAX_SNAPSHOT_BYTES", "262144")
            ),
            expected_revision=os.getenv(
                "CONVAGENT_DATABASE_EXPECTED_REVISION", "0001"
            ).strip()
            or "0001",
        ),
        llm=LLMConfig(
            provider=provider,  # type: ignore[arg-type]
            default_profile=registry.default_profile,
            model_registry=registry,
            api_key=SecretStr(api_key),
            auth_token=SecretStr(auth_token),
            base_url=base_url,
            model=registry.standard.model,
            max_tool_rounds=int(os.getenv("CONVERSATION_AGENT_MAX_TOOL_ROUNDS", "5")),
            max_retries=registry.standard.max_retries,
            read_timeout_max_retries=registry.standard.read_timeout_max_retries,
            retry_total_budget_seconds=registry.standard.retry_total_budget_seconds,
            overall_deadline_seconds=registry.standard.overall_deadline_seconds,
            retry_base_delay=float(os.getenv("CONVERSATION_AGENT_RETRY_BASE_DELAY", "1.0")),
            request_timeout=registry.standard.timeout_seconds,
            max_tokens=registry.standard.max_output_tokens,
        ),
        rag_service=RagServiceConfig(
            provider=os.getenv("CONVAGENT_RAG_PROVIDER", "external"),  # type: ignore[arg-type]
            base_url=os.getenv("CONVAGENT_RAG_BASE_URL", "http://127.0.0.1:8001"),
            timeout_seconds=float(os.getenv("CONVAGENT_RAG_TIMEOUT_SECONDS", "30")),
            fallback_to_local=_env_bool("CONVAGENT_RAG_FALLBACK_TO_LOCAL", True),
            include_raw_response=_env_bool("CONVAGENT_RAG_INCLUDE_RAW_RESPONSE", False),
        ),
        oidc=OIDCConfig(
            issuer=os.getenv("CONVAGENT_OIDC_ISSUER", "").strip(),
            audience=os.getenv("CONVAGENT_OIDC_AUDIENCE", "").strip(),
            jwks_url=os.getenv("CONVAGENT_OIDC_JWKS_URL", "").strip(),
            tenant_id=os.getenv("CONVAGENT_OIDC_TENANT_ID", "single_tenant").strip(),
            expected_organization_id=os.getenv(
                "CONVAGENT_OIDC_EXPECTED_ORGANIZATION_ID", "default_organization"
            ).strip(),
            clock_skew_seconds=int(os.getenv("CONVAGENT_OIDC_CLOCK_SKEW_SECONDS", "60")),
            max_token_lifetime_seconds=int(
                os.getenv("CONVAGENT_OIDC_MAX_TOKEN_LIFETIME_SECONDS", "3600")
            ),
            max_token_bytes=int(os.getenv("CONVAGENT_OIDC_MAX_TOKEN_BYTES", "8192")),
            min_rsa_key_size_bits=int(
                os.getenv("CONVAGENT_OIDC_MIN_RSA_KEY_SIZE_BITS", "2048")
            ),
            max_kid_bytes=int(os.getenv("CONVAGENT_OIDC_MAX_KID_BYTES", "128")),
            jwks_cache_ttl_seconds=float(
                os.getenv("CONVAGENT_OIDC_JWKS_CACHE_TTL_SECONDS", "300")
            ),
            negative_kid_ttl_seconds=float(
                os.getenv("CONVAGENT_OIDC_NEGATIVE_KID_TTL_SECONDS", "10")
            ),
            jwks_max_response_bytes=int(
                os.getenv("CONVAGENT_OIDC_JWKS_MAX_RESPONSE_BYTES", "262144")
            ),
            jwks_max_keys=int(os.getenv("CONVAGENT_OIDC_JWKS_MAX_KEYS", "20")),
            jwks_timeout_seconds=float(
                os.getenv("CONVAGENT_OIDC_JWKS_TIMEOUT_SECONDS", "5")
            ),
            required_token_use_claim=os.getenv(
                "CONVAGENT_OIDC_TOKEN_USE_CLAIM", "token_use"
            ).strip() or None,
            required_token_use_value=os.getenv(
                "CONVAGENT_OIDC_TOKEN_USE_VALUE", "access"
            ).strip() or None,
            required_typ_header=os.getenv("CONVAGENT_OIDC_REQUIRED_TYP", "").strip()
            or None,
        ),
        api=APIConfig(
            docs_enabled=_env_bool(
                "CONVAGENT_API_DOCS_ENABLED", runtime_mode != "production"
            )
        ),
        storage=StorageConfig(
            data_dir=Path(os.getenv("CONVERSATION_AGENT_DATA_DIR", "./data")).resolve(),
        ),
        logging=LoggingConfig(
            level=os.getenv("CONVERSATION_AGENT_LOG_LEVEL", "INFO"),  # type: ignore[arg-type]
        ),
        follow_up_rules=[
            FollowUpRule(
                condition="quotation_sent_days > 3",
                priority="high",
                action="电话确认是否收到报价，了解客户反馈",
            ),
            FollowUpRule(
                condition="last_interaction_days >= 7",
                priority="medium",
                action="发送行业案例或产品资料，重新激活联系",
            ),
            FollowUpRule(
                condition="procurement_cycle_days < 30",
                priority="high",
                action="高优先级跟进，确保交付周期可控",
            ),
            FollowUpRule(
                condition="has_competitor and deal_score >= 60",
                priority="high",
                action="快速输出差异化方案，突出自身优势",
            ),
        ],
    )


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load configuration from a JSON file, falling back to env vars + defaults.

    If config_path is provided, loads from that file and overlays env vars.
    Otherwise builds from env vars and defaults.
    """
    global _config

    if config_path:
        path = Path(config_path)
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            raise ValueError(f"Unsupported config format: {path.suffix} (use .json)")

        _config = AppConfig(**data)
        if api_key := os.getenv("ANTHROPIC_API_KEY"):
            _config = _config.model_copy(
                update={
                    "llm": _config.llm.model_copy(
                        update={"api_key": SecretStr(api_key)}
                    )
                }
            )
        if model := os.getenv("CONVERSATION_AGENT_MODEL"):
            _config.llm.model = model
    else:
        _config = _build_default_config()

    return _config


def get_config() -> AppConfig:
    """Return the current AppConfig singleton, building one if needed."""
    global _config
    if _config is None:
        _config = _build_default_config()
    return _config


def reset_config() -> None:
    """Reset the config singleton (useful for tests)."""
    global _config
    _config = None


# ═══════════════════════════════════════════════════════════════════════════════
# Backward-compatible module-level accessors via __getattr__
# ═══════════════════════════════════════════════════════════════════════════════
#
# These resolve through get_config() so code that does
#   from conversation_agent.config import CUSTOMERS_DIR, SCORING_WEIGHTS
# continues to work, returning plain Python types (Path, dict, int, etc.).

def _cfg() -> AppConfig:
    return get_config()


# Map of legacy name → resolver function returning a plain Python value
_DEFERRED: dict[str, callable] = {  # type: ignore[type-arg]
    "CUSTOMERS_DIR": lambda: _cfg().storage.customers_dir,
    "INTERACTIONS_DIR": lambda: _cfg().storage.interactions_dir,
    "BACKUPS_DIR": lambda: _cfg().storage.backups_dir,
    "ALIASES_FILE": lambda: _cfg().storage.aliases_file,
    "DATA_DIR": lambda: _cfg().storage.data_dir,
    "CURRENT_SCHEMA_VERSION": lambda: _cfg().schema_version,
    "LOG_LEVEL": lambda: _cfg().logging.level,
    # Deprecated compatibility accessor. Do not log or serialize this value.
    "ANTHROPIC_API_KEY": lambda: _cfg().llm.api_key_value(),
    "ANTHROPIC_MODEL": lambda: _cfg().llm.model,
    "LLM_MAX_TOOL_ROUNDS": lambda: _cfg().llm.max_tool_rounds,
    "LLM_MAX_RETRIES": lambda: _cfg().llm.max_retries,
    "LLM_RETRY_BASE_DELAY": lambda: _cfg().llm.retry_base_delay,
    "LLM_REQUEST_TIMEOUT": lambda: _cfg().llm.request_timeout,
    "SCORING_WEIGHTS": lambda: _cfg().scoring.as_dict(),
    "SCORE_LEVEL_MAP": lambda: {
        lvl.label: (lvl.min_score, lvl.max_score)
        for lvl in _cfg().score_levels.levels
    },
    "HEALTH_MAX_SCORE": lambda: _cfg().health_weights.max_total,
    "HEALTH_DIMENSIONS": lambda: _cfg().health_weights.as_dict(),
    "HEALTH_TIME_DECAY": lambda: [
        (t.days, t.multiplier) for t in _cfg().health_time_decay.thresholds
    ],
    "HEALTH_STATUS_MAP": lambda: {
        "cold": (0, _cfg().health_status.cold_max),
        "warm": (_cfg().health_status.cold_max + 1, _cfg().health_status.warm_max),
        "healthy": (_cfg().health_status.warm_max + 1, _cfg().health_status.healthy_max),
    },
    "RISK_PENALTY_MAP": lambda: _cfg().risk_penalty.as_dict(),
    "FOLLOW_UP_RULES": lambda: [r.model_dump() for r in _cfg().follow_up_rules],
    "RAG_PROVIDER": lambda: _cfg().rag_service.provider,
    "RAG_BASE_URL": lambda: _cfg().rag_service.base_url,
    "RAG_TIMEOUT_SECONDS": lambda: _cfg().rag_service.timeout_seconds,
    "RAG_FALLBACK_TO_LOCAL": lambda: _cfg().rag_service.fallback_to_local,
    "RAG_INCLUDE_RAW_RESPONSE": lambda: _cfg().rag_service.include_raw_response,
}


def __getattr__(name: str):
    """Resolve legacy module-level names lazily via get_config().

    Called by Python when an import like
        from conversation_agent.config import CUSTOMERS_DIR
    resolves, returning a plain Path / dict / int — not a proxy.
    """
    if name in _DEFERRED:
        return _DEFERRED[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
