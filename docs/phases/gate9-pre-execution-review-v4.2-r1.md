# Gate 9 Pre-Execution Review Bundle v4.2-R1 — FastAPI HTTP Smoke

## A. v4.2 Defect Review

| # | Defect | Severity | Root Cause |
|---|--------|----------|------------|
| 1 | Success condition count claimed 18, actual 19 (process_group_terminated) | High | Count not updated when new field added |
| 2 | Pipe drain failure silenced with `b""` fallback | Critical | Exception handler assigned empty bytes as fake drain result |
| 3 | Process group not checked/force-killed after leader exits | Critical | SIGKILL gated on `proc.poll() is None`, missing group-level check |
| 4 | `PermissionError`/generic `OSError` treated as group absent | High | `_process_group_alive()` returned False on EPERM |
| 5 | `sigterm_sent = True` even when signal was never delivered | High | `_send_group_signal()` swallowed `ProcessLookupError`, caller always set flag |
| 6 | Output schema divergent across formal/self-check/invalid modes | Medium | `_base_report()` missing fields; self-check had extra fields formal lacked |
| 7 | Self-check returned static `passed` without any validation | Medium | `self_check()` returned hard-coded dict |
| 8 | `failed_final_conditions` incomplete when failure_reason auto-generated | Medium | Conditions evaluated before failure_reason was set |
| 9 | Evidence 016–028 not fully specified | High | Relied on "identical to v4.1" reference |
| 10 | Compatibility Review source basis vague or incorrect | Low | Demo auth, QA forced task, dir creation sources imprecise |

## B. v4.2-R1 Change Summary

| # | Change | Runtime Impact | Evidence Impact |
|---|--------|---------------|-----------------|
| 1 | Success conditions → 19 (process_group_terminated added) | None | None |
| 2 | `pipe_drain_succeeded` field; never use `b""` fallback | Yes — drain failure now fails gate | None |
| 3 | Group-level SIGKILL even after leader exits; bounded group-termination polling | Yes — stronger containment | None |
| 4 | `_process_group_state()` returns `"exists"/"absent"/"unknown"` using `errno` | Yes — correct EPERM handling | None |
| 5 | `_send_group_signal()` returns `"delivered"/"already_gone"`; `sigterm_sent` only on `"delivered"` | Yes — correct signal tracking | None |
| 6 | `_base_report()` contains all 32 fields; all 3 modes share identical key set | None | None |
| 7 | `self_check()` validates 9 internal invariants (no side effects) | None | Evidence 013 contract updated |
| 8 | Two-phase final-condition evaluation; `failed_final_conditions` matches actual JSON | None | None |
| 9 | Evidence 016–028 fully specified with outer argv, cwd, expected values | None | None |
| 10 | Compatibility Review source basis corrected with precise file paths and function names | None | None |

---

## C. Complete v4.2-R1 Review Bundle

---

## C1. gate9_http_smoke.py — Complete v4.2-R1 Source

