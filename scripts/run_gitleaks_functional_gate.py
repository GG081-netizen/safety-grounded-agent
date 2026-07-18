"""Verify Gitleaks with two positive controls before scanning real refs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from conversation_agent.evaluation.phase14_evidence import (
    GitleaksRepositoryScanResult,
    create_evidence_envelope,
)


def _run(command: list[str], *, cwd: Path, expected: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if process.returncode not in expected:
        raise RuntimeError(f"scanner_command_failed:{command[1]}:{process.returncode}")
    return process


def _write_canaries(repository: Path) -> None:
    builtin = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    dashscope = "ds_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4"
    (repository / "positive-control.txt").write_text(
        f"GITHUB_TOKEN={builtin}\nDASHSCOPE_API_KEY={dashscope}\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--github-trust", type=Path)
    parser.add_argument("--subject-commit-sha", required=True)
    parser.add_argument("--checksum-valid", action="store_true")
    parser.add_argument("--version-valid", action="store_true")
    args = parser.parse_args()
    trust = None
    if args.github_trust is not None and args.github_trust.is_file():
        trust = json.loads(args.github_trust.read_text(encoding="utf-8"))
        if not isinstance(trust, dict) or trust.get("current_job_runtime_state_verified") is not True:
            raise RuntimeError("producer_runtime_identity_unverified")

    summary: dict[str, object] = {
        "gitleaks_version": "8.30.1",
        "scan_scope": "all_refs",
        "gitleaks_checksum_valid": args.checksum_valid,
        "gitleaks_version_valid": args.version_valid,
        "gitleaks_builtin_canary_detected": False,
        "gitleaks_custom_canary_detected": False,
        "gitleaks_real_repository_scan_passed": False,
    }
    with tempfile.TemporaryDirectory(prefix="convagent-gitleaks-") as directory:
        control = Path(directory)
        _run(["git", "init", "-q"], cwd=control)
        _run(["git", "config", "user.email", "phase14@example.invalid"], cwd=control)
        _run(["git", "config", "user.name", "Phase14 Gate"], cwd=control)
        _write_canaries(control)
        _run(["git", "add", "positive-control.txt"], cwd=control)
        _run(["git", "commit", "-q", "-m", "positive control"], cwd=control)
        report = control / "canary-report.json"
        process = _run(
            [
                args.binary,
                "detect",
                "--source",
                str(control),
                "--config",
                str(args.config.resolve()),
                "--report-format",
                "json",
                "--report-path",
                str(report),
                "--no-banner",
                "--log-opts=--all",
            ],
            cwd=control,
            expected=(1,),
        )
        del process
        findings = json.loads(report.read_text(encoding="utf-8"))
        rule_ids = {finding.get("RuleID") for finding in findings}
        summary["gitleaks_builtin_canary_detected"] = any(rule_id != "convagent-dashscope-api-key" for rule_id in rule_ids)
        summary["gitleaks_custom_canary_detected"] = "convagent-dashscope-api-key" in rule_ids

    if summary["gitleaks_builtin_canary_detected"] and summary["gitleaks_custom_canary_detected"]:
        real_report = args.summary.with_suffix(".findings.json")
        real = _run(
            [
                args.binary,
                "detect",
                "--source",
                str(args.repository.resolve()),
                "--config",
                str(args.config.resolve()),
                "--report-format",
                "json",
                "--report-path",
                str(real_report),
                "--no-banner",
            ],
            cwd=args.repository,
            expected=(0, 1),
        )
        findings = json.loads(real_report.read_text(encoding="utf-8"))
        if not isinstance(findings, list):
            raise RuntimeError("gitleaks_findings_report_invalid")
        finding_count = len(findings)
        scan_status = (
            "pass" if real.returncode == 0 and finding_count == 0 else "fail"
        )
        scan_result = GitleaksRepositoryScanResult(
            gitleaks_process_return_code=real.returncode,
            gitleaks_all_refs_scan_status=scan_status,
            gitleaks_all_refs_findings=finding_count,
            gitleaks_real_repository_scan_passed=(
                real.returncode == 0
                and scan_status == "pass"
                and finding_count == 0
            ),
        )
        summary.update(scan_result.model_dump())
        real_report.unlink(missing_ok=True)

    required = (
        summary["gitleaks_checksum_valid"] is True,
        summary["gitleaks_version_valid"] is True,
        summary["gitleaks_builtin_canary_detected"] is True,
        summary["gitleaks_custom_canary_detected"] is True,
        summary.get("gitleaks_real_repository_scan_passed") is True,
        summary.get("gitleaks_all_refs_scan_status") == "pass",
        summary.get("gitleaks_all_refs_findings") == 0,
    )
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    if trust is None:
        args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    else:
        envelope = create_evidence_envelope(
            report_type="gitleaks-report",
            subject_commit_sha=args.subject_commit_sha,
            repository_id=os.environ["GITHUB_REPOSITORY_ID"],
            workflow_run_id=os.environ["GITHUB_RUN_ID"],
            workflow_run_attempt=os.environ["GITHUB_RUN_ATTEMPT"],
            producer_job_name="secret-scan",
            producer_workflow_job_id=trust.get("current_workflow_job_id"),
            producer_check_run_id=trust.get("current_check_run_id"),
            generated_at=datetime.now(timezone.utc),
            payload=summary,
        )
        args.summary.write_text(envelope.model_dump_json(indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(args.repository / ".gitleaks-tmp", ignore_errors=True)
    return 0 if all(required) else 2


if __name__ == "__main__":
    raise SystemExit(main())
