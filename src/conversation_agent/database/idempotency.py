"""Pure persistent-idempotency policy and state validation."""

from __future__ import annotations

import hashlib
from datetime import datetime

from conversation_agent.database.errors import InvalidIdempotencyStateError
from conversation_agent.database.records import (
    IdempotencyScope,
    IdempotencyStatus,
    StoredIdempotencyRecord,
    require_utc,
)


def hash_idempotency_key(raw_key: str) -> str:
    """Hash the exact application string without normalization."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def scope_from_values(
    *,
    tenant_id: str,
    organization_id: str,
    principal_user_id: str,
    operation: str,
    raw_key: str,
) -> IdempotencyScope:
    return IdempotencyScope(
        tenant_id=tenant_id,
        organization_id=organization_id,
        principal_user_id=principal_user_id,
        operation=operation,
        key_hash=hash_idempotency_key(raw_key),
    )


class IdempotencyStateValidator:
    """Fail closed on malformed persisted state; never repairs it."""

    def validate(self, record: StoredIdempotencyRecord) -> IdempotencyStatus:
        try:
            status = IdempotencyStatus(record.status)
        except ValueError as exc:
            raise InvalidIdempotencyStateError(
                "The idempotency record has an unsupported state."
            ) from exc

        if type(record.claim_version) is not int or record.claim_version < 1:
            raise InvalidIdempotencyStateError(
                "The idempotency record has an invalid claim version."
            )
        if not record.owner_request_id:
            raise InvalidIdempotencyStateError(
                "The idempotency record has no execution owner."
            )
        self._require_time(record.claimed_at, "claimed_at")
        self._require_time(record.lease_expires_at, "lease_expires_at")
        self._require_time(record.expires_at, "expires_at")

        snapshot_paired = (record.response_snapshot is None) == (
            record.response_snapshot_version is None
        )
        if not snapshot_paired:
            raise InvalidIdempotencyStateError(
                "The replay snapshot and version are not paired."
            )

        if status is IdempotencyStatus.ACTIVE:
            if (
                record.completed_run_record_id is not None
                or record.response_snapshot is not None
            ):
                raise InvalidIdempotencyStateError(
                    "An active claim contains terminal result state."
                )
        elif status is IdempotencyStatus.COMPLETED:
            if (
                record.completed_run_record_id is None
                or record.response_snapshot is None
                or record.response_snapshot_version is None
            ):
                raise InvalidIdempotencyStateError(
                    "A completed claim is missing replay state."
                )
        elif status is IdempotencyStatus.FAILED:
            if (
                record.completed_run_record_id is not None
                or record.response_snapshot is not None
                or record.response_snapshot_version is not None
            ):
                raise InvalidIdempotencyStateError(
                    "A failed claim contains completed replay state."
                )
        return status

    @staticmethod
    def _require_time(value: datetime | None, name: str) -> None:
        if value is None:
            raise InvalidIdempotencyStateError(
                f"The idempotency record is missing {name}."
            )
        try:
            require_utc(value, name)
        except ValueError as exc:
            raise InvalidIdempotencyStateError(
                f"The idempotency record has an invalid {name}."
            ) from exc
