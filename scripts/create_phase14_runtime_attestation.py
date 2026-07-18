"""Create Runtime Attestation V2 from protected inputs and verified GitHub evidence."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from conversation_agent.evaluation.phase14_evidence import RuntimeAttestationV2


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"missing_protected_input:{name}")
    return value


def required_true(name: str) -> bool:
    value = required(name)
    if value != "true":
        raise RuntimeError(f"protected_input_not_verified:{name}")
    return True


def main() -> int:
    output = Path(required("CONVAGENT_PHASE14_ATTESTATION_PATH"))
    trust_path = Path(required("CONVAGENT_PHASE14_GITHUB_EVIDENCE_PATH"))
    trust = json.loads(trust_path.read_text(encoding="utf-8"))
    attestation = RuntimeAttestationV2(
        provider="dashscope",
        credential_fingerprint_prefix=required("PHASE14_CREDENTIAL_FINGERPRINT_PREFIX"),
        credential_revocation_verified=required_true(
            "PHASE14_CREDENTIAL_REVOCATION_VERIFIED"
        ),
        credential_rotation_verified=required_true(
            "PHASE14_CREDENTIAL_ROTATION_VERIFIED"
        ),
        provider_usage_review_status=required("PHASE14_PROVIDER_USAGE_REVIEW_STATUS"),
        discovered_at=required("PHASE14_DISCOVERED_AT"),
        revocation_verified_at=required("PHASE14_REVOCATION_VERIFIED_AT"),
        rotation_verified_at=required("PHASE14_ROTATION_VERIFIED_AT"),
        provider_usage_reviewed_at=required("PHASE14_PROVIDER_USAGE_REVIEWED_AT"),
        attestation_generated_at=datetime.now(timezone.utc),
        attestation_source="protected_environment",
        subject_commit_sha=trust["subject_commit_sha"],
        protected_ref=trust["protected_ref"],
        environment_name=trust["environment_name"],
        environment_id=trust["environment_id"],
        deployment_identifier=trust["deployment_identifier"],
        verified_by_role=required("PHASE14_VERIFIED_BY_ROLE"),
        approval_event_verified=trust["approval_event_verified"],
        approval_event_count=trust["approval_event_count"],
        approval_environment_id=trust["approval_environment_id"],
        approval_reference=trust["approval_reference"],
    )
    attestation.validate_runtime(
        now=datetime.now(timezone.utc),
        subject_commit_sha=trust["subject_commit_sha"],
        protected_ref=trust["protected_ref"],
        environment_name=trust["environment_name"],
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(attestation.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print("runtime_attestation_schema=phase14_incident_attestation_v2")
    print("runtime_attestation_status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
