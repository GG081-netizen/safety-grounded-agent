from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import anyio
import pytest

from conversation_agent.application.idempotency_mappers import ReplaySnapshotMapper
from conversation_agent.application.idempotent_durable_service import (
    IdempotentDurableApplicationService,
)
from conversation_agent.application.models import UserRequest
from conversation_agent.database.errors import (
    DurableApplicationExecutionError,
    FingerprintVersionError,
    PersistenceFinalizationError,
    RequestInitializationError,
    ReplaySnapshotError,
)
from conversation_agent.authorization.models import AuthorizationDecision
from conversation_agent.database.fake_execution import (
    FakeIdempotentUnitOfWorkFactory,
)
from conversation_agent.database.records import (
    IdempotencyPolicy,
    IdempotentResultOutcome,
)
from tests.test_durable_application_service import (
    BASE_TIME,
    RecordingChatService,
    _context,
)


pytestmark = pytest.mark.unit


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _service(factory, chat, *, policy=None):
    business_clock = MutableClock(BASE_TIME)
    return IdempotentDurableApplicationService(
        chat_service=chat,
        uow_factory=factory,
        policy=policy or IdempotencyPolicy(),
        clock=business_clock,
        run_id_factory=lambda: f"idem-run-{uuid.uuid4()}",
        event_id_factory=lambda: f"idem-event-{uuid.uuid4()}",
    )


@pytest.mark.asyncio
async def test_execute_then_replay_uses_current_context_without_chat_call():
    database_clock = MutableClock(BASE_TIME)
    factory = FakeIdempotentUnitOfWorkFactory(database_clock=database_clock)
    first_chat = RecordingChatService(factory)
    first = await _service(factory, first_chat).execute(
        UserRequest(text="same request"),
        context=_context("request-1"),
        operation="POST:/v1/chat",
        idempotency_key=" Key ",
    )
    assert first.outcome is IdempotentResultOutcome.EXECUTED
    assert first_chat.call_count == 1

    second_chat = RecordingChatService(factory)
    second_context = _context("request-2")
    replay = await _service(factory, second_chat).execute(
        UserRequest(text="same request"),
        context=second_context,
        operation="POST:/v1/chat",
        idempotency_key=" Key ",
    )
    assert replay.outcome is IdempotentResultOutcome.REPLAYED
    assert replay.original_request_id == "request-1"
    assert second_chat.call_count == 0
    assert replay.application_result.context is second_context
    assert replay.application_result.orchestration.trace[0].step_name == (
        "idempotency_replay"
    )
    assert factory.state.requests["request-2"]["record"].replayed_from_request_record_id == (
        factory.state.requests["request-1"]["database_id"]
    )
    assert len(factory.state.runs) == 1


@pytest.mark.asyncio
async def test_transaction_a_ambiguous_commit_never_executes_and_retry_reads_state():
    clock = MutableClock(BASE_TIME)
    factory = FakeIdempotentUnitOfWorkFactory(database_clock=clock)
    factory.ambiguous_commit_attempts.add(1)
    first_chat = RecordingChatService(factory)
    with pytest.raises(RequestInitializationError):
        await _service(factory, first_chat).execute(
            UserRequest(text="same"),
            context=_context("ambiguous-a-1"),
            operation="chat",
            idempotency_key="key",
        )
    assert first_chat.call_count == 0
    assert factory.state.requests["ambiguous-a-1"]["status"] == "in_progress"

    retry_chat = RecordingChatService(factory)
    retry = await _service(factory, retry_chat).execute(
        UserRequest(text="same"),
        context=_context("ambiguous-a-2"),
        operation="chat",
        idempotency_key="key",
    )
    assert retry.outcome is IdempotentResultOutcome.IN_PROGRESS
    assert retry_chat.call_count == 0


@pytest.mark.asyncio
async def test_transaction_b_ambiguous_commit_returns_no_success_then_replays():
    clock = MutableClock(BASE_TIME)
    factory = FakeIdempotentUnitOfWorkFactory(database_clock=clock)
    factory.ambiguous_commit_attempts.add(2)
    first_chat = RecordingChatService(factory)
    with pytest.raises(PersistenceFinalizationError):
        await _service(factory, first_chat).execute(
            UserRequest(text="same"),
            context=_context("ambiguous-b-1"),
            operation="chat",
            idempotency_key="key",
        )
    assert first_chat.call_count == 1
    record = next(iter(factory.state.idempotency.values()))
    assert record["status"] == "completed"

    retry_chat = RecordingChatService(factory)
    retry = await _service(factory, retry_chat).execute(
        UserRequest(text="same"),
        context=_context("ambiguous-b-2"),
        operation="chat",
        idempotency_key="key",
    )
    assert retry.outcome is IdempotentResultOutcome.REPLAYED
    assert retry_chat.call_count == 0


