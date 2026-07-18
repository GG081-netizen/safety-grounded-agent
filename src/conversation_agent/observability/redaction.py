"""Recursive, fail-closed redaction for logs and diagnostics."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, SecretStr

REDACTED = "[REDACTED]"
_MAX_DEPTH = 8
_SENSITIVE_KEYS = {
    "apikey",
    "accesstoken",
    "authtoken",
    "authorization",
    "bearertoken",
    "password",
    "passwd",
    "secret",
    "clientsecret",
    "privatekey",
    "cookie",
    "setcookie",
    "databaseurl",
    "dsn",
    "idempotencykey",
}
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b"
)
_API_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_DSN_RE = re.compile(
    r"(?P<prefix>postgres(?:ql)?(?:\+[a-z0-9_]+)?://[^\s:/@]+:)[^\s@]+(?P<suffix>@[^\s]+)",
    re.IGNORECASE,
)
_ENV_SECRET_RE = re.compile(
    r"(?im)\b([A-Z0-9_]*(?:API_KEY|AUTH_TOKEN|ACCESS_TOKEN|PASSWORD|SECRET|DATABASE_URL))\s*=\s*([^\s]+)"
)
_COOKIE_RE = re.compile(r"(?i)\b(Set-Cookie|Cookie)\s*:\s*[^\r\n]+")
_IDEMPOTENCY_RE = re.compile(r"(?i)\bIdempotency-Key\s*:\s*[^\s,;]+")


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _is_sensitive_key(key: str | None) -> bool:
    if not key:
        return False
    normalized = _normalized_key(key)
    return normalized in _SENSITIVE_KEYS or any(
        token in normalized
        for token in ("password", "secret", "privatekey", "authtoken", "apikey")
    )


def redact_text(value: str) -> str:
    """Redact known credential forms without raising or echoing failures."""
    try:
        result = _PRIVATE_KEY_RE.sub(REDACTED, value)
        result = _BEARER_RE.sub(f"Bearer {REDACTED}", result)
        result = _JWT_RE.sub(REDACTED, result)
        result = _API_KEY_RE.sub(REDACTED, result)
        result = _DSN_RE.sub(
            lambda match: f"{match.group('prefix')}{REDACTED}{match.group('suffix')}",
            result,
        )
        result = _ENV_SECRET_RE.sub(
            lambda match: f"{match.group(1)}={REDACTED}", result
        )
        result = _COOKIE_RE.sub(lambda match: f"{match.group(1)}: {REDACTED}", result)
        return _IDEMPOTENCY_RE.sub(f"Idempotency-Key: {REDACTED}", result)
    except Exception:
        return REDACTED


def redact_value(
    value: object,
    *,
    key: str | None = None,
    depth: int = 0,
    seen: set[int] | None = None,
) -> object:
    """Recursively convert a value to a low-sensitivity logging form."""
    if _is_sensitive_key(key):
        return REDACTED
    if depth > _MAX_DEPTH:
        return "<max-depth-reached>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, SecretStr):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes):
        return REDACTED
    if isinstance(value, BaseException):
        return {"error_type": type(value).__name__}

    active = seen if seen is not None else set()
    identity = id(value)
    if identity in active:
        return "<cycle-detected>"
    active.add(identity)
    try:
        if isinstance(value, BaseModel):
            return redact_mapping(
                value.model_dump(mode="python"), depth=depth + 1, seen=active
            )
        if isinstance(value, Mapping):
            return redact_mapping(value, depth=depth + 1, seen=active)
        if isinstance(value, list):
            return [
                redact_value(item, depth=depth + 1, seen=active) for item in value
            ]
        if isinstance(value, tuple):
            return tuple(
                redact_value(item, depth=depth + 1, seen=active) for item in value
            )
        if isinstance(value, (set, frozenset)):
            redacted = [
                redact_value(item, depth=depth + 1, seen=active) for item in value
            ]
            return sorted(redacted, key=lambda item: repr(item))
        try:
            return redact_text(str(value))
        except Exception:
            return "<unprintable>"
    except Exception:
        return REDACTED
    finally:
        active.discard(identity)


def redact_mapping(
    value: Mapping[str, object],
    *,
    depth: int = 0,
    seen: set[int] | None = None,
) -> dict[str, object]:
    """Return a new mapping with sensitive keys and nested values redacted."""
    if depth > _MAX_DEPTH:
        return {"value": "<max-depth-reached>"}
    result: dict[str, object] = {}
    for raw_key, item in value.items():
        try:
            item_key = str(raw_key)
        except Exception:
            item_key = "<unprintable-key>"
        result[item_key] = redact_value(
            item,
            key=item_key,
            depth=depth,
            seen=seen,
        )
    return result
