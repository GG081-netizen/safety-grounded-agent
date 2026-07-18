"""Structured JSONL logging for the Procurement Sales Copilot Agent.

Usage:
    from conversation_agent.logging_config import setup_logging
    setup_logging(level="INFO", jsonl_file="logs/agent.jsonl")
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from conversation_agent.observability.redaction import redact_mapping, redact_text


class JsonlFormatter(logging.Formatter):
    """Format log records as JSON lines with consistent fields."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            entry: dict = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "msg": redact_text(record.getMessage()),
            }

        # Include exception info if present
            if record.exc_info and record.exc_info[1]:
                entry["exc"] = {"error_type": type(record.exc_info[1]).__name__}

        # Include extra fields passed via `extra=` kwarg
            for key in ("session_id", "customer_id", "intent", "tool", "tokens", "latency_ms"):
                val = getattr(record, key, None)
                if val is not None:
                    entry[key] = val

        # Catch-all for any other custom extra fields
            known = {"session_id", "customer_id", "intent", "tool", "tokens", "latency_ms"}
            extras: dict[str, object] = {}
            for key, val in record.__dict__.items():
                if key not in known and not key.startswith("_") and key not in {
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "levelname", "levelno", "lineno", "module", "msecs",
                "message", "msg", "name", "pathname", "process", "processName",
                "relativeCreated", "stack_info", "thread", "threadName",
                }:
                    extras[key] = val
            entry.update(redact_mapping(extras))

            return json.dumps(entry, ensure_ascii=False, default=str)
        except Exception:
            return json.dumps({"msg": "<logging-format-error>"})


class FriendlyConsoleFormatter(logging.Formatter):
    """Human-readable console formatter for non-JSONL output."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            message = redact_text(record.getMessage())
            return f"[{ts}] {record.levelname:<7} {record.name:<28} {message}"
        except Exception:
            return "<logging-format-error>"


def setup_logging(
    level: str = "INFO",
    jsonl_file: str | Path | None = "logs/agent.jsonl",
    jsonl_enabled: bool = True,
    console_enabled: bool = True,
) -> logging.Logger:
    """Configure the root logger with console + optional JSONL output.

    Args:
        level: Log level for the root logger.
        jsonl_file: Path for JSONL output. None disables file output.
        jsonl_enabled: Whether to enable JSONL output at all.
        console_enabled: Whether to log to stderr in human-readable format.

    Returns:
        The root logger, configured.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers (idempotent)
    root.handlers.clear()

    # Console handler (human-readable)
    if console_enabled:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.DEBUG)
        console.setFormatter(FriendlyConsoleFormatter())
        root.addHandler(console)

    # JSONL file handler (machine-readable)
    if jsonl_enabled and jsonl_file:
        path = Path(jsonl_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(path), encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JsonlFormatter())
        root.addHandler(file_handler)

    # Quiet down noisy third-party loggers
    for noisy in ("urllib3", "httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return root


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a module, ensuring the root logger is configured.

    Prefer this over logging.getLogger() so the logger is always usable
    even if setup_logging hasn't been called yet.
    """
    logger = logging.getLogger(name)
    if not logger.handlers and not logging.getLogger().handlers:
        # Auto-configure with sensible defaults if not yet set up
        setup_logging()
    return logger
