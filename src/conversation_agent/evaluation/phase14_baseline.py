"""Phase 14-G one-shot repository baseline evidence contracts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from conversation_agent.evaluation.phase14_evidence import (
    canonical_commit_sha,
    canonical_github_id,
    canonical_json_bytes,
)

BASELINE_REPOSITORY = "GG081-netizen/crispy-fortnight-baseline-2"
BASELINE_REPOSITORY_ID = "1305192784"
BASELINE_BRANCH = "main"
BASELINE_ENVIRONMENT = "phase14-baseline-closeout"
BASELINE_REVIEWER = "toshibanino6-creator"
BASELINE_TRIGGER_ACTOR = "GG081-netizen"
DISCOVERY_WORKFLOW_PATH = ".github/workflows/phase14-baseline-discovery.yml"
FORMAL_WORKFLOW_PATH = ".github/workflows/phase14-baseline-closeout.yml"
BASELINE_JOBS = (
    "test",
    "secret-scan",
    "postgres-integration",
    "operational-postgres",
    "baseline-approval",
    "baseline-closeout",
)
BASELINE_FILES = frozenset(
    {
        "phase14-baseline-closeout.json",
        "phase14-baseline-closeout.md",
        "phase14-candidate-manifest.json",
    }
)
DISCOVERY_FILES = frozenset(
    {"phase14-discovery-evidence.json", "phase14-candidate-manifest.json"}
)
_HEX64 = r"^[0-9a-f]{64}$"
_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".venv",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "logs",
        "tmp",
        "temp",
        "venv",
        "env",
    }
)
_EXCLUDED_FILES = frozenset({".env", ".env.local", ".coverage"})


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}_must_be_timezone_aware")
    return value.astimezone(timezone.utc)


def candidate_manifest_sha256(manifest: "CandidateManifestV1") -> str:
    return hashlib.sha256(canonical_json_bytes(manifest.model_dump(mode="json"))).hexdigest()


class CandidateManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    git_mode: Literal["100644", "100755"]
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=_HEX64)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("candidate_manifest_path_unsafe")
        if value != path.as_posix() or any(part in _EXCLUDED_PARTS for part in path.parts):
            raise ValueError("candidate_manifest_path_not_canonical")
        return value


class CandidateManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["phase14_candidate_manifest_v1"] = (
        "phase14_candidate_manifest_v1"
    )
    repository: Literal[BASELINE_REPOSITORY] = BASELINE_REPOSITORY
    repository_id: Literal[BASELINE_REPOSITORY_ID] = BASELINE_REPOSITORY_ID
    default_branch: Literal[BASELINE_BRANCH] = BASELINE_BRANCH
    entries: tuple[CandidateManifestEntry, ...]

    @model_validator(mode="after")
    def _entries_are_canonical(self) -> "CandidateManifestV1":
        paths = tuple(item.path for item in self.entries)
        if not paths or paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("candidate_manifest_entries_not_canonical")
        return self


def _entry(path: str, data: bytes, executable: bool) -> CandidateManifestEntry:
    return CandidateManifestEntry(
        path=path,
        git_mode="100755" if executable else "100644",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        capture_output=True,
    )


def _assert_candidate_repository_state(root: Path) -> None:
    inside = _run_git(root, "rev-parse", "--is-inside-work-tree").stdout.strip()
    if inside != b"true":
        raise ValueError("candidate_manifest_git_repository_required")

    git_dir_raw = _run_git(root, "rev-parse", "--git-dir").stdout.decode("utf-8").strip()
    git_dir = (root / git_dir_raw).resolve() if not Path(git_dir_raw).is_absolute() else Path(git_dir_raw).resolve()
    if not git_dir.is_relative_to(root) or not git_dir.is_dir():
        raise ValueError("candidate_manifest_git_dir_outside_root")

    head = _run_git(root, "rev-parse", "--verify", "HEAD", check=False)
    if head.returncode == 0:
        raise ValueError("candidate_manifest_repository_not_unborn")
    if _run_git(root, "ls-files", "-z").stdout:
        raise ValueError("candidate_manifest_index_not_empty")

    info_exclude = git_dir / "info" / "exclude"
    if info_exclude.exists():
        for line in info_exclude.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                raise ValueError("candidate_manifest_info_exclude_not_empty")


def _candidate_path(root: Path, raw_path: bytes) -> tuple[str, Path]:
    relative_text = raw_path.decode("utf-8")
    relative = PurePosixPath(relative_text)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.as_posix() != relative_text
    ):
        raise ValueError("candidate_manifest_path_unsafe")
    if relative_text in _EXCLUDED_FILES or any(
        part in _EXCLUDED_PARTS for part in relative.parts
    ):
        raise ValueError("candidate_manifest_forbidden_path_not_ignored")

    path = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"candidate_manifest_symlink:{relative_text}")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError("candidate_manifest_path_outside_root")
    return relative_text, path


def build_candidate_manifest(root: Path) -> CandidateManifestV1:
    root = root.resolve()
    _assert_candidate_repository_state(root)
    result = _run_git(
        root,
        "-c",
        "core.excludesFile=/dev/null",
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    entries: list[CandidateManifestEntry] = []
    for raw_path in sorted(item for item in result.stdout.split(b"\0") if item):
        relative_text, path = _candidate_path(root, raw_path)
        data = path.read_bytes()
        executable = bool(path.stat().st_mode & stat.S_IXUSR)
        entries.append(_entry(relative_text, data, executable))
    return CandidateManifestV1(entries=tuple(sorted(entries, key=lambda item: item.path)))


def build_index_manifest(root: Path) -> CandidateManifestV1:
    command = ["git", "ls-files", "--stage", "-z"]
    result = subprocess.run(command, cwd=root, check=True, capture_output=True)
    entries: list[CandidateManifestEntry] = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, _, stage = metadata.decode("ascii").split(" ")
        if stage != "0" or mode not in {"100644", "100755"}:
            raise ValueError("candidate_index_entry_unsupported")
        path = raw_path.decode("utf-8")
        content = subprocess.run(
            ["git", "show", f":{path}"], cwd=root, check=True, capture_output=True
        ).stdout
        entries.append(_entry(path, content, mode == "100755"))
    return CandidateManifestV1(entries=tuple(sorted(entries, key=lambda item: item.path)))


def verify_manifest_equal(
    expected: CandidateManifestV1, actual: CandidateManifestV1
) -> None:
    if expected != actual:
        raise ValueError("candidate_manifest_mismatch")


class DiscoveryEvidenceV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["phase14_discovery_evidence_v1"] = (
        "phase14_discovery_evidence_v1"
    )
    authoritative: Literal[False]
    discovery_status: Literal["pass"]
    repository: Literal[BASELINE_REPOSITORY]
    repository_id: Literal[BASELINE_REPOSITORY_ID]
    workflow_id: str
    workflow_path: Literal[DISCOVERY_WORKFLOW_PATH]
    workflow_run_id: str
    workflow_run_attempt: Literal["1"]
    subject_commit_sha: str
    subject_tree_sha: str
    candidate_manifest_sha256: str = Field(pattern=_HEX64)
    producer_job_id: str
    producer_check_run_id: str
    generated_at: datetime

    @field_validator(
        "workflow_id", "workflow_run_id", "producer_job_id", "producer_check_run_id"
    )
    @classmethod
    def _ids(cls, value: str) -> str:
        return canonical_github_id(value)

    @field_validator("subject_commit_sha", "subject_tree_sha")
    @classmethod
    def _shas(cls, value: str) -> str:
        return canonical_commit_sha(value)

    @field_validator("generated_at")
    @classmethod
    def _time(cls, value: datetime) -> datetime:
        return _utc(value, "generated_at")


class DiscoveryArtifactBindingV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["phase14_discovery_artifact_binding_v1"] = (
        "phase14_discovery_artifact_binding_v1"
    )
    repository: Literal[BASELINE_REPOSITORY]
    repository_id: Literal[BASELINE_REPOSITORY_ID]
    workflow_run_id: str
    workflow_run_attempt: Literal["1"]
    subject_commit_sha: str
    artifact_id: str
    artifact_name: str = Field(min_length=1, max_length=200)
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    artifact_size_in_bytes: int = Field(gt=0)
    artifact_content_verified: Literal[True]
    github_artifact_origin_verified: Literal[True]

    @field_validator("workflow_run_id", "artifact_id")
    @classmethod
    def _ids(cls, value: str) -> str:
        return canonical_github_id(value)

    @field_validator("subject_commit_sha")
    @classmethod
    def _sha(cls, value: str) -> str:
        return canonical_commit_sha(value)


class DiscoveryJobIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_name: Literal["discovery"]
    workflow_job_id: str
    check_run_id: str

    @field_validator("workflow_job_id", "check_run_id")
    @classmethod
    def _ids(cls, value: str) -> str:
        return canonical_github_id(value)


class FormalBaselineJobIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_name: Literal[
        "test",
        "secret-scan",
        "postgres-integration",
        "operational-postgres",
        "baseline-approval",
        "baseline-closeout",
    ]
    workflow_job_id: str
    check_run_id: str

    @field_validator("workflow_job_id", "check_run_id")
    @classmethod
    def _ids(cls, value: str) -> str:
        return canonical_github_id(value)


class ApprovalAttestationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_verified: Literal[True]
    approval_actor_differs_from_trigger: Literal[True]
    approval_environment_id: str
    approval_event_count: int = Field(ge=1)

    @field_validator("approval_environment_id")
    @classmethod
    def _id(cls, value: str) -> str:
        return canonical_github_id(value)


class BaselineFormalInputManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["phase14_baseline_formal_input_manifest_v1"] = (
        "phase14_baseline_formal_input_manifest_v1"
    )
    candidate_manifest_sha256: str = Field(pattern=_HEX64)
    discovery_workflow_id: str
    discovery_workflow_path: Literal[DISCOVERY_WORKFLOW_PATH]
    discovery_run_id: str
    discovery_run_attempt: Literal["1"]
    discovery_artifact_id: str
    discovery_artifact_name: str = Field(min_length=1, max_length=200)
    discovery_artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator(
        "discovery_workflow_id", "discovery_run_id", "discovery_artifact_id"
    )
    @classmethod
    def _ids(cls, value: str) -> str:
        return canonical_github_id(value)


class BaselineCloseoutPayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["phase14_repository_baseline_closeout_v1"] = (
        "phase14_repository_baseline_closeout_v1"
    )
    authoritative: Literal[False]
    repository_baseline_candidate_status: Literal["pass"]
    phase14_authoritative_phase_status: Literal["blocked"]
    repository: Literal[BASELINE_REPOSITORY]
    repository_id: Literal[BASELINE_REPOSITORY_ID]
    workflow_id: str
    workflow_path: Literal[FORMAL_WORKFLOW_PATH]
    workflow_run_id: str
    workflow_run_attempt: Literal["1"]
    subject_commit_sha: str
    subject_tree_sha: str
    candidate_manifest_sha256: str = Field(pattern=_HEX64)
    discovery_binding: DiscoveryArtifactBindingV1
    approval_attestation_summary: ApprovalAttestationSummary
    required_jobs: tuple[FormalBaselineJobIdentity, ...]
    baseline_closeout_job_id: str
    baseline_closeout_check_run_id: str
    baseline_closeout_runtime_status: Literal["in_progress"]
    baseline_closeout_runtime_conclusion: None
    database_revision: Literal["0001"]
    generated_at: datetime

    @field_validator(
        "workflow_id",
        "workflow_run_id",
        "baseline_closeout_job_id",
        "baseline_closeout_check_run_id",
    )
    @classmethod
    def _ids(cls, value: str) -> str:
        return canonical_github_id(value)

    @field_validator("subject_commit_sha", "subject_tree_sha")
    @classmethod
    def _shas(cls, value: str) -> str:
        return canonical_commit_sha(value)

    @field_validator("generated_at")
    @classmethod
    def _time(cls, value: datetime) -> datetime:
        return _utc(value, "generated_at")

    @model_validator(mode="after")
    def _job_contract(self) -> "BaselineCloseoutPayloadV1":
        if tuple(item.job_name for item in self.required_jobs) != BASELINE_JOBS:
            raise ValueError("baseline_required_job_set_mismatch")
        identities = {item.job_name: item for item in self.required_jobs}
        formal = identities["baseline-closeout"]
        if (
            formal.workflow_job_id != self.baseline_closeout_job_id
            or formal.check_run_id != self.baseline_closeout_check_run_id
        ):
            raise ValueError("baseline_closeout_runtime_identity_mismatch")
        return self


class BaselineArtifactDocumentV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["phase14_baseline_artifact_document_v1"] = (
        "phase14_baseline_artifact_document_v1"
    )
    closeout_payload: BaselineCloseoutPayloadV1
    formal_input_manifest: BaselineFormalInputManifestV1
    repository_baseline_attestation: dict[str, Any]
    approval_attestation_summary: ApprovalAttestationSummary
    producer_evidence_references: tuple[dict[str, Any], ...]


def _validate_runtime_self_job_values(
    job: dict[str, Any], *, expected_name: str, run_id: str, sha: str, check_run_id: str
) -> tuple[str, str]:
    check_url = job.get("check_run_url")
    if (
        job.get("name") != expected_name
        or canonical_github_id(job.get("run_id")) != canonical_github_id(run_id)
        or canonical_commit_sha(job.get("head_sha")) != canonical_commit_sha(sha)
        or job.get("status") not in {"queued", "in_progress"}
        or job.get("conclusion") is not None
        or not isinstance(check_url, str)
        or canonical_github_id(check_url.rstrip("/").rsplit("/", 1)[-1])
        != canonical_github_id(check_run_id)
    ):
        raise ValueError("baseline_illegal_self_attestation")
    return canonical_github_id(job.get("id")), canonical_github_id(check_run_id)


def validate_discovery_runtime_self_job(
    job: dict[str, Any], *, run_id: str, sha: str, check_run_id: str
) -> DiscoveryJobIdentity:
    workflow_job_id, canonical_check_run_id = _validate_runtime_self_job_values(
        job,
        expected_name="discovery",
        run_id=run_id,
        sha=sha,
        check_run_id=check_run_id,
    )
    return DiscoveryJobIdentity(
        job_name="discovery",
        workflow_job_id=workflow_job_id,
        check_run_id=canonical_check_run_id,
    )


def validate_formal_runtime_self_job(
    job: dict[str, Any], *, expected_name: str, run_id: str, sha: str, check_run_id: str
) -> FormalBaselineJobIdentity:
    workflow_job_id, canonical_check_run_id = _validate_runtime_self_job_values(
        job,
        expected_name=expected_name,
        run_id=run_id,
        sha=sha,
        check_run_id=check_run_id,
    )
    return FormalBaselineJobIdentity(
        job_name=expected_name,
        workflow_job_id=workflow_job_id,
        check_run_id=canonical_check_run_id,
    )


def validate_completed_formal_job(
    job: dict[str, Any], *, expected_name: str, run_id: str, sha: str
) -> FormalBaselineJobIdentity:
    check_url = job.get("check_run_url")
    if (
        job.get("name") != expected_name
        or canonical_github_id(job.get("run_id")) != canonical_github_id(run_id)
        or canonical_commit_sha(job.get("head_sha")) != canonical_commit_sha(sha)
        or job.get("status") != "completed"
        or job.get("conclusion") != "success"
        or not isinstance(check_url, str)
    ):
        raise ValueError(f"baseline_required_job_not_successful:{expected_name}")
    return FormalBaselineJobIdentity(
        job_name=expected_name,
        workflow_job_id=canonical_github_id(job.get("id")),
        check_run_id=canonical_github_id(check_url.rstrip("/").rsplit("/", 1)[-1]),
    )


def validate_generated_time(value: datetime, *, now: datetime) -> None:
    if _utc(value, "generated_at") > _utc(now, "now") + timedelta(seconds=60):
        raise ValueError("baseline_evidence_generated_in_future")


def write_json(path: Path, value: BaseModel | dict[str, Any]) -> None:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload) + b"\n")
