"""Versioned, bounded replay snapshot mapping for M1.4-D."""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

from conversation_agent.application.service import ApplicationResult
from conversation_agent.database.errors import (
    ReplaySnapshotError,
    UnsupportedReplaySnapshotVersionError,
)
from conversation_agent.orchestration.models import AgentStep, OrchestrationResult
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.rag.models import RagResult
from conversation_agent.runtime.models import RequestContext
from conversation_agent.sales.models import IntentResult
from conversation_agent.orchestration.models import TaskRoute

REPLAY_SNAPSHOT_VERSION = 1
SUPPORTED_REPLAY_SNAPSHOT_READER_VERSIONS = (1,)
_FORBIDDEN_KEYS = frozenset(
    {
        "raw_response",
        "debug",
        "token",
        "jwt",
        "claims",
        "email",
        "prompt",
        "authorization",
        "security_trace",
    }
)


def _safe_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ReplaySnapshotError(
                "The replay snapshot contains a non-finite number."
            )
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or key.lower() in _FORBIDDEN_KEYS:
                raise ReplaySnapshotError(
                    "The replay snapshot contains a forbidden field."
                )
            result[key] = _safe_json(item)
        return result
    raise ReplaySnapshotError(
        "The replay snapshot contains a non-JSON value."
    )


class ReplaySnapshotMapper:
    def __init__(self, *, max_bytes: int) -> None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self._max_bytes = max_bytes

    def map(self, result: ApplicationResult) -> dict[str, Any]:
        orchestration = result.orchestration
        rag = orchestration.rag_result
        payload = {
            "snapshot_schema_version": REPLAY_SNAPSHOT_VERSION,
            "policy": {
                "status": orchestration.policy.status,
                "reason": orchestration.policy.reason,
                "matched_rules": list(orchestration.policy.matched_rules),
                "warnings": list(orchestration.policy.warnings),
                "classifier_used": orchestration.policy.classifier_used,
                "confidence": orchestration.policy.confidence,
            },
            "intent_result": (
                None
                if orchestration.intent_result is None
                else orchestration.intent_result.model_dump(mode="json")
            ),
            "task_route": (
                None
                if orchestration.task_route is None
                else orchestration.task_route.model_dump(mode="json")
            ),
            "final_response": orchestration.final_response,
            "rag_result": None if rag is None else self._rag_payload(rag),
            "citations": _safe_json(orchestration.citations),
            "confidence": orchestration.confidence,
        }
        safe_payload = _safe_json(payload)
        encoded = json.dumps(
            safe_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > self._max_bytes:
            raise ReplaySnapshotError(
                "The replay snapshot exceeds the configured byte limit."
            )
        return json.loads(encoded.decode("utf-8"))

    def restore(
        self,
        snapshot: dict[str, Any],
        *,
        snapshot_version: int,
        context: RequestContext,
        user_text: str,
        replayed_at: datetime,
    ) -> ApplicationResult:
        if snapshot_version not in SUPPORTED_REPLAY_SNAPSHOT_READER_VERSIONS:
            raise UnsupportedReplaySnapshotVersionError(
                "The replay snapshot version is not supported."
            )
        if snapshot.get("snapshot_schema_version") != REPLAY_SNAPSHOT_VERSION:
            raise ReplaySnapshotError(
                "The replay snapshot payload version is not supported."
            )
        try:
            policy = PolicyDecision.model_validate(snapshot["policy"])
            intent = (
                None
                if snapshot.get("intent_result") is None
                else IntentResult.model_validate(snapshot["intent_result"])
            )
            route = (
                None
                if snapshot.get("task_route") is None
                else TaskRoute.model_validate(snapshot["task_route"])
            )
            rag = (
                None
                if snapshot.get("rag_result") is None
                else RagResult.model_validate(snapshot["rag_result"])
            )
            orchestration = OrchestrationResult(
                session_id=context.session_id,
                user_input=user_text,
                policy=policy,
                intent_result=intent,
                task_route=route,
                final_response=snapshot["final_response"],
                rag_result=rag,
                citations=snapshot.get("citations", []),
                confidence=snapshot["confidence"],
                trace=[
                    AgentStep(
                        step_name="idempotency_replay",
                        output_summary="A persisted result was replayed.",
                        confidence=snapshot["confidence"],
                    )
                ],
                timestamp=replayed_at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ReplaySnapshotError(
                "The replay snapshot does not match the approved contract."
            ) from exc
        return ApplicationResult(context=context, orchestration=orchestration)

    @staticmethod
    def _rag_payload(rag: RagResult) -> dict[str, Any]:
        evidence = [
            {
                "source_id": item.source_id,
                "title": item.title,
                "source_path": item.source_path,
                "text": item.text,
                "score": item.score,
                "metadata": _safe_json(item.metadata),
            }
            for item in rag.evidence
        ]
        diagnostics = [
            {
                "step_name": item.step_name,
                "provider": item.provider,
                "success": item.success,
                "error_type": item.error_type,
                "message": item.message,
                "latency_ms": item.latency_ms,
            }
            for item in rag.diagnostics
        ]
        return {
            "answer": rag.answer,
            "evidence": evidence,
            "sources": _safe_json(rag.sources),
            "confidence": rag.confidence,
            "warnings": list(rag.warnings),
            "provider": rag.provider,
            "diagnostics": diagnostics,
        }
