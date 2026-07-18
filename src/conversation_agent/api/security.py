"""HTTP authentication and deterministic authorization boundary."""

from __future__ import annotations

from dataclasses import dataclass

from conversation_agent.api.models import RequestTraceStep
from conversation_agent.authorization.models import AuthorizationDecision
from conversation_agent.authorization.service import AuthorizationService
from conversation_agent.identity.authentication import (
    AuthenticationFailure,
    BearerTokenParser,
    JWTVerifier,
    PrincipalMappingPolicy,
)
from conversation_agent.identity.models import Principal
from conversation_agent.runtime.builder import development_security_context


class SecurityBoundaryError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        trace: tuple[RequestTraceStep, ...],
        challenge: str | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.trace = trace
        self.challenge = challenge


@dataclass(frozen=True)
class SecurityContext:
    principal: Principal
    authorization: AuthorizationDecision
    trace: tuple[RequestTraceStep, ...]


class RequestSecurityService:
    def __init__(
        self,
        *,
        runtime_mode: str,
        bearer_parser: BearerTokenParser,
        authorization_service: AuthorizationService,
        verifier: JWTVerifier | None = None,
        principal_mapper: PrincipalMappingPolicy | None = None,
    ) -> None:
        self._runtime_mode = runtime_mode
        self._bearer = bearer_parser
        self._authorization = authorization_service
        self._verifier = verifier
        self._mapper = principal_mapper

    async def secure(
        self,
        raw_headers: list[tuple[bytes, bytes]],
        required_permissions: tuple[str, ...],
    ) -> SecurityContext:
        try:
            token = self._bearer.parse(raw_headers)
        except AuthenticationFailure as exc:
            raise self._authentication_error(exc) from exc

        if token is None:
            if self._runtime_mode != "demo":
                raise SecurityBoundaryError(
                    status_code=401,
                    code="authentication_required",
                    message="Authentication is required.",
                    challenge="Bearer",
                    trace=(self._trace("authentication", "denied", "authentication_required"),),
                )
            principal, _ = development_security_context()
        else:
            if self._verifier is None or self._mapper is None:
                raise SecurityBoundaryError(
                    status_code=503,
                    code="authentication_service_unavailable",
                    message="Authentication service is unavailable.",
                    trace=(self._trace("authentication", "failed", "authentication_service_unavailable"),),
                )
            try:
                claims = await self._verifier.verify(token)
            except AuthenticationFailure as exc:
                raise self._authentication_error(exc) from exc
            principal = self._mapper.map(claims)

        auth_trace = self._trace("authentication", "succeeded", "authenticated")
        decision = self._authorization.authorize(principal, required_permissions)
        if not decision.allowed:
            raise SecurityBoundaryError(
                status_code=403,
                code="authorization_denied",
                message="The authenticated principal is not authorized for this route.",
                challenge='Bearer error="insufficient_scope"',
                trace=(
                    auth_trace,
                    self._trace("authorization", "denied", decision.code),
                ),
            )
        return SecurityContext(
            principal=principal,
            authorization=decision,
            trace=(auth_trace, self._trace("authorization", "succeeded", "allowed")),
        )

    def _authentication_error(self, exc: AuthenticationFailure) -> SecurityBoundaryError:
        status = "failed" if exc.status_code == 503 else "denied"
        message = (
            "Authentication service is unavailable."
            if exc.status_code == 503
            else "Authentication failed."
        )
        return SecurityBoundaryError(
            status_code=exc.status_code,
            code=exc.code,
            message=message,
            challenge=exc.challenge,
            trace=(self._trace("authentication", status, exc.code),),
        )

    @staticmethod
    def _trace(component: str, status: str, code: str) -> RequestTraceStep:
        summaries = {
            "authentication_required": "Authentication is required.",
            "invalid_request": "The Bearer authentication request is malformed.",
            "invalid_access_token": "Authentication failed.",
            "authentication_service_unavailable": "Authentication service is unavailable.",
            "authenticated": "Authentication succeeded.",
            "allowed": "Authorization succeeded.",
            "denied_disabled_principal": "The principal is disabled.",
            "denied_missing_permission": "Required route permissions are missing.",
        }
        return RequestTraceStep(
            component=component,
            status=status,  # type: ignore[arg-type]
            code=code,
            summary=summaries.get(code, "Security boundary evaluated."),
        )
