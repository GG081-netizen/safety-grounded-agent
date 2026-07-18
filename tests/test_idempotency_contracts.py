from __future__ import annotations

from datetime import datetime, timezone

import pytest

from conversation_agent.database.errors import InvalidIdempotencyStateError
from conversation_agent.database.idempotency import (
    IdempotencyStateValidator,
    hash_idempotency_key,
)
from conversation_agent.database.models import IdempotencyRecord
from conversation_agent.database.records import (
    IdempotencyPolicy,
    IdempotencyScope,
    StoredIdempotencyRecord,
)
from conversation_agent.database.repository import (
    ExecutionRepository,
    IdempotencyRepository,
)
from conversation_agent.database.sqlalchemy_idempotency_repository import (
    SQLAlchemyIdempotencyRepository,
)
from conversation_agent.database.sqlalchemy_repository import (
    SQLAlchemyExecutionRepository,
)


pytestmark = pytest.mark.unit


def _stored(**overrides: object) -> StoredIdempotencyRecord:
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    values: dict[str, object] = {
        "database_id": 1,
        "scope": IdempotencyScope("tenant", "org", "user", "chat", "a" * 64),
        "request_fingerprint": "b" * 64,
        "fingerprint_version": 1,
        "status": "in_progress",
        "claim_version": 1,
        "owner_request_id": "request-1",
        "claimed_at": now,
        "lease_expires_at": now,
        "completed_run_record_id": None,
        "response_snapshot": None,
        "response_snapshot_version": None,
        "expires_at": now,
    }
    values.update(overrides)
    return StoredIdempotencyRecord(**values)  # type: ignore[arg-type]


def test_scoped_unique_columns_are_all_not_nullable() -> None:
    for name in (
        "tenant_id",
        "organization_id",
        "principal_user_id",
        "operation",
        "idempotency_key_hash",
    ):
        assert IdempotencyRecord.__table__.c[name].nullable is False


def test_raw_key_hash_uses_exact_utf8_without_normalization() -> None:
    assert hash_idempotency_key(" Key ") != hash_idempotency_key("Key")
    assert hash_idempotency_key("A") != hash_idempotency_key("a")
    assert hash_idempotency_key("é") != hash_idempotency_key("e\u0301")
    assert hash_idempotency_key(" Key ") == (
        "3b3cab50694cde29f20b3a510a28d34943293fa9a9d4c77be418b49635037233"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner_request_id", None),
        ("claim_version", None),
        ("claim_version", 0),
        ("claim_version", True),
        ("claimed_at", None),
        ("lease_expires_at", None),
    ],
)
def test_active_state_rejects_missing_or_invalid_owner_state(
    field: str, value: object
) -> None:
    with pytest.raises(InvalidIdempotencyStateError):
        IdempotencyStateValidator().validate(_stored(**{field: value}))


def test_completed_state_requires_run_snapshot_version_and_expiry() -> None:
    for field in (
        "completed_run_record_id",
        "response_snapshot",
        "response_snapshot_version",
        "expires_at",
    ):
        values = {
            "status": "completed",
            "completed_run_record_id": 2,
            "response_snapshot": {"schema": 1},
            "response_snapshot_version": 1,
        }
        values[field] = None
        with pytest.raises(InvalidIdempotencyStateError):
            IdempotencyStateValidator().validate(_stored(**values))


def test_failed_state_rejects_completed_snapshot_state() -> None:
    with pytest.raises(InvalidIdempotencyStateError):
        IdempotencyStateValidator().validate(
            _stored(
                status="failed",
                completed_run_record_id=2,
                response_snapshot={"schema": 1},
                response_snapshot_version=1,
            )
        )


def test_unknown_state_and_snapshot_pairing_fail_closed() -> None:
    validator = IdempotencyStateValidator()
    with pytest.raises(InvalidIdempotencyStateError):
        validator.validate(_stored(status="future_state"))
    with pytest.raises(InvalidIdempotencyStateError):
        validator.validate(_stored(response_snapshot={"schema": 1}))


def test_idempotency_policy_requires_positive_exact_integers() -> None:
    assert IdempotencyPolicy().max_replay_snapshot_bytes == 262_144
    with pytest.raises(ValueError):
        IdempotencyPolicy(lease_duration_seconds=True)


def test_repository_interfaces_remain_segregated() -> None:
    assert issubclass(SQLAlchemyExecutionRepository, ExecutionRepository)
    assert not issubclass(SQLAlchemyExecutionRepository, IdempotencyRepository)
    assert issubclass(SQLAlchemyIdempotencyRepository, IdempotencyRepository)
    assert not issubclass(SQLAlchemyIdempotencyRepository, ExecutionRepository)
