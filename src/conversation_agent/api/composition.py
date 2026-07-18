"""M1.4-E application composition without import-time resources."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from conversation_agent.api.idempotency import IdempotencyKeyParser
from conversation_agent.api.projector import ResponseProjector
from conversation_agent.application.durable_service import DurableApplicationService
from conversation_agent.application.execution_gateway import RequestExecutionGateway
from conversation_agent.application.idempotent_durable_service import (
    IdempotentDurableApplicationService,
)
from conversation_agent.application.service import ChatService
from conversation_agent.config import AppConfig, PersistenceMode
from conversation_agent.database.engine import DatabaseEngine
from conversation_agent.database.fake_execution import FakeIdempotentUnitOfWorkFactory
from conversation_agent.database.records import IdempotencyPolicy
from conversation_agent.database.sqlalchemy_uow import (
    SQLAlchemyExecutionUnitOfWork,
    SQLAlchemyIdempotentExecutionUnitOfWork,
)
from conversation_agent.runtime.builder import RequestContextBuilder


@dataclass(frozen=True, slots=True)
class ApplicationDependencies:
    gateway: RequestExecutionGateway
    context_builder: RequestContextBuilder
    projector: ResponseProjector
    idempotency_parser: IdempotencyKeyParser
    database_engine: DatabaseEngine | None = None


def build_dependencies(
    *,
    config: AppConfig,
    chat_service: ChatService,
    context_builder: RequestContextBuilder,
    projector: ResponseProjector,
    database_engine: DatabaseEngine | None = None,
    fake_uow_factory: FakeIdempotentUnitOfWorkFactory | None = None,
) -> ApplicationDependencies:
    mode = config.database.effective_persistence_mode
    durable = None
    idempotent = None
    policy = IdempotencyPolicy(
        lease_duration_seconds=config.database.stale_in_progress_timeout_seconds,
        replay_ttl_seconds=config.database.idempotency_ttl_seconds,
        max_replay_snapshot_bytes=config.database.max_replay_snapshot_bytes,
    )

    if mode is PersistenceMode.POSTGRES:
        if database_engine is None or not database_engine.is_started:
            raise RuntimeError("PostgreSQL persistence is not ready.")
        session_factory = database_engine.session_factory
        durable = DurableApplicationService(
            chat_service=chat_service,
            uow_factory=lambda: SQLAlchemyExecutionUnitOfWork(session_factory),
        )
        idempotent = IdempotentDurableApplicationService(
            chat_service=chat_service,
            uow_factory=lambda: SQLAlchemyIdempotentExecutionUnitOfWork(
                session_factory
            ),
            policy=policy,
        )
    elif mode is PersistenceMode.FAKE:
        factory = fake_uow_factory or FakeIdempotentUnitOfWorkFactory(
            database_clock=lambda: datetime.now(timezone.utc)
        )
        durable = DurableApplicationService(
            chat_service=chat_service,
            uow_factory=factory,
        )
        idempotent = IdempotentDurableApplicationService(
            chat_service=chat_service,
            uow_factory=factory,
            policy=policy,
        )

    gateway = RequestExecutionGateway(
        persistence_mode=mode,
        header_mode=config.database.effective_idempotency_header_mode,
        chat_service=chat_service,
        durable_service=durable,
        idempotent_service=idempotent,
    )
    return ApplicationDependencies(
        gateway=gateway,
        context_builder=context_builder,
        projector=projector,
        idempotency_parser=IdempotencyKeyParser(
            max_bytes=config.database.max_idempotency_key_bytes
        ),
        database_engine=database_engine,
    )
