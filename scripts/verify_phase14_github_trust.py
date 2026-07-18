"""Collect fail-closed GitHub.com runtime identity for Phase 14-F."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from conversation_agent.evaluation.phase14_evidence import (
    GITHUB_COM,
    REQUIRED_PHASE14_JOBS,
    approval_reference,
    bind_current_job,
    canonical_commit_sha,
    canonical_github_id,
    validate_approval_history,
    validate_environment_response,
)

MAX_WORKFLOW_JOBS = 100
EXPECTED_WORKFLOW_PATH = ".github/workflows/ci.yml"


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"missing_github_runtime_value:{name}")
    return value


def get_json(client: httpx.Client, path: str) -> object:
    response = client.get(path)
    if response.status_code != 200:
        raise RuntimeError(f"github_api_unavailable:{response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("github_api_invalid_json") from exc


def get_all_attempt_jobs(
    client: httpx.Client,
    *,
    repository: str,
    run_id: str,
    attempt: str,
) -> dict[str, object]:
    jobs: list[dict[str, object]] = []
    total_count: int | None = None
    page = 1
    while total_count is None or len(jobs) < total_count:
        payload = get_json(
            client,
            f"/repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs"
            f"?per_page=100&page={page}",
        )
        if not isinstance(payload, dict) or type(payload.get("total_count")) is not int:
            raise RuntimeError("invalid_workflow_jobs_response")
        page_jobs = payload.get("jobs")
        if not isinstance(page_jobs, list) or not all(
            isinstance(item, dict) for item in page_jobs
        ):
            raise RuntimeError("invalid_workflow_jobs_response")
        if total_count is None:
            total_count = payload["total_count"]
            if total_count < 1 or total_count > MAX_WORKFLOW_JOBS:
                raise RuntimeError("workflow_job_count_out_of_bounds")
        elif payload["total_count"] != total_count:
            raise RuntimeError("workflow_job_total_changed_during_pagination")
        jobs.extend(page_jobs)
        if len(jobs) > total_count or not page_jobs:
            raise RuntimeError("workflow_job_pagination_incomplete")
        page += 1
    if len(jobs) != total_count:
        raise RuntimeError("workflow_job_total_mismatch")
    ids = [canonical_github_id(item.get("id")) for item in jobs]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate_workflow_job_id")
    names = [item.get("name") for item in jobs]
    if len(names) != len(REQUIRED_PHASE14_JOBS) or set(names) != set(
        REQUIRED_PHASE14_JOBS
    ):
        raise RuntimeError("workflow_job_allowlist_mismatch")
    return {"total_count": total_count, "jobs": jobs}


def _job_check_run_id(job: dict[str, object]) -> str:
    value = job.get("check_run_url")
    if not isinstance(value, str):
        raise RuntimeError("workflow_job_check_run_url_missing")
    return canonical_github_id(value.rstrip("/").rsplit("/", 1)[-1])


def verify_completed_jobs(
    jobs_payload: object,
    names: tuple[str, ...],
    *,
    workflow_run_id: str,
    subject_commit_sha: str,
) -> tuple[dict[str, str], ...]:
    if not isinstance(jobs_payload, dict) or not isinstance(
        jobs_payload.get("jobs"), list
    ):
        raise RuntimeError("invalid_workflow_jobs_response")
    identities: list[dict[str, str]] = []
    for name in names:
        matches = [
            job
            for job in jobs_payload["jobs"]
            if isinstance(job, dict) and job.get("name") == name
        ]
        if len(matches) != 1:
            raise RuntimeError(f"required_job_not_unique:{name}")
        job = matches[0]
        if canonical_github_id(job.get("run_id")) != workflow_run_id:
            raise RuntimeError(f"required_job_run_mismatch:{name}")
        if canonical_commit_sha(job.get("head_sha")) != subject_commit_sha:
            raise RuntimeError(f"required_job_commit_mismatch:{name}")
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            raise RuntimeError(f"required_job_not_successful:{name}")
        identities.append(
            {
                "job_name": name,
                "workflow_job_id": canonical_github_id(job.get("id")),
                "check_run_id": _job_check_run_id(job),
            }
        )
    return tuple(identities)


def validate_runtime_workflow(
    client: httpx.Client,
    *,
    repository: str,
    run_id: str,
    repository_id: str,
    subject_commit_sha: str,
    protected_ref: str,
    expected_workflow_path: str,
) -> dict[str, object]:
    run = get_json(client, f"/repos/{repository}/actions/runs/{run_id}")
    if not isinstance(run, dict):
        raise RuntimeError("invalid_workflow_run_response")
    if canonical_github_id(run.get("id")) != run_id:
        raise RuntimeError("workflow_run_id_mismatch")
    for key in ("repository", "head_repository"):
        value = run.get(key)
        if not isinstance(value, dict) or canonical_github_id(value.get("id")) != repository_id:
            raise RuntimeError(f"workflow_run_{key}_mismatch")
    if run.get("event") != "workflow_dispatch" or canonical_github_id(
        run.get("run_attempt")
    ) != "1":
        raise RuntimeError("workflow_run_not_fresh_dispatch")
    if canonical_commit_sha(run.get("head_sha")) != subject_commit_sha:
        raise RuntimeError("workflow_run_commit_mismatch")
    if not protected_ref.startswith("refs/heads/") or run.get("head_branch") != protected_ref[11:]:
        raise RuntimeError("workflow_run_branch_mismatch")
    if run.get("status") not in {"queued", "in_progress"} or run.get("conclusion") is not None:
        raise RuntimeError("workflow_run_illegal_runtime_state")
    workflow_id = canonical_github_id(run.get("workflow_id"))
    workflow = get_json(client, f"/repos/{repository}/actions/workflows/{workflow_id}")
    if not isinstance(workflow, dict):
        raise RuntimeError("invalid_workflow_response")
    if canonical_github_id(workflow.get("id")) != workflow_id:
        raise RuntimeError("workflow_id_mismatch")
    if workflow.get("path") != expected_workflow_path or workflow.get("state") != "active":
        raise RuntimeError("workflow_definition_not_approved")
    run_path = run.get("path")
    if run_path not in {expected_workflow_path, f"{expected_workflow_path}@{protected_ref}"}:
        raise RuntimeError("workflow_run_path_mismatch")
    return {
        "workflow_id": workflow_id,
        "workflow_path": expected_workflow_path,
        "workflow_state_at_closeout": "active",
        "workflow_state_verified_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("producer", "incident", "formal"), required=True)
    parser.add_argument("--producer-job-name", choices=REQUIRED_PHASE14_JOBS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if required("GITHUB_SERVER_URL") != GITHUB_COM:
        raise RuntimeError("unsupported_github_platform")
    if required("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise RuntimeError("formal_closeout_requires_workflow_dispatch")
    if canonical_github_id(required("GITHUB_RUN_ATTEMPT")) != "1":
        raise RuntimeError("formal_closeout_requires_fresh_run")

    repository = required("GITHUB_REPOSITORY")
    repository_id = canonical_github_id(required("GITHUB_REPOSITORY_ID"))
    run_id = canonical_github_id(required("GITHUB_RUN_ID"))
    sha = canonical_commit_sha(required("GITHUB_SHA"))
    check_run_id = canonical_github_id(required("PHASE14_CURRENT_CHECK_RUN_ID"))
    expected_job_name = args.producer_job_name or (
        "incident-closure" if args.mode == "incident" else "formal-closeout"
    )
    api_url = required("GITHUB_API_URL")
    token = required("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(base_url=api_url, headers=headers, timeout=10.0) as client:
        jobs = get_all_attempt_jobs(
            client,
            repository=repository,
            run_id=run_id,
            attempt="1",
        )
        job_id, normalized_check_run = bind_current_job(
            jobs,
            check_run_id=check_run_id,
            subject_commit_sha=sha,
            expected_name=expected_job_name,
            workflow_run_id=run_id,
        )
        completed_names = {
            "producer": (),
            "incident": REQUIRED_PHASE14_JOBS[:4],
            "formal": REQUIRED_PHASE14_JOBS[:5],
        }[args.mode]
        completed = verify_completed_jobs(
            jobs,
            completed_names,
            workflow_run_id=run_id,
            subject_commit_sha=sha,
        )
        payload: dict[str, object] = {
            "github_server_url": GITHUB_COM,
            "fresh_workflow_dispatch": True,
            "repository_id": repository_id,
            "workflow_run_id": run_id,
            "workflow_run_attempt": "1",
            "subject_commit_sha": sha,
            "current_workflow_job_id": job_id,
            "current_check_run_id": normalized_check_run,
            "current_job_runtime_state_verified": True,
            "completed_job_identities": completed,
        }
        if args.mode == "formal":
            protected_ref = required("PHASE14_PROTECTED_REF")
            payload.update(
                validate_runtime_workflow(
                    client,
                    repository=repository,
                    run_id=run_id,
                    repository_id=repository_id,
                    subject_commit_sha=sha,
                    protected_ref=protected_ref,
                    expected_workflow_path=EXPECTED_WORKFLOW_PATH,
                )
            )
        if args.mode == "incident":
            if required("GITHUB_REF_TYPE") != "branch" or required(
                "GITHUB_REF_PROTECTED"
            ) != "true":
                raise RuntimeError("unprotected_github_ref")
            protected_ref = required("PHASE14_PROTECTED_REF")
            if required("GITHUB_REF") != protected_ref:
                raise RuntimeError("protected_ref_mismatch")
            environment_name = required("PHASE14_ENVIRONMENT_NAME")
            environment = get_json(
                client,
                f"/repos/{repository}/environments/{quote(environment_name, safe='')}",
            )
            if not isinstance(environment, dict):
                raise RuntimeError("invalid_environment_response")
            environment_id, environment_name = validate_environment_response(
                environment, expected_name=environment_name
            )
            custom = get_json(
                client,
                f"/repos/{repository}/environments/{quote(environment_name, safe='')}"
                "/deployment_protection_rules",
            )
            if not isinstance(custom, dict) or custom.get("total_count") != 0:
                raise RuntimeError("custom_deployment_protection_rule_not_allowed")
            approvals = get_json(client, f"/repos/{repository}/actions/runs/{run_id}/approvals")
            approval_count = validate_approval_history(
                approvals,
                environment_id=environment_id,
                environment_name=environment_name,
            )
            deployment = (
                f"github-environment:{environment_id}:workflow-job:{job_id}"
                f":check-run:{normalized_check_run}"
            )
            payload.update(
                environment_protection_valid=True,
                environment_id=environment_id,
                environment_name=environment_name,
                approval_event_verified=True,
                approval_event_count=approval_count,
                approval_environment_id=environment_id,
                deployment_identifier=deployment,
                approval_reference=approval_reference(
                    repository_id=repository_id,
                    workflow_run_id=run_id,
                    workflow_run_attempt="1",
                    environment_name=environment_name,
                    deployment_identifier=deployment,
                ),
                protected_ref=protected_ref,
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"github_trust_mode={args.mode}")
    print("github_trust_status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
