from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from conversation_agent.evaluation.phase14_evidence import (
    EvidenceEnvelope,
    RuntimeAttestationV2,
    approval_reference,
    bind_current_job,
    canonical_github_id,
    create_evidence_envelope,
    validate_approval_history,
    validate_environment_response,
    verify_evidence_provenance,
)

pytestmark = pytest.mark.unit
UTC = timezone.utc
NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
SHA = "a" * 40


@pytest.mark.parametrize("value", ["0", 0, -1, True, 1.0, "001", "+1", "1 ", "1e2"])
def test_canonical_github_id_rejects_ambiguous_values(value):
    with pytest.raises(ValueError, match="invalid_github_id"):
        canonical_github_id(value)


def test_canonical_github_id_normalizes_rest_integer_and_context_string():
    assert canonical_github_id(123) == "123"
    assert canonical_github_id("123") == "123"


def test_evidence_envelope_detects_payload_tampering():
    envelope = create_evidence_envelope(
        report_type="test-report",
        subject_commit_sha=SHA.upper(),
        repository_id=1,
        workflow_run_id="2",
        workflow_run_attempt=1,
        producer_job_name="test",
        producer_workflow_job_id=10,
        producer_check_run_id=20,
        generated_at=NOW,
        payload={"passed": 10},
    )
    payload = envelope.model_dump(mode="json")
    payload["payload"]["passed"] = 11

    with pytest.raises(ValidationError, match="payload_sha256_mismatch"):
        EvidenceEnvelope.model_validate(payload)


def test_evidence_provenance_rejects_cross_attempt_artifact():
    envelope = create_evidence_envelope(
        report_type="test-report",
        subject_commit_sha=SHA,
        repository_id=1,
        workflow_run_id=2,
        workflow_run_attempt=1,
        producer_job_name="test",
        producer_workflow_job_id=10,
        producer_check_run_id=20,
        generated_at=NOW,
        payload={"passed": 10},
    )

    with pytest.raises(ValueError, match="evidence_provenance_mismatch"):
        verify_evidence_provenance(
            envelope,
            report_type="test-report",
            subject_commit_sha=SHA,
            repository_id=1,
            workflow_run_id=2,
            workflow_run_attempt=2,
            producer_job_name="test",
            now=NOW,
        )


def _environment_payload():
    return {
        "id": 101,
        "name": "phase14-incident-closure",
        "protection_rules": [
            {
                "type": "required_reviewers",
                "reviewers": [{"type": "Team", "reviewer": {"id": 7}}],
                "prevent_self_review": True,
            },
            {"type": "branch_policy"},
        ],
        "deployment_branch_policy": {
            "protected_branches": True,
            "custom_branch_policies": False,
        },
    }


def test_environment_contract_accepts_only_reviewers_and_protected_branch_policy():
    assert validate_environment_response(
        _environment_payload(),
        expected_name="phase14-incident-closure",
    ) == ("101", "phase14-incident-closure")


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["protection_rules"].append({"type": "wait_timer"}),
        lambda value: value["protection_rules"].append({"type": "required_reviewers"}),
        lambda value: value["protection_rules"][0].update({"prevent_self_review": False}),
        lambda value: value["deployment_branch_policy"].update({"custom_branch_policies": True}),
    ],
)
def test_environment_contract_rejects_unapproved_protection_configuration(mutator):
    payload = _environment_payload()
    mutator(payload)

    with pytest.raises(ValueError):
        validate_environment_response(
            payload,
            expected_name="phase14-incident-closure",
        )


def test_approval_history_matches_environments_array_without_exposing_user():
    approvals = [
        {
            "state": "approved",
            "comment": "private comment",
            "user": {"login": "private-reviewer"},
            "environments": [{"id": 101, "name": "phase14-incident-closure"}],
        }
    ]

    assert validate_approval_history(
        approvals,
        environment_id="101",
        environment_name="phase14-incident-closure",
    ) == 1


