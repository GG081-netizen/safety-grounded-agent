import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conversation_agent.evaluation.phase14_evidence import (
    EvidenceEnvelope,
    RuntimeAttestationV2,
    create_evidence_envelope,
)


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
JOB_IDS = {
    "test": ("1", "11"),
    "secret-scan": ("2", "12"),
    "postgres-integration": ("3", "13"),
    "operational-postgres": ("4", "14"),
    "incident-closure": ("5", "15"),
    "formal-closeout": ("6", "16"),
}


def load_closeout_module():
    path = ROOT / "scripts" / "create_phase14_closeout.py"
    spec = importlib.util.spec_from_file_location("phase14_closeout_script", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def set_context(monkeypatch):
    values = {
        "GITHUB_SHA": "a" * 40,
        "GITHUB_REPOSITORY_ID": "101",
        "GITHUB_RUN_ID": "202",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REF": "refs/heads/main",
        "CONVAGENT_PHASE14_ENVIRONMENT_NAME": "phase14-incident-closure",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def write_envelope(path, *, report_type, producer, payload):
    job_id, check_id = JOB_IDS[producer]
    envelope = create_evidence_envelope(
        report_type=report_type,
        subject_commit_sha="a" * 40,
        repository_id="101",
        workflow_run_id="202",
        workflow_run_attempt="1",
        producer_job_name=producer,
        producer_workflow_job_id=job_id,
        producer_check_run_id=check_id,
        generated_at=datetime.now(timezone.utc),
        payload=payload,
    )
    path.write_text(envelope.model_dump_json(indent=2), encoding="utf-8")
    return path


def gitleaks_payload(**overrides):
    payload = {
        "gitleaks_checksum_valid": True,
        "gitleaks_version_valid": True,
        "gitleaks_builtin_canary_detected": True,
        "gitleaks_custom_canary_detected": True,
        "gitleaks_process_return_code": 0,
        "gitleaks_all_refs_scan_status": "pass",
        "gitleaks_all_refs_findings": 0,
        "gitleaks_real_repository_scan_passed": True,
        "gitleaks_version": "8.30.1",
        "scan_scope": "all_refs",
    }
    payload.update(overrides)
    return payload


def fixture_files(tmp_path):
    paths = {
        "test": write_envelope(
            tmp_path / "test.json",
            report_type="test-report",
            producer="test",
            payload={"status": "pass"},
        ),
        "gitleaks": write_envelope(
            tmp_path / "gitleaks.json",
            report_type="gitleaks-report",
            producer="secret-scan",
            payload=gitleaks_payload(),
        ),
        "postgres": write_envelope(
            tmp_path / "postgres.json",
            report_type="postgres-report",
            producer="postgres-integration",
            payload={"status": "pass", "database_revision": "0001"},
        ),
        "operational": write_envelope(
            tmp_path / "operational.json",
            report_type="operational-report",
            producer="operational-postgres",
            payload={"status": "pass", "database_revision": "0001"},
        ),
    }
    now = datetime.now(timezone.utc)
    attestation = RuntimeAttestationV2(
        provider="dashscope",
        credential_fingerprint_prefix="".join(("a1b2", "c3d4", "e5f6")),
        credential_revocation_verified=True,
        credential_rotation_verified=True,
        provider_usage_review_status="reviewed_no_anomaly",
        discovered_at=now - timedelta(hours=4),
        revocation_verified_at=now - timedelta(hours=3),
        rotation_verified_at=now - timedelta(hours=2),
        provider_usage_reviewed_at=now - timedelta(hours=1),
        attestation_generated_at=now,
        attestation_source="protected_environment",
        subject_commit_sha="a" * 40,
        protected_ref="refs/heads/main",
        environment_name="phase14-incident-closure",
        environment_id="303",
        deployment_identifier="github-environment:303:workflow-job:404:check-run:505",
        verified_by_role="incident-reviewer",
        approval_event_verified=True,
        approval_event_count=1,
        approval_environment_id="303",
        approval_reference="apr_" + "b" * 64,
    )
    paths["attestation"] = tmp_path / "attestation.json"
    paths["attestation"].write_text(attestation.model_dump_json(indent=2), encoding="utf-8")
    def identities(names):
        return [
            {"job_name": name, "workflow_job_id": JOB_IDS[name][0], "check_run_id": JOB_IDS[name][1]}
            for name in names
        ]

    paths["incident_trust"] = tmp_path / "incident-trust.json"
    paths["incident_trust"].write_text(
        json.dumps(
            {
                "current_job_runtime_state_verified": True,
                "current_workflow_job_id": "5",
                "current_check_run_id": "15",
                "completed_job_identities": identities(tuple(JOB_IDS)[:4]),
            }
        ),
        encoding="utf-8",
    )
    paths["formal_trust"] = tmp_path / "formal-trust.json"
    paths["formal_trust"].write_text(
        json.dumps(
            {
                "current_job_runtime_state_verified": True,
                "current_workflow_job_id": "6",
                "current_check_run_id": "16",
                "completed_job_identities": identities(tuple(JOB_IDS)[:5]),
                "workflow_id": "99",
                "workflow_path": ".github/workflows/ci.yml",
                "workflow_state_at_closeout": "active",
                "workflow_state_verified_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    return paths


def invoke(module, monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", ["create_phase14_closeout.py", *arguments])
    assert module.main() == 0


def test_incident_candidate_and_formal_closeout_round_trip(tmp_path, monkeypatch):
    set_context(monkeypatch)
    paths = fixture_files(tmp_path)
    module = load_closeout_module()
    incident = tmp_path / "incident.json"
    common = [
        "--test", str(paths["test"]), "--gitleaks", str(paths["gitleaks"]),
        "--postgres", str(paths["postgres"]), "--operational", str(paths["operational"]),
        "--attestation", str(paths["attestation"]), "--github-trust", str(paths["incident_trust"]),
    ]
    invoke(module, monkeypatch, ["--mode", "incident", *common, "--output", str(incident)])
    incident_envelope = EvidenceEnvelope.model_validate_json(incident.read_text(encoding="utf-8"))
    assert incident_envelope.payload["authoritative"] is False
    assert "phase_status" not in incident_envelope.payload

    manifest = tmp_path / "manifest.json"
    invoke(
        module,
        monkeypatch,
        [
            "--mode", "prepare-formal", *common,
            "--github-trust", str(paths["formal_trust"]),
            "--incident", str(incident), "--output", str(manifest),
        ],
    )
    formal = tmp_path / "formal" / "phase14-formal-closeout.json"
    invoke(
        module,
        monkeypatch,
        [
            "--mode", "finalize", "--attestation", str(paths["attestation"]),
            "--manifest", str(manifest), "--github-trust", str(paths["formal_trust"]),
            "--output", str(formal),
            "--markdown", str(formal.with_suffix(".md")),
            "--attestation-output", str(formal.parent / "phase14_incident_attestation.json"),
        ],
    )
    formal_envelope = EvidenceEnvelope.model_validate_json(formal.read_text(encoding="utf-8"))
    assert formal_envelope.payload["authoritative"] is True
    assert formal_envelope.payload["phase_status"] == "pass"


@pytest.mark.parametrize(
    "overrides",
    [
        {"gitleaks_real_repository_scan_passed": False},
        {
            "gitleaks_all_refs_scan_status": "fail",
            "gitleaks_real_repository_scan_passed": True,
        },
        {
            "gitleaks_all_refs_findings": 1,
            "gitleaks_real_repository_scan_passed": True,
        },
        {
            "gitleaks_process_return_code": 1,
            "gitleaks_real_repository_scan_passed": True,
        },
    ],
)
def test_formal_consumer_rejects_inconsistent_gitleaks_evidence(
    tmp_path, monkeypatch, overrides
):
    set_context(monkeypatch)
    paths = fixture_files(tmp_path)
    write_envelope(
        paths["gitleaks"],
        report_type="gitleaks-report",
        producer="secret-scan",
        payload=gitleaks_payload(**overrides),
    )
    module = load_closeout_module()
    producer_paths = {
        "test": paths["test"],
        "secret-scan": paths["gitleaks"],
        "postgres-integration": paths["postgres"],
        "operational-postgres": paths["operational"],
    }

    with pytest.raises(RuntimeError, match="gitleaks_evidence_inconsistent"):
        module.validate_producers(producer_paths)


def test_formal_consumer_rejects_consistent_failed_gitleaks_scan(
    tmp_path, monkeypatch
):
    set_context(monkeypatch)
    paths = fixture_files(tmp_path)
    write_envelope(
        paths["gitleaks"],
        report_type="gitleaks-report",
        producer="secret-scan",
        payload=gitleaks_payload(
            gitleaks_process_return_code=1,
            gitleaks_all_refs_scan_status="fail",
            gitleaks_all_refs_findings=1,
            gitleaks_real_repository_scan_passed=False,
        ),
    )
    module = load_closeout_module()
    producer_paths = {
        "test": paths["test"],
        "secret-scan": paths["gitleaks"],
        "postgres-integration": paths["postgres"],
        "operational-postgres": paths["operational"],
    }

    with pytest.raises(RuntimeError, match="gitleaks_all_refs_scan_failed"):
        module.validate_producers(producer_paths)


def test_formal_prepare_rejects_attestation_not_bound_to_incident(tmp_path, monkeypatch):
    set_context(monkeypatch)
    paths = fixture_files(tmp_path)
    module = load_closeout_module()
    incident = tmp_path / "incident.json"
    common = [
        "--test", str(paths["test"]), "--gitleaks", str(paths["gitleaks"]),
        "--postgres", str(paths["postgres"]), "--operational", str(paths["operational"]),
        "--attestation", str(paths["attestation"]), "--github-trust", str(paths["incident_trust"]),
    ]
    invoke(module, monkeypatch, ["--mode", "incident", *common, "--output", str(incident)])
    payload = json.loads(paths["attestation"].read_text(encoding="utf-8"))
    payload["verified_by_role"] = "different-role"
    paths["attestation"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="incident_attestation_binding_mismatch"):
        invoke(
            module,
            monkeypatch,
            [
                "--mode", "prepare-formal", *common, "--incident", str(incident),
                "--github-trust", str(paths["formal_trust"]),
                "--output", str(tmp_path / "manifest.json"),
            ],
        )
