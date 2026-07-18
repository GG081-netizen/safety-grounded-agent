"""FastAPI composition root for the M1.4-E durable HTTP boundary."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from conversation_agent.api.composition import (
    ApplicationDependencies,
    build_dependencies,
)
from conversation_agent.api.idempotency import IdempotencyHTTPError
from conversation_agent.api.models import (
    APIErrorItem,
    APIErrorResponse,
    AgentResponse,
    HealthResponse,
    ReadinessResponse,
    RequestTraceStep,
)
from conversation_agent.api.projector import ResponseProjector
from conversation_agent.api.security import (
    RequestSecurityService,
    SecurityBoundaryError,
    SecurityContext,
)
from conversation_agent.application.execution_gateway import (
    ExecutionGatewayError,
    RequestExecutionGateway,
)
from conversation_agent.application.models import UserRequest
from conversation_agent.application.service import ApplicationExecutionError, ChatService
from conversation_agent.authorization.service import AuthorizationService
from conversation_agent.config import AppConfig, PersistenceMode, get_config
from conversation_agent.database.engine import DatabaseEngine
from conversation_agent.database.errors import (
    DurableApplicationExecutionError,
    FingerprintVersionError,
    IdempotencyOwnershipLostError,
    InvalidIdempotencyStateError,
    PersistenceError,
    PersistenceFinalizationError,
    RequestInitializationError,
    UnsupportedReplaySnapshotVersionError,
)
from conversation_agent.database.fake_execution import FakeIdempotentUnitOfWorkFactory
from conversation_agent.identity.authentication import (
    BearerTokenParser,
    JWTVerifier,
    PrincipalMappingPolicy,
)
from conversation_agent.identity.jwks import RemoteJwksProvider
from conversation_agent.orchestration.coordinator import Coordinator
from conversation_agent.runtime.builder import create_development_context_builder
from conversation_agent.runtime.models import RequestMetadata
from conversation_agent.task_types import TaskName


class ResponseProjectionError(RuntimeError):
    """The committed application result could not enter the public contract."""


def _new_id() -> str:
    return str(uuid.uuid4())


def _metadata(request: Request) -> RequestMetadata:
    return request.state.metadata


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request: Request,
    details: tuple[APIErrorItem, ...] = (),
    trace: tuple[RequestTraceStep, ...] = (),
    challenge: str | None = None,
) -> JSONResponse:
    metadata = _metadata(request)
    body = APIErrorResponse(
        code=code,
        message=message,
        request_id=metadata.request_id,
        trace_id=metadata.trace_id,
        details=details,
        trace=trace,
    )
    headers = {"WWW-Authenticate": challenge} if challenge else None
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers=headers,
    )


def _unconfigured_security(config: AppConfig) -> RequestSecurityService:
    return RequestSecurityService(
        runtime_mode=config.runtime_mode,
        bearer_parser=BearerTokenParser(config.oidc.max_token_bytes),
        authorization_service=AuthorizationService(),
    )


def create_app(
    *,
    service: ChatService | None = None,
    id_factory: Callable[[], str] | None = None,
    config: AppConfig | None = None,
    security_service: RequestSecurityService | None = None,
    http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    database_engine_factory: Callable[[], DatabaseEngine] | None = None,
    fake_uow_factory: FakeIdempotentUnitOfWorkFactory | None = None,
    projector: ResponseProjector | None = None,
) -> FastAPI:
    """Create one worker-local application and its lifespan-owned resources."""

    cfg = config or get_config()
    id_factory = id_factory or _new_id
    context_builder = create_development_context_builder()
    service = service or ChatService(
        coordinator=Coordinator(),
        context_builder=context_builder,
    )
    projector = projector or ResponseProjector()
    initial_security = security_service or _unconfigured_security(cfg)

    initial_dependencies: ApplicationDependencies | None = None
    if cfg.database.effective_persistence_mode is not PersistenceMode.POSTGRES:
        initial_dependencies = build_dependencies(
            config=cfg,
            chat_service=service,
            context_builder=context_builder,
            projector=projector,
            fake_uow_factory=fake_uow_factory,
        )

    @asynccontextmanager
    async def lifespan(api: FastAPI):
        stack = AsyncExitStack()
        api.state.readiness = False
        try:
            if security_service is None and cfg.oidc.is_configured:
                client = (
                    http_client_factory()
                    if http_client_factory is not None
                    else httpx.AsyncClient(follow_redirects=False)
                )
                stack.push_async_callback(client.aclose)
                provider = RemoteJwksProvider(config=cfg.oidc, client=client)
                verifier = JWTVerifier(config=cfg.oidc, jwks_provider=provider)
                api.state.security_service = RequestSecurityService(
                    runtime_mode=cfg.runtime_mode,
                    bearer_parser=BearerTokenParser(cfg.oidc.max_token_bytes),
                    authorization_service=AuthorizationService(),
                    verifier=verifier,
                    principal_mapper=PrincipalMappingPolicy(cfg.oidc),
                )

            engine = None
            if cfg.database.effective_persistence_mode is PersistenceMode.POSTGRES:
                engine = (
                    database_engine_factory()
                    if database_engine_factory is not None
                    else DatabaseEngine(cfg.database)
                )
                await engine.start()
                stack.push_async_callback(engine.stop)
                await engine.check_connectivity()
                await engine.check_revision(cfg.database.expected_revision)

            api.state.dependencies = build_dependencies(
                config=cfg,
                chat_service=service,
                context_builder=context_builder,
                projector=projector,
                database_engine=engine,
                fake_uow_factory=fake_uow_factory,
            )
            api.state.readiness = True
            yield
        finally:
            api.state.readiness = False
            await stack.aclose()

    api = FastAPI(
        title="Safety-Grounded Agent Orchestration API",
        version="0.1.0",
        docs_url="/docs" if cfg.api.docs_enabled else None,
        redoc_url="/redoc" if cfg.api.docs_enabled else None,
        openapi_url="/openapi.json" if cfg.api.docs_enabled else None,
        lifespan=lifespan,
    )
    api.state.security_service = initial_security
    api.state.dependencies = initial_dependencies
    api.state.readiness = initial_dependencies is not None

    @api.middleware("http")
    async def attach_request_metadata(request: Request, call_next):
        metadata = RequestMetadata(
            request_id=id_factory(),
            trace_id=id_factory(),
            received_at=datetime.now(timezone.utc),
        )
        request.state.metadata = metadata
        response = await call_next(request)
        response.headers["X-Request-ID"] = metadata.request_id
        response.headers["X-Trace-ID"] = metadata.trace_id
        return response

    @api.exception_handler(SecurityBoundaryError)
    async def security_error(request: Request, exc: SecurityBoundaryError):
        return _error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            request=request,
            trace=exc.trace,
            challenge=exc.challenge,
        )

    @api.exception_handler(IdempotencyHTTPError)
    async def idempotency_header_error(request: Request, exc: IdempotencyHTTPError):
        return _error_response(
            status_code=exc.status_code,
            code=exc.code,
            message="The Idempotency-Key header is invalid.",
            request=request,
            trace=getattr(request.state, "security_trace", ()),
        )

    @api.exception_handler(ExecutionGatewayError)
    async def gateway_error(request: Request, exc: ExecutionGatewayError):
        return _error_response(
            status_code=exc.status_code,
            code=exc.code,
            message="The request could not enter the selected execution mode.",
            request=request,
            trace=getattr(request.state, "security_trace", ()),
        )

    @api.exception_handler(UnsupportedReplaySnapshotVersionError)
    async def unsupported_snapshot(request: Request, exc):
        return _error_response(
            status_code=409,
            code="replay_snapshot_version_unsupported",
            message="The persisted response version is not supported.",
            request=request,
            trace=getattr(request.state, "security_trace", ()),
        )

    @api.exception_handler(FingerprintVersionError)
    async def incompatible_fingerprint(request: Request, exc):
        return _error_response(
            status_code=409,
            code="idempotency_fingerprint_version_incompatible",
            message="The persisted request fingerprint version is incompatible.",
            request=request,
            trace=getattr(request.state, "security_trace", ()),
        )

    @api.exception_handler(IdempotencyOwnershipLostError)
    async def ownership_lost(request: Request, exc):
        return _error_response(
            status_code=409,
            code="idempotency_ownership_lost",
            message="The request no longer owns the idempotency claim.",
            request=request,
            trace=getattr(request.state, "security_trace", ()),
        )

    @api.exception_handler(InvalidIdempotencyStateError)
    async def invalid_idempotency_state(request: Request, exc):
        return _error_response(
            status_code=500,
            code="invalid_idempotency_state",
            message="The persisted idempotency state is invalid.",
            request=request,
            trace=getattr(request.state, "security_trace", ()),
        )

    @api.exception_handler(RequestInitializationError)
    @api.exception_handler(PersistenceFinalizationError)
    @api.exception_handler(PersistenceError)
    async def persistence_error(request: Request, exc):
        return _error_response(
            status_code=503,
            code="persistence_unavailable",
            message="The durable request service is unavailable.",
            request=request,
            trace=getattr(request.state, "security_trace", ()),
        )

    @api.exception_handler(DurableApplicationExecutionError)
    @api.exception_handler(ApplicationExecutionError)
    async def application_error(request: Request, exc):
        trace = getattr(request.state, "security_trace", ()) + (
            RequestTraceStep(
                component="application",
                status="failed",
                code="application_execution_error",
                summary="The orchestration request failed.",
            ),
        )
        return _error_response(
            status_code=500,
            code="application_execution_error",
            message="The orchestration request could not be completed.",
            request=request,
            trace=trace,
        )

    @api.exception_handler(ResponseProjectionError)
    async def projection_error(request: Request, exc):
        return _error_response(
            status_code=500,
            code="response_projection_failed",
            message="The committed result could not be projected safely.",
            request=request,
            trace=getattr(request.state, "security_trace", ()),
        )

    @api.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = tuple(
            APIErrorItem(
                field=".".join(str(part) for part in error.get("loc", ())),
                message=error.get("msg", "Invalid request value."),
                error_type=error.get("type", "validation_error"),
            )
            for error in exc.errors()
        )
        return _error_response(
            status_code=422,
            code="request_validation_error",
            message="The request body or headers are invalid.",
            request=request,
            details=details,
            trace=getattr(request.state, "security_trace", ()),
        )

    @api.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        return _error_response(
            status_code=500,
            code="internal_server_error",
            message="The request could not be completed.",
            request=request,
            trace=getattr(request.state, "security_trace", ()),
        )

    @api.get("/healthz", response_model=HealthResponse, tags=["operations"])
    def healthz() -> HealthResponse:
        return HealthResponse()

    @api.get("/readyz", response_model=ReadinessResponse, tags=["operations"])
    async def readyz(request: Request, response: Response) -> ReadinessResponse:
        dependencies = request.app.state.dependencies
        if not request.app.state.readiness or dependencies is None:
            response.status_code = 503
            return ReadinessResponse(status="not_ready")
        engine = dependencies.database_engine
        if engine is not None:
            try:
                await engine.check_connectivity()
                await engine.check_revision(cfg.database.expected_revision)
            except PersistenceError:
                response.status_code = 503
                return ReadinessResponse(status="not_ready")
        return ReadinessResponse(status="ready")

    async def secure_chat(request: Request) -> SecurityContext:
        context = await request.app.state.security_service.secure(
            list(request.scope.get("headers", [])),
            ("chat:invoke", "rag:read", "crm:read"),
        )
        request.state.security_trace = context.trace
        return context

    async def secure_qa(request: Request) -> SecurityContext:
        context = await request.app.state.security_service.secure(
            list(request.scope.get("headers", [])),
            ("qa:invoke", "rag:read"),
        )
        request.state.security_trace = context.trace
        return context

    async def execute(
        request: Request,
        body: UserRequest,
        security: SecurityContext,
        *,
        operation: str,
        forced_task: TaskName | None = None,
    ) -> tuple[AgentResponse, str | None]:
        dependencies: ApplicationDependencies | None = request.app.state.dependencies
        if dependencies is None:
            raise ExecutionGatewayError("persistence_unavailable", status_code=503)
        parsed_key = dependencies.idempotency_parser.parse(
            list(request.scope.get("headers", []))
        )
        key = None if parsed_key is None else parsed_key.value
        metadata = _metadata(request)
        trusted_context = dependencies.context_builder.build(
            principal=security.principal,
            authorization=security.authorization,
            session_id=body.session_id,
            request_id=metadata.request_id,
            trace_id=metadata.trace_id,
            received_at=metadata.received_at,
            idempotency_key=key,
        )
        raw_response_requested = (
            cfg.rag_service.include_raw_response
            and "raw_response:view" in security.authorization.permissions
        )
        outcome = await dependencies.gateway.execute(
            body,
            context=trusted_context,
            operation=operation,
            idempotency_key=key,
            replay_compatible=not raw_response_requested,
            forced_task=forced_task,
        )
        try:
            projected = dependencies.projector.project(
                outcome.application_result,
                security_trace=security.trace,
                include_raw_response=cfg.rag_service.include_raw_response,
            )
            validated = AgentResponse.model_validate(
                projected.model_dump(mode="json")
            )
        except Exception as exc:
            raise ResponseProjectionError(
                "The public response projection failed."
            ) from exc
        return validated, outcome.idempotency_status

    common_responses = {
        400: {"model": APIErrorResponse},
        401: {"model": APIErrorResponse},
        403: {"model": APIErrorResponse},
        409: {"model": APIErrorResponse},
        422: {"model": APIErrorResponse},
        500: {"model": APIErrorResponse},
        503: {"model": APIErrorResponse},
    }
    idempotency_openapi = {
        "parameters": [
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": (
                    cfg.database.effective_idempotency_header_mode.value
                    == "required"
                ),
                "schema": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": cfg.database.max_idempotency_key_bytes,
                },
                "description": (
                    "Project-defined durable request key. Duplicate header "
                    "fields are rejected."
                ),
            }
        ]
    }

    @api.post(
        "/v1/chat",
        response_model=AgentResponse,
        responses=common_responses,
        tags=["agent"],
        openapi_extra=idempotency_openapi,
    )
    async def chat(
        request: Request,
        response: Response,
        body: UserRequest,
        security: SecurityContext = Depends(secure_chat),
    ) -> AgentResponse:
        result, status = await execute(
            request, body, security, operation="v1.chat"
        )
        if status is not None:
            response.headers["Idempotency-Status"] = status
        return result

    @api.post(
        "/v1/qa",
        response_model=AgentResponse,
        responses=common_responses,
        tags=["agent"],
        openapi_extra=idempotency_openapi,
    )
    async def qa(
        request: Request,
        response: Response,
        body: UserRequest,
        security: SecurityContext = Depends(secure_qa),
    ) -> AgentResponse:
        result, status = await execute(
            request,
            body,
            security,
            operation="v1.qa",
            forced_task="qa",
        )
        if status is not None:
            response.headers["Idempotency-Status"] = status
        return result

    return api


app = create_app()
