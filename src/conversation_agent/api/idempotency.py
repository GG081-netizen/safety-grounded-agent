"""Strict HTTP idempotency header parsing without persistence side effects."""

from __future__ import annotations

from dataclasses import dataclass


class IdempotencyHTTPError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class ParsedIdempotencyKey:
    value: str
    byte_length: int


class IdempotencyKeyParser:
    def __init__(self, *, max_bytes: int) -> None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self._max_bytes = max_bytes

    def parse(
        self, raw_headers: list[tuple[bytes, bytes]]
    ) -> ParsedIdempotencyKey | None:
        values = [
            value
            for name, value in raw_headers
            if name.lower() == b"idempotency-key"
        ]
        if not values:
            return None
        if len(values) != 1:
            raise IdempotencyHTTPError("duplicate_idempotency_key")
        raw = values[0].strip(b" \t")
        if not raw:
            raise IdempotencyHTTPError("invalid_idempotency_key")
        if len(raw) > self._max_bytes:
            raise IdempotencyHTTPError("idempotency_key_too_long")
        if any(byte < 0x21 or byte > 0x7E for byte in raw):
            raise IdempotencyHTTPError("invalid_idempotency_key")
        try:
            value = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise IdempotencyHTTPError("invalid_idempotency_key") from exc
        return ParsedIdempotencyKey(value=value, byte_length=len(raw))
