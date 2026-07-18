"""Strict Phase 14-F evidence contracts and trust-validation helpers."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PhaseStatus = Literal["pass", "warning", "fail", "blocked"]
GITHUB_COM = "https://github.com"
ATTESTATION_SCHEMA = "phase14_incident_attestation_v2"
EVIDENCE_SCHEMA = "phase14_evidence_envelope_v1"
FORMAL_CLOSEOUT_SCHEMA = "phase14_formal_closeout_v1"
FORMAL_INPUT_MANIFEST_SCHEMA = "phase14_formal_input_manifest_v1"
REQUIRED_PHASE14_JOBS = (
    "test",
    "secret-scan",
    "postgres-integration",
    "operational-postgres",
    "incident-closure",
    "formal-closeout",
)
_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{12}$")
_APPROVAL_PATTERN = re.compile(r"^apr_[0-9a-f]{64}$")


def canonical_github_id(value: str | int) -> str:
    """Return one unambiguous positive decimal representation of a GitHub ID."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("invalid_github_id")
    text = str(value)
    if not text.isascii() or not text.isdecimal():
        raise ValueError("invalid_github_id")
    number = int(text, 10)
    if number <= 0 or (isinstance(value, str) and text != str(number)):
        raise ValueError("invalid_github_id")
    return str(number)


def canonical_commit_sha(value: str) -> str:
    if not isinstance(value, str) or not _SHA_PATTERN.fullmatch(value):
        raise ValueError("invalid_subject_commit_sha")
    return value.lower()


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def payload_sha256(payload: object) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}_must_be_timezone_aware")
    return value.astimezone(timezone.utc)


class EvidenceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["phase14_evidence_envelope_v1"] = EVIDENCE_SCHEMA
    report_type: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_-]+$")
    subject_commit_sha: str
    repository_id: str
    workflow_run_id: str
    workflow_run_attempt: str
    producer_job_name: str = Field(min_length=1, max_length=80)
    producer_workflow_job_id: str
    producer_check_run_id: str
    generated_at: datetime
    payload: dict[str, Any]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("subject_commit_sha")
    @classmethod
    def _validate_sha(cls, value: str) -> str:
        return canonical_commit_sha(value)

    @field_validator(
        "repository_id",
        "workflow_run_id",
        "workflow_run_attempt",
        "producer_workflow_job_id",
        "producer_check_run_id",
    )
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return canonical_github_id(value)

    @field_validator("generated_at")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return _utc(value, "generated_at")

    @model_validator(mode="after")
    def _verify_payload_hash(self) -> "EvidenceEnvelope":
        if payload_sha256(self.payload) != self.payload_sha256:
            raise ValueError("payload_sha256_mismatch")
        return self