@pytest.mark.asyncio
async def test_multiple_replays_always_reference_original_execution_request():
    clock = MutableClock(BASE_TIME)
    factory = FakeIdempotentUnitOfWorkFactory(database_clock=clock)
    await _service(factory, RecordingChatService(factory)).execute(
        UserRequest(text="same"),
        context=_context("original"),
        operation="chat",
        idempotency_key="key",
    )
    for request_id in ("replay-1", "replay-2"):
        replay = await _service(factory, RecordingChatService(factory)).execute(
            UserRequest(text="same"),
            context=_context(request_id),
            operation="chat",
            idempotency_key="key",
        )
        assert replay.original_request_id == "original"
        assert factory.state.requests[request_id][
            "record"
        ].replayed_from_request_record_id == factory.state.requests["original"][
            "database_id"
        ]


@pytest.mark.asyncio
async def test_same_key_different_fingerprint_returns_conflict_without_chat():
    clock = MutableClock(BASE_TIME)
    factory = FakeIdempotentUnitOfWorkFactory(database_clock=clock)
    await _service(factory, RecordingChatService(factory)).execute(
        UserRequest(text="one"),
        context=_context("request-1"),
        operation="chat",
        idempotency_key="key",
    )
    chat = RecordingChatService(factory)
    result = await _service(factory, chat).execute(
        UserRequest(text="two"),
        context=_context("request-2"),
        operation="chat",
        idempotency_key="key",
    )
    assert result.outcome is IdempotentResultOutcome.CONFLICT
    assert chat.call_count == 0
    assert "request-2" not in factory.state.requests


@pytest.mark.asyncio
async def test_failed_result_is_retained_and_not_automatically_retried():
    clock = MutableClock(BASE_TIME)
    factory = FakeIdempotentUnitOfWorkFactory(database_clock=clock)
    with pytest.raises(DurableApplicationExecutionError):
        await _service(factory, RecordingChatService(factory, raises=True)).execute(
            UserRequest(text="same"),
            context=_context("request-1"),
            operation="chat",
            idempotency_key="key",
        )
    chat = RecordingChatService(factory)
    result = await _service(factory, chat).execute(
        UserRequest(text="same"),
        context=_context("request-2"),
        operation="chat",
        idempotency_key="key",
    )
    assert result.outcome is IdempotentResultOutcome.PREVIOUS_FAILURE
    assert result.safe_failure_code == "application_service_failed"
    assert chat.call_count == 0


@pytest.mark.asyncio
async def test_fingerprint_version_mismatch_fails_closed_until_terminal_expiry():
    clock = MutableClock(BASE_TIME)
    factory = FakeIdempotentUnitOfWorkFactory(database_clock=clock)
    await _service(factory, RecordingChatService(factory)).execute(
        UserRequest(text="same"),
        context=_context("request-1"),
        operation="chat",
        idempotency_key="key",
    )
    record = next(iter(factory.state.idempotency.values()))
    record["fingerprint_version"] = 999
    chat = RecordingChatService(factory)
    with pytest.raises(FingerprintVersionError):
        await _service(factory, chat).execute(
            UserRequest(text="same"),
            context=_context("request-2"),
            operation="chat",
            idempotency_key="key",
        )
    assert chat.call_count == 0
    record["expires_at"] = BASE_TIME - timedelta(seconds=1)
    result = await _service(factory, chat).execute(
        UserRequest(text="new payload"),
        context=_context("request-3"),
        operation="chat",
        idempotency_key="key",
    )
    assert result.outcome is IdempotentResultOutcome.EXECUTED
    assert next(iter(factory.state.idempotency.values()))["claim_version"] == 2


@pytest.mark.asyncio
async def test_snapshot_size_failure_rolls_back_entire_finalization():
    clock = MutableClock(BASE_TIME)
    factory = FakeIdempotentUnitOfWorkFactory(database_clock=clock)
    policy = IdempotencyPolicy(max_replay_snapshot_bytes=32)
    with pytest.raises(PersistenceFinalizationError):
        await _service(
            factory,
            RecordingChatService(factory),
            policy=policy,
        ).execute(
            UserRequest(text="same"),
            context=_context("request-1"),
            operation="chat",
            idempotency_key="key",
        )
    assert factory.state.requests["request-1"]["status"] == "in_progress"
    assert factory.state.runs == {}
    record = next(iter(factory.state.idempotency.values()))
    assert record["status"] == "in_progress"
    assert record["response_snapshot"] is None


