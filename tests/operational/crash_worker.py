"""Child process that commits Transaction A and blocks outside the database."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from conversation_agent.application.idempotent_durable_service import (
    IdempotentDurableApplicationService,
)
from conversation_agent.application.models import UserRequest
from conversation_agent.application.service import ApplicationResult
from conversation_agent.authorization.models import AuthorizationDecision, ResourceScope
from conversation_agent.database.records import IdempotencyPolicy
from conversation_agent.database.sqlalchemy_uow import (
    SQLAlchemyIdempotentExecutionUnitOfWork,
)
from conversation_agent.identity.models import Principal
from conversation_agent.orchestration.models import OrchestrationResult, TaskRoute
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.runtime.models import RequestContext, RuntimeVersionSnapshot


def _context(prefix: str) -> RequestContext:
    principal = Principal(
        tenant_id=f"{prefix}-tenant",
        organization_id=f"{prefix}-org",
        user_id=f"{prefix}-user",
        roles=("agent_user",),
    )
    return RequestContext(
        request_id=f"{prefix}-owner-a",
        trace_id=f"{prefix}-trace-a",
        session_id=f"{prefix}-session-a",
        principal=principal,
        authorization=AuthorizationDecision(
            allowed=True,
            code="allowed",
            permissions=("chat:invoke", "crm:read", "rag:read"),
            resource_scopes=(
                ResourceScope(
                    tenant_id=principal.tenant_id,
                    organization_id=principal.organization_id,
                    resource_type="organization",
                    scope_type="organization",
                ),
            ),
        ),
        versions=RuntimeVersionSnapshot(
            model_registry_version="models-v1",
            model_routing_policy_version="not_implemented",
            application_version="0.1.0",
            policy_version="policy-v1",
            rag_contract_version="rag-v1",
            crm_connector_version="not_configured",
            authorization_policy_version="authz-v1",
        ),
        received_at=datetime.now(timezone.utc),
    )


class BlockingChatService:
    def __init__(self, marker: Path) -> None:
        self._marker = marker

    def execute_with_context(self, request, *, context, forced_task=None):
        self._marker.write_text("transaction-a-committed\n", encoding="utf-8")
        threading.Event().wait(120)
        return ApplicationResult(
            context=context,
            orchestration=OrchestrationResult(
                session_id=context.session_id,
                user_input=request.text,
                policy=PolicyDecision(status="SAFE"),
                task_route=TaskRoute(task="qa"),
                final_response="answer",
                confidence=0.8,
            ),
        )


async def main(prefix: str, marker: Path) -> None:
    url = os.environ["CONVAGENT_POSTGRES_TEST_URL"]
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = IdempotentDurableApplicationService(
        chat_service=BlockingChatService(marker),
        uow_factory=lambda: SQLAlchemyIdempotentExecutionUnitOfWork(factory),
        policy=IdempotencyPolicy(lease_duration_seconds=300),
        run_id_factory=lambda: f"{prefix}-run-{uuid.uuid4()}",
        event_id_factory=lambda: f"{prefix}-event-{uuid.uuid4()}",
    )
    try:
        await service.execute(
            UserRequest(text="crash recovery"),
            context=_context(prefix),
            operation="v1.chat",
            idempotency_key="crash-key",
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], Path(sys.argv[2])))
