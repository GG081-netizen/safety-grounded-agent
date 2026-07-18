"""Strict JWKS parsing, validation, caching, and asynchronous retrieval."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

import httpx
from jwt import PyJWK

from conversation_agent.config import OIDCConfig


class JwksError(RuntimeError):
    """Base error for signing-key retrieval."""


class UnknownSigningKey(JwksError):
    """Raised when a valid JWKS does not contain the requested key."""


class JwksUnavailable(JwksError):
    """Raised when trusted signing material cannot be obtained."""


def _reject_duplicate_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_member")
        result[key] = value
    return result


class JwkSecurityValidator:
    """Apply the same project security profile to every JWK source."""

    _PRIVATE_FIELDS = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth"})

    def __init__(self, config: OIDCConfig) -> None:
        self._config = config

    def validate(self, raw: object) -> tuple[str, PyJWK]:
        if not isinstance(raw, dict):
            raise ValueError("jwk_must_be_object")
        if self._PRIVATE_FIELDS.intersection(raw):
            raise ValueError("private_jwk_not_allowed")
        if raw.get("kty") != "RSA":
            raise ValueError("unsupported_jwk_type")
        kid = raw.get("kid")
        if type(kid) is not str or not kid.strip():
            raise ValueError("invalid_jwk_kid")
        if raw.get("alg") not in (None, "RS256"):
            raise ValueError("invalid_jwk_algorithm")
        if raw.get("use") not in (None, "sig"):
            raise ValueError("invalid_jwk_use")
        key_ops = raw.get("key_ops")
        if key_ops is not None:
            if not isinstance(key_ops, list) or any(type(item) is not str for item in key_ops):
                raise ValueError("invalid_jwk_key_ops")
            if "verify" not in key_ops:
                raise ValueError("jwk_cannot_verify")
        try:
            parsed = PyJWK.from_dict(raw, algorithm="RS256")
        except Exception as exc:
            raise ValueError("invalid_rsa_jwk") from exc
        key_size = getattr(parsed.key, "key_size", 0)
        if key_size < self._config.min_rsa_key_size_bits:
            raise ValueError("rsa_key_too_short")
        return kid, parsed


class JwksDocumentParser:
    """Decode a complete JWKS document into an immutable validated key map."""

    def __init__(self, config: OIDCConfig) -> None:
        self._config = config
        self._validator = JwkSecurityValidator(config)

    def parse(self, body: bytes) -> Mapping[str, PyJWK]:
        try:
            text = body.decode("utf-8", errors="strict")
            document = json.loads(text, object_pairs_hook=_reject_duplicate_members)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise JwksUnavailable("invalid_jwks_document") from exc
        if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
            raise JwksUnavailable("invalid_jwks_keys")
        raw_keys = document["keys"]
        if not raw_keys or len(raw_keys) > self._config.jwks_max_keys:
            raise JwksUnavailable("invalid_jwks_key_count")
        parsed: dict[str, PyJWK] = {}
        try:
            for raw in raw_keys:
                kid, key = self._validator.validate(raw)
                if kid in parsed:
                    raise ValueError("duplicate_jwk_kid")
                parsed[kid] = key
        except ValueError as exc:
            raise JwksUnavailable(str(exc)) from exc
        return MappingProxyType(parsed)


class JwksProvider(Protocol):
    async def get_signing_key(self, kid: str) -> PyJWK:
        ...


class StaticJwksProvider:
    """Validated in-memory JWKS provider for tests and offline deployments."""

    def __init__(self, document: bytes, config: OIDCConfig) -> None:
        self._keys = JwksDocumentParser(config).parse(document)

    async def get_signing_key(self, kid: str) -> PyJWK:
        try:
            return self._keys[kid]
        except KeyError as exc:
            raise UnknownSigningKey("unknown_signing_key") from exc


@dataclass(frozen=True)
class _NegativeKid:
    generation: int
    expires_at: float


class RemoteJwksProvider:
    """Async JWKS provider with bounded streaming and transactional refresh."""

    def __init__(
        self,
        *,
        config: OIDCConfig,
        client: httpx.AsyncClient,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._client = client
        self._clock = monotonic_clock
        self._parser = JwksDocumentParser(config)
        self._positive_keys: Mapping[str, PyJWK] = MappingProxyType({})
        self._positive_cache_expires_at = 0.0
        self._negative_kids: dict[str, _NegativeKid] = {}
        self._generation = 0
        self._refresh_lock = asyncio.Lock()

    @property
    def generation(self) -> int:
        return self._generation

    def _kid_hash(self, kid: str) -> str:
        return hashlib.sha256(kid.encode("ascii")).hexdigest()

    def _negative_hit(self, kid: str, now: float) -> bool:
        entry = self._negative_kids.get(self._kid_hash(kid))
        return bool(
            entry
            and entry.generation == self._generation
            and entry.expires_at > now
        )

    async def get_signing_key(self, kid: str) -> PyJWK:
        now = self._clock()
        cache_expired = now >= self._positive_cache_expires_at
        if not cache_expired:
            if key := self._positive_keys.get(kid):
                return key
            if self._negative_hit(kid, now):
                raise UnknownSigningKey("unknown_signing_key")
        observed_generation = self._generation

        async with self._refresh_lock:
            now = self._clock()
            cache_expired = now >= self._positive_cache_expires_at
            if not cache_expired:
                if key := self._positive_keys.get(kid):
                    return key
                if self._negative_hit(kid, now):
                    raise UnknownSigningKey("unknown_signing_key")
            if cache_expired or self._generation == observed_generation:
                await self._refresh()
            if key := self._positive_keys.get(kid):
                return key
            self._negative_kids[self._kid_hash(kid)] = _NegativeKid(
                generation=self._generation,
                expires_at=self._clock() + self._config.negative_kid_ttl_seconds,
            )
            raise UnknownSigningKey("unknown_signing_key")

    async def _refresh(self) -> None:
        new_keys = await self._fetch_and_validate()
        now = self._clock()
        self._positive_keys = new_keys
        self._positive_cache_expires_at = now + self._config.jwks_cache_ttl_seconds
        self._generation += 1
        self._negative_kids.clear()

    async def _fetch_and_validate(self) -> Mapping[str, PyJWK]:
        body = bytearray()
        try:
            async with self._client.stream(
                "GET",
                self._config.jwks_url,
                follow_redirects=False,
                timeout=self._config.jwks_timeout_seconds,
            ) as response:
                if response.is_redirect:
                    raise JwksUnavailable("jwks_redirect_not_allowed")
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > self._config.jwks_max_response_bytes:
                            raise JwksUnavailable("jwks_response_too_large")
                    except ValueError:
                        pass
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._config.jwks_max_response_bytes:
                        raise JwksUnavailable("jwks_response_too_large")
        except JwksUnavailable:
            raise
        except Exception as exc:
            raise JwksUnavailable("jwks_fetch_failed") from exc
        return self._parser.parse(bytes(body))
