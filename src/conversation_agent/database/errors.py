"""Safe persistence errors for the M1.4-C execution write path."""

from __future__ import annotations


class PersistenceError(RuntimeError):
    """Base error that never includes SQL, parameters, or database URLs."""


class PersistenceConnectionError(PersistenceError):
    """The database connection could not complete an operation."""


class DatabaseRevisionError(PersistenceError):
    """The live database revision does not match the configured contract."""


class PersistenceWriteError(PersistenceError):
    """A database write or transaction commit failed."""


class PersistenceConflictError(PersistenceError):
    """A persisted uniqueness or state invariant was violated."""


class DuplicateRequestError(PersistenceConflictError):
    """The externally generated request ID already exists."""


class RequestNotFoundError(PersistenceError):
    """The request selected for finalization does not exist."""


class InvalidRequestTransitionError(PersistenceConflictError):
    """The request is not in the required in-progress state."""


class DurableApplicationError(RuntimeError):
    """Base error for the durable application component."""


class RequestInitializationError(DurableApplicationError):
    """Transaction A could not durably accept the request."""


class DurableApplicationExecutionError(DurableApplicationError):
    """The application execution failed after the request was accepted."""


class PersistenceFinalizationError(DurableApplicationError):
    """Transaction B could not durably finalize an accepted request."""


class IdempotencyError(PersistenceError):
    """Base error for persistent idempotency decisions."""


class FingerprintVersionError(IdempotencyError):
    """The stored and current fingerprint versions cannot be compared."""


class InvalidIdempotencyStateError(IdempotencyError):
    """A persisted idempotency record violates the approved state contract."""


class IdempotencyOwnershipLostError(IdempotencyError):
    """The claim token no longer owns the persistent record."""


class ReplaySnapshotError(IdempotencyError):
    """A replay snapshot is invalid, unsupported, or too large."""


class UnsupportedReplaySnapshotVersionError(ReplaySnapshotError):
    """The persisted replay snapshot has no compatible reader."""
