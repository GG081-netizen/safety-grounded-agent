"""Tests for Phase 15-D deterministic portfolio demo generation."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_portfolio_examples.py"
EXAMPLES = ROOT / "examples"
SCENARIOS = ["procurement-planning", "policy-blocked", "rag-fallback"]
EXPECTED_FILES = {"request.json", "response.json", "trace.json", "README.md"}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_generate(output_dir: Path) -> None:
    env = {
        k: v for k, v in os.environ.items()
        if not any(forbidden in k for forbidden in (
            "ANTHROPIC", "DASHSCOPE", "DEEPSEEK", "DASHSCOPE",
            "OIDC", "DATABASE_URL",
        ))
    }
    env["PATH"] = os.environ.get("PATH", "")
    env["HOME"] = os.environ.get("HOME", "")
    result = subprocess.run(
        ["uv", "run", "python", str(SCRIPT),
         "--output-root", str(output_dir)],
        capture_output=True, text=True, env=env, cwd=str(ROOT),
        timeout=120,
    )
    assert result.returncode == 0, f"Generate failed: {result.stderr}\n{result.stdout}"


def _run_check(output_dir: Path) -> int:
    env = {
        k: v for k, v in os.environ.items()
        if not any(forbidden in k for forbidden in (
            "ANTHROPIC", "DASHSCOPE", "DEEPSEEK", "DASHSCOPE",
            "OIDC", "DATABASE_URL",
        ))
    }
    env["PATH"] = os.environ.get("PATH", "")
    env["HOME"] = os.environ.get("HOME", "")
    result = subprocess.run(
        ["uv", "run", "python", str(SCRIPT),
         "--check", "--output-root", str(output_dir)],
        capture_output=True, text=True, env=env, cwd=str(ROOT),
        timeout=120,
    )
    return result.returncode


# ── Generation Tests ──────────────────────────────────────────────────────────

def test_all_three_scenarios_exist_with_exact_four_files():
    for scenario in SCENARIOS:
        d = EXAMPLES / scenario
        assert d.is_dir(), f"Missing: {d}"
        entries = list(d.iterdir())
        for e in entries:
            assert not e.is_symlink(), f"Symlink: {e}"
            assert not e.is_dir(), f"Subdir: {e}"
        names = {e.name for e in entries}
        assert names == EXPECTED_FILES, f"{scenario}: {names}"


def test_all_json_parseable_no_nan():
    for scenario in SCENARIOS:
        for fn in ["request.json", "response.json", "trace.json"]:
            path = EXAMPLES / scenario / fn
            data = json.loads(path.read_text(encoding="utf-8"))
            text = path.read_text(encoding="utf-8")
            assert "NaN" not in text
            assert "Infinity" not in text


def test_ids_consistent_across_files():
    for scenario in SCENARIOS:
        req = json.loads((EXAMPLES / scenario / "request.json").read_text())
        resp = json.loads((EXAMPLES / scenario / "response.json").read_text())
        trace = json.loads((EXAMPLES / scenario / "trace.json").read_text())
        rid = req.get("expected_request_id")
        tid = req.get("expected_trace_id")
        assert rid == resp.get("request_id") == trace.get("request_id"), f"{scenario}: request_id mismatch"
        assert tid == resp.get("trace_id") == trace.get("trace_id"), f"{scenario}: trace_id mismatch"


def test_generation_is_deterministic_same_process():
    """Two independent subprocess runs produce byte-identical output."""
    import tempfile
    tmp_a = Path(tempfile.mkdtemp(prefix="det-a-"))
    tmp_b = Path(tempfile.mkdtemp(prefix="det-b-"))
    try:
        _run_generate(tmp_a)
        _run_generate(tmp_b)
        for scenario in SCENARIOS:
            for fn in EXPECTED_FILES:
                ba = (tmp_a / scenario / fn).read_bytes()
                bb = (tmp_b / scenario / fn).read_bytes()
                assert ba == bb, f"{scenario}/{fn} differs across processes"
    finally:
        import shutil
        shutil.rmtree(tmp_a, ignore_errors=True)
        shutil.rmtree(tmp_b, ignore_errors=True)


# ── Check Mode Tests ──────────────────────────────────────────────────────────

def test_check_mode_reports_match():
    rc = _run_check(EXAMPLES)
    assert rc == 0, "--check should report match for clean examples"


def test_check_mode_reports_diff_on_modified_file():
    import tempfile, shutil
    tmp = Path(tempfile.mkdtemp(prefix="check-diff-"))
    shutil.copytree(str(EXAMPLES), str(tmp), dirs_exist_ok=True)
    try:
        # Corrupt a file
        (tmp / "procurement-planning" / "response.json").write_text("corrupted")
        rc = _run_check(tmp)
        assert rc != 0, "--check should report mismatch for corrupted file"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_check_mode_does_not_modify_files():
    import stat
    path = EXAMPLES / "procurement-planning" / "response.json"
    orig_mtime = path.stat().st_mtime
    orig_content = path.read_bytes()
    _run_check(EXAMPLES)
    assert path.stat().st_mtime == orig_mtime, "--check must not modify mtime"
    assert path.read_bytes() == orig_content, "--check must not modify content"


# ── FixedIdFactory Tests ──────────────────────────────────────────────────────

def test_fixed_id_factory_exhaustion_fails():
    from scripts.generate_portfolio_examples import FixedIdFactory
    f = FixedIdFactory(["a"])
    assert f() == "a"
    with pytest.raises(RuntimeError, match="exhausted"):
        f()


def test_fixed_id_factory_verify_consumed():
    from scripts.generate_portfolio_examples import FixedIdFactory
    f = FixedIdFactory(["a", "b"])
    f()
    f()
    f.verify_consumed(2)
    with pytest.raises(RuntimeError, match="consumed"):
        f.verify_consumed(3)


# ── Guard Tests ───────────────────────────────────────────────────────────────

def test_guard_blocks_socket_create_connection():
    from scripts.generate_portfolio_examples import _install_guards, _uninstall_guards
    _install_guards()
    try:
        with pytest.raises(RuntimeError, match="blocked"):
            socket.create_connection(("127.0.0.1", 8001))
    finally:
        _uninstall_guards()


@pytest.mark.enable_socket
def test_guard_blocks_socket_connect():
    from scripts.generate_portfolio_examples import _install_guards, _uninstall_guards
    _install_guards()
    try:
        with pytest.raises(RuntimeError, match="blocked"):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.connect(("127.0.0.1", 8001))
            finally:
                s.close()
    finally:
        _uninstall_guards()


def test_guard_blocks_real_http_transport():
    from scripts.generate_portfolio_examples import _install_guards, _uninstall_guards
    _install_guards()
    try:
        with pytest.raises(RuntimeError, match="blocked"):
            httpx.HTTPTransport()
    finally:
        _uninstall_guards()


def test_guard_blocks_real_async_http_transport():
    from scripts.generate_portfolio_examples import _install_guards, _uninstall_guards
    _install_guards()
    try:
        with pytest.raises(RuntimeError, match="blocked"):
            httpx.AsyncHTTPTransport()
    finally:
        _uninstall_guards()


def test_guard_blocks_httpx_post():
    from scripts.generate_portfolio_examples import _install_guards, _uninstall_guards
    _install_guards()
    try:
        with pytest.raises(RuntimeError, match="blocked"):
            httpx.post("http://127.0.0.1:1/test")
    finally:
        _uninstall_guards()


def test_guard_blocks_external_rag_factory():
    from scripts.generate_portfolio_examples import _install_guards, _uninstall_guards
    _install_guards()
    try:
        import conversation_agent.rag.factory as rf
        with pytest.raises(RuntimeError, match="blocked"):
            rf.create_rag_client(None)
    finally:
        _uninstall_guards()


def test_guard_blocks_create_llm_client():
    from scripts.generate_portfolio_examples import _install_guards, _uninstall_guards
    _install_guards()
    try:
        import conversation_agent.llm.factory as lf
        with pytest.raises(RuntimeError, match="blocked"):
            lf.create_llm_client(None)
    finally:
        _uninstall_guards()


def test_guard_blocks_get_config():
    from scripts.generate_portfolio_examples import _install_guards, _uninstall_guards
    _install_guards()
    try:
        import conversation_agent.config as cfg
        with pytest.raises(RuntimeError, match="blocked"):
            cfg.get_config()
    finally:
        _uninstall_guards()


def test_guard_restores_on_exit():
    from scripts.generate_portfolio_examples import (
        _install_guards, _uninstall_guards,
    )
    import conversation_agent.config as cfg
    import conversation_agent.rag.factory as rf
    import conversation_agent.llm.factory as lf

    orig_get_config = cfg.get_config
    orig_create_rag = rf.create_rag_client
    orig_create_llm = lf.create_llm_client
    orig_conn = socket.create_connection
    orig_httpx_post = httpx.post

    _install_guards()
    _uninstall_guards()

    assert cfg.get_config is orig_get_config
    assert rf.create_rag_client is orig_create_rag
    assert lf.create_llm_client is orig_create_llm
    assert socket.create_connection is orig_conn
    assert httpx.post is orig_httpx_post


# ── Contract Tests ────────────────────────────────────────────────────────────

def test_procurement_contract():
    trace = json.loads((EXAMPLES / "procurement-planning" / "trace.json").read_text())
    ca = trace["contract_assertions"]
    assert ca["policy_decision"] == "SAFE"
    assert ca["orchestrator_entry_calls"] == 1
    assert ca["intent_router_calls"] >= 1
    assert ca["task_router_calls"] >= 1
    assert ca["downstream_task_execution_calls"] == 1
    assert ca["primary_rag_calls"] == 1
    assert ca["fallback_rag_calls"] == 0
    assert ca["fallback_used"] is False
    assert ca["citations_count"] >= 1
    assert ca["evidence_count"] >= 1
    assert ca["trace_complete"] is True
    assert ca["provider"] == "deterministic_portfolio_rag"
    assert ca["network_access"] is False

    # Verify request records natural router result
    req = json.loads((EXAMPLES / "procurement-planning" / "request.json").read_text())
    assert "method" in req
    assert "endpoint" in req
    assert req["endpoint"] == "/v1/qa"


def test_blocked_contract():
    trace = json.loads((EXAMPLES / "policy-blocked" / "trace.json").read_text())
    ca = trace["contract_assertions"]
    assert ca["policy_decision"] == "BLOCKED"
    assert ca["orchestrator_entry_calls"] == 1
    assert ca["intent_router_calls"] == 0
    assert ca["task_router_calls"] == 0
    assert ca["downstream_task_execution_calls"] == 0
    assert ca["primary_rag_calls"] == 0
    assert ca["fallback_rag_calls"] == 0
    assert ca["tool_calls"] == 0
    assert ca["domain_agent_calls"] == 0
    assert ca["runtime_steps"] == ["policy_engine"]
    assert ca["normalized_stages"] == ["policy"]


def test_fallback_contract():
    trace = json.loads((EXAMPLES / "rag-fallback" / "trace.json").read_text())
    ca = trace["contract_assertions"]
    assert ca["policy_decision"] == "SAFE"
    assert ca["orchestrator_entry_calls"] == 1
    assert ca["primary_rag_calls"] == 1
    assert ca["external_failure_type"] == "timeout"
    assert ca["fallback_rag_calls"] == 1
    assert ca["fallback_used"] is True
    assert ca["result_provider"] == "fallback"
    assert ca["fallback_source_adapter"] == "deterministic_local_fallback"
    assert ca["network_access"] is False
    assert ca["confidence"] <= 0.55
    assert ca["confidence_within_fallback_cap"] is True
    assert ca["warning_visible"] is True
    assert "External RAG unavailable" in ca.get("warning_text", "")
    assert ca["no_fake_external_citation"] is True
    assert ca["citation_sources_all_local"] is True


def test_fallback_no_external_citation_sources():
    resp = json.loads((EXAMPLES / "rag-fallback" / "response.json").read_text())
    rag = resp.get("result", {}).get("rag_result") or {}
    sources = rag.get("sources", [])
    for s in sources:
        path = s.get("source_path", "")
        sid = s.get("source_id", "")
        assert not path.startswith(("http://", "https://")), f"External URL: {path}"
        assert not sid.startswith(("external", "remote", "live")), f"External ID: {sid}"
    assert rag.get("provider") == "fallback", "Provider must be fallback, not external"


# ── Structural Integrity Tests ────────────────────────────────────────────────

def test_response_is_real_agent_response():
    resp = json.loads((EXAMPLES / "procurement-planning" / "response.json").read_text())
    for key in ("request_id", "trace_id", "session_id", "result", "trace"):
        assert key in resp, f"AgentResponse missing: {key}"
    result = resp["result"]
    for key in ("policy", "final_response", "confidence", "trace"):
        assert key in result, f"OrchestrationResult missing: {key}"


def test_runtime_steps_not_confused_with_diagnostics():
    trace = json.loads((EXAMPLES / "procurement-planning" / "trace.json").read_text())
    assert "runtime_steps" in trace
    assert "rag_diagnostics" in trace
    assert isinstance(trace["runtime_steps"], list)
    assert isinstance(trace["rag_diagnostics"], list)
    # Verify distinction: runtime_steps has step_name, rag_diagnostics has provider
    if trace["runtime_steps"]:
        assert "step_name" in trace["runtime_steps"][0]
    if trace["rag_diagnostics"]:
        assert "provider" in trace["rag_diagnostics"][0]


def test_normalized_stages_have_source_annotations():
    trace = json.loads((EXAMPLES / "procurement-planning" / "trace.json").read_text())
    for stage in trace.get("normalized_stages", []):
        assert "stage" in stage
        assert "source_type" in stage
        assert "source_name" in stage


def test_adapter_counts_distinct_from_observed():
    trace = json.loads((EXAMPLES / "procurement-planning" / "trace.json").read_text())
    ac = trace.get("adapter_call_counts", {})
    ob = trace.get("observed_runtime_counts", {})
    assert "tool_calls" not in ac, "tool_calls should be in observed_runtime_counts"
    assert "domain_agent_calls" not in ac, "domain_agent_calls should be in observed_runtime_counts"


def test_request_json_no_internal_fields():
    req = json.loads((EXAMPLES / "procurement-planning" / "request.json").read_text())
    assert "expected_request_id" in req
    assert "expected_trace_id" in req
    assert "token" not in req
    assert "secret" not in req
    assert "Authorization" not in req
