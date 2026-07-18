"""Build and revalidate non-authoritative and authoritative Phase 14 artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from conversation_agent.evaluation.phase14_evidence import (
    EvidenceReference,
    EvidenceEnvelope,
    FormalCloseoutPayloadV1,
    FormalInputManifestV1,
    RequiredJobIdentity,
    RuntimeAttestationV2,
    GitleaksRepositoryScanResult,
    create_evidence_envelope,
    payload_sha256,
    verify_evidence_provenance,
)


REPORTS = (
    ("test", "test-report"),
    ("secret-scan", "gitleaks-report"),
    ("postgres-integration", "postgres-report"),
    ("operational-postgres", "operational-report"),
)


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"missing_github_runtime_value:{name}")
    return value


def context() -> dict[str, str]:
    return {
        "subject_commit_sha": required("GITHUB_SHA"),
        "repository_id": required("GITHUB_REPOSITORY_ID"),
        "workflow_run_id": required("GITHUB_RUN_ID"),
        "workflow_run_attempt": required("GITHUB_RUN_ATTEMPT"),
    }


def load_envelope(path: Path, *, report_type: str, producer: str) -> EvidenceEnvelope:
    envelope = EvidenceEnvelope.model_validate_json(path.read_text(encoding="utf-8"))
    verify_evidence_provenance(
        envelope,
        report_type=report_type,
        producer_job_name=producer,
        now=datetime.now(timezone.utc),
        **context(),
    )
    return envelope


def validate_producers(paths: dict[str, Path]) -> dict[str, EvidenceEnvelope]:
    envelopes = {
        producer: load_envelope(paths[producer], report_type=report_type, producer=producer)
        for producer, report_type in REPORTS
    }
    for producer, envelope in envelopes.items():
        payload = envelope.payload
        if producer == "secret-scan":
            required_gates = (
                "gitleaks_checksum_valid",
                "gitleaks_version_valid",
                "gitleaks_builtin_canary_detected",
                "gitleaks_custom_canary_detected",
            )
            if any(payload.get(gate) is not True for gate in required_gates):
                raise RuntimeError("gitleaks_runtime_gate_failed")
            try:
                scan_result = GitleaksRepositoryScanResult.model_validate(payload)
            except ValidationError as exc:
                raise RuntimeError("gitleaks_evidence_inconsistent") from exc
            if not scan_result.gitleaks_real_repository_scan_passed:
                raise RuntimeError("gitleaks_all_refs_scan_failed")
        elif payload.get("status") != "pass":
            raise RuntimeError(f"producer_payload_failed:{producer}")
    if envelopes["postgres-integration"].payload.get("database_revision") != "0001":
        raise RuntimeError("postgres_revision_mismatch")
    if envelopes["operational-postgres"].payload.get("database_revision") != "0001":
        raise RuntimeError("operational_revision_mismatch")
    return envelopes


def load_trust(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("current_job_runtime_state_verified") is not True:
        raise RuntimeError("github_trust_evidence_invalid")
    return payload


def completed_identity_map(trust: dict[str, object]) -> dict[str, RequiredJobIdentity]:
    values = trust.get("completed_job_identities")
    if not isinstance(values, list):
        raise RuntimeError("completed_job_identities_missing")
    identities = [RequiredJobIdentity.model_validate(item) for item in values]
    if len({item.job_name for item in identities}) != len(identities):
        raise RuntimeError("completed_job_identity_duplicate")
    return {item.job_name: item for item in identities}


def verify_producer_job_identities(
    envelopes: dict[str, EvidenceEnvelope], trust: dict[str, object]
) -> None:
    identities = completed_identity_map(trust)
    for producer, envelope in envelopes.items():
        identity = identities.get(producer)
        if identity is None or (
            envelope.producer_workflow_job_id != identity.workflow_job_id
            or envelope.producer_check_run_id != identity.check_run_id
        ):
            raise RuntimeError(f"producer_job_identity_mismatch:{producer}")


def load_attestation(path: Path) -> RuntimeAttestationV2:
    attestation = RuntimeAttestationV2.model_validate_json(path.read_text(encoding="utf-8"))
    attestation.validate_runtime(
        now=datetime.now(timezone.utc),
        subject_commit_sha=required("GITHUB_SHA"),
        protected_ref=required("GITHUB_REF"),
        environment_name=required("CONVAGENT_PHASE14_ENVIRONMENT_NAME"),
    )
    return attestation


def write_envelope(
    output: Path,
    *,
    report_type: str,
    producer: str,
    producer_workflow_job_id: str,
    producer_check_run_id: str,
    payload: dict[str, object],
) -> EvidenceEnvelope:
    envelope = create_evidence_envelope(
        report_type=report_type,
        producer_job_name=producer,
        producer_workflow_job_id=producer_workflow_job_id,
        producer_check_run_id=producer_check_run_id,
        generated_at=datetime.now(timezone.utc),
        payload=payload,
        **context(),
    )
    reparsed = EvidenceEnvelope.model_validate_json(envelope.model_dump_json())
    if reparsed != envelope:
        raise RuntimeError("evidence_round_trip_mismatch")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(envelope.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return envelope


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("incident", "prepare-formal", "finalize"), required=True)
    parser.add_argument("--test", type=Path)
    parser.add_argument("--gitleaks", type=Path)
    parser.add_argument("--postgres", type=Path)
    parser.add_argument("--operational", type=Path)
    parser.add_argument("--incident", type=Path)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--github-trust", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--attestation-output", type=Path)
    return parser.parse_args()


def producer_paths(args: argparse.Namespace) -> dict[str, Path]:
    values = {
        "test": args.test,
        "secret-scan": args.gitleaks,
        "postgres-integration": args.postgres,
        "operational-postgres": args.operational,
    }
    if any(value is None for value in values.values()):
        raise RuntimeError("producer_evidence_missing")
    return values  # type: ignore[return-value]


def main() -> int:
    args = parse_args()
    if args.mode == "incident":
        producers = validate_producers(producer_paths(args))
        attestation = load_attestation(args.attestation)
        if args.github_trust is None:
            raise RuntimeError("github_trust_evidence_missing")
        trust = load_trust(args.github_trust)
        verify_producer_job_identities(producers, trust)
        write_envelope(
            args.output,
            report_type="incident-evidence",
            producer="incident-closure",
            producer_workflow_job_id=str(trust["current_workflow_job_id"]),
            producer_check_run_id=str(trust["current_check_run_id"]),
            payload={
                "authoritative": False,
                "incident_evidence_status": "pass",
                "phase_candidate_status": "pass",
                "evidence_revalidation_passed": True,
                "runtime_attestation_valid": True,
                "runtime_attestation_payload_sha256": payload_sha256(
                    attestation.model_dump(mode="json")
                ),
                "approval_reference": attestation.approval_reference,
                "environment_id": attestation.environment_id,
                "approval_event_verified": attestation.approval_event_verified,
                "environment_protection_valid": True,
                "database_revision": "0001",
                "producer_payload_sha256": {
                    name: envelope.payload_sha256 for name, envelope in producers.items()
                },
            },
        )
    elif args.mode == "prepare-formal":
        producers = validate_producers(producer_paths(args))
        attestation = load_attestation(args.attestation)
        if args.incident is None or args.github_trust is None:
            raise RuntimeError("formal_evidence_missing")
        incident = load_envelope(
            args.incident,
            report_type="incident-evidence",
            producer="incident-closure",
        )
        trust = load_trust(args.github_trust)
        verify_producer_job_identities(producers, trust)
        completed = completed_identity_map(trust)
        incident_identity = completed.get("incident-closure")
        if incident_identity is None or (
            incident.producer_workflow_job_id != incident_identity.workflow_job_id
            or incident.producer_check_run_id != incident_identity.check_run_id
        ):
            raise RuntimeError("incident_job_identity_mismatch")
        if incident.payload.get("authoritative") is not False:
            raise RuntimeError("incident_evidence_claimed_authority")
        if incident.payload.get("incident_evidence_status") != "pass":
            raise RuntimeError("incident_evidence_not_passed")
        if incident.payload.get("runtime_attestation_payload_sha256") != payload_sha256(
            attestation.model_dump(mode="json")
        ):
            raise RuntimeError("incident_attestation_binding_mismatch")
        if incident.payload.get("approval_reference") != attestation.approval_reference:
            raise RuntimeError("incident_approval_binding_mismatch")
        expected_hashes = {
            name: envelope.payload_sha256 for name, envelope in producers.items()
        }
        if incident.payload.get("producer_payload_sha256") != expected_hashes:
            raise RuntimeError("incident_producer_binding_mismatch")
        references = tuple(
            EvidenceReference.from_envelope(envelopes)
            for _, envelopes in sorted(producers.items())
        )
        manifest = FormalInputManifestV1(
            producer_evidence=references,
            incident_evidence=EvidenceReference.from_envelope(incident),
            runtime_attestation_payload_sha256=payload_sha256(
                attestation.model_dump(mode="json")
            ),
            repository_gitleaks_evidence=EvidenceReference.from_envelope(
                producers["secret-scan"]
            ),
            database_revision="0001",
        )
        write_envelope(
            args.output,
            report_type="formal-input-manifest",
            producer="formal-closeout",
            producer_workflow_job_id=str(trust["current_workflow_job_id"]),
            producer_check_run_id=str(trust["current_check_run_id"]),
            payload=manifest.model_dump(mode="json"),
        )
    else:
        if args.manifest is None:
            raise RuntimeError("formal_manifest_missing")
        manifest_envelope = load_envelope(
            args.manifest,
            report_type="formal-input-manifest",
            producer="formal-closeout",
        )
        manifest = FormalInputManifestV1.model_validate(manifest_envelope.payload)
        attestation = load_attestation(args.attestation)
        if manifest.runtime_attestation_payload_sha256 != payload_sha256(
            attestation.model_dump(mode="json")
        ):
            raise RuntimeError("formal_manifest_payload_mismatch")
        if args.github_trust is None:
            raise RuntimeError("formal_github_trust_missing")
        trust = load_trust(args.github_trust)
        if (
            manifest_envelope.producer_workflow_job_id
            != str(trust["current_workflow_job_id"])
            or manifest_envelope.producer_check_run_id
            != str(trust["current_check_run_id"])
        ):
            raise RuntimeError("formal_manifest_job_identity_mismatch")
        completed = completed_identity_map(trust)
        required_jobs = tuple(completed.values()) + (
            RequiredJobIdentity(
                job_name="formal-closeout",
                workflow_job_id=str(trust["current_workflow_job_id"]),
                check_run_id=str(trust["current_check_run_id"]),
            ),
        )
        payload = FormalCloseoutPayloadV1(
            authoritative=True,
            authoritative_resolution_source="formal-closeout",
            implementation_status="pass",
            incident_closure_status="pass",
            phase_status="pass",
            database_revision="0001",
            runtime_attestation_valid=True,
            evidence_revalidation_passed=True,
            workflow_id=str(trust["workflow_id"]),
            workflow_path=str(trust["workflow_path"]),
            workflow_state_at_closeout=str(trust["workflow_state_at_closeout"]),
            workflow_state_verified_at=str(trust["workflow_state_verified_at"]),
            formal_workflow_job_id=str(trust["current_workflow_job_id"]),
            formal_check_run_id=str(trust["current_check_run_id"]),
            runtime_attestation_payload_sha256=manifest.runtime_attestation_payload_sha256,
            formal_input_manifest_payload_sha256=manifest_envelope.payload_sha256,
            required_jobs=required_jobs,
        )
        formal_envelope = write_envelope(
            args.output,
            report_type="formal-closeout",
            producer="formal-closeout",
            producer_workflow_job_id=str(trust["current_workflow_job_id"]),
            producer_check_run_id=str(trust["current_check_run_id"]),
            payload=payload.model_dump(mode="json"),
        )
        payload.validate_closeout_time(
            envelope_generated_at=formal_envelope.generated_at,
            now=datetime.now(timezone.utc),
        )
        if args.markdown is None or args.attestation_output is None:
            raise RuntimeError("formal_artifact_paths_required")
        args.markdown.write_text(
            "# Phase 14 Formal Closeout\n\n"
            "- implementation_status: pass\n"
            "- incident_closure_status: pass\n"
            "- phase_status: pass\n"
            "- authoritative_resolution_source: formal-closeout\n"
            "- database_revision: 0001\n",
            encoding="utf-8",
        )
        shutil.copyfile(args.attestation, args.attestation_output)
    print(f"closeout_mode={args.mode}")
    print("closeout_artifact_status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
