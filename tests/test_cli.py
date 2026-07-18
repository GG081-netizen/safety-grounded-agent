"""Tests for CLI (Phase 9)."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from conversation_agent.config import reset_config, get_config
from conversation_agent.cli.main import cli


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    reset_config()
    cfg = get_config()
    cfg.storage.data_dir = tmp_path / "data"
    yield
    reset_config()


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def _seed(runner):
    runner.invoke(cli, ["seed", "--count", "5", "--clear"])


# ═══════════════════════════════════════════════════════════════════════════════
# Seed
# ═══════════════════════════════════════════════════════════════════════════════


class TestSeedCommand:
    def test_generates_customers(self, runner):
        result = runner.invoke(cli, ["seed", "--count", "3", "--clear"])
        assert result.exit_code == 0
        assert "已生成 3 个客户" in result.output


# ═══════════════════════════════════════════════════════════════════════════════
# Customer list
# ═══════════════════════════════════════════════════════════════════════════════


class TestCustomerList:
    def test_list_empty(self, runner):
        result = runner.invoke(cli, ["customer", "list"])
        assert result.exit_code == 0

    def test_list_with_data(self, runner, _seed):
        result = runner.invoke(cli, ["customer", "list"])
        assert result.exit_code == 0
        assert len(result.output.splitlines()) >= 1

    def test_list_json(self, runner, _seed):
        result = runner.invoke(cli, ["--json", "customer", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "customer_id" in data[0]


# ═══════════════════════════════════════════════════════════════════════════════
# Customer show
# ═══════════════════════════════════════════════════════════════════════════════


class TestCustomerShow:
    @pytest.fixture
    def _first_name(self, runner, _seed):
        """Return the name of the first customer from --json list."""
        r = runner.invoke(cli, ["--json", "customer", "list"])
        data = json.loads(r.output)
        assert len(data) > 0
        return data[0]["customer_name"]

    def test_show_by_name(self, runner, _seed, _first_name):
        result = runner.invoke(cli, ["customer", "show", _first_name])
        assert result.exit_code == 0
        assert _first_name in result.output

    def test_show_nonexistent(self, runner):
        result = runner.invoke(cli, ["customer", "show", "nonexistent_xyz"])
        assert result.exit_code != 0

    def test_show_json(self, runner, _seed, _first_name):
        result = runner.invoke(cli, ["--json", "customer", "show", _first_name])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["customer_name"] == _first_name


# ═══════════════════════════════════════════════════════════════════════════════
# Customer search
# ═══════════════════════════════════════════════════════════════════════════════


class TestCustomerSearch:
    def test_search_by_name(self, runner, _seed):
        result = runner.invoke(cli, ["customer", "search", "--name", "华"])
        assert result.exit_code == 0

    def test_search_json(self, runner, _seed):
        result = runner.invoke(cli, ["--json", "customer", "search", "--name", "华"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)


# ═══════════════════════════════════════════════════════════════════════════════
# Chat
# ═══════════════════════════════════════════════════════════════════════════════


class TestChatCommand:
    def test_chat_customer_intake(self, runner, _seed):
        result = runner.invoke(cli, ["chat", "新客户采购100台服务器"])
        assert result.exit_code == 0
        assert "agent" in result.output.lower() or "意图" in result.output

    def test_chat_query(self, runner, _seed):
        result = runner.invoke(cli, ["chat", "查一下有哪些客户"])
        assert result.exit_code == 0

    def test_chat_json(self, runner, _seed):
        result = runner.invoke(cli, ["--json", "chat", "查一下有哪些客户"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "session_id" in data
        assert "intent_result" in data
        assert "agent_response" in data

    def test_chat_no_input(self, runner):
        result = runner.invoke(cli, ["chat"])
        assert result.exit_code != 0


# ═══════════════════════════════════════════════════════════════════════════════
# Verbose flag
# ═══════════════════════════════════════════════════════════════════════════════


class TestVerboseFlag:
    def test_verbose_does_not_break(self, runner, _seed):
        result = runner.invoke(cli, ["-v", "customer", "list"])
        assert result.exit_code == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Evaluation
# ═══════════════════════════════════════════════════════════════════════════════


class TestEvalCommand:
    def test_rag_adapter_eval_human_output(self, runner):
        result = runner.invoke(cli, ["eval", "rag-adapter"])
        assert result.exit_code == 0
        assert "RAG Adapter Evaluation" in result.output
        assert "Cases: 6" in result.output
        assert "Status: PASS" in result.output

    def test_rag_adapter_eval_json_output(self, runner):
        result = runner.invoke(cli, ["--json", "eval", "rag-adapter"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "summary" in data
        assert "cases" in data
        assert data["summary"]["case_count"] == 6
        assert data["summary"]["raw_response_exposure_rate"] == 0.0

    def test_policy_boundary_eval_human_output(self, runner):
        result = runner.invoke(cli, ["eval", "policy-boundary"])
        assert result.exit_code == 0
        assert "Policy Boundary Evaluation" in result.output
        assert "Status: PASS" in result.output
        assert "Covered categories:" in result.output

    def test_policy_boundary_eval_json_output(self, runner):
        result = runner.invoke(cli, ["--json", "eval", "policy-boundary"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "summary" in data
        assert "cases" in data
        assert data["summary"]["status"] == "pass"
        assert data["summary"]["uncertain_detection_rate"] >= 0.8
        assert "covered_categories" in data["summary"]

    def test_production_blockers_phase_strict_uses_blocked_exit_code(self, runner):
        result = runner.invoke(
            cli,
            ["eval", "production-blockers", "--scope", "phase", "--strict"],
        )

        assert result.exit_code == 3, repr(result.exception)
        assert "Phase: BLOCKED" in result.output
