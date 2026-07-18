import json

import pytest

from conversation_agent.evaluation.production_blockers import (
    _validate_runtime_attestation,
    evaluate_production_blockers,
    exit_code_for_status,
)

pytestmark = pytest.mark.unit


def test_implementation_scope_passes_without_claiming_incident_closure():
    report = evaluate_production_blockers(scope="implementation")
    assert report.summary.implementation_status == "pass", report.summary
    assert report.summary.phase_status is None
    assert report.summary.incident_closure_status is None
    assert report.summary.authoritative is False
    assert report.summary.fixture_case_count >= 243
    assert report.summary.concurrency_rounds == 100


def test_phase_scope_is_blocked_without_protected_attestation_and_git_history():
    report = evaluate_production_blockers(scope="phase")
    assert report.summary.phase_status == "blocked", report.summary
    assert report.summary.git_history_scan_status == "blocked"
    assert report.summary.credential_revocation_verified is False
    assert exit_code_for_status(report.summary.phase_status) == 3


def _write_attestation(tmp_path, **overrides):
    payload = {
        "schema_version": "phase14_incident_attestation_v2",
        "provider": "dashscope",
        "credential_fingerprint_prefix": "".join(("a1b2", "c3d4", "e5f6")),
        "credential_revocation_verified": True,
        "credential_rotation_verified": True,
        "provider_usage_review_status": "reviewed_no_anomaly",
        "discovered_at": "2026-07-17T08:00:00Z",
        "revocation_verified_at": "2026-07-17T09:00:00Z",
        "rotation_verified_at": "2026-07-17T08:30:00Z",
        "provider_usage_reviewed_at": "2026-07-17T10:00:00Z",
        "attestation_generated_at": "2026-07-17T10:30:00Z",
        "attestation_source": "protected_environment",
        "subject_commit_sha": "a" * 40,
        "protected_ref": "refs/heads/main",
        "environment_name": "phase14-incident-closure",
        "environment_id": "123",
        "deployment_identifier": "github-environment:123:workflow-job:456:check-run:789",
        "verified_by_role": "authorized-incident-reviewer",
        "approval_event_verified": True,
        "approval_event_count": 1,
        "approval_environment_id": "123",
        "approval_reference": "apr_" + "b" * 64,
    }
    payload.update(overrides)
    path = tmp_path / "runtime-attestation.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _set_protected_environment(monkeypatch):
    monkeypatch.setenv("CONVAGENT_PHASE14_PROTECTED_ENVIRONMENT", "true")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("CONVAGENT_PHASE14_ENVIRONMENT_NAME", "phase14-incident-closure")


def test_matching_protected_runtime_attestation_verifies_incident_actions(
    tmp_path,
    monkeypatch,
):
    _set_protected_environment(monkeypatch)

    result = _validate_runtime_attestation(_write_attestation(tmp_path))

    assert result["credential_revocation_verified"] is True
    assert result["credential_rotation_verified"] is True
    assert result["blocking_reasons"] == []


@pytest.mark.parametrize(
    ("override", "environment"),
    [
        ({"attestation_source": "pull_request"}, {}),
        ({"subject_commit_sha": "b" * 40}, {}),
        ({"protected_ref": "refs/heads/feature"}, {}),
        ({"approval_reference": ""}, {}),
        ({}, {"CONVAGENT_PHASE14_PROTECTED_ENVIRONMENT": "false"}),
        ({}, {"GITHUB_RUN_ATTEMPT": "2"}),
    ],
)
def test_untrusted_runtime_attestation_cannot_close_incident(
    tmp_path,
    monkeypatch,
    override,
    environment,
):
    _set_protected_environment(monkeypatch)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    result = _validate_runtime_attestation(
        _write_attestation(tmp_path, **override),
    )

    assert result["credential_revocation_verified"] is False
    assert result["credential_rotation_verified"] is False
    assert result["blocking_reasons"]


def test_incident_evidence_scope_never_claims_authoritative_phase_pass():
    report = evaluate_production_blockers(scope="incident-evidence")

    assert report.summary.authoritative is False
    assert report.summary.phase_status is None
    assert report.summary.incident_closure_status is None
    assert report.summary.phase_candidate_status == "blocked"
