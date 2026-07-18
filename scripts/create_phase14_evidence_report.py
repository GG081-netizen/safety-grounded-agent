"""Wrap a producer payload in a provenance-bound Phase 14 EvidenceEnvelope."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from conversation_agent.evaluation.phase14_evidence import GITHUB_COM, create_evidence_envelope


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"missing_github_runtime_value:{name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-type", required=True)
    parser.add_argument("--producer-job-name", required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--github-trust", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if required("GITHUB_SERVER_URL") != GITHUB_COM:
        raise RuntimeError("unsupported_github_platform")
    if required("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise RuntimeError("evidence_requires_workflow_dispatch")
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("evidence_payload_must_be_object")
    trust = json.loads(args.github_trust.read_text(encoding="utf-8"))
    if not isinstance(trust, dict) or trust.get("current_job_runtime_state_verified") is not True:
        raise RuntimeError("producer_runtime_identity_unverified")
    envelope = create_evidence_envelope(
        report_type=args.report_type,
        subject_commit_sha=required("GITHUB_SHA"),
        repository_id=required("GITHUB_REPOSITORY_ID"),
        workflow_run_id=required("GITHUB_RUN_ID"),
        workflow_run_attempt=required("GITHUB_RUN_ATTEMPT"),
        producer_job_name=args.producer_job_name,
        producer_workflow_job_id=trust.get("current_workflow_job_id"),
        producer_check_run_id=trust.get("current_check_run_id"),
        generated_at=datetime.now(timezone.utc),
        payload=payload,
    )
    if envelope.workflow_run_attempt != "1":
        raise RuntimeError("evidence_requires_fresh_run")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(envelope.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"evidence_report_type={envelope.report_type}")
    print("evidence_envelope_status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