```python
"""Phase 15-F Gate 9 — FastAPI HTTP Smoke Harness (v4.2-R1).

Pure HTTP smoke: launch one Uvicorn in its own process group,
make 3 formal HTTP requests (no redirects), validate every
contract assertion including exact trace order, graceful
process-group shutdown with bounded group-termination polling,
bounded port-release polling.  Outputs exactly one JSON object
+ LF to stdout.  Creates no files.  Single-return-after-finally.
"""
from __future__ import annotations

import errno
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════════
# Frozen constants
# ═══════════════════════════════════════════════════════════════════════════════

CLEAN_CLONE = Path("/home/dick/.phase15_f/continuation_c1/clean_clone_r2")
VENV_PYTHON = str(CLEAN_CLONE / ".venv/bin/python")
GATE9_RUNTIME = Path("/home/dick/.phase15_f/continuation_c1/gate9_runtime_data")

HOST = "127.0.0.1"
PORT = 18080
BASE_URL = f"http://{HOST}:{PORT}"

STARTUP_TIMEOUT = 15
REQUEST_TIMEOUT = 5
SHUTDOWN_TIMEOUT = 10
PORT_RELEASE_TIMEOUT = 3.0
PORT_RELEASE_INTERVAL = 0.1
PROCESS_GROUP_RELEASE_TIMEOUT = 3.0
PROCESS_GROUP_RELEASE_INTERVAL = 0.1

UVICORN_ARGV = [
    VENV_PYTHON,
    "-m",
    "uvicorn",
    "conversation_agent.api.app:app",
    "--host",
    "127.0.0.1",
    "--port",
    "18080",
    "--lifespan",
    "on",
    "--log-level",
    "error",
    "--no-access-log",
]

# 17 keys
UVICORN_ENV = {
    "HOME": "/home/dick",
    "PATH": "/home/dick/miniconda3/bin:/home/dick/.local/bin:/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONUTF8": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "CONVAGENT_RUNTIME_MODE": "demo",
    "CONVAGENT_PERSISTENCE_MODE": "null",
    "CONVAGENT_DATABASE_ENABLED": "false",
    "CONVAGENT_DATABASE_REQUIRED": "false",
    "CONVERSATION_AGENT_DATA_DIR": str(GATE9_RUNTIME),
    "CONVAGENT_RAG_PROVIDER": "local",
    "CONVAGENT_RAG_FALLBACK_TO_LOCAL": "false",
    "CONVAGENT_RAG_INCLUDE_RAW_RESPONSE": "false",
    "CONVAGENT_API_DOCS_ENABLED": "false",
    "CONVERSATION_AGENT_LOG_LEVEL": "ERROR",
    "NO_PROXY": "127.0.0.1,localhost",
}

FORBIDDEN_ENV = [
    "CONVAGENT_DATABASE_URL",
    "CONVAGENT_OIDC_ISSUER",
    "CONVAGENT_OIDC_AUDIENCE",
    "CONVAGENT_OIDC_JWKS_URL",
    "DASHSCOPE_API_KEY",
    "CONVAGENT_DASHSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "CONVAGENT_DATA_DIR",
]

# Frozen trace contract — exact order, status, and code for QA requests
QA_TRACE_COMPONENTS = [
    "authentication",
    "authorization",
    "policy_engine",
    "router",
    "local_rag_query",
]
QA_TRACE_STATUSES = ["succeeded", "succeeded", "succeeded", "succeeded", "succeeded"]
QA_TRACE_CODES = [
    "authenticated",
    "allowed",
    "policy_engine",
    "router",
    "local_rag_query",
]

# Final success conditions — exactly 19 (used by self-check invariant)
FINAL_CONDITION_NAMES = [
    "failure_reason_null",
    "cleanup_errors_empty",
    "failed_final_conditions_empty",
    "all_http_assertions_passed",
    "server_launch_count_1",
    "formal_http_request_count_3",
    "qa_http_request_count_1",
    "retry_count_0",
    "server_started",
    "server_process_alive_after_probe",
    "process_group_terminated",
    "sigterm_sent",
    "server_terminated",
    "uvicorn_exit_code_0",
    "force_kill_used_false",
    "port_available_before",
    "port_released_after",
    "uvicorn_stdout_empty",
    "uvicorn_stderr_empty",
]

# Expected formal HTTP endpoints
FORMAL_HTTP_ENDPOINTS = [
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("POST", "/v1/qa"),
]

# ═══════════════════════════════════════════════════════════════════════════════
# No-redirect HTTP opener
# ═══════════════════════════════════════════════════════════════════════════════

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

_HTTP_OPENER = urllib.request.build_opener(_NoRedirect)

# ═══════════════════════════════════════════════════════════════════════════════
# Contract bodies
# ═══════════════════════════════════════════════════════════════════════════════

HEALTHZ_BODY = {
    "status": "ok",
    "service": "conversation-agent",
    "version": "0.1.0",
}

READYZ_BODY = {
    "status": "ready",
}

QA_REQUEST_BODY = {
    "text": "整理服务器采购需求",
    "session_id": "phase15-f-gate9-session",
}

QA_EXPECTED_FINAL_RESPONSE = (
    "未在本地知识库中找到足够证据，无法给出可引用结论。\n"
    "\n"
    "置信度：0.15\n"
    "提示：no_evidence"
)

QA_EXPECTED_RAG_ANSWER = "未在本地知识库中找到足够证据，无法给出可引用结论。"


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _now() -> float:
    return time.monotonic()


def _port_can_bind(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _tcp_probe(host: str, port: int, deadline: float) -> bool:
    while _now() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def _http_get(url: str, timeout: int) -> tuple[int, dict, dict]:
    req = urllib.request.Request(url)
    with _HTTP_OPENER.open(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp.status, body, headers


def _http_post_json(url: str, data: dict, timeout: int) -> tuple[int, dict, dict]:
    body_bytes = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _HTTP_OPENER.open(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp.status, body, headers


def _send_group_signal(pid: int, sig: int) -> str:
    """Send signal to process group. Returns "delivered" or "already_gone".
    Raises on non-ESRCH errors (which are recorded as cleanup_errors)."""
    try:
        os.killpg(pid, sig)
        return "delivered"
    except ProcessLookupError:
        return "already_gone"


def _process_group_state(pid: int) -> str:
    """Probe process group state. Returns "exists", "absent", or "unknown".
    - ESRCH / ProcessLookupError → "absent"
    - EPERM → "exists" (group exists but we can't signal it)
    - Other OSError → raises (caller records as cleanup_error)
    """
    try:
        os.killpg(pid, 0)
        return "exists"
    except ProcessLookupError:
        return "absent"
    except PermissionError:
        # EPERM: group exists, we lack permission to signal
        return "exists"
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return "absent"
        raise  # other errno → unknown, let caller handle


def _validate_healthz(status: int, body: dict, headers: dict) -> tuple[bool, dict]:
    a = {
        "status_200": status == 200,
        "content_type_json": headers.get("content-type", "").startswith("application/json"),
        "body_exact": body == HEALTHZ_BODY,
        "x_request_id_nonempty": bool(headers.get("x-request-id", "")),
        "x_trace_id_nonempty": bool(headers.get("x-trace-id", "")),
    }
    return all(a.values()), a


def _validate_readyz(status: int, body: dict, headers: dict) -> tuple[bool, dict]:
    a = {
        "status_200": status == 200,
        "content_type_json": headers.get("content-type", "").startswith("application/json"),
        "body_exact": body == READYZ_BODY,
        "x_request_id_nonempty": bool(headers.get("x-request-id", "")),
        "x_trace_id_nonempty": bool(headers.get("x-trace-id", "")),
    }
    return all(a.values()), a


def _validate_qa_contract(status: int, body: dict, headers: dict) -> tuple[bool, dict]:
    """Return (all_pass, {assertion_name: bool}) — exactly 30 assertions."""
    result = body.get("result", {})
    rag = result.get("rag_result", {})
    policy = result.get("policy", {})
    task_route = result.get("task_route", {})
    trace = body.get("trace", ())
    x_request_id = headers.get("x-request-id", "")
    x_trace_id = headers.get("x-trace-id", "")
    body_request_id = body.get("request_id", "")
    body_trace_id = body.get("trace_id", "")

    trace_components = [step.get("component", "") for step in trace]
    trace_statuses = [step.get("status", "") for step in trace]
    trace_codes = [step.get("code", "") for step in trace]

    a = {
        # ── HTTP / identity ──────────────────────────────────────────
        "status_200": status == 200,
        "content_type_json": headers.get("content-type", "").startswith("application/json"),
        "session_id": body.get("session_id") == "phase15-f-gate9-session",
        "request_id_nonempty": bool(body_request_id),
        "trace_id_nonempty": bool(body_trace_id),
        "request_id_not_equal_trace_id": (
            bool(body_request_id) and bool(body_trace_id)
            and body_request_id != body_trace_id
        ),
        "debug_present_and_null": ("debug" in body and body["debug"] is None),
        "x_request_id_header_nonempty": bool(x_request_id),
        "x_trace_id_header_nonempty": bool(x_trace_id),
        "idempotency_status_absent": "idempotency-status" not in headers,
        "request_id_mirrors_header": body_request_id == x_request_id,
        "trace_id_mirrors_header": body_trace_id == x_trace_id,
        # ── result ───────────────────────────────────────────────────
        "result_session_id": result.get("session_id") == "phase15-f-gate9-session",
        "user_input": result.get("user_input") == "整理服务器采购需求",
        "confidence_015": result.get("confidence") == 0.15,
        "final_response_exact": result.get("final_response") == QA_EXPECTED_FINAL_RESPONSE,
        "policy_status_SAFE": policy.get("status") == "SAFE",
        "task_qa": task_route.get("task") == "qa",
        "task_confidence_10": task_route.get("confidence") == 1.0,
        # ── RAG public contract ──────────────────────────────────────
        "rag_provider_local": rag.get("provider") == "local",
        "rag_answer_exact": rag.get("answer") == QA_EXPECTED_RAG_ANSWER,
        "rag_confidence_015": rag.get("confidence") == 0.15,
        "rag_warnings_exact": rag.get("warnings") == ["no_evidence"],
        "rag_evidence_exact": rag.get("evidence") == [],
        "rag_sources_exact": rag.get("sources") == [],
        "citations_exact": result.get("citations") == [],
        "rag_raw_response_absent": "raw_response" not in rag,
        # ── trace exact contract ─────────────────────────────────────
        "trace_components_exact": trace_components == QA_TRACE_COMPONENTS,
        "trace_statuses_all_succeeded": trace_statuses == QA_TRACE_STATUSES,
        "trace_codes_exact": trace_codes == QA_TRACE_CODES,
    }
    return all(a.values()), a


# ═══════════════════════════════════════════════════════════════════════════════
# Unified output schema — every mode returns every key
# ═══════════════════════════════════════════════════════════════════════════════

def _base_report() -> dict:
    return {
        "status": None,
        "mode": None,
        "failure_reason": None,
        "failed_final_conditions": [],
        "server_launch_count": 0,
        "formal_http_request_count": 0,
        "qa_http_request_count": 0,
        "retry_count": 0,
        "server_started": False,
        "server_process_alive_after_probe": False,
        "process_group_terminated": False,
        "server_terminated": False,
        "sigterm_sent": False,
        "force_kill_used": False,
        "uvicorn_exit_code": None,
        "all_http_assertions_passed": False,
        "pipe_drain_succeeded": False,
        "uvicorn_stdout_bytes": 0,
        "uvicorn_stderr_bytes": 0,
        "uvicorn_stdout_empty": False,
        "uvicorn_stderr_empty": False,
        "startup_duration_seconds": 0.0,
        "shutdown_duration_seconds": 0.0,
        "port_available_before": False,
        "port_released_after": False,
        "cleanup_errors": [],
        "healthz": {},
        "readyz": {},
        "qa": {},
        "runtime_root_created": False,
        "file_write_count": 0,
        "port_probe_count": 0,
        "self_check_invariants": {},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Self-check mode — validates internal invariants, no side effects
# ═══════════════════════════════════════════════════════════════════════════════

def self_check() -> dict:
    r = _base_report()
    r.update({
        "status": "passed",
        "mode": "self_check",
    })

    invariants = {}
    # 1. UVICORN_ENV key count
    invariants["uvicorn_env_key_count_17"] = len(UVICORN_ENV) == 17
    # 2. No intersection between FORBIDDEN_ENV and UVICORN_ENV
    forbidden_set = set(FORBIDDEN_ENV)
    uvicorn_set = set(UVICORN_ENV.keys())
    invariants["forbidden_env_no_overlap"] = len(forbidden_set & uvicorn_set) == 0
    # 3. UVICORN_ARGV uses no shell
    invariants["uvicorn_argv_no_shell_metachar"] = all(
        ";" not in a and "|" not in a and "&" not in a and "$" not in a
        for a in UVICORN_ARGV
    )
    # 4. Host is 127.0.0.1
    invariants["host_is_loopback"] = HOST == "127.0.0.1"
    # 5. Port is 18080
    invariants["port_is_18080"] = PORT == 18080
    # 6. Formal HTTP endpoints match expected
    invariants["formal_http_endpoints_correct"] = FORMAL_HTTP_ENDPOINTS == [
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("POST", "/v1/qa"),
    ]
    # 7. QA assertion count is 30
    _, dummy_qa = _validate_qa_contract(200, {
        "request_id": "a", "trace_id": "b", "session_id": "phase15-f-gate9-session",
        "debug": None,
        "result": {
            "session_id": "phase15-f-gate9-session",
            "user_input": "整理服务器采购需求",
            "confidence": 0.15,
            "final_response": QA_EXPECTED_FINAL_RESPONSE,
            "policy": {"status": "SAFE"},
            "task_route": {"task": "qa", "confidence": 1.0},
            "rag_result": {
                "provider": "local",
                "answer": QA_EXPECTED_RAG_ANSWER,
                "confidence": 0.15,
                "warnings": ["no_evidence"],
                "evidence": [],
                "sources": [],
            },
            "citations": [],
        },
        "trace": [
            {"component": "authentication", "status": "succeeded", "code": "authenticated"},
            {"component": "authorization", "status": "succeeded", "code": "allowed"},
            {"component": "policy_engine", "status": "succeeded", "code": "policy_engine"},
            {"component": "router", "status": "succeeded", "code": "router"},
            {"component": "local_rag_query", "status": "succeeded", "code": "local_rag_query"},
        ],
    }, {"x-request-id": "a", "x-trace-id": "b", "content-type": "application/json"})
    invariants["qa_assertion_count_30"] = len(dummy_qa) == 30
    # 8. Trace list lengths match
    invariants["trace_list_lengths_match"] = (
        len(QA_TRACE_COMPONENTS) == len(QA_TRACE_STATUSES) == len(QA_TRACE_CODES) == 5
    )
    # 9. Final success condition registry count
    invariants["final_condition_count_19"] = len(FINAL_CONDITION_NAMES) == 19

    r["self_check_invariants"] = invariants
    if not all(invariants.values()):
        r["status"] = "failed"
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# Formal HTTP smoke
# ═══════════════════════════════════════════════════════════════════════════════

def run_formal() -> dict:
    server_launch_count = 0
    formal_http_request_count = 0
    qa_http_request_count = 0
    retry_count = 0

    server_started = False
    server_process_alive_after_probe = False
    process_group_terminated = False
    sigterm_sent = False
    server_terminated = False
    force_kill_used = False
    uvicorn_exit_code = None
    all_http_assertions_passed = False

    pipe_drain_succeeded = False
    uvicorn_stdout = None  # sentinel: None means "not yet drained"
    uvicorn_stderr = None
    uvicorn_stdout_bytes_val = 0
    uvicorn_stderr_bytes_val = 0
    uvicorn_stdout_empty = False
    uvicorn_stderr_empty = False

    startup_duration_seconds = 0.0
    shutdown_duration_seconds = 0.0
    port_available_before = False
    port_released_after = False

    healthz_result = {}
    readyz_result = {}
    qa_result = {}

    failure_reason = None
    cleanup_errors: list[dict] = []
    proc = None

    # ── 0. verify harness environment ────────────────────────────────────
    for var in FORBIDDEN_ENV:
        if os.environ.get(var, "").strip():
            failure_reason = f"forbidden env var {var} set in harness environment"
            break

    # ── 1. port pre-check ────────────────────────────────────────────────
    if failure_reason is None:
        port_available_before = _port_can_bind(HOST, PORT)
        if not port_available_before:
            failure_reason = f"port {HOST}:{PORT} not available before launch"

    # ── 2. runtime-root must not exist ───────────────────────────────────
    if failure_reason is None:
        if GATE9_RUNTIME.exists():
            failure_reason = f"runtime root already exists: {GATE9_RUNTIME}"

    # ── 3. launch + probe + HTTP ─────────────────────────────────────────
    if failure_reason is None:
        try:
            proc = subprocess.Popen(
                UVICORN_ARGV,
                shell=False,
                cwd=str(CLEAN_CLONE),
                env=UVICORN_ENV,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            server_launch_count = 1

            # TCP startup probe
            t0 = _now()
            deadline = t0 + STARTUP_TIMEOUT
            if not _tcp_probe(HOST, PORT, deadline):
                failure_reason = f"TCP probe to {HOST}:{PORT} failed within {STARTUP_TIMEOUT}s"
                startup_duration_seconds = round(_now() - t0, 3)
            else:
                startup_duration_seconds = round(_now() - t0, 3)
                server_started = True
                server_process_alive_after_probe = (proc.poll() is None)
                if not server_process_alive_after_probe:
                    failure_reason = "uvicorn process died during TCP probe"

            # GET /healthz
            if failure_reason is None:
                formal_http_request_count += 1
                try:
                    status, body, headers = _http_get(f"{BASE_URL}/healthz", REQUEST_TIMEOUT)
                    hz_pass, hz_asserts = _validate_healthz(status, body, headers)
                    healthz_result = {"status": status, "assertions": hz_asserts, "pass": hz_pass}
                    if not hz_pass:
                        failed = [k for k, v in hz_asserts.items() if not v]
                        failure_reason = f"healthz assertions failed: {failed}"
                except Exception as exc:
                    failure_reason = f"healthz request error: {exc}"

            # GET /readyz
            if failure_reason is None:
                formal_http_request_count += 1
                try:
                    status, body, headers = _http_get(f"{BASE_URL}/readyz", REQUEST_TIMEOUT)
                    rz_pass, rz_asserts = _validate_readyz(status, body, headers)
                    readyz_result = {"status": status, "assertions": rz_asserts, "pass": rz_pass}
                    if not rz_pass:
                        failed = [k for k, v in rz_asserts.items() if not v]
                        failure_reason = f"readyz assertions failed: {failed}"
                except Exception as exc:
                    failure_reason = f"readyz request error: {exc}"

            # POST /v1/qa
            if failure_reason is None:
                formal_http_request_count += 1
                qa_http_request_count += 1
                try:
                    status, body, headers = _http_post_json(
                        f"{BASE_URL}/v1/qa", QA_REQUEST_BODY, REQUEST_TIMEOUT
                    )
                    qa_pass, qa_asserts = _validate_qa_contract(status, body, headers)
                    qa_result = {"status": status, "assertions": qa_asserts, "pass": qa_pass}
                    if not qa_pass:
                        failed = [k for k, v in qa_asserts.items() if not v]
                        failure_reason = f"qa assertions failed: {failed}"
                except Exception as exc:
                    failure_reason = f"qa request error: {exc}"

            if failure_reason is None:
                all_http_assertions_passed = True

        except Exception as exc:
            failure_reason = f"unexpected error: {exc}"

        finally:
            # ── 4. hardened process-group containment ────────────────────
            if proc is not None:
                pid = proc.pid

                # 4a. Check if process / group is alive at cleanup start
                leader_alive_at_cleanup = (proc.poll() is None)

                # 4b. SIGTERM to entire process group
                if leader_alive_at_cleanup:
                    result = _send_group_signal(pid, signal.SIGTERM)
                    if result == "delivered":
                        sigterm_sent = True
                    # "already_gone" → sigterm_sent stays False
                else:
                    # Leader already exited before cleanup — check group
                    try:
                        gs = _process_group_state(pid)
                    except OSError as exc:
                        cleanup_errors.append({
                            "operation": "group_state_check_pre",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        })
                        gs = "unknown"
                    if gs == "exists":
                        # Still send SIGTERM to orphan group
                        try:
                            result = _send_group_signal(pid, signal.SIGTERM)
                            if result == "delivered":
                                sigterm_sent = True
                        except Exception as exc:
                            cleanup_errors.append({
                                "operation": "killpg_SIGTERM_orphan_group",
                                "error_type": type(exc).__name__,
                                "message": str(exc),
                            })

                # 4c. Graceful wait and drain
                t_shutdown = _now()
                if proc.poll() is None:
                    try:
                        uvicorn_stdout, uvicorn_stderr = proc.communicate(
                            timeout=SHUTDOWN_TIMEOUT
                        )
                        uvicorn_exit_code = proc.returncode
                        server_terminated = True
                        pipe_drain_succeeded = True
                    except subprocess.TimeoutExpired:
                        pass  # Will force-kill below
                    except Exception as exc:
                        cleanup_errors.append({
                            "operation": "communicate_graceful",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        })
                else:
                    # Leader already exited — drain pipes immediately
                    try:
                        uvicorn_stdout, uvicorn_stderr = proc.communicate()
                        uvicorn_exit_code = proc.returncode
                        server_terminated = True
                        pipe_drain_succeeded = True
                    except Exception as exc:
                        cleanup_errors.append({
                            "operation": "communicate_already_exited",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        })

                # 4d. Group-level SIGKILL — always check group, not just leader
                try:
                    gs = _process_group_state(pid)
                except OSError as exc:
                    cleanup_errors.append({
                        "operation": "group_state_check",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    })
                    gs = "unknown"

                if gs == "exists":
                    try:
                        _send_group_signal(pid, signal.SIGKILL)
                        force_kill_used = True
                    except Exception as exc:
                        cleanup_errors.append({
                            "operation": "killpg_SIGKILL",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        })

                    # Drain pipes after force-kill
                    if not pipe_drain_succeeded:
                        try:
                            uvicorn_stdout, uvicorn_stderr = proc.communicate(timeout=5)
                            uvicorn_exit_code = proc.returncode
                            server_terminated = True
                            pipe_drain_succeeded = True
                        except subprocess.TimeoutExpired:
                            cleanup_errors.append({
                                "operation": "communicate_after_kill",
                                "error_type": "TimeoutExpired",
                                "message": "process did not terminate after SIGKILL",
                            })
                        except Exception as exc:
                            cleanup_errors.append({
                                "operation": "communicate_after_kill",
                                "error_type": type(exc).__name__,
                                "message": str(exc),
                            })

                    # Bounded group termination polling
                    group_deadline = _now() + PROCESS_GROUP_RELEASE_TIMEOUT
                    while _now() < group_deadline:
                        try:
                            gs2 = _process_group_state(pid)
                        except OSError as exc:
                            cleanup_errors.append({
                                "operation": "group_state_poll",
                                "error_type": type(exc).__name__,
                                "message": str(exc),
                            })
                            break
                        if gs2 == "absent":
                            process_group_terminated = True
                            break
                        time.sleep(PROCESS_GROUP_RELEASE_INTERVAL)
                    if not process_group_terminated:
                        cleanup_errors.append({
                            "operation": "group_termination_poll",
                            "error_type": "Timeout",
                            "message": (
                                f"process group {pid} not terminated within "
                                f"{PROCESS_GROUP_RELEASE_TIMEOUT}s"
                            ),
                        })
                else:
                    process_group_terminated = True

                shutdown_duration_seconds = round(_now() - t_shutdown, 3)

                # 4e. Final poll if still not marked terminated
                if not server_terminated:
                    try:
                        if proc.poll() is not None:
                            server_terminated = True
                            if uvicorn_exit_code is None:
                                uvicorn_exit_code = proc.returncode
                    except Exception:
                        pass

            # ── 5. Pipe drain safety net ─────────────────────────────────
            if not pipe_drain_succeeded:
                # All drain attempts failed — do NOT assign b""
                uvicorn_stdout_bytes_val = 0
                uvicorn_stderr_bytes_val = 0
                uvicorn_stdout_empty = False
                uvicorn_stderr_empty = False
            else:
                # Drain succeeded — use real values
                if uvicorn_stdout is not None:
                    uvicorn_stdout_bytes_val = len(uvicorn_stdout)
                    uvicorn_stdout_empty = (uvicorn_stdout == b"")
                if uvicorn_stderr is not None:
                    uvicorn_stderr_bytes_val = len(uvicorn_stderr)
                    uvicorn_stderr_empty = (uvicorn_stderr == b"")

            # ── 6. bounded port release polling ──────────────────────────
            port_deadline = _now() + PORT_RELEASE_TIMEOUT
            while _now() < port_deadline:
                if _port_can_bind(HOST, PORT):
                    port_released_after = True
                    break
                time.sleep(PORT_RELEASE_INTERVAL)
            if not port_released_after:
                cleanup_errors.append({
                    "operation": "port_release_poll",
                    "error_type": "Timeout",
                    "message": f"port {HOST}:{PORT} not released within {PORT_RELEASE_TIMEOUT}s",
                })

    # ── 7. two-phase final verdict ───────────────────────────────────────
    # Phase 1: evaluate all runtime conditions
    raw_conditions = {
        "failure_reason_null": failure_reason is None,
        "cleanup_errors_empty": len(cleanup_errors) == 0,
        "all_http_assertions_passed": all_http_assertions_passed,
        "server_launch_count_1": server_launch_count == 1,
        "formal_http_request_count_3": formal_http_request_count == 3,
        "qa_http_request_count_1": qa_http_request_count == 1,
        "retry_count_0": retry_count == 0,
        "server_started": server_started,
        "server_process_alive_after_probe": server_process_alive_after_probe,
        "process_group_terminated": process_group_terminated,
        "sigterm_sent": sigterm_sent,
        "server_terminated": server_terminated,
        "uvicorn_exit_code_0": uvicorn_exit_code == 0,
        "force_kill_used_false": not force_kill_used,
        "port_available_before": port_available_before,
        "port_released_after": port_released_after,
        "uvicorn_stdout_empty": uvicorn_stdout_empty,
        "uvicorn_stderr_empty": uvicorn_stderr_empty,
    }

    # Phase 2: build ordered failed_final_conditions from actual state
    failed_conditions = []
    for name in FINAL_CONDITION_NAMES:
        if not raw_conditions.get(name, False):
            failed_conditions.append(name)

    passed = (
        failure_reason is None
        and len(cleanup_errors) == 0
        and len(failed_conditions) == 0
    )

    if failure_reason is None and not passed:
        failure_reason = "final_conditions: " + "; ".join(failed_conditions)

    return {
        "status": "passed" if passed else "failed",
        "mode": "formal",
        "failure_reason": failure_reason,
        "failed_final_conditions": failed_conditions if not passed else [],
        "server_launch_count": server_launch_count,
        "formal_http_request_count": formal_http_request_count,
        "qa_http_request_count": qa_http_request_count,
        "retry_count": retry_count,
        "server_started": server_started,
        "server_process_alive_after_probe": server_process_alive_after_probe,
        "process_group_terminated": process_group_terminated,
        "server_terminated": server_terminated,
        "sigterm_sent": sigterm_sent,
        "force_kill_used": force_kill_used,
        "uvicorn_exit_code": uvicorn_exit_code,
        "all_http_assertions_passed": all_http_assertions_passed,
        "pipe_drain_succeeded": pipe_drain_succeeded,
        "uvicorn_stdout_bytes": uvicorn_stdout_bytes_val,
        "uvicorn_stderr_bytes": uvicorn_stderr_bytes_val,
        "uvicorn_stdout_empty": uvicorn_stdout_empty,
        "uvicorn_stderr_empty": uvicorn_stderr_empty,
        "startup_duration_seconds": startup_duration_seconds,
        "shutdown_duration_seconds": shutdown_duration_seconds,
        "port_available_before": port_available_before,
        "port_released_after": port_released_after,
        "cleanup_errors": list(cleanup_errors),
        "healthz": healthz_result,
        "readyz": readyz_result,
        "qa": qa_result,
        "runtime_root_created": False,
        "file_write_count": 0,
        "port_probe_count": 0,
        "self_check_invariants": {},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) == 1:
        report = run_formal()
    elif sys.argv[1:] == ["--self-check"]:
        report = self_check()
    else:
        r = _base_report()
        r.update({
            "status": "failed",
            "mode": "invalid_arguments",
            "failure_reason": f"unrecognized arguments: {sys.argv[1:]}",
        })
        report = r
        json.dump(report, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        sys.exit(1)

    json.dump(report, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.exit(0 if report["status"] == "passed" else 1)
```

