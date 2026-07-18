"""OIDC-compatible JWT verification and trusted principal mapping."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

import jwt
from pydantic import BaseModel, ConfigDict, Field

from conversation_agent.config import OIDCConfig
from conversation_agent.identity.jwks import (
    JwksProvider,
    JwksUnavailable,
    UnknownSigningKey,
)
from conversation_agent.identity.models import Principal


class AuthenticationFailure(RuntimeError):
    def __init__(self, code: str, *, status_code: int, challenge: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.challenge = challenge


class VerifiedClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    subject: str = Field(min_length=1, max_length=255)
    issuer: str = Field(min_length=1)
    audiences: tuple[str, ...]
    organization_id: str = Field(min_length=1, max_length=128)
    department_id: str | None = Field(default=None, max_length=128)
    display_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    roles: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    enabled: bool = True
    issued_at: int
    expires_at: int
    not_before: int | None = None


def require_numeric_date(value: object, claim: str) -> int:
    if type(value) is not int:
        raise AuthenticationFailure(
            "invalid_access_token", status_code=401, challenge='Bearer error="invalid_token"'
        )
    return value


class BearerTokenParser:
    """Parse exactly one ASCII Bearer credential from raw ASGI headers."""

    def __init__(self, max_token_bytes: int) -> None:
        self._max_token_bytes = max_token_bytes

    def parse(self, headers: Sequence[tuple[bytes, bytes]]) -> str | None:
        values = [value for name, value in headers if name.lower() == b"authorization"]
        if not values:
            return None
        if len(values) != 1:
            self._invalid_request()
        raw = values[0]
        if len(raw) > self._max_token_bytes + len(b"Bearer "):
            self._invalid_request()
        try:
            scheme, token_bytes = raw.split(b" ", 1)
            token = token_bytes.decode("ascii")
        except (ValueError, UnicodeDecodeError):
            self._invalid_request()
        if scheme.lower() != b"bearer" or not token or " " in token:
            self._invalid_request()
        if len(token_bytes) > self._max_token_bytes:
            self._invalid_request()
        return token

    def _invalid_request(self) -> None:
        raise AuthenticationFailure(
            "invalid_request",
            status_code=400,
            challenge='Bearer error="invalid_request"',
        )


class JWTVerifier:
    def __init__(
        self,
        *,
        config: OIDCConfig,
        jwks_provider: JwksProvider,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._jwks = jwks_provider
        self._clock = wall_clock

    async def verify(self, token: str) -> VerifiedClaims:
        if token.count(".") != 2:
            self._invalid_token()
        try:
            header = jwt.get_unverified_header(token)
            kid = self._validate_header(header)
        except AuthenticationFailure:
            raise
        except Exception:
            self._invalid_token()
        try:
            signing_key = await self._jwks.get_signing_key(kid)
        except UnknownSigningKey:
            self._invalid_token()
        except JwksUnavailable as exc:
            raise AuthenticationFailure(
                "authentication_service_unavailable", status_code=503
            ) from exc
        try:
            payload = jwt.decode(
                token,
                key=signing_key.key,
                algorithms=["RS256"],
                issuer=self._config.issuer,
                audience=self._config.audience,
                leeway=self._config.clock_skew_seconds,
                options={
                    "require": ["iss", "sub", "aud", "exp", "iat"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_nbf": True,
                    "verify_iss": True,
                    "verify_aud": True,
                    "enforce_minimum_key_length": True,
                },
            )
            return self._validate_claims(payload)
        except AuthenticationFailure:
            raise
        except Exception:
            self._invalid_token()

    def _validate_header(self, header: object) -> str:
        if not isinstance(header, dict) or header.get("alg") != "RS256":
            self._invalid_token()
        if any(name in header for name in ("jku", "x5u", "jwk", "x5c", "crit")):
            self._invalid_token()
        kid = header.get("kid")
        if type(kid) is not str or not kid:
            self._invalid_token()
        try:
            encoded = kid.encode("ascii")
        except UnicodeEncodeError:
            self._invalid_token()
        if len(encoded) > self._config.max_kid_bytes or any(
            byte < 0x20 or byte > 0x7E for byte in encoded
        ):
            self._invalid_token()
        required_typ = self._config.required_typ_header
        if required_typ is not None and header.get("typ") != required_typ:
            self._invalid_token()
        return kid

    def _validate_claims(self, payload: object) -> VerifiedClaims:
        if not isinstance(payload, dict):
            self._invalid_token()
        issuer = self._strict_string(payload.get("iss"), "iss")
        subject = self._strict_string(payload.get("sub"), "sub", max_length=255)
        organization = self._strict_string(payload.get("organization_id"), "organization_id")
        if organization != self._config.expected_organization_id:
            self._invalid_token()
        audiences = self._audiences(payload.get("aud"))
        if self._config.audience not in audiences:
            self._invalid_token()
        issued_at = require_numeric_date(payload.get("iat"), "iat")
        expires_at = require_numeric_date(payload.get("exp"), "exp")
        not_before = (
            require_numeric_date(payload.get("nbf"), "nbf") if "nbf" in payload else None
        )
        now = int(self._clock())
        skew = self._config.clock_skew_seconds
        if expires_at <= issued_at or expires_at - issued_at > self._config.max_token_lifetime_seconds:
            self._invalid_token()
        if issued_at > now + skew or (not_before is not None and not_before > now + skew):
            self._invalid_token()
        claim_name = self._config.required_token_use_claim
        claim_value = self._config.required_token_use_value
        if claim_name and claim_value:
            if self._strict_string(payload.get(claim_name), claim_name) != claim_value:
                self._invalid_token()
        roles = self._string_array(payload.get("roles", []), "roles")
        groups = self._string_array(payload.get("groups", []), "groups")
        enabled = payload.get("enabled", True)
        if type(enabled) is not bool:
            self._invalid_token()
        return VerifiedClaims(
            subject=subject,
            issuer=issuer,
            audiences=audiences,
            organization_id=organization,
            department_id=self._optional_string(payload.get("department_id"), "department_id", 128),
            display_name=self._optional_string(payload.get("display_name"), "display_name", 255),
            email=self._optional_string(payload.get("email"), "email", 320),
            roles=roles,
            groups=groups,
            enabled=enabled,
            issued_at=issued_at,
            expires_at=expires_at,
            not_before=not_before,
        )

    def _strict_string(self, value: object, claim: str, max_length: int | None = None) -> str:
        if type(value) is not str or not value.strip():
            self._invalid_token()
        result = value.strip()
        if max_length is not None and len(result) > max_length:
            self._invalid_token()
        return result

    def _optional_string(self, value: object, claim: str, max_length: int) -> str | None:
        if value is None:
            return None
        return self._strict_string(value, claim, max_length)

    def _string_array(self, value: object, claim: str) -> tuple[str, ...]:
        if not isinstance(value, list) or any(type(item) is not str for item in value):
            self._invalid_token()
        values = {item.strip() for item in value}
        if "" in values:
            self._invalid_token()
        return tuple(sorted(values))

    def _audiences(self, value: object) -> tuple[str, ...]:
        if type(value) is str and value:
            return (value,)
        if not isinstance(value, list) or not value or any(
            type(item) is not str or not item for item in value
        ):
            self._invalid_token()
        if len(set(value)) != len(value):
            self._invalid_token()
        return tuple(value)

    def _invalid_token(self) -> None:
        raise AuthenticationFailure(
            "invalid_access_token",
            status_code=401,
            challenge='Bearer error="invalid_token"',
        )


class PrincipalMappingPolicy:
    def __init__(self, config: OIDCConfig) -> None:
        self._config = config

    def map(self, claims: VerifiedClaims) -> Principal:
        return Principal(
            tenant_id=self._config.tenant_id,
            organization_id=self._config.expected_organization_id,
            user_id=claims.subject,
            display_name=claims.display_name,
            email=claims.email,
            department_id=claims.department_id,
            roles=claims.roles,
            groups=claims.groups,
            enabled=claims.enabled,
        )
