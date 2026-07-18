from __future__ import annotations

import json
import logging

import pytest

from conversation_agent.logging_config import FriendlyConsoleFormatter, JsonlFormatter
from conversation_agent.observability.redaction import REDACTED, redact_value


pytestmark = pytest.mark.unit


def test_recursive_redaction_covers_keys_patterns_and_cycles() -> None:
    value: dict[str, object] = {
        "api_key": "sk-test-canary-not-real",
        "nested": [
            "Authorization: Bearer header.payload.signature",
            "postgresql://user:canary-password@localhost/db",
        ],
    }
    value["cycle"] = value

    rendered = repr(redact_value(value))

    assert "sk-test-canary-not-real" not in rendered
    assert "canary-password" not in rendered
    assert REDACTED in rendered
    assert "<cycle-detected>" in rendered


def test_jsonl_formatter_redacts_message_and_nested_extra() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Bearer secret-token-value",
        args=(),
        exc_info=None,
    )
    record.payload = {"password": "canary-password", "safe": "ok"}

    output = JsonlFormatter().format(record)
    parsed = json.loads(output)

    assert "secret-token-value" not in output
    assert "canary-password" not in output
    assert parsed["payload"]["password"] == REDACTED
    assert parsed["payload"]["safe"] == "ok"


def test_console_formatter_redacts_message() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Idempotency-Key: canary-idempotency-value",
        args=(),
        exc_info=None,
    )

    output = FriendlyConsoleFormatter().format(record)

    assert "canary-idempotency-value" not in output
    assert REDACTED in output
