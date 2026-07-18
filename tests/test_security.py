import asyncio
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from conversation_agent.api.app import create_app
from conversation_agent.api.security import RequestSecurityService
from conversation_agent.application.service import ChatService
from conversation_agent.authorization.service import AuthorizationService
from conversation_agent.config import APIConfig, AppConfig, OIDCConfig, RagServiceConfig
from conversation_agent.identity.authentication import (
    BearerTokenParser,
    JWTVerifier,
    PrincipalMappingPolicy,
)
from conversation_agent.identity.jwks import (
    JwksDocumentParser,
    JwksUnavailable,
    RemoteJwksProvider,
    StaticJwksProvider,
    UnknownSigningKey,
)
from conversation_agent.identity.models import Principal
from conversation_agent.orchestration.models import AgentStep, OrchestrationResult
from conversation_agent.policy.models import PolicyDecision
from conversation_agent.rag.models import RagResult
from conversation_agent.runtime.builder import create_development_context_builder


pytestmark = pytest.mark.unit


def _oidc(**overrides) -> OIDCConfig:
    values = {
        "issuer": "https://issuer.example",
        "audience": "conversation-agent",
        "jwks_url": "https://issuer.example/.well-known/jwks.json",
        "tenant_id": "tenant-server",
        "expected_organization_id": "org-server",
    }
    values.update(overrides)
    return OIDCConfig(**values)


def _key_and_jwks(bits: int = 2048, kid: str = "key-1"):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    raw = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    raw.update({"kid": kid, "alg": "RS256", "use": "sig", "key_ops": ["verify"]})
    document = json.dumps({"keys": [raw]}).encode()
    return private_key, document


def _token(private_key, *, kid="key-1", assigned_roles=("agent_user",), **claims):
    now = int(time.time())
    payload = {
        "iss": "https://issuer.example",
        "sub": "user-1",
        "aud": "conversation-agent",
        "iat": now - 60,
        "nbf": now - 60,
        "exp": now + 600,
        "organization_id": "org-server",
        "roles": list(assigned_roles),
        "groups": ["sales"],
        "enabled": True,
        "token_use": "access",
    }
    payload.update(claims)
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


def _security(private_key, document, *, roles=("agent_user",)):
    config = _oidc()
    verifier = JWTVerifier(
        config=config,
        jwks_provider=StaticJwksProvider(document, config),
    )
    service = RequestSecurityService(
        runtime_mode="demo",
        bearer_parser=BearerTokenParser(config.max_token_bytes),
        authorization_service=AuthorizationService(),
        verifier=verifier,
        principal_mapper=PrincipalMappingPolicy(config),
    )
    return service, _token(private_key, assigned_roles=roles)


class RecordingCoordinator:
    def __init__(self, *, blocked=False):
        self.calls = []
        self.blocked = blocked

    def run(self, user_input, session_id=None, task_override=None, *, request_metadata=None):
        del request_metadata
        self.calls.append(user_input)
        policy = PolicyDecision(status="BLOCKED" if self.blocked else "SAFE")
        rag = None if self.blocked else RagResult(
            answer="answer",
            provider="external",
            confidence=0.8,
            raw_response={"private": True},
        )
        return OrchestrationResult(
            session_id=session_id,
            user_input=user_input,
            policy=policy,
            final_response="blocked" if self.blocked else "answer",
            rag_result=rag,
            confidence=0.8,
            trace=[AgentStep(step_name="policy_engine", output_summary=policy.status)],
        )


def _api(coordinator, security_service, *, include_raw=False, runtime_mode="demo"):
    config = AppConfig(
        runtime_mode=runtime_mode,
        oidc=_oidc(),
        rag_service=RagServiceConfig(provider="local", include_raw_response=include_raw),
    )
    service = ChatService(
        coordinator=coordinator,
        context_builder=create_development_context_builder(),
    )
    ids = iter(f"security-id-{index}" for index in range(100))
    return create_app(
        service=service,
        security_service=security_service,
        config=config,
        id_factory=lambda: next(ids),
    )


def _request(app, path="/v1/chat", *, token=None, headers=None):
    async def send():
        transport = httpx.ASGITransport(app=app)
        request_headers = list(headers or [])
        if token is not None:
            request_headers.append(("Authorization", f"Bearer {token}"))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, json={"text": "query"}, headers=request_headers)

    return asyncio.run(send())