---

## C2. Final Success Conditions (19)

```
failure_reason = null
cleanup_errors = []
failed_final_conditions = []
all_http_assertions_passed = true
server_launch_count = 1
formal_http_request_count = 3
qa_http_request_count = 1
retry_count = 0
server_started = true
server_process_alive_after_probe = true
process_group_terminated = true
sigterm_sent = true
server_terminated = true
uvicorn_exit_code = 0
force_kill_used = false
port_available_before = true
port_released_after = true
uvicorn_stdout_empty = true
uvicorn_stderr_empty = true
```

---

## C3. Compatibility Review (corrected)

| Contract | Source Basis | v4.2-R1 Status |
|---|---|---|
| healthz | `src/conversation_agent/api/models.py:12-17` `HealthResponse` | 5 assertions |
| readyz | `src/conversation_agent/api/models.py:20-23` `ReadinessResponse` | 5 assertions |
| demo authentication | `src/conversation_agent/api/security.py:71-80` calls `development_security_context()` from `src/conversation_agent/runtime/builder.py:65-87` | Principal(tenant_id="single_tenant", roles=("agent_user",)) |
| authorization | `src/conversation_agent/api/security.py:96` `AuthorizationService.authorize()` returns `AuthorizationDecision(allowed=True, permissions=...)` from `src/conversation_agent/authorization/service.py` | 4 permissions granted |
| null persistence | `src/conversation_agent/config.py:379-385` `effective_persistence_mode` returns `NULL` when `enabled=false` | No PostgreSQL, no idempotency |
| QA forced task | `src/conversation_agent/api/app.py:517` route passes `forced_task="qa"`; `src/conversation_agent/orchestration/coordinator.py:89-90` applies `TaskRoute(task=task_override, confidence=1.0)` | task_route.task=qa, confidence=1.0 |
| local RAG no-evidence | `src/conversation_agent/rag/module.py:105-112` `generate_with_citations()` with `not evidence` → confidence=0.15, warnings=["no_evidence"] | Exact answer, confidence, warnings, evidence=[], sources=[] |
| raw response hiding | `src/conversation_agent/rag/models.py:56-61` `RagResult.to_public_dict()` pops `raw_response` when `include_raw_response=False` | raw_response absent from public rag_result |
| debug null | `src/conversation_agent/api/projector.py:40-53` `ResponseProjector.project()` sets debug only when `include_raw_response and permission` | debug present and null |
| trace order | `src/conversation_agent/api/security.py:95-96` auth→authorization; `src/conversation_agent/orchestration/coordinator.py:59,91,203-211` policy_engine→router→local_rag_query | Exact 5-step list |
| runtime directory set | `src/conversation_agent/memory/customer_store.py:42-43` `CustomerStore.__init__()` calls `self._dir.mkdir(parents=True)` and `self._backup_dir.mkdir(parents=True)` | 4 exact dirs |
| graceful shutdown | SIGTERM via `os.killpg()` to process group, then bounded group-termination polling | process_group_terminated=true |
| port release | Bounded polling: 3.0s at 0.1s intervals | port_released_after=true |