class GitleaksRepositoryScanResult(BaseModel):
    """Internally consistent result of one real all-refs Gitleaks process."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    gitleaks_process_return_code: int = Field(ge=0)
    gitleaks_all_refs_scan_status: Literal["pass", "fail"]
    gitleaks_all_refs_findings: int = Field(ge=0)
    gitleaks_real_repository_scan_passed: bool

    @model_validator(mode="after")
    def _result_is_consistent(self) -> "GitleaksRepositoryScanResult":
        expected_status = (
            "pass"
            if self.gitleaks_process_return_code == 0
            and self.gitleaks_all_refs_findings == 0
            else "fail"
        )
        expected_passed = (
            self.gitleaks_process_return_code == 0
            and self.gitleaks_all_refs_scan_status == "pass"
            and self.gitleaks_all_refs_findings == 0
        )
        if (
            self.gitleaks_all_refs_scan_status != expected_status
            or self.gitleaks_real_repository_scan_passed != expected_passed
        ):
            raise ValueError("gitleaks_repository_scan_result_inconsistent")
        return self


def create_evidence_envelope(
    *,
    report_type: str,
    subject_commit_sha: str,
    repository_id: str | int,
    workflow_run_id: str | int,
    workflow_run_attempt: str | int,
    producer_job_name: str,
    producer_workflow_job_id: str | int,
    producer_check_run_id: str | int,
    generated_at: datetime,
    payload: dict[str, Any],
) -> EvidenceEnvelope:
    return EvidenceEnvelope(
        report_type=report_type,
        subject_commit_sha=canonical_commit_sha(subject_commit_sha),
        repository_id=canonical_github_id(repository_id),
        workflow_run_id=canonical_github_id(workflow_run_id),
        workflow_run_attempt=canonical_github_id(workflow_run_attempt),
        producer_job_name=producer_job_name,
        producer_workflow_job_id=canonical_github_id(producer_workflow_job_id),
        producer_check_run_id=canonical_github_id(producer_check_run_id),
        generated_at=generated_at,
        payload=payload,
        payload_sha256=payload_sha256(payload),
    )


def verify_evidence_provenance(
    envelope: EvidenceEnvelope,
    *,
    report_type: str,
    subject_commit_sha: str,
    repository_id: str | int,
    workflow_run_id: str | int,
    workflow_run_attempt: str | int,
    producer_job_name: str,
    producer_workflow_job_id: str | int | None = None,
    producer_check_run_id: str | int | None = None,
    now: datetime,
    future_skew_seconds: int = 60,
) -> None:
    expected = (
        report_type,
        canonical_commit_sha(subject_commit_sha),
        canonical_github_id(repository_id),
        canonical_github_id(workflow_run_id),
        canonical_github_id(workflow_run_attempt),
        producer_job_name,
    )
    actual = (
        envelope.report_type,
        envelope.subject_commit_sha,
        envelope.repository_id,
        envelope.workflow_run_id,
        envelope.workflow_run_attempt,
        envelope.producer_job_name,
    )
    if actual != expected:
        raise ValueError("evidence_provenance_mismatch")
    if producer_workflow_job_id is not None and (
        envelope.producer_workflow_job_id
        != canonical_github_id(producer_workflow_job_id)
    ):
        raise ValueError("evidence_producer_job_id_mismatch")
    if producer_check_run_id is not None and (
        envelope.producer_check_run_id != canonical_github_id(producer_check_run_id)
    ):
        raise ValueError("evidence_producer_check_run_id_mismatch")
    if envelope.generated_at > _utc(now, "now") + timedelta(seconds=future_skew_seconds):
        raise ValueError("evidence_generated_in_future")


class RuntimeAttestationV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["phase14_incident_attestation_v2"] = ATTESTATION_SCHEMA
    provider: Literal["dashscope"]
    credential_fingerprint_prefix: str
    credential_revocation_verified: Literal[True]
    credential_rotation_verified: Literal[True]
    provider_usage_review_status: Literal["reviewed_no_anomaly"]
    discovered_at: datetime
    revocation_verified_at: datetime
    rotation_verified_at: datetime
    provider_usage_reviewed_at: datetime
    attestation_generated_at: datetime
    attestation_source: Literal["protected_environment"]
    subject_commit_sha: str
    protected_ref: str = Field(min_length=1)
    environment_name: str = Field(min_length=1)
    environment_id: str
    deployment_identifier: str = Field(min_length=1)
    verified_by_role: str = Field(min_length=1, max_length=80)
    approval_event_verified: Literal[True]
    approval_event_count: int = Field(ge=1)
    approval_environment_id: str
    approval_reference: str

    @field_validator("credential_fingerprint_prefix")
    @classmethod
    def _validate_fingerprint(cls, value: str) -> str:
        if not _FINGERPRINT_PATTERN.fullmatch(value):
            raise ValueError("invalid_credential_fingerprint_prefix")
        return value

    @field_validator("subject_commit_sha")
    @classmethod
    def _attestation_sha(cls, value: str) -> str:
        return canonical_commit_sha(value)

    @field_validator("environment_id", "approval_environment_id")
    @classmethod
    def _attestation_id(cls, value: str) -> str:
        return canonical_github_id(value)

    @field_validator(
        "discovered_at",
        "revocation_verified_at",
        "rotation_verified_at",
        "provider_usage_reviewed_at",
        "attestation_generated_at",
    )
    @classmethod
    def _attestation_time(cls, value: datetime, info) -> datetime:
        return _utc(value, info.field_name)

    @field_validator("approval_reference")
    @classmethod
    def _approval_reference(cls, value: str) -> str:
        if not _APPROVAL_PATTERN.fullmatch(value):
            raise ValueError("invalid_approval_reference")
        return value

    @model_validator(mode="after")
    def _validate_attestation_invariants(self) -> "RuntimeAttestationV2":
        if self.environment_id != self.approval_environment_id:
            raise ValueError("approval_environment_id_mismatch")
        if not self.deployment_identifier.startswith(
            f"github-environment:{self.environment_id}:workflow-job:"
        ):
            raise ValueError("deployment_identifier_mismatch")
        if self.discovered_at > self.revocation_verified_at:
            raise ValueError("invalid_revocation_timeline")
        if self.discovered_at > self.rotation_verified_at:
            raise ValueError("invalid_rotation_timeline")
        if self.revocation_verified_at > self.provider_usage_reviewed_at:
            raise ValueError("invalid_usage_review_timeline")
        for field_name, value in (
            ("revocation_verified_at", self.revocation_verified_at),
            ("rotation_verified_at", self.rotation_verified_at),
            ("provider_usage_reviewed_at", self.provider_usage_reviewed_at),
        ):
            if value > self.attestation_generated_at:
                raise ValueError(f"{field_name}_after_attestation")
        return self

    def validate_runtime(
        self,
        *,
        now: datetime,
        subject_commit_sha: str,
        protected_ref: str,
        environment_name: str,
        future_skew_seconds: int = 60,
    ) -> None:
        if self.subject_commit_sha != canonical_commit_sha(subject_commit_sha):
            raise ValueError("attestation_commit_mismatch")
        if self.protected_ref != protected_ref:
            raise ValueError("attestation_ref_mismatch")
        if self.environment_name != environment_name:
            raise ValueError("attestation_environment_mismatch")
        limit = _utc(now, "now") + timedelta(seconds=future_skew_seconds)
        for value in (
            self.discovered_at,
            self.revocation_verified_at,
            self.rotation_verified_at,
            self.provider_usage_reviewed_at,
            self.attestation_generated_at,
        ):
            if value > limit:
                raise ValueError("attestation_time_in_future")


def approval_reference(
    *,
    repository_id: str | int,
    workflow_run_id: str | int,
    workflow_run_attempt: str | int,
    environment_name: str,
    deployment_identifier: str,
) -> str:
    parts = (
        canonical_github_id(repository_id),
        canonical_github_id(workflow_run_id),
        canonical_github_id(workflow_run_attempt),
        environment_name,
        deployment_identifier,
    )
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return f"apr_{digest}"


class EnvironmentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment_protection_valid: Literal[True]
    environment_id: str
    environment_name: str
    approval_event_verified: Literal[True]
    approval_event_count: int = Field(ge=1)
    current_workflow_job_id: str
    current_check_run_id: str
    deployment_identifier: str

    @field_validator(
        "environment_id", "current_workflow_job_id", "current_check_run_id"
    )
    @classmethod
    def _environment_id(cls, value: str) -> str:
        return canonical_github_id(value)


def validate_environment_response(
    payload: dict[str, Any],
    *,
    expected_name: str,
) -> tuple[str, str]:
    if payload.get("name") != expected_name:
        raise ValueError("environment_name_mismatch")
    environment_id = canonical_github_id(payload.get("id"))
    rules = payload.get("protection_rules")
    if not isinstance(rules, list):
        raise ValueError("invalid_environment_protection_rules")
    allowed = {"required_reviewers", "branch_policy"}
    types = [rule.get("type") for rule in rules if isinstance(rule, dict)]
    if len(types) != len(rules) or any(item not in allowed for item in types):
        raise ValueError("unsupported_environment_protection_rule")
    if types.count("required_reviewers") != 1 or types.count("branch_policy") != 1:
        raise ValueError("invalid_environment_protection_rule_count")
    reviewer_rule = rules[types.index("required_reviewers")]
    if not reviewer_rule.get("reviewers") or reviewer_rule.get("prevent_self_review") is not True:
        raise ValueError("invalid_required_reviewers_rule")
    branch = payload.get("deployment_branch_policy")
    if not isinstance(branch, dict):
        raise ValueError("missing_deployment_branch_policy")
    if branch.get("protected_branches") is not True or branch.get("custom_branch_policies") is not False:
        raise ValueError("invalid_deployment_branch_policy")
    return environment_id, expected_name


def validate_approval_history(
    approvals: object,
    *,
    environment_id: str | int,
    environment_name: str,
) -> int:
    expected_id = canonical_github_id(environment_id)
    if not isinstance(approvals, list):
        raise ValueError("invalid_approval_history")
    count = 0
    for approval in approvals:
        if not isinstance(approval, dict) or approval.get("state") != "approved":
            continue
        environments = approval.get("environments")
        if not isinstance(environments, list):
            continue
        if any(
            isinstance(item, dict)
            and canonical_github_id(item.get("id")) == expected_id
            and item.get("name") == environment_name
            for item in environments
        ):
            count += 1
    if count == 0:
        raise ValueError("approved_environment_event_missing")
    return count


def bind_current_job(
    jobs_payload: object,
    *,
    check_run_id: str | int,
    subject_commit_sha: str,
    expected_name: str,
    workflow_run_id: str | int | None = None,
    allowed_statuses: tuple[str, ...] = ("queued", "in_progress"),
) -> tuple[str, str]:
    if not isinstance(jobs_payload, dict) or not isinstance(jobs_payload.get("jobs"), list):
        raise ValueError("invalid_workflow_jobs_response")
    expected_check = canonical_github_id(check_run_id)
    matches: list[dict[str, Any]] = []
    for job in jobs_payload["jobs"]:
        if not isinstance(job, dict):
            continue
        check_url = job.get("check_run_url")
        if not isinstance(check_url, str):
            continue
        try:
            candidate = canonical_github_id(check_url.rstrip("/").rsplit("/", 1)[-1])
        except ValueError:
            continue
        if candidate == expected_check:
            matches.append(job)
    if len(matches) != 1:
        raise ValueError("current_workflow_job_not_unique")
    job = matches[0]
    if canonical_commit_sha(job.get("head_sha")) != canonical_commit_sha(subject_commit_sha):
        raise ValueError("workflow_job_commit_mismatch")
    if job.get("status") not in allowed_statuses or job.get("name") != expected_name:
        raise ValueError("workflow_job_identity_mismatch")
    if job.get("conclusion") is not None:
        raise ValueError("workflow_job_illegal_self_attestation")
    if workflow_run_id is not None and canonical_github_id(
        job.get("run_id")
    ) != canonical_github_id(workflow_run_id):
        raise ValueError("workflow_job_run_mismatch")
    return canonical_github_id(job.get("id")), expected_check


class RequiredJobIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_name: str = Field(min_length=1, max_length=80)
    workflow_job_id: str
    check_run_id: str

    @field_validator("workflow_job_id", "check_run_id")
    @classmethod
    def _job_id(cls, value: str) -> str:
        return canonical_github_id(value)


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_type: str = Field(min_length=1, max_length=80)
    producer_job_name: str = Field(min_length=1, max_length=80)
    producer_workflow_job_id: str
    producer_check_run_id: str
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("producer_workflow_job_id", "producer_check_run_id")
    @classmethod
    def _reference_id(cls, value: str) -> str:
        return canonical_github_id(value)

    @classmethod
    def from_envelope(cls, envelope: EvidenceEnvelope) -> "EvidenceReference":
        return cls(
            report_type=envelope.report_type,
            producer_job_name=envelope.producer_job_name,
            producer_workflow_job_id=envelope.producer_workflow_job_id,
            producer_check_run_id=envelope.producer_check_run_id,
            payload_sha256=envelope.payload_sha256,
        )


class FormalInputManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["phase14_formal_input_manifest_v1"] = (
        FORMAL_INPUT_MANIFEST_SCHEMA
    )
    producer_evidence: tuple[EvidenceReference, ...]
    incident_evidence: EvidenceReference
    runtime_attestation_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_gitleaks_evidence: EvidenceReference
    database_revision: Literal["0001"]

    @model_validator(mode="after")
    def _manifest_is_acyclic_and_complete(self) -> "FormalInputManifestV1":
        expected = {
            "test-report",
            "gitleaks-report",
            "postgres-report",
            "operational-report",
        }
        actual = {item.report_type for item in self.producer_evidence}
        if actual != expected or len(self.producer_evidence) != len(expected):
            raise ValueError("formal_manifest_producer_set_mismatch")
        if self.incident_evidence.report_type != "incident-evidence":
            raise ValueError("formal_manifest_incident_type_mismatch")
        if self.repository_gitleaks_evidence.report_type != "gitleaks-report":
            raise ValueError("formal_manifest_gitleaks_type_mismatch")
        return self


class FormalCloseoutPayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["phase14_formal_closeout_v1"] = FORMAL_CLOSEOUT_SCHEMA
    authoritative: Literal[True]
    authoritative_resolution_source: Literal["formal-closeout"]
    implementation_status: Literal["pass"]
    incident_closure_status: Literal["pass"]
    phase_status: Literal["pass"]
    database_revision: Literal["0001"]
    runtime_attestation_valid: Literal[True]
    evidence_revalidation_passed: Literal[True]
    workflow_id: str
    workflow_path: str = Field(min_length=1)
    workflow_state_at_closeout: Literal["active"]
    workflow_state_verified_at: datetime
    formal_workflow_job_id: str
    formal_check_run_id: str
    runtime_attestation_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    formal_input_manifest_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_jobs: tuple[RequiredJobIdentity, ...]

    @field_validator("workflow_id", "formal_workflow_job_id", "formal_check_run_id")
    @classmethod
    def _formal_id(cls, value: str) -> str:
        return canonical_github_id(value)

    @field_validator("workflow_state_verified_at")
    @classmethod
    def _workflow_time(cls, value: datetime) -> datetime:
        return _utc(value, "workflow_state_verified_at")

    @model_validator(mode="after")
    def _formal_job_set(self) -> "FormalCloseoutPayloadV1":
        names = tuple(item.job_name for item in self.required_jobs)
        if len(set(names)) != len(names) or set(names) != set(REQUIRED_PHASE14_JOBS):
            raise ValueError("formal_required_job_set_mismatch")
        formal = next(item for item in self.required_jobs if item.job_name == "formal-closeout")
        if (
            formal.workflow_job_id != self.formal_workflow_job_id
            or formal.check_run_id != self.formal_check_run_id
        ):
            raise ValueError("formal_job_identity_mismatch")
        return self

    def validate_closeout_time(
        self,
        *,
        envelope_generated_at: datetime,
        now: datetime,
        future_skew_seconds: int = 60,
    ) -> None:
        if self.workflow_state_verified_at > _utc(envelope_generated_at, "generated_at"):
            raise ValueError("workflow_state_verified_after_formal_evidence")
        if self.workflow_state_verified_at > _utc(now, "now") + timedelta(
            seconds=future_skew_seconds
        ):
            raise ValueError("workflow_state_verified_in_future")