def test_valid_token_maps_trusted_principal_and_authorizes():
    private_key, document = _key_and_jwks()
    service, token = _security(private_key, document)
    result = asyncio.run(
        service.secure(
            [(b"authorization", f"Bearer {token}".encode())],
            ("chat:invoke", "rag:read", "crm:read"),
        )
    )
    assert result.principal.tenant_id == "tenant-server"
    assert result.principal.organization_id == "org-server"
    assert result.authorization.allowed is True


@pytest.mark.parametrize("claim", ["iat", "exp", "nbf"])
def test_numeric_dates_reject_boolean_values(claim):
    private_key, document = _key_and_jwks()
    service, token = _security(private_key, document)
    token = _token(private_key, **{claim: True})
    response = _request(_api(RecordingCoordinator(), service), token=token)
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_access_token"


def test_demo_invalid_token_never_falls_back_to_development_principal():
    private_key, document = _key_and_jwks()
    service, _ = _security(private_key, document)
    coordinator = RecordingCoordinator()
    response = _request(_api(coordinator, service), token="invalid.token.value")
    assert response.status_code == 401
    assert coordinator.calls == []


def test_test_mode_without_token_rejects_placeholder_identity():
    private_key, document = _key_and_jwks()
    configured, _ = _security(private_key, document)
    configured._runtime_mode = "test"
    coordinator = RecordingCoordinator()
    response = _request(_api(coordinator, configured, runtime_mode="test"))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert coordinator.calls == []


def test_demo_duplicate_authorization_header_is_invalid_request():
    private_key, document = _key_and_jwks()
    service, token = _security(private_key, document)
    response = _request(
        _api(RecordingCoordinator(), service),
        headers=[("Authorization", f"Bearer {token}"), ("Authorization", f"Bearer {token}")],
    )
    assert response.status_code == 400
    assert response.json()["trace"][0]["code"] == "invalid_request"


def test_debug_viewer_is_additive_not_a_superuser():
    private_key, document = _key_and_jwks()
    debug_only, token = _security(private_key, document, roles=("debug_viewer",))
    coordinator = RecordingCoordinator()
    response = _request(_api(coordinator, debug_only, include_raw=True), token=token)
    assert response.status_code == 403
    assert coordinator.calls == []


def test_debug_payload_requires_route_and_debug_permissions_plus_config():
    private_key, document = _key_and_jwks()
    service, token = _security(
        private_key, document, roles=("agent_user", "debug_viewer")
    )
    response = _request(_api(RecordingCoordinator(), service, include_raw=True), token=token)
    assert response.status_code == 200
    assert response.json()["debug"] == {
        "rag_raw_response": {
            "provider": "external",
            "payload": {"private": True},
        }
    }


def test_debug_payload_stays_null_when_config_gate_is_closed():
    private_key, document = _key_and_jwks()
    service, token = _security(
        private_key, document, roles=("agent_user", "debug_viewer")
    )
    response = _request(_api(RecordingCoordinator(), service, include_raw=False), token=token)
    assert response.status_code == 200
    assert response.json()["debug"] is None


def test_policy_blocked_is_http_200_after_authentication_and_authorization():
    private_key, document = _key_and_jwks()
    service, token = _security(private_key, document)
    response = _request(_api(RecordingCoordinator(blocked=True), service), token=token)
    assert response.status_code == 200
    assert [step["component"] for step in response.json()["trace"]] == [
        "authentication",
        "authorization",
        "policy_engine",
    ]
    assert response.json()["trace"][-1]["status"] == "blocked"


def test_role_permissions_are_a_stable_union_and_disabled_is_denied():
    auth = AuthorizationService()
    principal = Principal(
        tenant_id="t",
        organization_id="o",
        user_id="u",
        roles=("unknown", "debug_viewer", "agent_user", "agent_user"),
    )
    assert auth.effective_permissions(principal) == (
        "chat:invoke",
        "crm:read",
        "qa:invoke",
        "rag:read",
        "raw_response:view",
    )
    denied = auth.authorize(principal.model_copy(update={"enabled": False}), ("chat:invoke",))
    assert denied.allowed is False
    assert denied.code == "denied_disabled_principal"


def test_jwks_rejects_short_rsa_and_duplicate_json_members():
    _, short_document = _key_and_jwks(bits=1024)
    with pytest.raises(JwksUnavailable):
        JwksDocumentParser(_oidc()).parse(short_document)
    with pytest.raises(JwksUnavailable):
        JwksDocumentParser(_oidc()).parse(b'{"keys":[],"keys":[]}')