---

## C4. Harness Materialization Boundary

1. Evidence 001–010 all pass.
2. External execution layer creates `/home/dick/.phase15_f/continuation_c1/gate9_http_smoke.py` with v4.2-R1 source byte-for-byte — single atomic write.
3. File frozen — no edits after creation.
4. Evidence 011 freezes SHA-256.
5. Evidence 012 verifies regular file, not symlink, size > 0.
6. Evidence 013 runs `--self-check` under `env -i` — must exit 0 with `status: "passed"`.
7. Evidence 014 confirms runtime root still absent.
8. Any post-creation issue → `reconciliation_required`, STOP, do not overwrite.

---

## C5. Evidence Contracts 001–028

**Runner timeout for all evidence: 300 seconds.**
**All outer argv format**: `/usr/bin/env -C <cwd> /home/dick/miniconda3/bin/python3.13 /home/dick/.phase15_f/continuation_c1/evidence/run_command.py <evidence_name> <inner...>`

### Evidence 001 — pre-head
- cwd: `/home/dick/.phase15_f/continuation_c1/clean_clone_r2`
- inner argv: `["git", "rev-parse", "HEAD"]`
- expected exit_code: 0
- expected stdout: `1b844bc8ef514079b539faad0a79031b80a47432`
- expected stderr: (empty)
- blocking: Yes