def test_approval_history_rejects_wrong_environment():
    with pytest.raises(ValueError, match="approved_environment_event_missing"):
        validate_approval_history(
            [{"state": "approved", "environments": [{"id": 102, "name": "other"}]}],
            environment_id=101,
            environment_name="phase14-incident-closure",
        )


def test_current_job_binding_uses_check_run_url_not_name_only():
    jobs = {
        "jobs": [
            {
                "id": 201,
                "name": "incident-closure",
                "head_sha": SHA,
                "status": "in_progress",
                "conclusion": None,
                "check_run_url": "https://api.github.com/repos/acme/repo/check-runs/301",
            },
            {
                "id": 202,
                "name": "incident-closure",
                "head_sha": SHA,
                "status": "in_progress",
                "conclusion": None,
                "check_run_url": "https://api.github.com/repos/acme/repo/check-runs/302",
            },
        ]
    }

    assert bind_current_job(
        jobs,
        check_run_id="302",
        subject_commit_sha=SHA,
        expected_name="incident-closure",
    ) == ("202", "302")


@pytest.mark.parametrize(
    ("status", "conclusion"),
    [("completed", "success"), ("completed", "failure"), ("in_progress", "failure")],
)
def test_current_job_cannot_self_attest_completion(status, conclusion):
    jobs = {
        "jobs": [
            {
                "id": 202,
                "name": "test",
                "head_sha": SHA,
                "status": status,
                "conclusion": conclusion,
                "check_run_url": "https://api.github.com/repos/acme/repo/check-runs/302",
            }
        ]
    }
    with pytest.raises(ValueError):
        bind_current_job(
            jobs,
            check_run_id="302",
            subject_commit_sha=SHA,
            expected_name="test",
        )


def _attestation(**overrides):
    deployment = "github-environment:101:workflow-job:201:check-run:301"
    values = {
        "provider": "dashscope",
        "credential_fingerprint_prefix": "".join(("0123", "4567", "89ab")),
        "credential_revocation_verified": True,
        "credential_rotation_verified": True,
        "provider_usage_review_status": "reviewed_no_anomaly",
        "discovered_at": NOW - timedelta(days=3),
        "revocation_verified_at": NOW - timedelta(days=2),
        "rotation_verified_at": NOW - timedelta(days=1),
        "provider_usage_reviewed_at": NOW - timedelta(hours=1),
        "attestation_generated_at": NOW,
        "attestation_source": "protected_environment",
        "subject_commit_sha": SHA,
        "protected_ref": "refs/heads/main",
        "environment_name": "phase14-incident-closure",
        "environment_id": "101",
        "deployment_identifier": deployment,
        "verified_by_role": "security-operator",
        "approval_event_verified": True,
        "approval_event_count": 1,
        "approval_environment_id": "101",
        "approval_reference": approval_reference(
            repository_id=1,
            workflow_run_id=2,
            workflow_run_attempt=1,
            environment_name="phase14-incident-closure",
            deployment_identifier=deployment,
        ),
    }
    values.update(overrides)
    return RuntimeAttestationV2(**values)


def test_attestation_accepts_rotation_before_revocation_partial_order():
    attestation = _attestation(
        rotation_verified_at=NOW - timedelta(days=2, hours=1),
        revocation_verified_at=NOW - timedelta(days=2),
    )

    attestation.validate_runtime(
        now=NOW,
        subject_commit_sha=SHA,
        protected_ref="refs/heads/main",
        environment_name="phase14-incident-closure",
    )


def test_attestation_rejects_usage_review_before_revocation():
    with pytest.raises(ValidationError, match="invalid_usage_review_timeline"):
        _attestation(provider_usage_reviewed_at=NOW - timedelta(days=4))


def test_attestation_rejects_future_timestamp_beyond_skew():
    attestation = _attestation(attestation_generated_at=NOW + timedelta(seconds=61))

    with pytest.raises(ValueError, match="attestation_time_in_future"):
        attestation.validate_runtime(
            now=NOW,
            subject_commit_sha=SHA,
            protected_ref="refs/heads/main",
            environment_name="phase14-incident-closure",
        )
