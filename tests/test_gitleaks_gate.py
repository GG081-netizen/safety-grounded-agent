from pathlib import Path
import re
import subprocess
import sys
import tomllib

import pytest
from pydantic import ValidationError

from conversation_agent.evaluation.phase14_evidence import (
    GitleaksRepositoryScanResult,
)

pytestmark = pytest.mark.unit


def test_gitleaks_policy_extends_defaults_and_has_dashscope_rule():
    text = Path(".gitleaks.toml").read_text(encoding="utf-8")
    assert "useDefault = true" in text
    assert 'id = "convagent-dashscope-api-key"' in text


def _dashscope_pattern() -> re.Pattern[str]:
    config = tomllib.loads(Path(".gitleaks.toml").read_text(encoding="utf-8"))
    rule = next(
        item for item in config["rules"]
        if item["id"] == "convagent-dashscope-api-key"
    )
    return re.compile(rule["regex"])


def test_dashscope_rule_detects_same_line_horizontal_whitespace():
    token = "".join(("test", "Dash", "Scope", "Credential", "Value", "12345678"))
    match = _dashscope_pattern().search(f"DASHSCOPE_API_KEY \t= \t{token}")
    assert match is not None
    assert match.group(1) == token


@pytest.mark.parametrize("line_break", ["\n", "\r\n"])
def test_dashscope_rule_does_not_cross_empty_value_line(line_break: str):
    text = (
        "CONVAGENT_DASHSCOPE_API_KEY="
        + line_break
        + "CONVAGENT_DASHSCOPE_BASE_URL=https://example.invalid"
    )
    assert _dashscope_pattern().search(text) is None


def test_functional_gate_orders_canaries_before_real_scan():
    text = Path("scripts/run_gitleaks_functional_gate.py").read_text(encoding="utf-8")
    canary = text.index('summary["gitleaks_builtin_canary_detected"]')
    real_scan = text.index('real_report = args.summary.with_suffix')
    assert canary < real_scan


def test_failed_dual_canary_does_not_scan_real_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import scripts.run_gitleaks_functional_gate as gate

    repository = tmp_path / "repository"
    repository.mkdir()
    summary = tmp_path / "summary.json"
    real_scan_calls = 0

    def fake_run(command, *, cwd, expected=(0,)):
        nonlocal real_scan_calls
        if "detect" in command:
            report_path = Path(command[command.index("--report-path") + 1])
            if Path(cwd) == repository:
                real_scan_calls += 1
            report_path.write_text(
                '[{"RuleID":"convagent-dashscope-api-key"}]',
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 1, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(gate, "_run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_gitleaks_functional_gate.py",
            "--binary",
            "gitleaks",
            "--repository",
            str(repository),
            "--config",
            str(Path(".gitleaks.toml").resolve()),
            "--summary",
            str(summary),
            "--temporary-directory",
            str(tmp_path / "scanner-temp"),
            "--subject-commit-sha",
            "a" * 40,
            "--checksum-valid",
            "--version-valid",
        ],
    )

    assert gate.main() == 2
    assert real_scan_calls == 0


@pytest.mark.parametrize(
    ("return_code", "status", "findings", "passed"),
    [(0, "pass", 0, True), (1, "fail", 1, False)],
)
def test_gitleaks_repository_scan_result_accepts_consistent_states(
    return_code: int, status: str, findings: int, passed: bool
):
    result = GitleaksRepositoryScanResult(
        gitleaks_process_return_code=return_code,
        gitleaks_all_refs_scan_status=status,
        gitleaks_all_refs_findings=findings,
        gitleaks_real_repository_scan_passed=passed,
    )
    assert result.gitleaks_real_repository_scan_passed is passed


@pytest.mark.parametrize(
    ("return_code", "status", "findings", "passed"),
    [
        (0, "pass", 0, False),
        (0, "fail", 0, True),
        (0, "pass", 1, True),
        (1, "pass", 0, True),
    ],
)
def test_gitleaks_repository_scan_result_rejects_contradictions(
    return_code: int, status: str, findings: int, passed: bool
):
    with pytest.raises(
        ValidationError, match="gitleaks_repository_scan_result_inconsistent"
    ):
        GitleaksRepositoryScanResult(
            gitleaks_process_return_code=return_code,
            gitleaks_all_refs_scan_status=status,
            gitleaks_all_refs_findings=findings,
            gitleaks_real_repository_scan_passed=passed,
        )