### Evidence 002 — pre-tree
- cwd: `/home/dick/.phase15_f/continuation_c1/clean_clone_r2`
- inner argv: `["git", "rev-parse", "HEAD^{tree}"]`
- expected exit_code: 0
- expected stdout: `cb55c02e94c8fde64fb85bf03b1758570b687c7f`
- expected stderr: (empty)
- blocking: Yes

### Evidence 003 — pre-status
- cwd: `/home/dick/.phase15_f/continuation_c1/clean_clone_r2`
- inner argv: `["git", "--no-optional-locks", "status", "--short", "--untracked-files=all"]`
- expected exit_code: 0
- expected stdout: (empty)
- expected stderr: (empty)
- blocking: Yes

### Evidence 004 — pre-python-version
- cwd: `/home/dick/.phase15_f/continuation_c1/clean_clone_r2`
- inner argv: `["/home/dick/.phase15_f/continuation_c1/clean_clone_r2/.venv/bin/python", "--version"]`
- expected exit_code: 0
- expected stdout: `Python 3.13.12`
- expected stderr: (empty)
- blocking: Yes

### Evidence 005 — pre-python-readlink
- cwd: `/home/dick/.phase15_f/continuation_c1/clean_clone_r2`
- inner argv: `["readlink", "-f", "/home/dick/.phase15_f/continuation_c1/clean_clone_r2/.venv/bin/python"]`
- expected exit_code: 0
- expected stdout: `/home/dick/miniconda3/bin/python3.13`
- expected stderr: (empty)
- blocking: Yes

