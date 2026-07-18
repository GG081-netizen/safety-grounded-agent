"""Independent verifier for the Phase 14-G discovery and baseline artifacts."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from conversation_agent.evaluation.phase14_artifact import download_archive
from conversation_agent.evaluation.phase14_baseline import (
    BASELINE_BRANCH,
    BASELINE_ENVIRONMENT,
    BASELINE_FILES,
    BASELINE_JOBS,
    BASELINE_REPOSITORY,
    BASELINE_REPOSITORY_ID,
    BASELINE_REVIEWER,
    BASELINE_TRIGGER_ACTOR,
    DISCOVERY_FILES,
    DISCOVERY_WORKFLOW_PATH,
    FORMAL_WORKFLOW_PATH,
    ApprovalAttestationSummary,
    BaselineArtifactDocumentV1,
    CandidateManifestV1,
    DiscoveryArtifactBindingV1,
    DiscoveryEvidenceV1,
    candidate_manifest_sha256,
    write_json,
)
from conversation_agent.evaluation.phase14_evidence import (
    canonical_commit_sha,
    canonical_github_id,
)

MAX_EXTRACTED_BYTES = 5 * 1024 * 1024


def safe_extract(data: bytes, allowed: frozenset[str]) -> dict[str, bytes]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("baseline_artifact_invalid_zip") from exc
    files: dict[str, bytes] = {}
    total = 0
    with archive:
        for member in archive.infolist():
            path = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if (
                member.flag_bits & 1
                or path.is_absolute()
                or ".." in path.parts
                or len(path.parts) != 1
                or member.filename.startswith(".")
                or member.is_dir()
                or stat.S_ISLNK(mode)
                or member.filename in files
                or member.filename not in allowed
            ):
                raise ValueError("baseline_artifact_unsafe_member")
            content = archive.read(member)
            total += len(content)
            if total > MAX_EXTRACTED_BYTES:
                raise ValueError("baseline_artifact_expanded_too_large")
            files[member.filename] = content
    if set(files) != allowed:
        raise ValueError("baseline_artifact_file_set_mismatch")
    return files


def api_json(client: httpx.Client, path: str) -> Any:
    response = client.get(path)
    if response.status_code != 200:
        raise ValueError(f"github_api_unavailable:{response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError("github_api_invalid_json") from exc


def artifact_metadata(
    client: httpx.Client, *, artifact_id: str, expected_name: str, run_id: str, sha: str
) -> dict[str, Any]:
    artifact_id = canonical_github_id(artifact_id)
    payload = api_json(
        client, f"/repos/{BASELINE_REPOSITORY}/actions/artifacts/{artifact_id}"
    )
    if not isinstance(payload, dict):
        raise ValueError("artifact_metadata_invalid")
    workflow_run = payload.get("workflow_run")
    if (
        canonical_github_id(payload.get("id")) != artifact_id
        or payload.get("name") != expected_name
        or payload.get("expired") is not False
        or not isinstance(workflow_run, dict)
        or canonical_github_id(workflow_run.get("id")) != canonical_github_id(run_id)
        or canonical_github_id(workflow_run.get("repository_id")) != BASELINE_REPOSITORY_ID
        or canonical_github_id(workflow_run.get("head_repository_id")) != BASELINE_REPOSITORY_ID
        or canonical_commit_sha(workflow_run.get("head_sha")) != canonical_commit_sha(sha)
    ):
        raise ValueError("artifact_origin_metadata_invalid")
    digest = payload.get("digest")
    size = payload.get("size_in_bytes")
    if (
        not isinstance(digest, str)
        or len(digest) != 71
        or not digest.startswith("sha256:")
        or type(size) is not int
        or size <= 0
    ):
        raise ValueError("artifact_digest_metadata_invalid")
    return payload


def workflow_run(
    client: httpx.Client,
    *,
    run_id: str,
    sha: str,
    workflow_path: str,
    require_completed: bool,
) -> dict[str, Any]:
    run_id = canonical_github_id(run_id)
    payload = api_json(client, f"/repos/{BASELINE_REPOSITORY}/actions/runs/{run_id}")
    if not isinstance(payload, dict):
        raise ValueError("workflow_run_invalid")
    repository = payload.get("repository")
    head_repository = payload.get("head_repository")
    allowed_paths = {workflow_path, f"{workflow_path}@refs/heads/{BASELINE_BRANCH}"}
    if (
        canonical_github_id(payload.get("id")) != run_id
        or not isinstance(repository, dict)
        or canonical_github_id(repository.get("id")) != BASELINE_REPOSITORY_ID
        or not isinstance(head_repository, dict)
        or canonical_github_id(head_repository.get("id")) != BASELINE_REPOSITORY_ID
        or payload.get("event") != "workflow_dispatch"
        or canonical_github_id(payload.get("run_attempt")) != "1"
        or canonical_commit_sha(payload.get("head_sha")) != canonical_commit_sha(sha)
        or payload.get("head_branch") != BASELINE_BRANCH
        or payload.get("path") not in allowed_paths
    ):
        raise ValueError("workflow_run_origin_invalid")
    if require_completed and (
        payload.get("status") != "completed" or payload.get("conclusion") != "success"
    ):
        raise ValueError("workflow_run_not_successful")
    return payload


def attempt_jobs(client: httpx.Client, run_id: str) -> tuple[dict[str, Any], ...]:
    payload = api_json(
        client,
        f"/repos/{BASELINE_REPOSITORY}/actions/runs/{canonical_github_id(run_id)}"
        "/attempts/1/jobs?per_page=100&page=1",
    )
    if (
        not isinstance(payload, dict)
        or type(payload.get("total_count")) is not int
        or not isinstance(payload.get("jobs"), list)
        or payload["total_count"] != len(payload["jobs"])
        or payload["total_count"] > 100
    ):
        raise ValueError("workflow_jobs_invalid")
    jobs = tuple(payload["jobs"])
    if not all(isinstance(job, dict) for job in jobs):
        raise ValueError("workflow_jobs_invalid")
    ids = [canonical_github_id(job.get("id")) for job in jobs]
    if len(ids) != len(set(ids)):
        raise ValueError("workflow_job_id_duplicate")
    return jobs


def verify_success_jobs(
    jobs: tuple[dict[str, Any], ...], names: tuple[str, ...], *, run_id: str, sha: str
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    if len(jobs) != len(names) or {job.get("name") for job in jobs} != set(names):
        raise ValueError("workflow_job_allowlist_mismatch")
    for name in names:
        matches = [job for job in jobs if job.get("name") == name]
        if len(matches) != 1:
            raise ValueError(f"workflow_job_not_unique:{name}")
        job = matches[0]
        check_url = job.get("check_run_url")
        if (
            canonical_github_id(job.get("run_id")) != canonical_github_id(run_id)
            or canonical_commit_sha(job.get("head_sha")) != canonical_commit_sha(sha)
            or job.get("status") != "completed"
            or job.get("conclusion") != "success"
            or not isinstance(check_url, str)
        ):
            raise ValueError(f"workflow_job_not_successful:{name}")
        result[name] = {
            "workflow_job_id": canonical_github_id(job.get("id")),
            "check_run_id": canonical_github_id(check_url.rstrip("/").rsplit("/", 1)[-1]),
        }
    return result


def verify_discovery(
    client: httpx.Client,
    *,
    artifact_id: str,
    artifact_name: str,
    run_id: str,
    sha: str,
    expected_tree_sha: str,
    expected_manifest_sha256: str,
    expected_artifact_digest: str | None = None,
    expected_workflow_id: str | None = None,
    downloader=download_archive,
) -> tuple[DiscoveryArtifactBindingV1, DiscoveryEvidenceV1, CandidateManifestV1]:
    metadata = artifact_metadata(
        client, artifact_id=artifact_id, expected_name=artifact_name, run_id=run_id, sha=sha
    )
    if expected_artifact_digest is not None and metadata["digest"] != expected_artifact_digest:
        raise ValueError("discovery_artifact_digest_input_mismatch")
    run = workflow_run(
        client, run_id=run_id, sha=sha, workflow_path=DISCOVERY_WORKFLOW_PATH,
        require_completed=True,
    )
    workflow_id = canonical_github_id(run.get("workflow_id"))
    if expected_workflow_id is not None and workflow_id != canonical_github_id(
        expected_workflow_id
    ):
        raise ValueError("discovery_workflow_id_input_mismatch")
    workflow = api_json(client, f"/repos/{BASELINE_REPOSITORY}/actions/workflows/{workflow_id}")
    if (
        not isinstance(workflow, dict)
        or workflow.get("path") != DISCOVERY_WORKFLOW_PATH
        or workflow.get("state") != "active"
    ):
        raise ValueError("discovery_workflow_identity_invalid")
    jobs = attempt_jobs(client, run_id)
    identities = verify_success_jobs(jobs, ("discovery",), run_id=run_id, sha=sha)
    digest = metadata["digest"]
    size = metadata["size_in_bytes"]
    archive = downloader(
        client, repository=BASELINE_REPOSITORY, artifact_id=artifact_id,
        expected_size=size, expected_digest=digest[7:],
    )
    files = safe_extract(archive, DISCOVERY_FILES)
    evidence = DiscoveryEvidenceV1.model_validate_json(
        files["phase14-discovery-evidence.json"].decode("utf-8")
    )
    manifest = CandidateManifestV1.model_validate_json(
        files["phase14-candidate-manifest.json"].decode("utf-8")
    )
    identity = identities["discovery"]
    if (
        evidence.workflow_id != workflow_id
        or evidence.workflow_run_id != canonical_github_id(run_id)
        or evidence.subject_commit_sha != canonical_commit_sha(sha)
        or evidence.subject_tree_sha != canonical_commit_sha(expected_tree_sha)
        or evidence.candidate_manifest_sha256 != expected_manifest_sha256
        or candidate_manifest_sha256(manifest) != expected_manifest_sha256
        or evidence.producer_job_id != identity["workflow_job_id"]
        or evidence.producer_check_run_id != identity["check_run_id"]
    ):
        raise ValueError("discovery_internal_binding_mismatch")
    binding = DiscoveryArtifactBindingV1(
        repository=BASELINE_REPOSITORY,
        repository_id=BASELINE_REPOSITORY_ID,
        workflow_run_id=run_id,
        workflow_run_attempt="1",
        subject_commit_sha=sha,
        artifact_id=artifact_id,
        artifact_name=artifact_name,
        artifact_digest=digest,
        artifact_size_in_bytes=size,
        artifact_content_verified=True,
        github_artifact_origin_verified=True,
    )
    return binding, evidence, manifest


def _verify_root_commit(client: httpx.Client, sha: str, tree_sha: str) -> None:
    commit = api_json(client, f"/repos/{BASELINE_REPOSITORY}/git/commits/{sha}")
    tree = commit.get("tree") if isinstance(commit, dict) else None
    if (
        not isinstance(commit, dict)
        or commit.get("sha") != sha
        or commit.get("parents") != []
        or not isinstance(tree, dict)
        or canonical_commit_sha(tree.get("sha")) != tree_sha
    ):
        raise ValueError("root_commit_contract_invalid")
    ref = api_json(client, f"/repos/{BASELINE_REPOSITORY}/git/ref/heads/{BASELINE_BRANCH}")
    obj = ref.get("object") if isinstance(ref, dict) else None
    if not isinstance(obj, dict) or canonical_commit_sha(obj.get("sha")) != sha:
        raise ValueError("remote_main_not_root_commit")
    branches = api_json(client, f"/repos/{BASELINE_REPOSITORY}/branches?per_page=100")
    if not isinstance(branches, list) or [item.get("name") for item in branches] != [
        BASELINE_BRANCH
    ]:
        raise ValueError("remote_branch_set_invalid")
    tags = api_json(client, f"/repos/{BASELINE_REPOSITORY}/git/matching-refs/tags/")
    if tags != []:
        raise ValueError("remote_tags_not_empty")


def _verify_approval(client: httpx.Client, run_id: str, expected_id: str) -> None:
    environment = api_json(
        client, f"/repos/{BASELINE_REPOSITORY}/environments/{BASELINE_ENVIRONMENT}"
    )
    if not isinstance(environment, dict) or canonical_github_id(environment.get("id")) != expected_id:
        raise ValueError("baseline_environment_identity_invalid")
    rules = environment.get("protection_rules")
    if not isinstance(rules, list):
        raise ValueError("baseline_environment_rules_invalid")
    reviewer_rules = [rule for rule in rules if isinstance(rule, dict) and rule.get("type") == "required_reviewers"]
    branch_rules = [rule for rule in rules if isinstance(rule, dict) and rule.get("type") == "branch_policy"]
    if len(reviewer_rules) != 1 or len(branch_rules) != 1 or len(rules) != 2:
        raise ValueError("baseline_environment_rules_invalid")
    reviewer_rule = reviewer_rules[0]
    reviewers = reviewer_rule.get("reviewers")
    reviewer_logins = {
        item.get("reviewer", {}).get("login")
        for item in reviewers or ()
        if isinstance(item, dict) and isinstance(item.get("reviewer"), dict)
    }
    branch = environment.get("deployment_branch_policy")
    if (
        reviewer_logins != {BASELINE_REVIEWER}
        or reviewer_rule.get("prevent_self_review") is not True
        or not isinstance(branch, dict)
        or branch.get("protected_branches") is not True
        or branch.get("custom_branch_policies") is not False
    ):
        raise ValueError("baseline_environment_protection_invalid")
    custom = api_json(
        client,
        f"/repos/{BASELINE_REPOSITORY}/environments/{BASELINE_ENVIRONMENT}"
        "/deployment_protection_rules",
    )
    if not isinstance(custom, dict) or custom.get("total_count") != 0:
        raise ValueError("baseline_custom_protection_rule_not_allowed")
    approvals = api_json(client, f"/repos/{BASELINE_REPOSITORY}/actions/runs/{run_id}/approvals")
    matches = []
    for approval in approvals if isinstance(approvals, list) else ():
        user = approval.get("user") if isinstance(approval, dict) else None
        environments = approval.get("environments") if isinstance(approval, dict) else None
        if (
            approval.get("state") == "approved"
            and isinstance(user, dict)
            and user.get("login") == BASELINE_REVIEWER
            and any(
                isinstance(item, dict)
                and canonical_github_id(item.get("id")) == expected_id
                and item.get("name") == BASELINE_ENVIRONMENT
                for item in environments or ()
            )
        ):
            matches.append(approval)
    run = api_json(client, f"/repos/{BASELINE_REPOSITORY}/actions/runs/{run_id}")
    actor = run.get("actor") if isinstance(run, dict) else None
    if (
        not matches
        or not isinstance(actor, dict)
        or actor.get("login") != BASELINE_TRIGGER_ACTOR
        or actor.get("login") == BASELINE_REVIEWER
    ):
        raise ValueError("baseline_approval_event_missing")


def verify_baseline_online(
    client: httpx.Client,
    *,
    artifact_id: str,
    artifact_name: str,
    run_id: str,
    sha: str,
    downloader=download_archive,
) -> dict[str, Any]:
    metadata = artifact_metadata(
        client, artifact_id=artifact_id, expected_name=artifact_name, run_id=run_id, sha=sha
    )
    run = workflow_run(
        client, run_id=run_id, sha=sha, workflow_path=FORMAL_WORKFLOW_PATH,
        require_completed=True,
    )
    jobs = attempt_jobs(client, run_id)
    identities = verify_success_jobs(jobs, BASELINE_JOBS, run_id=run_id, sha=sha)
    archive = downloader(
        client, repository=BASELINE_REPOSITORY, artifact_id=artifact_id,
        expected_size=metadata["size_in_bytes"], expected_digest=metadata["digest"][7:],
    )
    files = safe_extract(archive, BASELINE_FILES)
    document = BaselineArtifactDocumentV1.model_validate_json(
        files["phase14-baseline-closeout.json"].decode("utf-8")
    )
    manifest = CandidateManifestV1.model_validate_json(
        files["phase14-candidate-manifest.json"].decode("utf-8")
    )
    payload = document.closeout_payload
    formal_input = document.formal_input_manifest
    if (
        payload.workflow_run_id != canonical_github_id(run_id)
        or payload.subject_commit_sha != canonical_commit_sha(sha)
        or payload.candidate_manifest_sha256 != candidate_manifest_sha256(manifest)
        or payload.baseline_closeout_job_id != identities["baseline-closeout"]["workflow_job_id"]
        or payload.baseline_closeout_check_run_id != identities["baseline-closeout"]["check_run_id"]
        or formal_input.candidate_manifest_sha256 != payload.candidate_manifest_sha256
        or formal_input.discovery_run_id != payload.discovery_binding.workflow_run_id
        or formal_input.discovery_run_attempt != "1"
        or formal_input.discovery_artifact_id != payload.discovery_binding.artifact_id
        or formal_input.discovery_artifact_name != payload.discovery_binding.artifact_name
        or formal_input.discovery_artifact_digest != payload.discovery_binding.artifact_digest
    ):
        raise ValueError("baseline_internal_binding_mismatch")
    payload_identities = {item.job_name: item for item in payload.required_jobs}
    for name, identity in identities.items():
        expected = payload_identities[name]
        if (
            expected.workflow_job_id != identity["workflow_job_id"]
            or expected.check_run_id != identity["check_run_id"]
        ):
            raise ValueError(f"baseline_job_identity_mismatch:{name}")
    _verify_root_commit(client, payload.subject_commit_sha, payload.subject_tree_sha)
    _verify_approval(
        client, run_id, payload.approval_attestation_summary.approval_environment_id
    )
    binding, discovery, discovered_manifest = verify_discovery(
        client,
        artifact_id=payload.discovery_binding.artifact_id,
        artifact_name=payload.discovery_binding.artifact_name,
        run_id=payload.discovery_binding.workflow_run_id,
        sha=payload.discovery_binding.subject_commit_sha,
        expected_tree_sha=payload.subject_tree_sha,
        expected_manifest_sha256=payload.candidate_manifest_sha256,
        expected_artifact_digest=formal_input.discovery_artifact_digest,
        expected_workflow_id=formal_input.discovery_workflow_id,
        downloader=downloader,
    )
    if binding != payload.discovery_binding or discovered_manifest != manifest:
        raise ValueError("formal_discovery_binding_mismatch")
    return {
        "phase14_g_repository_baseline_status": "pass",
        "phase14_implementation_status": "pass",
        "phase14_incident_closure_status": "blocked",
        "phase14_authoritative_phase_status": "blocked",
        "database_revision": "0001",
        "formal_artifact_id": canonical_github_id(artifact_id),
        "formal_artifact_digest": metadata["digest"],
        "formal_artifact_size": metadata["size_in_bytes"],
        "artifact_content_valid": True,
        "github_artifact_origin_verified": True,
        "formal_artifact_provenance": "valid",
        "authoritative_baseline_verified": True,
        "discovery_status": discovery.discovery_status,
    }


def verify_offline(path: Path) -> dict[str, Any]:
    if not path.is_dir():
        raise ValueError("baseline_artifact_directory_missing")
    entries = list(path.iterdir())
    if {entry.name for entry in entries} != BASELINE_FILES or any(
        entry.is_symlink() or not entry.is_file() or entry.name.startswith(".")
        for entry in entries
    ):
        raise ValueError("baseline_artifact_file_set_mismatch")
    document = BaselineArtifactDocumentV1.model_validate_json(
        (path / "phase14-baseline-closeout.json").read_text(encoding="utf-8")
    )
    manifest = CandidateManifestV1.model_validate_json(
        (path / "phase14-candidate-manifest.json").read_text(encoding="utf-8")
    )
    if document.closeout_payload.candidate_manifest_sha256 != candidate_manifest_sha256(manifest):
        raise ValueError("baseline_candidate_manifest_hash_mismatch")
    return {
        "artifact_content_valid": True,
        "github_artifact_origin_verified": False,
        "authoritative_baseline_verified": False,
    }


def github_client() -> httpx.Client:
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise RuntimeError("missing_gh_token")
    return httpx.Client(
        base_url="https://api.github.com",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=15.0,
        follow_redirects=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "discovery-binding", "online"), required=True)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--artifact-id")
    parser.add_argument("--artifact-name")
    parser.add_argument("--workflow-run-id")
    parser.add_argument("--subject-commit-sha")
    parser.add_argument("--expected-tree-sha")
    parser.add_argument("--candidate-manifest-sha256")
    parser.add_argument("--expected-artifact-digest")
    parser.add_argument("--expected-workflow-id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "offline":
        if args.artifact_dir is None:
            raise RuntimeError("artifact_dir_required")
        result = verify_offline(args.artifact_dir)
    else:
        required_values = (
            args.artifact_id, args.artifact_name, args.workflow_run_id,
            args.subject_commit_sha,
        )
        if not all(required_values):
            raise RuntimeError("online_verifier_input_missing")
        with github_client() as client:
            if args.mode == "discovery-binding":
                if not args.expected_tree_sha or not args.candidate_manifest_sha256:
                    raise RuntimeError("discovery_binding_input_missing")
                binding, _, _ = verify_discovery(
                    client,
                    artifact_id=args.artifact_id,
                    artifact_name=args.artifact_name,
                    run_id=args.workflow_run_id,
                    sha=args.subject_commit_sha,
                    expected_tree_sha=args.expected_tree_sha,
                    expected_manifest_sha256=args.candidate_manifest_sha256,
                    expected_artifact_digest=args.expected_artifact_digest,
                    expected_workflow_id=args.expected_workflow_id,
                )
                result = binding.model_dump(mode="json")
            else:
                result = verify_baseline_online(
                    client,
                    artifact_id=args.artifact_id,
                    artifact_name=args.artifact_name,
                    run_id=args.workflow_run_id,
                    sha=args.subject_commit_sha,
                )
    write_json(args.output, result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
