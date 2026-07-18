"""Explicit allowlist mappers for M1.4-C persistence snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from conversation_agent.application.service import ApplicationResult
from conversation_agent.database.records import (
    JsonObject,
    JsonValue,
    NewAgentRequest,
    NewAgentRun,
    NewAuditEvent,
    PersistedAgentRequestRef,
)
from conversation_agent.runtime.models import RequestContext

REQUEST_FINGERPRINT_VERSION = 2
AUTHORIZATION_SNAPSHOT_VERSION = 1
RESULT_SNAPSHOT_VERSION = 1
TRACE_SNAPSHOT_VERSION = 1
AUDIT_PAYLOAD_VERSION = 1

FAILURE_CODES = frozenset(
    {
        "application_service_failed",
        "coordinator_execution_failed",
        "duplicate_request_id",
        "invalid_request_transition",
        "request_persistence_failed",
        "result_persistence_failed",
        "idempotency_lease_reclaimed",
    }
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _freeze_json(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _object(value: Mapping[str, JsonValue]) -> JsonObject:
    frozen = _freeze_json(value)
    assert isinstance(frozen, Mapping)
    return frozen


class FailureCodeMapper:
    def require(self, code: str) -> str:
        if code not in FAILURE_CODES:
            raise ValueError("failure_code is not approved")
        return code


class RequestPersistenceMapper:
    def map(
        self,
        *,
        context: RequestContext,
        operation: str,
        user_text: str,
        task_override: str | None,
        request_session_id: str | None = None,
        created_at: datetime,
        idempotency_key_hash: str | None = None,
        status: str = "in_progress",
        replayed_from_request_record_id: int | None = None,
        completed_at: datetime | None = None,
    ) -> NewAgentRequest:
        user_text_hash = _sha256_text(user_text)
        fingerprint_payload = {
            "fingerprint_version": REQUEST_FINGERPRINT_VERSION,
            "operation": operation,
            "organization_id": context.principal.organization_id,
            "principal_user_id": context.principal.user_id,
            "request_session_id": request_session_id,
            "task_override": task_override,
            "tenant_id": context.principal.tenant_id,
            "user_text_hash": user_text_hash,
        }
        canonical = json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        scopes = tuple(
            _object(
                {
                    "resource_ids": scope.resource_ids,
                    "resource_type": scope.resource_type,
                    "scope_type": scope.scope_type,
                }
            )
            for scope in context.authorization.resource_scopes
        )
        authorization_snapshot = _object(
            {
                "allowed": context.authorization.allowed,
                "authorization_policy_version": (
                    context.versions.authorization_policy_version
                ),
                "code": context.authorization.code,
                "permissions": context.authorization.permissions,
                "resource_scopes": scopes,
            }
        )
        return NewAgentRequest(
            request_id=context.request_id,
            trace_id=context.trace_id,
            session_id=context.session_id,
            operation=operation,
            principal_user_id=context.principal.user_id,
            tenant_id=context.principal.tenant_id,
            organization_id=context.principal.organization_id,
            user_text_hash=user_text_hash,
            user_text_length=len(user_text),
            request_fingerprint=_sha256_text(canonical),
            fingerprint_version=REQUEST_FINGERPRINT_VERSION,
            authorization_snapshot=authorization_snapshot,
            authorization_snapshot_schema_version=AUTHORIZATION_SNAPSHOT_VERSION,
            created_at=created_at,
            status=status,
            idempotency_key_hash=idempotency_key_hash,
            replayed_from_request_record_id=replayed_from_request_record_id,
            completed_at=completed_at,
        )


class RunPersistenceMapper:
    def completed(
        self,
        *,
        result: ApplicationResult,
        request: PersistedAgentRequestRef,
        run_id: str,
        started_at: datetime,
        completed_at: datetime,
    ) -> NewAgentRun:
        orchestration = result.orchestration
        blocked = orchestration.policy.is_blocked
        status = "blocked" if blocked else "completed"
        routed_task = (
            str(orchestration.task_route.task)
            if orchestration.task_route is not None
            else None
        )
        result_snapshot = _object(
            {
                "answer_length": len(orchestration.final_response),
                "answer_sha256": _sha256_text(orchestration.final_response),
                "citation_count": len(orchestration.citations),
                "has_citations": bool(orchestration.citations),
                "outcome": status,
                "result_kind": "policy_refusal" if blocked else "agent_result",
            }
        )
        trace_snapshot = _object(
            {
                "fallback_used": (
                    orchestration.rag_result is not None
                    and orchestration.rag_result.provider == "fallback"
                ),
                "matched_rule_ids": tuple(orchestration.policy.matched_rules),
                "policy_outcome": orchestration.policy.status,
                "policy_version": result.context.versions.policy_version,
                "rag_provider": (
                    None
                    if orchestration.rag_result is None
                    else orchestration.rag_result.provider
                ),
                "rag_used": orchestration.rag_result is not None,
                "routed_task": routed_task,
                "stage_names": tuple(step.step_name for step in orchestration.trace),
            }
        )
        return NewAgentRun(
            run_id=run_id,
            original_request_record_id=request.database_id,
            session_id=result.context.session_id,
            status=status,
            routed_task=routed_task,
            policy_outcome=orchestration.policy.status,
            result_snapshot=result_snapshot,
            result_snapshot_schema_version=RESULT_SNAPSHOT_VERSION,
            confidence=orchestration.confidence,
            trace_snapshot=trace_snapshot,
            trace_snapshot_schema_version=TRACE_SNAPSHOT_VERSION,
            rag_provider=(
                None
                if orchestration.rag_result is None
                else orchestration.rag_result.provider
            ),
            started_at=started_at,
            completed_at=completed_at,
        )

    def failed(
        self,
        *,
        context: RequestContext,
        request: PersistedAgentRequestRef,
        run_id: str,
        failure_code: str,
        started_at: datetime,
        completed_at: datetime,
    ) -> NewAgentRun:
        trace_snapshot = _object(
            {
                "failure_code": failure_code,
                "outcome": "failed",
            }
        )
        return NewAgentRun(
            run_id=run_id,
            original_request_record_id=request.database_id,
            session_id=context.session_id,
            status="failed",
            routed_task=None,
            policy_outcome=None,
            result_snapshot=None,
            result_snapshot_schema_version=None,
            confidence=None,
            trace_snapshot=trace_snapshot,
            trace_snapshot_schema_version=TRACE_SNAPSHOT_VERSION,
            rag_provider=None,
            started_at=started_at,
            completed_at=completed_at,
        )

    def lease_reclaimed(
        self,
        *,
        request: PersistedAgentRequestRef,
        run_id: str,
        event_time: datetime,
    ) -> NewAgentRun:
        return NewAgentRun(
            run_id=run_id,
            original_request_record_id=request.database_id,
            session_id=request.session_id,
            status="failed",
            routed_task=None,
            policy_outcome=None,
            result_snapshot=None,
            result_snapshot_schema_version=None,
            confidence=None,
            trace_snapshot=_object(
                {
                    "failure_code": "idempotency_lease_reclaimed",
                    "outcome": "failed",
                }
            ),
            trace_snapshot_schema_version=TRACE_SNAPSHOT_VERSION,
            rag_provider=None,
            started_at=event_time,
            completed_at=event_time,
        )


class AuditPersistenceMapper:
    def request_accepted(
        self,
        *,
        context: RequestContext,
        event_id: str,
        operation: str,
        created_at: datetime,
        idempotency_outcome: str | None = None,
        claim_version: int | None = None,
        reclaimed: bool = False,
        expired_reuse: bool = False,
    ) -> NewAuditEvent:
        return self._event(
            context=context,
            event_id=event_id,
            event_type="request_accepted",
            outcome="accepted",
            details={
                "audit_payload_version": AUDIT_PAYLOAD_VERSION,
                "operation": operation,
                "idempotency_outcome": idempotency_outcome,
                "claim_version": claim_version,
                "reclaimed": reclaimed,
                "expired_reuse": expired_reuse,
            },
            created_at=created_at,
        )

    def request_completed(
        self,
        *,
        result: ApplicationResult,
        event_id: str,
        run_id: str,
        created_at: datetime,
    ) -> NewAuditEvent:
        route = result.orchestration.task_route
        rag = result.orchestration.rag_result
        return self._event(
            context=result.context,
            event_id=event_id,
            event_type="request_completed",
            outcome="completed",
            details={
                "audit_payload_version": AUDIT_PAYLOAD_VERSION,
                "rag_provider": None if rag is None else rag.provider,
                "run_id": run_id,
                "task": None if route is None else str(route.task),
            },
            created_at=created_at,
        )

    def policy_blocked(
        self,
        *,
        result: ApplicationResult,
        event_id: str,
        run_id: str,
        created_at: datetime,
    ) -> NewAuditEvent:
        return self._event(
            context=result.context,
            event_id=event_id,
            event_type="policy_blocked",
            outcome="blocked",
            details={
                "audit_payload_version": AUDIT_PAYLOAD_VERSION,
                "matched_rule_ids": tuple(result.orchestration.policy.matched_rules),
                "policy_version": result.context.versions.policy_version,
                "run_id": run_id,
            },
            created_at=created_at,
        )

    def request_failed(
        self,
        *,
        context: RequestContext,
        event_id: str,
        run_id: str,
        failure_code: str,
        created_at: datetime,
    ) -> NewAuditEvent:
        return self._event(
            context=context,
            event_id=event_id,
            event_type="request_failed",
            outcome="failed",
            details={
                "audit_payload_version": AUDIT_PAYLOAD_VERSION,
                "failure_code": failure_code,
                "run_id": run_id,
            },
            created_at=created_at,
        )

    def request_replayed(
        self,
        *,
        context: RequestContext,
        event_id: str,
        operation: str,
        created_at: datetime,
    ) -> NewAuditEvent:
        return self._event(
            context=context,
            event_id=event_id,
            event_type="request_completed",
            outcome="replayed",
            details={
                "audit_payload_version": AUDIT_PAYLOAD_VERSION,
                "operation": operation,
                "idempotency_outcome": "replayed",
                "replayed": True,
            },
            created_at=created_at,
        )

    def lease_reclaimed(
        self,
        *,
        context: RequestContext,
        old_request: PersistedAgentRequestRef,
        event_id: str,
        run_id: str,
        claim_version: int,
        created_at: datetime,
    ) -> NewAuditEvent:
        event = self._event(
            context=context,
            event_id=event_id,
            event_type="request_failed",
            outcome="failed",
            details={
                "audit_payload_version": AUDIT_PAYLOAD_VERSION,
                "failure_code": "idempotency_lease_reclaimed",
                "idempotency_outcome": "reclaimed",
                "claim_version": claim_version,
                "reclaimed": True,
                "run_id": run_id,
            },
            created_at=created_at,
        )
        return NewAuditEvent(
            event_id=event.event_id,
            request_id=old_request.request_id,
            trace_id=old_request.trace_id,
            tenant_id=event.tenant_id,
            organization_id=event.organization_id,
            event_type=event.event_type,
            principal_user_id=event.principal_user_id,
            outcome=event.outcome,
            details_json=event.details_json,
            created_at=event.created_at,
        )

    @staticmethod
    def _event(
        *,
        context: RequestContext,
        event_id: str,
        event_type: str,
        outcome: str,
        details: Mapping[str, JsonValue],
        created_at: datetime,
    ) -> NewAuditEvent:
        return NewAuditEvent(
            event_id=event_id,
            request_id=context.request_id,
            trace_id=context.trace_id,
            tenant_id=context.principal.tenant_id,
            organization_id=context.principal.organization_id,
            event_type=event_type,
            principal_user_id=context.principal.user_id,
            outcome=outcome,
            details_json=_object(details),
            created_at=created_at,
        )