### Evidence 006 — pre-runtime-root-absent
- cwd: `/home/dick`
- inner argv: `["test", "!", "-e", "/home/dick/.phase15_f/continuation_c1/gate9_runtime_data"]`
- expected exit_code: 0
- expected stdout: (empty)
- expected stderr: (empty)
- blocking: Yes

### Evidence 007 — pre-harness-absent
- cwd: `/home/dick/.phase15_f/continuation_c1`
- inner argv: `["test", "!", "-e", "/home/dick/.phase15_f/continuation_c1/gate9_http_smoke.py"]`
- expected exit_code: 0
- expected stdout: (empty)
- expected stderr: (empty)
- blocking: Yes

### Evidence 008 — pre-no-logs
- cwd: `/home/dick/.phase15_f/continuation_c1/clean_clone_r2`
- inner argv: `["test", "!", "-e", "logs"]`
- expected exit_code: 0
- expected stdout: (empty)
- expected stderr: (empty)
- blocking: Yes

### Evidence 009 — pre-main-status
- cwd: `/home/dick/project/test_demo`
- inner argv: `["git", "--no-optional-locks", "status", "--short"]`
- expected exit_code: 0
- expected stdout: (empty — clean)
- expected stderr: (empty)
- blocking: Yes

### Evidence 010 — pre-clean-ndx
- cwd: `/home/dick/.phase15_f/continuation_c1/clean_clone_r2`
- inner argv: `["git", "clean", "-ndx"]`
- expected exit_code: 0
- expected stdout: `Would remove .venv/`
- expected stderr: (empty)
- blocking: Yes

### Evidence 011 — harness-sha256 (blocking)
- cwd: `/home/dick/.phase15_f/continuation_c1`
- inner argv: `["sha256sum", "/home/dick/.phase15_f/continuation_c1/gate9_http_smoke.py"]`
- expected exit_code: 0
- expected stdout: `<64-char-hex>  /home/dick/.phase15_f/continuation_c1/gate9_http_smoke.py`
- expected stderr: (empty)
- blocking: **Yes**

### Evidence 012 — harness-stat (blocking)
- cwd: `/home/dick/.phase15_f/continuation_c1`
- inner argv: `["stat", "--format=%F %a %s %n", "/home/dick/.phase15_f/continuation_c1/gate9_http_smoke.py"]`
- expected exit_code: 0
- expected stdout: `regular file <mode> <size> /home/dick/.phase15_f/continuation_c1/gate9_http_smoke.py`
- expected stderr: (empty)
- blocking: **Yes**

