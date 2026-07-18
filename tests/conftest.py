"""Shared test fixtures."""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from conversation_agent.config import reset_config


LEGACY_UNIT_FILES = {
    "test_agent.py",
    "test_cli.py",
    "test_evaluation.py",
    "test_intent_router.py",
    "test_llm.py",
    "test_models.py",
    "test_orchestration.py",
    "test_policy.py",
    "test_rag.py",
    "test_rag_external.py",
    "test_scorer.py",
    "test_stores.py",
    "test_task_router.py",
    "test_tools.py",
}


def pytest_collection_modifyitems(config, items):
    unit = pytest.mark.unit
    integration = pytest.mark.integration
    for item in items:
        markers = {marker.name for marker in item.iter_markers()}
        if markers & {"unit", "integration", "e2e", "operational_integration"}:
            continue
        path = str(item.path).replace("\\", "/")
        if "/tests/integration/" in path:
            item.add_marker(integration)
        elif item.path.name in LEGACY_UNIT_FILES:
            item.add_marker(unit)
        else:
            item.warn(pytest.PytestWarning(f"Unclassified test: {item.nodeid}"))


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    credentials = (
        "CONVAGENT_DASHSCOPE_API_KEY",
        "DASHSCOPE_API_KEY",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CONVAGENT_OIDC_ISSUER",
        "CONVAGENT_OIDC_AUDIENCE",
        "CONVAGENT_OIDC_JWKS_URL",
    )
    for name in credentials:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONVAGENT_RAG_PROVIDER", "local")
    monkeypatch.setenv("CONVAGENT_RUNTIME_MODE", "demo")
    reset_config()
    yield
    reset_config()


@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory for isolated tests."""
    tmp = Path(tempfile.mkdtemp(prefix="convagent_test_"))
    customers = tmp / "customers"
    interactions = tmp / "interactions"
    backups = tmp / "backups" / "customers"
    customers.mkdir(parents=True, exist_ok=True)
    interactions.mkdir(parents=True, exist_ok=True)
    backups.mkdir(parents=True, exist_ok=True)
    (tmp / "aliases.json").write_text("{}")
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def sample_contact():
    return {
        "name": "李经理",
        "title": "采购主管",
        "department": "采购部",
        "influence_level": "high",
        "email": "li@example.com",
        "phone": "13800001111",
    }


@pytest.fixture
def sample_procurement_item():
    return {
        "product_name": "笔记本电脑",
        "category": "it_equipment",
        "quantity": 100,
        "unit_budget": 8000.0,
        "total_budget": 800000.0,
        "requirements": ["30天交付", "3年保修"],
    }