def test_jwks_accepts_4096_bit_rsa_and_rejects_private_material():
    _, large_document = _key_and_jwks(bits=4096)
    assert "key-1" in JwksDocumentParser(_oidc()).parse(large_document)
    private_key, _ = _key_and_jwks()
    private_jwk = json.loads(RSAAlgorithm.to_jwk(private_key))
    private_jwk.update({"kid": "private", "alg": "RS256", "use": "sig"})
    document = json.dumps({"keys": [private_jwk]}).encode()
    with pytest.raises(JwksUnavailable):
        JwksDocumentParser(_oidc()).parse(document)


@pytest.mark.parametrize("header_name", ["jku", "x5u", "jwk", "x5c", "crit"])
def test_unapproved_jose_headers_are_rejected(header_name):
    private_key, document = _key_and_jwks()
    service, _ = _security(private_key, document)
    now = int(time.time())
    payload = {
        "iss": "https://issuer.example",
        "sub": "user-1",
        "aud": "conversation-agent",
        "iat": now - 30,
        "exp": now + 300,
        "organization_id": "org-server",
        "token_use": "access",
        "roles": ["agent_user"],
    }
    value = {"kty": "RSA"} if header_name == "jwk" else ["exp"] if header_name == "crit" else "https://attacker.invalid/key"
    token = jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": "key-1", header_name: value},
    )
    response = _request(_api(RecordingCoordinator(), service), token=token)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": ["conversation-agent", "conversation-agent"]},
        {"aud": ["conversation-agent", 7]},
        {"organization_id": "other-org"},
        {"token_use": "id"},
        {"sub": 123},
        {"roles": "agent_user"},
        {"enabled": "false"},
    ],
)
def test_strict_claim_contract_rejects_malformed_values(claims):
    private_key, document = _key_and_jwks()
    service, _ = _security(private_key, document)
    token = _token(private_key, **claims)
    response = _request(_api(RecordingCoordinator(), service), token=token)
    assert response.status_code == 401


def test_remote_jwks_single_flight_negative_cache_and_expiry_fail_closed():
    async def scenario():
        _, document = _key_and_jwks(kid="known")
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=document)

        clock = [0.0]
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = RemoteJwksProvider(
            config=_oidc(jwks_cache_ttl_seconds=10),
            client=client,
            monotonic_clock=lambda: clock[0],
        )
        results = await asyncio.gather(
            *(provider.get_signing_key("missing") for _ in range(5)),
            return_exceptions=True,
        )
        assert all(isinstance(result, UnknownSigningKey) for result in results)
        assert calls == 1
        generation = provider.generation
        clock[0] = 11.0

        async def failed_handler(request):
            return httpx.Response(500)

        await client.aclose()
        failed_client = httpx.AsyncClient(transport=httpx.MockTransport(failed_handler))
        provider._client = failed_client
        with pytest.raises(JwksUnavailable):
            await provider.get_signing_key("known")
        assert provider.generation == generation
        await failed_client.aclose()

    asyncio.run(scenario())


def test_remote_jwks_enforces_streamed_size_without_content_length():
    async def scenario():
        async def handler(request):
            return httpx.Response(200, content=b"x" * 2048)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = RemoteJwksProvider(
            config=_oidc(jwks_max_response_bytes=1024),
            client=client,
        )
        with pytest.raises(JwksUnavailable):
            await provider.get_signing_key("key")
        assert not client.is_closed
        await client.aclose()

    asyncio.run(scenario())


def test_expired_jwks_ignores_negative_cache_and_discovers_rotated_key():
    async def scenario():
        _, first_document = _key_and_jwks(kid="old")
        _, rotated_document = _key_and_jwks(kid="new")
        documents = [first_document, rotated_document]
        calls = 0

        async def handler(request):
            nonlocal calls
            index = min(calls, 1)
            calls += 1
            return httpx.Response(200, content=documents[index])

        clock = [0.0]
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = RemoteJwksProvider(
            config=_oidc(jwks_cache_ttl_seconds=10),
            client=client,
            monotonic_clock=lambda: clock[0],
        )
        with pytest.raises(UnknownSigningKey):
            await provider.get_signing_key("new")
        clock[0] = 11.0
        assert await provider.get_signing_key("new") is not None
        assert calls == 2
        await client.aclose()

    asyncio.run(scenario())


def test_production_can_disable_openapi_and_docs():
    config = AppConfig(
        runtime_mode="production",
        oidc=_oidc(),
        api=APIConfig(docs_enabled=False),
        database={
            "url": "postgresql+asyncpg://localhost:5432/db",
            "persistence_mode": "postgres",
            "idempotency_header_mode": "required",
        },
    )
    app = create_app(config=config)
    assert app.openapi_url is None
    assert not any(route.path in {"/docs", "/redoc"} for route in app.routes)
