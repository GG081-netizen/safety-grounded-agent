"""Create Phase 14-G runtime evidence without allowing Job self-completion claims."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from conversation_agent.evaluation.phase14_baseline import (
    BASELINE_ENVIRONMENT,
    BASELINE_JOBS,
    BASELINE_REPOSITORY,
    BASELINE_REPOSITORY_ID,
    BASELINE_REVIEWER,
    BASELINE_TRIGGER_ACTOR,
    DISCOVERY_WORKFLOW_PATH,
    FORMAL_WORKFLOW_PATH,
    ApprovalAttestationSummary,
    BaselineArtifactDocumentV1,
    BaselineCloseoutPayloadV1,
    BaselineFormalInputManifestV1,
    CandidateManifestV1,
    DiscoveryArtifactBindingV1,
    DiscoveryEvidenceV1,
    candidate_manifest_sha256,
    validate_completed_formal_job,
    validate_discovery_runtime_self_job,
    validate_formal_runtime_self_job,
    write_json,
)
from conversation_agent.evaluation.phase14_evidence import (
    canonical_commit_sha,
    canonical_github_id,
    validate_environment_response,
)


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"missing_github_runtime_value:{name}")
    return value


def api_client() -> httpx.Client:
    return httpx.Client(
        base_url=required("GITHUB_API_URL"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {required('GITHUB_TOKEN')}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=15.0,
        follow_redirects=False,
    )


def get_json(client: httpx.Client, path: str) -> Any:
    response = client.get(path)
    if response.status_code != 200:
        raise RuntimeError(f"github_api_unavailable:{response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("github_api_invalid_json") from exc


def attempt_jobs(client: httpx.Client, run_id: str) -> tuple[dict[str, Any], ...]:
    payload = get_json(
        client,
        f"/repos/{BASELINE_REPOSITORY}/actions/runs/{run_id}/attempts/1/jobs"
        "?per_page=100&page=1",
    )
    if (
        not isinstance(payload, dict)
        or type(payload.get("total_count")) is not int
        or not isinstance(payload.get("jobs"), list)
        or payload["total_count"] != len(payload["jobs"])
        or payload["total_count"] > 100
    ):
        raise RuntimeError("baseline_jobs_response_invalid")
    jobs = tuple(payload["jobs"])
    if not all(isinstance(job, dict) for job in jobs):
        raise RuntimeError("baseline_jobs_response_invalid")
    ids = [canonical_github_id(job.get("id")) for job in jobs]
    if len(ids) != len(set(ids)):
        raise RuntimeError("baseline_duplicate_job_id")
    return jobs


def unique_job(jobs: tuple[dict[str, Any], ...], name: str) -> dict[str, Any]:
    matches = [job for job in jobs if job.get("name") == name]
    if len(matches) != 1:
        raise RuntimeError(f"baseline_job_not_unique:{name}")
    return matches[0]


def runtime_values() -> tuple[str, str, str]:
    if required("GITHUB_SERVER_URL") != "https://github.com":
        raise RuntimeError("unsupported_github_platform")
    if required("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise RuntimeError("baseline_requires_workflow_dispatch")
    if canonical_github_id(required("GITHUB_RUN_ATTEMPT")) != "1":
        raise RuntimeError("baseline_requires_fresh_run")
    if required("GITHUB_REPOSITORY") != BASELINE_REPOSITORY:
        raise RuntimeError("baseline_repository_mismatch")
    if canonical_github_id(required("GITHUB_REPOSITORY_ID")) != BASELINE_REPOSITORY_ID:
        raise RuntimeError("baseline_repository_id_mismatch")
    if required("GITHUB_ACTOR") != BASELINE_TRIGGER_ACTOR:
        raise RuntimeError("baseline_trigger_actor_mismatch")
    return (
        canonical_github_id(required("GITHUB_RUN_ID")),
        canonical_commit_sha(required("GITHUB_SHA")),
        canonical_github_id(required("PHASE14_CURRENT_CHECK_RUN_ID")),
    )


def git_tree_sha() -> str:
    value = subprocess.run(
        ["git", "show", "-s", "--format=%T", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return canonical_commit_sha(value)


def discovery(args: argparse.Namespace) -> None:
    run_id, sha, check_id = runtime_values()
    manifest = CandidateManifestV1.model_validate_json(args.candidate_manifest.read_text())
    with api_client() as client:
        jobs = attempt_jobs(client, run_id)
        identity = validate_discovery_runtime_self_job(
            unique_job(jobs, "discovery"),
            run_id=run_id,
            sha=sha,
            check_run_id=check_id,
        )
        run = get_json(client, f"/repos/{BASELINE_REPOSITORY}/actions/runs/{run_id}")
        workflow_id = canonical_github_id(run.get("workflow_id"))
        workflow = get_json(client, f"/repos/{BASELINE_REPOSITORY}/actions/workflows/{workflow_id}")
        if workflow.get("path") != DISCOVERY_WORKFLOW_PATH or workflow.get("state") != "active":
            raise RuntimeError("discovery_workflow_identity_invalid")
    evidence = DiscoveryEvidenceV1(
        authoritative=False,
        discovery_status="pass",
        repository=BASELINE_REPOSITORY,
        repository_id=BASELINE_REPOSITORY_ID,
        workflow_id=workflow_id,
        workflow_path=DISCOVERY_WORKFLOW_PATH,
        workflow_run_id=run_id,
        workflow_run_attempt="1",
        subject_commit_sha=sha,
        subject_tree_sha=git_tree_sha(),
        candidate_manifest_sha256=candidate_manifest_sha256(manifest),
        producer_job_id=identity.workflow_job_id,
        producer_check_run_id=identity.check_run_id,
        generated_at=datetime.now(timezone.utc),
    )
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "phase14-discovery-evidence.json", evidence)
    write_json(args.output / "phase14-candidate-manifest.json", manifest)


def producer(args: argparse.Namespace) -> None:
    run_id, sha, check_id = runtime_values()
    with api_client() as client:
        identity = validate_formal_runtime_self_job(
            unique_job(attempt_jobs(client, run_id), args.job_name),
            expected_name=args.job_name,
            run_id=run_id,
            sha=sha,
            check_run_id=check_id,
        )
    write_json(args.output, {
        "schema_version": "phase14_baseline_producer_v1",
        "status": "pass",
        "job_identity": identity.model_dump(mode="json"),
        "subject_commit_sha": sha,
        "workflow_run_id": run_id,
        "workflow_run_attempt": "1",
    })


def _approval_summary(client: httpx.Client, run_id: str) -> ApprovalAttestationSummary:
    environment_payload = get_json(
        client, f"/repos/{BASELINE_REPOSITORY}/environments/{BASELINE_ENVIRONMENT}"
    )
    if not isinstance(environment_payload, dict):
        raise RuntimeError("baseline_environment_invalid")
    environment_id, _ = validate_environment_response(
        environment_payload, expected_name=BASELINE_ENVIRONMENT
    )
    reviewer_rule = next(
        rule
        for rule in environment_payload["protection_rules"]
        if rule.get("type") == "required_reviewers"
    )
    reviewer_logins = {
        item.get("reviewer", {}).get("login")
        for item in reviewer_rule.get("reviewers", ())
        if isinstance(item, dict) and isinstance(item.get("reviewer"), dict)
    }
    if reviewer_logins != {BASELINE_REVIEWER}:
        raise RuntimeError("baseline_required_reviewer_mismatch")
    custom = get_json(
        client,
        f"/repos/{BASELINE_REPOSITORY}/environments/{BASELINE_ENVIRONMENT}"
        "/deployment_protection_rules",
    )
    if not isinstance(custom, dict) or custom.get("total_count") != 0:
        raise RuntimeError("baseline_custom_protection_rule_not_allowed")
    approvals = get_json(client, f"/repos/{BASELINE_REPOSITORY}/actions/runs/{run_id}/approvals")
    if not isinstance(approvals, list):
        raise RuntimeError("baseline_approval_history_invalid")
    matches = []
    for approval in approvals:
        if not isinstance(approval, dict) or approval.get("state") != "approved":
            continue
        user = approval.get("user")
        environments = approval.get("environments")
        if not isinstance(user, dict) or user.get("login") != BASELINE_REVIEWER:
            continue
        if any(
            isinstance(item, dict) and item.get("name") == BASELINE_ENVIRONMENT
            for item in environments or ()
        ):
            matches.append(approval)
    if not matches or required("GITHUB_ACTOR") == BASELINE_REVIEWER:
        raise RuntimeError("baseline_approval_not_verified")
    environments = matches[0]["environments"]
    approved_environment = next(
        item for item in environments if item.get("name") == BASELINE_ENVIRONMENT
    )
    if canonical_github_id(approved_environment.get("id")) != environment_id:
        raise RuntimeError("baseline_approval_environment_id_mismatch")
    return ApprovalAttestationSummary(
        approval_verified=True,
        approval_actor_differs_from_trigger=True,
        approval_environment_id=environment_id,
        approval_event_count=len(matches),
    )


def approval(args: argparse.Namespace) -> None:
    run_id, sha, check_id = runtime_values()
    with api_client() as client:
        jobs = attempt_jobs(client, run_id)
        completed = tuple(
            validate_completed_formal_job(unique_job(jobs, name), expected_name=name, run_id=run_id, sha=sha)
            for name in BASELINE_JOBS[:4]
        )
        current = validate_formal_runtime_self_job(
            unique_job(jobs, "baseline-approval"), expected_name="baseline-approval",
            run_id=run_id, sha=sha, check_run_id=check_id,
        )
        summary = _approval_summary(client, run_id)
    write_json(args.output, {
        "schema_version": "phase14_baseline_approval_v1",
        "status": "pass",
        "completed_jobs": [item.model_dump(mode="json") for item in completed],
        "current_job": current.model_dump(mode="json"),
        "approval_attestation_summary": summary.model_dump(mode="json"),
    })


def closeout(args: argparse.Namespace) -> None:
    run_id, sha, check_id = runtime_values()
    manifest = CandidateManifestV1.model_validate_json(args.candidate_manifest.read_text())
    binding = DiscoveryArtifactBindingV1.model_validate_json(args.discovery_binding.read_text())
    approval_payload = json.loads(args.approval_evidence.read_text())
    summary = ApprovalAttestationSummary.model_validate(
        approval_payload["approval_attestation_summary"]
    )
    with api_client() as client:
        jobs = attempt_jobs(client, run_id)
        identities = [
            validate_completed_formal_job(unique_job(jobs, name), expected_name=name, run_id=run_id, sha=sha)
            for name in BASELINE_JOBS[:5]
        ]
        current = validate_formal_runtime_self_job(
            unique_job(jobs, "baseline-closeout"), expected_name="baseline-closeout",
            run_id=run_id, sha=sha, check_run_id=check_id,
        )
        identities.append(current)
        run = get_json(client, f"/repos/{BASELINE_REPOSITORY}/actions/runs/{run_id}")
        workflow_id = canonical_github_id(run.get("workflow_id"))
        workflow = get_json(client, f"/repos/{BASELINE_REPOSITORY}/actions/workflows/{workflow_id}")
        if workflow.get("path") != FORMAL_WORKFLOW_PATH or workflow.get("state") != "active":
            raise RuntimeError("formal_workflow_identity_invalid")
    if (
        binding.subject_commit_sha != sha
        or binding.workflow_run_attempt != "1"
        or binding.workflow_run_id != canonical_github_id(args.discovery_run_id)
    ):
        raise RuntimeError("discovery_binding_subject_mismatch")
    payload = BaselineCloseoutPayloadV1(
        authoritative=False,
        repository_baseline_candidate_status="pass",
        phase14_authoritative_phase_status="blocked",
        repository=BASELINE_REPOSITORY,
        repository_id=BASELINE_REPOSITORY_ID,
        workflow_id=workflow_id,
        workflow_path=FORMAL_WORKFLOW_PATH,
        workflow_run_id=run_id,
        workflow_run_attempt="1",
        subject_commit_sha=sha,
        subject_tree_sha=git_tree_sha(),
        candidate_manifest_sha256=candidate_manifest_sha256(manifest),
        discovery_binding=binding,
        approval_attestation_summary=summary,
        required_jobs=tuple(identities),
        baseline_closeout_job_id=current.workflow_job_id,
        baseline_closeout_check_run_id=current.check_run_id,
        baseline_closeout_runtime_status="in_progress",
        baseline_closeout_runtime_conclusion=None,
        database_revision="0001",
        generated_at=datetime.now(timezone.utc),
    )
    document = BaselineArtifactDocumentV1(
        closeout_payload=payload,
        formal_input_manifest=BaselineFormalInputManifestV1(
            candidate_manifest_sha256=candidate_manifest_sha256(manifest),
            discovery_workflow_id=args.discovery_workflow_id,
            discovery_workflow_path=args.discovery_workflow_path,
            discovery_run_id=args.discovery_run_id,
            discovery_run_attempt=args.discovery_run_attempt,
            discovery_artifact_id=binding.artifact_id,
            discovery_artifact_name=binding.artifact_name,
            discovery_artifact_digest=binding.artifact_digest,
        ),
        repository_baseline_attestation={
            "root_commit_parent_count": 0,
            "root_commit_tree_sha": payload.subject_tree_sha,
            "remote_commit_count": 1,
            "remote_tag_count": 0,
        },
        approval_attestation_summary=summary,
        producer_evidence_references=tuple(
            {"job_name": item.job_name, "workflow_job_id": item.workflow_job_id,
             "check_run_id": item.check_run_id}
            for item in identities[:4]
        ),
    )
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "phase14-baseline-closeout.json", document)
    write_json(args.output / "phase14-candidate-manifest.json", manifest)
    (args.output / "phase14-baseline-closeout.md").write_text(
        "# Phase 14-G Repository Baseline\n\n"
        "- repository_baseline_candidate_status: pass\n"
        "- authoritative_result: requires independent online verification\n"
        "- phase14_authoritative_phase_status: blocked\n"
        "- database_revision: 0001\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("discovery", "producer", "approval", "closeout"), required=True)
    parser.add_argument("--job-name", choices=BASELINE_JOBS[:4])
    parser.add_argument("--candidate-manifest", type=Path)
    parser.add_argument("--discovery-binding", type=Path)
    parser.add_argument("--approval-evidence", type=Path)
    parser.add_argument("--discovery-workflow-id")
    parser.add_argument("--discovery-workflow-path")
    parser.add_argument("--discovery-run-id")
    parser.add_argument("--discovery-run-attempt")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "discovery":
        discovery(args)
    elif args.mode == "producer":
        if args.job_name is None:
            raise RuntimeError("producer_job_name_required")
        producer(args)
    elif args.mode == "approval":
        approval(args)
    else:
        if not all((
            args.candidate_manifest,
            args.discovery_binding,
            args.approval_evidence,
            args.discovery_workflow_id,
            args.discovery_workflow_path,
            args.discovery_run_id,
            args.discovery_run_attempt,
        )):
            raise RuntimeError("baseline_closeout_input_missing")
        closeout(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