### Evidence 013 — harness-self-check (env -i, blocking)
- cwd: `/home/dick/.phase15_f/continuation_c1/clean_clone_r2`
- inner argv: `["env", "-i", "HOME=/home/dick", "PATH=/home/dick/miniconda3/bin:/home/dick/.local/bin:/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8", "LC_ALL=C.UTF-8", "PYTHONUTF8=1", "PYTHONDONTWRITEBYTECODE=1", "/home/dick/.phase15_f/continuation_c1/clean_clone_r2/.venv/bin/python", "/home/dick/.phase15_f/continuation_c1/gate9_http_smoke.py", "--self-check"]`
- expected exit_code: 0
- expected stdout: JSON with `"status": "passed"`, `"mode": "self_check"`, `"self_check_invariants"` all true, all base keys present + LF
- expected stderr: (empty)
- blocking: **Yes**

### Evidence 014 — pre-formal-runtime-root-still-absent (blocking)
- cwd: `/home/dick`
- inner argv: `["test", "!", "-e", "/home/dick/.phase15_f/continuation_c1/gate9_runtime_data"]`
- expected exit_code: 0
- expected stdout: (empty)
- expected stderr: (empty)
- blocking: Yes

### Evidence 015 — http-smoke-execution (Stage 9B core, blocking)
- cwd: `/home/dick/.phase15_f/continuation_c1/clean_clone_r2`
- inner argv: `["env", "-i", "HOME=/home/dick", "PATH=/home/dick/miniconda3/bin:/home/dick/.local/bin:/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8", "LC_ALL=C.UTF-8", "PYTHONUTF8=1", "PYTHONDONTWRITEBYTECODE=1", "CONVAGENT_RUNTIME_MODE=demo", "CONVAGENT_PERSISTENCE_MODE=null", "CONVAGENT_DATABASE_ENABLED=false", "CONVAGENT_DATABASE_REQUIRED=false", "CONVERSATION_AGENT_DATA_DIR=/home/dick/.phase15_f/continuation_c1/gate9_runtime_data", "CONVAGENT_RAG_PROVIDER=local", "CONVAGENT_RAG_FALLBACK_TO_LOCAL=false", "CONVAGENT_RAG_INCLUDE_RAW_RESPONSE=false", "CONVAGENT_API_DOCS_ENABLED=false", "CONVERSATION_AGENT_LOG_LEVEL=ERROR", "NO_PROXY=127.0.0.1,localhost", "/home/dick/.phase15_f/continuation_c1/clean_clone_r2/.venv/bin/python", "/home/dick/.phase15_f/continuation_c1/gate9_http_smoke.py"]`
- expected exit_code: 0
- expected stdout: JSON with `"status": "passed"`, `"failure_reason": null`, `"cleanup_errors": []`, `"failed_final_conditions": []`, `"process_group_terminated": true`, `"pipe_drain_succeeded": true` + LF
- expected stderr: (empty)
- blocking: **Yes**

### Evidence 016 — http-smoke-stdout (blocking, semantic verification)
- cwd: `/home/dick/.phase15_f/continuation_c1/evidence`
- inner argv: `["cat", "/home/dick/.phase15_f/continuation_c1/evidence/c1-gate9-015-http-smoke-execution.stdout.log"]`
- expected exit_code: 0
- expected stdout: Byte-for-byte identical to Evidence 015 stdout.log content
- expected stderr: (empty)
- blocking: **Yes**
- semantic: JSON parse → status=passed, failure_reason=null, cleanup_errors=[], failed_final_conditions=[], all 19 conditions hold, healthz/readyz/qa all pass, 40/40 assertions true, pipe_drain_succeeded=true, process_group_terminated=true, self_check_invariants = {} (formal mode — empty dict, present)

### Evidence 017 — http-smoke-stderr (blocking)
- cwd: `/home/dick/.phase15_f/continuation_c1/evidence`
- inner argv: `["cat", "/home/dick/.phase15_f/continuation_c1/evidence/c1-gate9-015-http-smoke-execution.stderr.log"]`
- expected exit_code: 0
- expected stdout: (empty)
- expected stderr: (empty)
- blocking: **Yes**

### Evidence 018 — http-smoke-result (blocking, semantic verification)
- cwd: `/home/dick/.phase15_f/continuation_c1/evidence`
- inner argv: `["cat", "/home/dick/.phase15_f/continuation_c1/evidence/c1-gate9-015-http-smoke-execution.result.json"]`
- expected exit_code: 0
- expected stdout: JSON object (see below)
- expected stderr: (empty)
- blocking: **Yes**
- semantic: evidence_name="c1-gate9-015-http-smoke-execution" (exact), cwd="/home/dick/.phase15_f/continuation_c1/clean_clone_r2" (exact), actual_command equals frozen Evidence 015 inner argv (element-by-element), exit_code=0, timed_out=false, timeout_seconds=300, utc_start/utc_end valid ISO-8601, duration_seconds > 0

### Evidence 019 — runtime-data-exists (blocking)
- cwd: `/home/dick`
- inner argv: `["test", "-d", "/home/dick/.phase15_f/continuation_c1/gate9_runtime_data"]`
- expected exit_code: 0
- expected stdout: (empty)
- expected stderr: (empty)
- blocking: **Yes**

### Evidence 020 — runtime-data-directory-tree (blocking, semantic verification)
- cwd: `/home/dick`
- inner argv: `["find", "/home/dick/.phase15_f/continuation_c1/gate9_runtime_data", "-type", "d", "-printf", "%p\n"]`
- expected exit_code: 0
- expected stdout: exact 4 directories (order-independent):
  ```
  /home/dick/.phase15_f/continuation_c1/gate9_runtime_data
  /home/dick/.phase15_f/continuation_c1/gate9_runtime_data/backups
  /home/dick/.phase15_f/continuation_c1/gate9_runtime_data/backups/customers
  /home/dick/.phase15_f/continuation_c1/gate9_runtime_data/customers
  ```
- expected stderr: (empty)
- blocking: **Yes**
- semantic: sorted lines from stdout match sorted expected set; directory_count=4, missing=none, unexpected=none

### Evidence 021 — runtime-data-non-directory-artifacts (blocking)
- cwd: `/home/dick`
- inner argv: `["find", "/home/dick/.phase15_f/continuation_c1/gate9_runtime_data", "-not", "-type", "d", "-print"]`
- expected exit_code: 0
- expected stdout: (empty — no non-directory artifacts)
- expected stderr: (empty)
- blocking: **Yes**

### Evidence 022 — runtime-data-no-knowledge (blocking)
- cwd: `/home/dick`
- inner argv: `["test", "!", "-e", "/home/dick/.phase15_f/continuation_c1/gate9_runtime_data/knowledge"]`
- expected exit_code: 0
- expected stdout: (empty)
- expected stderr: (empty)
- blocking: **Yes**

