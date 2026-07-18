from __future__ import annotations

import pytest
from pydantic import ValidationError

from conversation_agent.config import (
    AppConfig,
    DatabaseConfig,
    DatabaseTlsMode,
    IdempotencyHeaderMode,
    PersistenceMode,
)
from conversation_agent.operations.persistence import (
    DoctorCheck,
    DoctorReport,
    IdempotencyPruner,
    IntegrityIssue,
    IntegrityReport,
    PruneReport,
    audit_production_config,
)


pytestmark = pytest.mark.unit


def test_integrity_report_exposes_counts_but_not_persisted_values():
    report = IntegrityReport(
        status="unhealthy",
        complete=True,
        mode="quick",
        issues=(IntegrityIssue("replay_lineage_invalid", 2),),
        stale_active_count=1,
        expired_terminal_count=3,
        maximum_replay_snapshot_bytes=128,
        table_counts=(("agent_requests", 4),),
    )
    payload = report.to_dict()
    assert payload["issues"] == [
        {"code": "replay_lineage_invalid", "count": 2}
    ]
    assert "snapshot" not in payload


def test_doctor_report_json_contract_is_stable_and_low_sensitivity():
    report = DoctorReport(
        status="healthy",
        exit_code=0,
        complete=True,
        mode="quick",
        checks=(DoctorCheck("revision", "passed", "0001"),),
        integrity=None,
    )
    assert report.to_dict() == {
        "status": "healthy",
        "exit_code": 0,
        "complete": True,
        "mode": "quick",
        "checks": [{"name": "revision", "status": "passed", "value": "0001"}],
        "integrity": None,
    }


def test_prune_report_never_contains_record_identifiers():
    payload = PruneReport(True, 4, 4, 1, True, 0.02).to_dict()
    assert set(payload) == {
        "applied",
        "candidate_count",
        "deleted_count",
        "batches",
        "complete",
        "elapsed_seconds",
    }


@pytest.mark.parametrize(
    "kwargs",
    (
        {"batch_size": 0},
        {"batch_size": 1001},
        {"safety_margin_seconds": 0},
        {"max_batches": 0},
        {"overall_timeout_seconds": 0},
    ),
)
def test_pruner_rejects_unbounded_or_unsafe_configuration(kwargs):
    with pytest.raises(ValueError):
        IdempotencyPruner(object(), **kwargs)


def test_database_security_and_timeout_configuration_is_typed_and_secret_safe():
    config = DatabaseConfig(
        url="postgresql+asyncpg://user:password-canary@db.example/agent",
        tls_mode=DatabaseTlsMode.VERIFY_FULL,
        tls_ca_file="/secret/ca.pem",
        tls_client_cert_file="/secret/client.pem",
        tls_client_key_file="/secret/key.pem",
    )
    representation = repr(config)
    assert "password-canary" not in representation
    assert "/secret" not in representation
    assert config.statement_timeout_ms == 45_000
    assert config.lock_timeout_ms == 5_000
    assert config.idle_in_transaction_session_timeout_ms == 30_000


def test_verified_tls_requires_ca_and_certificate_pair():
    with pytest.raises(ValidationError):
        DatabaseConfig(tls_mode="verify_full")
    with pytest.raises(ValidationError):
        DatabaseConfig(
            tls_mode="require",
            tls_client_cert_file="client.pem",
        )


def test_production_remote_postgres_rejects_plaintext_transport():
    with pytest.raises(ValidationError, match="requires TLS"):
        AppConfig(
            runtime_mode="production",
            oidc={
                "issuer": "https://idp.example",
                "audience": "agent",
                "jwks_url": "https://idp.example/jwks",
            },
            database={
                "url": "postgresql+asyncpg://db.example/agent",
                "persistence_mode": "postgres",
                "idempotency_header_mode": "required",
                "tls_mode": "disable",
            },
        )


def test_production_config_audit_calculates_connection_budget_without_secrets():
    config = AppConfig(
        runtime_mode="production",
        oidc={
            "issuer": "https://idp.example",
            "audience": "agent",
            "jwks_url": "https://idp.example/jwks",
        },
        database={
            "url": "postgresql+asyncpg://user:secret@db.example/agent",
            "persistence_mode": PersistenceMode.POSTGRES,
            "idempotency_header_mode": IdempotencyHeaderMode.REQUIRED,
            "tls_mode": DatabaseTlsMode.REQUIRE,
            "pool_size": 4,
            "max_overflow": 2,
        },
    )
    report = audit_production_config(config)
    assert report.status == "healthy"
    assert report.maximum_app_connections_per_worker == 6
    assert "secret" not in str(report.to_dict())