@pytest.mark.asyncio
async def test_blocked_result_uses_structured_policy_and_replays_without_chat():
    clock = MutableClock(BASE_TIME)
    factory = FakeIdempotentUnitOfWorkFactory(database_clock=clock)
    blocked_chat = RecordingChatService(factory, blocked=True)
    first = await _service(factory, blocked_chat).execute(
        UserRequest(text="neutral text"),
        context=_context("blocked-original"),
        operation="chat",
        idempotency_key="key",
    )
    assert first.application_result.orchestration.policy.is_blocked
    assert factory.state.runs[next(iter(factory.state.runs))]["record"].status == (
        "blocked"
    )
    assert factory.state.audits[-1]["record"].event_type == "policy_blocked"

    replay_chat = RecordingChatService(factory)
    replay = await _service(factory, replay_chat).execute(
        UserRequest(text="neutral text"),
        context=_context("blocked-replay"),
        operation="chat",
        idempotency_key="key",
    )
    assert replay.outcome is IdempotentResultOutcome.REPLAYED
    assert replay.application_result.orchestration.policy.is_blocked
    assert replay_chat.call_count == 0


@pytest.mark.asyncio
async def test_transaction_a_audit_failure_rolls_back_claim_and_request():
    clock = MutableClock(BASE_TIME)
    factory = FakeIdempotentUnitOfWorkFactory(database_clock=clock)
    factory.fail_operations.add("create_audit_event")
    chat = RecordingChatService(factory)
    with pytest.raises(RequestInitializationError):
        await _service(factory, chat).execute(
            UserRequest(text="same"),
            context=_context("request-1"),
            operation="chat",
            idempotency_key="raw-key-canary",
        )
    assert chat.call_count == 0
    assert factory.state.idempotency == {}
    assert factory.state.requests == {}
    assert factory.state.audits == []


@pytest.mark.asyncio
async def test_current_authorization_is_required_before_claim_or_replay():
    clock = MutableClock(BASE_TIME)
    factory = FakeIdempotentUnitOfWorkFactory(database_clock=clock)
    context = _context("denied").model_copy(
        update={
            "authorization": AuthorizationDecision(
                allowed=False,
                code="denied_scope",
                reason="not allowed",
            )
        }
    )
    chat = RecordingChatService(factory)
    with pytest.raises(RequestInitializationError):
        await _service(factory, chat).execute(
            UserRequest(text="same"),
            context=context,
            operation="chat",
            idempotency_key="key",
        )
    assert chat.call_count == 0
    assert factory.state.idempotency == {}


def test_replay_mapper_rejects_nan_and_forbidden_nested_fields():
    factory = FakeIdempotentUnitOfWorkFactory(
        database_clock=MutableClock(BASE_TIME)
    )
    result = RecordingChatService(factory).execute_with_context(
        UserRequest(text="same"), context=_context("request-1")
    )
    result.orchestration.citations = [{"score": float("nan")}]
    with pytest.raises(ReplaySnapshotError):
        ReplaySnapshotMapper(max_bytes=100_000).map(result)
    result.orchestration.citations = [{"prompt": "secret"}]
    with pytest.raises(ReplaySnapshotError):
        ReplaySnapshotMapper(max_bytes=100_000).map(result)


class BlockingChatService(RecordingChatService):
    def __init__(self, factory, entered: threading.Event, release: threading.Event):
        super().__init__(factory)
        self.entered = entered
        self.release = release

    def execute_with_context(self, request, *, context, forced_task=None):
        self.entered.set()
        self.release.wait(timeout=2)
        return super().execute_with_context(
            request, context=context, forced_task=forced_task
        )


@pytest.mark.asyncio
async def test_cancellation_propagates_and_leaves_active_claim_for_reclaim():
    clock = MutableClock(BASE_TIME)
    factory = FakeIdempotentUnitOfWorkFactory(database_clock=clock)
    entered = threading.Event()
    release = threading.Event()
    chat = BlockingChatService(factory, entered, release)
    cancelled = anyio.Event()

    async def run() -> None:
        try:
            await _service(
                factory,
                chat,
                policy=IdempotencyPolicy(lease_duration_seconds=1),
            ).execute(
                UserRequest(text="same"),
                context=_context("old-owner"),
                operation="chat",
                idempotency_key="key",
            )
        except anyio.get_cancelled_exc_class():
            cancelled.set()
            raise

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run)
        await anyio.to_thread.run_sync(entered.wait)
        task_group.cancel_scope.cancel()
    await cancelled.wait()
    release.set()
    await anyio.sleep(0)
    record = next(iter(factory.state.idempotency.values()))
    assert record["status"] == "in_progress"
    assert factory.state.requests["old-owner"]["status"] == "in_progress"
    assert factory.active_uow_count == 0

    clock.value = BASE_TIME + timedelta(seconds=2)
    result = await _service(factory, RecordingChatService(factory)).execute(
        UserRequest(text="same"),
        context=_context("new-owner"),
        operation="chat",
        idempotency_key="key",
    )
    assert result.outcome is IdempotentResultOutcome.EXECUTED
    assert factory.state.requests["old-owner"]["status"] == "failed"
    assert factory.state.requests["old-owner"]["failure_code"] == (
        "idempotency_lease_reclaimed"
    )
    assert next(iter(factory.state.idempotency.values()))["claim_version"] == 2