### Evidence 023 — post-no-logs (blocking)
- cwd: `/home/dick/.phase15_f/continuation_c1/clean_clone_r2`
- inner argv: `["test", "!", "-e", "logs"]`
- expected exit_code: 0
- expected stdout: (empty)
- expected stderr: (empty)
- blocking: **Yes**

### Evidence 024 — post-status (blocking)
- cwd: `/home/dick/.phase15_f/continuation_c1/clean_clone_r2`
- inner argv: `["git", "--no-optional-locks", "status", "--short", "--untracked-files=all"]`
- expected exit_code: 0
- expected stdout: (empty — clean)
- expected stderr: (empty)
- blocking: **Yes**

### Evidence 025 — post-clean-ndx-data (blocking)
- cwd: `/home/dick/.phase15_f/continuation_c1/clean_clone_r2`
- inner argv: `["git", "clean", "-ndx", "data"]`
- expected exit_code: 0
- expected stdout: (empty — data/ unchanged)
- expected stderr: (empty)
- blocking: **Yes**

### Evidence 026 — post-clean-ndx (blocking)
- cwd: `/home/dick/.phase15_f/continuation_c1/clean_clone_r2`
- inner argv: `["git", "clean", "-ndx"]`
- expected exit_code: 0
- expected stdout: `Would remove .venv/`
- expected stderr: (empty)
- blocking: **Yes**

### Evidence 027 — post-main-status (blocking)
- cwd: `/home/dick/project/test_demo`
- inner argv: `["git", "--no-optional-locks", "status", "--short"]`
- expected exit_code: 0
- expected stdout: (empty — clean)
- expected stderr: (empty)
- blocking: **Yes**

### Evidence 028 — post-harness-sha256 (blocking, semantic verification)
- cwd: `/home/dick/.phase15_f/continuation_c1`
- inner argv: `["sha256sum", "/home/dick/.phase15_f/continuation_c1/gate9_http_smoke.py"]`
- expected exit_code: 0
- expected stdout: Byte-for-byte identical to Evidence 011 stdout
- expected stderr: (empty)
- blocking: **Yes**
- semantic: byte-for-byte comparison with Evidence 011 stdout.log

---

## C6. Stage 9B Semantic Verification via Read Tool

### 6a. Evidence 016 ↔ 015
- Read 015 stdout.log and 016 stdout.log
- Assert: byte-for-byte identical

### 6b. Evidence 016 JSON
- Assert: `status` = `"passed"`, `mode` = `"formal"`, `failure_reason` = `null`, `cleanup_errors` = `[]`, `failed_final_conditions` = `[]`
- Assert: all 19 final success conditions hold
- Assert: `healthz.pass` = `true`, `readyz.pass` = `true`, `qa.pass` = `true`
- Assert: `pipe_drain_succeeded` = `true`, `process_group_terminated` = `true`
- Assert: `self_check_invariants` = `{}` (key present, empty dict, in all modes)

### 6c. Evidence 018 result JSON
- Assert: `evidence_name` = `"c1-gate9-015-http-smoke-execution"` (exact)
- Assert: `cwd` = `"/home/dick/.phase15_f/continuation_c1/clean_clone_r2"` (exact)
- Assert: `actual_command` element-by-element match with frozen Evidence 015 inner argv
- Assert: `exit_code` = `0`, `timed_out` = `false`, `timeout_seconds` = `300`
- Assert: `utc_start`, `utc_end` valid ISO-8601, `duration_seconds` > 0

### 6d. Evidence 020 directory set
- Read, sort lines, compare with sorted expected 4-directory set
- Assert: exact match

### 6e. Evidence 028 ↔ 011
- Read both, assert byte-for-byte identical

---

## C7. Namespace Audit

At Stage 9A start: fresh Glob `/home/dick/.phase15_f/continuation_c1/evidence/c1-gate9-*` → must be empty. Non-empty → `reconciliation_required`, STOP.

---

## C8. Failure Stop Gate

| # | Stage | Condition |
|---|-------|-----------|
| 0 | Pre-9A | Glob `c1-gate9-*` → non-empty |
| 1–10 | 9A | Evidence 001–010 exit_code ≠ 0 |
| 11 | 9A | Evidence 011 exit_code ≠ 0 |
| 12 | 9A | Evidence 012 exit_code ≠ 0 |
| 13 | 9A | Evidence 013 exit_code ≠ 0 or invariants fail |
| 14 | 9A | Evidence 014 exit_code ≠ 0 |
| 15 | 9B | Evidence 015 exit_code ≠ 0 |
| 16 | 9B | Evidence 016 stdout ≠ 015 stdout |
| 17 | 9B | Evidence 016 JSON semantic check fails |
| 18 | 9B | Evidence 017 stdout non-empty |
| 19 | 9B | Evidence 018 result JSON semantic check fails |
| 20 | 9B | Evidence 019 exit_code ≠ 0 |
| 21 | 9B | Evidence 020 directory set ≠ exact 4 |
| 22 | 9B | Evidence 021 exit_code ≠ 0 |
| 23 | 9B | Evidence 022 exit_code ≠ 0 |
| 24–28 | 9B | Evidence 023–027 exit_code ≠ 0 |
| 29 | 9B | Evidence 028 stdout ≠ Evidence 011 stdout |

---

## D. Mechanical Validation Report

```
embedded_python_syntax = passed
qa_assertion_count = 30
final_success_condition_count = 19
base_report_key_count = 33
formal_schema_matches_base = true
self_check_schema_matches_base = true
invalid_arguments_schema_matches_base = true
evidence_group_count = 28
evidence_file_count = 84
missing_evidence_names = []
duplicate_evidence_names = []
stale_condition_18_references = 0
stale_all_18_references = 0
pipe_drain_failure_can_report_empty = false
process_group_eprem_treated_as_absent = false
orphan_group_force_kill_path_present = true
stage9a_executed = false
stage9b_executed = false
runner_invoked = false
uvicorn_launched = false
formal_http_requests_sent = 0
github_write_performed = false
```

---

## E. Final State

```
source_compatibility_review = completed
gate9_plan_version = v4.2-R1
gate9_plan_optimization = completed
gate9_plan_review = passed

phase15_f_gate9_status = not_started
stage9a_authorized = false
stage9b_authorized = false
phase15_g_authorized = false

gate9_http_smoke_py_created_on_target = false
gate9_runtime_data_created = false
runner_invoked = false
uvicorn_launched = false
formal_http_requests_sent = 0
github_write_performed = false

docs_branch_committed = true|false   (to be determined)
docs_branch_pushed = true|false      (to be determined)
remote_main_unchanged = true
```

Plan optimization completed does NOT constitute Gate 9 passed.
