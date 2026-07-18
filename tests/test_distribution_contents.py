from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "check_distribution_contents.py"
    spec = importlib.util.spec_from_file_location("distribution_check", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "member",
    (
        "package/.env",
        "package/.venv/bin/python",
        "package/logs/agent.jsonl",
        "package/customer/data/customer.json",
        "package/module.pyc",
    ),
)
def test_distribution_check_rejects_sensitive_members(member: str) -> None:
    module = _load_script()
    assert module._unsafe(member) is True


def test_distribution_check_accepts_normal_package_member() -> None:
    module = _load_script()
    assert module._unsafe("conversation_agent/policy/engine.py") is False
