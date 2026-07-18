from __future__ import annotations

import inspect
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from conversation_agent.orchestration.coordinator import Coordinator
from conversation_agent.orchestration.models import OrchestrationRequestMetadata
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.rag.models import RagResult


pytestmark = pytest.mark.unit


class ControlledPolicy:
    def __init__(self, statuses: dict[str, str], blocked_release: threading.Event | None = None):
        self._statuses = statuses
        self._blocked_release = blocked_release

    def decide(self, text: str) -> PolicyDecision:
        status = self._statuses.get(text, "SAFE")
        if status == "BLOCKED" and self._blocked_release is not None:
            assert self._blocked_release.wait(timeout=2)
        return PolicyDecision(status=status, reason=f"controlled:{status}", confidence=0.9)

    def rejection_message(self, decision: PolicyDecision) -> str:
        return f"controlled rejection:{decision.status}"


class RecordingRagClient:
    def __init__(
        self,
        *,
        barrier: threading.Barrier | None = None,
        entered: threading.Event | None = None,
        failing_question: str | None = None,
    ) -> None:
        self._barrier = barrier
        self._entered = entered
        self._failing_question = failing_question
        self._lock = threading.Lock()
        self.calls: list[dict[str, object]] = []

    def query(self, question, *, trace_id=None, metadata=None):
        if self._entered is not None:
            self._entered.set()
        if self._barrier is not None:
            self._barrier.wait(timeout=2)
        with self._lock:
            self.calls.append(
                {"question": question, "trace_id": trace_id, "metadata": dict(metadata or {})}
            )
        if question == self._failing_question:
            raise RuntimeError("controlled rag failure")
        return RagResult(answer=f"answer:{question}", confidence=0.8, provider="external")


def _metadata(label: str, *, session: str | None = None) -> OrchestrationRequestMetadata:
    return OrchestrationRequestMetadata(
        request_id=f"request-{label}",
        trace_id=f"trace-{label}",
        session_id=session or f"session-{label}",
    )


def _run(coordinator: Coordinator, text: str, metadata: OrchestrationRequestMetadata):
    return coordinator.run(text, task_override="qa", request_metadata=metadata)


@pytest.mark.parametrize(
    "statuses",
    (
        {"A": "SAFE", "B": "SAFE"},
        {"A": "SAFE", "B": "UNCERTAIN"},
    ),
)
def test_concurrent_task_requests_keep_metadata_and_policy_isolated(statuses) -> None:
    rag = RecordingRagClient(barrier=threading.Barrier(2))
    coordinator = Coordinator(policy=ControlledPolicy(statuses), rag_client=rag)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(_run, coordinator, "A", _metadata("a"))
        future_b = pool.submit(_run, coordinator, "B", _metadata("b"))
        result_a = future_a.result(timeout=3)
        result_b = future_b.result(timeout=3)

    calls = {call["question"]: call for call in rag.calls}
    assert calls["A"]["trace_id"] == "trace-a"
    assert calls["B"]["trace_id"] == "trace-b"
    assert calls["A"]["metadata"]["policy_status"] == statuses["A"]
    assert calls["B"]["metadata"]["policy_status"] == statuses["B"]
    assert result_a.session_id == "session-a"
    assert result_b.session_id == "session-b"


def test_blocked_and_safe_interleaving_never_sends_blocked_request_to_rag() -> None:
    safe_entered = threading.Event()
    rag = RecordingRagClient(entered=safe_entered)
    policy = ControlledPolicy(
        {"blocked": "BLOCKED", "safe": "SAFE"},
        blocked_release=safe_entered,
    )
    coordinator = Coordinator(policy=policy, rag_client=rag)
    with ThreadPoolExecutor(max_workers=2) as pool:
        blocked = pool.submit(_run, coordinator, "blocked", _metadata("blocked"))
        safe = pool.submit(_run, coordinator, "safe", _metadata("safe"))
        blocked_result = blocked.result(timeout=3)
        safe.result(timeout=3)

    assert [call["question"] for call in rag.calls] == ["safe"]
    assert [step.step_name for step in blocked_result.trace] == ["policy_engine"]


def test_rag_exception_does_not_pollute_concurrent_success() -> None:
    rag = RecordingRagClient(
        barrier=threading.Barrier(2), failing_question="failure"
    )
    coordinator = Coordinator(
        policy=ControlledPolicy({"failure": "SAFE", "success": "SAFE"}),
        rag_client=rag,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        failed = pool.submit(_run, coordinator, "failure", _metadata("failure"))
        succeeded = pool.submit(_run, coordinator, "success", _metadata("success"))
        with pytest.raises(RuntimeError):
            failed.result(timeout=3)
        result = succeeded.result(timeout=3)

    assert result.session_id == "session-success"
    success_call = next(call for call in rag.calls if call["question"] == "success")
    assert success_call["trace_id"] == "trace-success"


def test_same_session_different_trace_and_podcast_nested_qa() -> None:
    rag = RecordingRagClient(barrier=threading.Barrier(2))
    coordinator = Coordinator(
        policy=ControlledPolicy({"A": "SAFE", "B": "SAFE"}), rag_client=rag
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_run, coordinator, "A", _metadata("a", session="shared"))
        second = pool.submit(_run, coordinator, "B", _metadata("b", session="shared"))
        first.result(timeout=3)
        second.result(timeout=3)
    assert {call["trace_id"] for call in rag.calls} == {"trace-a", "trace-b"}

    podcast_rag = RecordingRagClient()
    podcast = Coordinator(
        policy=ControlledPolicy({"生成采购播客": "SAFE"}), rag_client=podcast_rag
    )
    metadata = _metadata("podcast")
    result = podcast.run(
        "生成采购播客",
        task_override="podcast_script",
        request_metadata=metadata,
    )
    call = podcast_rag.calls[0]
    assert call["trace_id"] == metadata.trace_id
    assert call["metadata"] == {
        "request_id": metadata.request_id,
        "trace_id": metadata.trace_id,
        "session_id": metadata.session_id,
        "task_type": "podcast_script",
        "policy_status": "SAFE",
    }
    assert result.session_id == metadata.session_id


def test_shared_coordinator_survives_one_hundred_concurrent_rounds() -> None:
    mismatch_count = 0
    for round_number in range(100):
        rag = RecordingRagClient(barrier=threading.Barrier(2))
        coordinator = Coordinator(
            policy=ControlledPolicy({"A": "SAFE", "B": "UNCERTAIN"}),
            rag_client=rag,
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(_run, coordinator, "A", _metadata(f"{round_number}-a")),
                pool.submit(_run, coordinator, "B", _metadata(f"{round_number}-b")),
            )
            for future in futures:
                future.result(timeout=3)
        for call in rag.calls:
            suffix = "a" if call["question"] == "A" else "b"
            expected = f"{round_number}-{suffix}"
            metadata = call["metadata"]
            mismatch_count += int(call["trace_id"] != f"trace-{expected}")
            mismatch_count += int(metadata["request_id"] != f"request-{expected}")
            mismatch_count += int(metadata["session_id"] != f"session-{expected}")
    assert mismatch_count == 0


def test_coordinator_has_no_request_scoped_current_state() -> None:
    coordinator = Coordinator(policy=ControlledPolicy({}), rag_client=RecordingRagClient())
    source = inspect.getsource(type(coordinator))
    assert not any(name.startswith("_current_") for name in coordinator.__dict__)
    assert "self._current_" not in source
