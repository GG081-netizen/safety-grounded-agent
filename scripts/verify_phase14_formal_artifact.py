"""Verify a Phase 14 formal artifact online or diagnose its contents offline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx

from conversation_agent.evaluation.phase14_artifact import verify_offline, verify_online


def main() -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--artifact-dir", type=Path)
    modes.add_argument("--artifact-id")
    parser.add_argument("--repository")
    parser.add_argument("--repository-id")
    parser.add_argument("--expected-artifact-name")
    parser.add_argument("--workflow-run-id")
    parser.add_argument("--workflow-run-attempt", default="1")
    parser.add_argument("--subject-commit-sha")
    parser.add_argument("--expected-protected-ref")
    parser.add_argument("--expected-environment-name")
    parser.add_argument("--expected-workflow-path")
    parser.add_argument("--expected-workflow-id")
    args = parser.parse_args()
    if args.artifact_dir is not None:
        result = verify_offline(args.artifact_dir)
    else:
        required_values = {
            "repository": args.repository,
            "repository_id": args.repository_id,
            "expected_artifact_name": args.expected_artifact_name,
            "workflow_run_id": args.workflow_run_id,
            "subject_commit_sha": args.subject_commit_sha,
            "expected_protected_ref": args.expected_protected_ref,
            "expected_environment_name": args.expected_environment_name,
            "expected_workflow_path": args.expected_workflow_path,
        }
        if any(not value for value in required_values.values()) or args.workflow_run_attempt != "1":
            raise RuntimeError("online_artifact_verification_arguments_invalid")
        token = os.environ.get("GH_TOKEN", "")
        if not token:
            raise RuntimeError("gh_token_missing")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        with httpx.Client(
            base_url="https://api.github.com",
            headers=headers,
            timeout=10.0,
            follow_redirects=False,
        ) as client:
            result = verify_online(
                client,
                artifact_id=args.artifact_id,
                expected_workflow_id=args.expected_workflow_id,
                **required_values,
            )
    print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
