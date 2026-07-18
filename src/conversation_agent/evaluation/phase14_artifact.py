"""Independent Phase 14 formal artifact verification."""

from __future__ import annotations

import hashlib
import ipaddress
import io
import json
import socket
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import urlparse

import httpx

from conversation_agent.evaluation.phase14_evidence import (
    EvidenceEnvelope,
    FormalCloseoutPayloadV1,
    REQUIRED_PHASE14_JOBS,
    RuntimeAttestationV2,
    canonical_commit_sha,
    canonical_github_id,
)

FORMAL_FILES = frozenset(
    {
        "phase14-formal-closeout.json",
        "phase14-formal-closeout.md",
        "phase14_incident_attestation.json",
    }
)
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024
MAX_WORKFLOW_JOBS = 100


@dataclass(frozen=True)
class VerificationResult:
    artifact_content_valid: bool
    github_artifact_origin_verified: bool
    authoritative_closeout_verified: bool
    formal_artifact_provenance: str
    workflow_identity_valid: bool = False
    workflow_state_at_closeout_valid: bool = False
    workflow_current_state: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_content_valid": self.artifact_content_valid,
            "github_artifact_origin_verified": self.github_artifact_origin_verified,
            "authoritative_closeout_verified": self.authoritative_closeout_verified,
            "formal_artifact_provenance": self.formal_artifact_provenance,
            "workflow_identity_valid": self.workflow_identity_valid,
            "workflow_state_at_closeout_valid": self.workflow_state_at_closeout_valid,
            "workflow_current_state": self.workflow_current_state,
        }


def _safe_formal_files(
    files: dict[str, bytes]
) -> tuple[EvidenceEnvelope, FormalCloseoutPayloadV1, RuntimeAttestationV2]:
    if set(files) != FORMAL_FILES:
        raise ValueError("formal_artifact_file_set_mismatch")
    envelope = EvidenceEnvelope.model_validate_json(
        files["phase14-formal-closeout.json"].decode("utf-8")
    )
    if envelope.report_type != "formal-closeout" or envelope.producer_job_name != "formal-closeout":
        raise ValueError("formal_artifact_envelope_identity_mismatch")
    payload = FormalCloseoutPayloadV1.model_validate(envelope.payload)
    payload.validate_closeout_time(
        envelope_generated_at=envelope.generated_at,
        now=datetime.now(timezone.utc),
    )
    attestation = RuntimeAttestationV2.model_validate_json(
        files["phase14_incident_attestation.json"].decode("utf-8")
    )
    attestation.validate_runtime(
        now=datetime.now(timezone.utc),
        subject_commit_sha=envelope.subject_commit_sha,
        protected_ref=attestation.protected_ref,
        environment_name=attestation.environment_name,
    )
    from conversation_agent.evaluation.phase14_evidence import payload_sha256

    if payload.runtime_attestation_payload_sha256 != payload_sha256(
        attestation.model_dump(mode="json")
    ):
        raise ValueError("formal_attestation_hash_mismatch")
    markdown = files["phase14-formal-closeout.md"].decode("utf-8")
    if "phase_status: pass" not in markdown or "database_revision: 0001" not in markdown:
        raise ValueError("formal_markdown_contract_mismatch")
    return envelope, payload, attestation


def load_offline_directory(path: Path) -> dict[str, bytes]:
    if not path.is_dir():
        raise ValueError("formal_artifact_directory_missing")
    entries = list(path.iterdir())
    if len(entries) != len(FORMAL_FILES):
        raise ValueError("formal_artifact_file_set_mismatch")
    files: dict[str, bytes] = {}
    for entry in entries:
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_file():
            raise ValueError("formal_artifact_unsafe_entry")
        files[entry.name] = entry.read_bytes()
    return files


def extract_formal_archive(data: bytes) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    total = 0
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("formal_artifact_invalid_zip") from exc
    with archive:
        for member in archive.infolist():
            name = member.filename
            path = PurePosixPath(name)
            mode = member.external_attr >> 16
            if (
                member.flag_bits & 0x1
                or path.is_absolute()
                or ".." in path.parts
                or len(path.parts) != 1
                or name.startswith(".")
                or member.is_dir()
                or stat.S_ISLNK(mode)
                or name in result
                or name not in FORMAL_FILES
            ):
                raise ValueError("formal_artifact_unsafe_zip_member")
            content = archive.read(member)
            total += len(content)
            if total > MAX_EXTRACTED_BYTES:
                raise ValueError("formal_artifact_expanded_too_large")
            result[name] = content
    if set(result) != FORMAL_FILES:
        raise ValueError("formal_artifact_file_set_mismatch")
    return result


def _public_addresses(host: str, port: int) -> frozenset[str]:
    addresses: set[str] = set()
    for family, _, _, _, sockaddr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global:
            raise ValueError("artifact_redirect_non_public_address")
        addresses.add(address.compressed)
    if not addresses:
        raise ValueError("artifact_redirect_dns_empty")
    return frozenset(addresses)


def _peer_address(response: httpx.Response) -> str:
    stream = response.extensions.get("network_stream")
    if stream is None or not hasattr(stream, "get_extra_info"):
        raise ValueError("artifact_download_peer_unavailable")
    address = stream.get_extra_info("server_addr")
    if not isinstance(address, tuple) or not address:
        raw_socket = stream.get_extra_info("socket")
        address = raw_socket.getpeername() if raw_socket is not None else None
    if not isinstance(address, tuple) or not address:
        raise ValueError("artifact_download_peer_unavailable")
    return ipaddress.ip_address(address[0]).compressed


def download_archive(
    api_client: httpx.Client,
    *,
    repository: str,
    artifact_id: str,
    expected_size: int,
    expected_digest: str,
    resolver: Callable[[str, int], frozenset[str]] = _public_addresses,
    download_client_factory: Callable[[], httpx.Client] | None = None,
) -> bytes:
    response = api_client.get(
        f"/repos/{repository}/actions/artifacts/{artifact_id}/zip",
        follow_redirects=False,
    )
    if response.status_code != 302:
        raise ValueError("artifact_download_redirect_missing")
    location = response.headers.get("location")
    if not location:
        raise ValueError("artifact_download_location_missing")
    parsed = urlparse(location)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("artifact_download_location_unsafe")
    port = parsed.port or 443
    allowed_addresses = resolver(parsed.hostname, port)
    factory = download_client_factory or (
        lambda: httpx.Client(
            timeout=httpx.Timeout(30.0, connect=5.0),
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
        )
    )
    with factory() as client:
        with client.stream("GET", location) as downloaded:
            if downloaded.status_code != 200:
                raise ValueError("artifact_download_failed")
            if _peer_address(downloaded) not in allowed_addresses:
                raise ValueError("artifact_download_peer_mismatch")
            encoding = downloaded.headers.get("content-encoding")
            if encoding is not None and encoding.strip() != "identity":
                raise ValueError("artifact_download_content_encoding_rejected")
            content_type = downloaded.headers.get("content-type", "").split(";", 1)[0].strip()
            if content_type not in {
                "application/zip",
                "application/octet-stream",
                "application/x-zip-compressed",
            }:
                raise ValueError("artifact_download_content_type_rejected")
            length = downloaded.headers.get("content-length")
            if length is None or not length.isascii() or not length.isdecimal():
                raise ValueError("artifact_download_content_length_invalid")
            if int(length) != expected_size or expected_size > MAX_ARTIFACT_BYTES:
                raise ValueError("artifact_download_size_mismatch")
            body = bytearray()
            for chunk in downloaded.iter_raw():
                body.extend(chunk)
                if len(body) > expected_size or len(body) > MAX_ARTIFACT_BYTES:
                    raise ValueError("artifact_download_size_exceeded")
    raw = bytes(body)
    if len(raw) != expected_size:
        raise ValueError("artifact_download_size_mismatch")
    if hashlib.sha256(raw).hexdigest() != expected_digest:
        raise ValueError("artifact_download_digest_mismatch")
    return raw


def _api_json(client: httpx.Client, path: str) -> dict[str, object]:
    response = client.get(path)
    if response.status_code != 200:
        raise ValueError(f"github_api_unavailable:{response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("github_api_invalid_json") from exc
    if not isinstance(payload, dict):
        raise ValueError("github_api_invalid_object")
    return payload


def _attempt_jobs(
    client: httpx.Client, *, repository: str, run_id: str
) -> tuple[dict[str, object], ...]:
    page = 1
    total: int | None = None
    jobs: list[dict[str, object]] = []
    while total is None or len(jobs) < total:
        payload = _api_json(
            client,
            f"/repos/{repository}/actions/runs/{run_id}/attempts/1/jobs"
            f"?per_page=100&page={page}",
        )
        if type(payload.get("total_count")) is not int or not isinstance(payload.get("jobs"), list):
            raise ValueError("invalid_workflow_jobs_response")
        if total is None:
            total = payload["total_count"]
            if total < 1 or total > MAX_WORKFLOW_JOBS:
                raise ValueError("workflow_job_count_out_of_bounds")
        elif payload["total_count"] != total:
            raise ValueError("workflow_job_total_changed")
        page_jobs = payload["jobs"]
        if not page_jobs or not all(isinstance(item, dict) for item in page_jobs):
            raise ValueError("workflow_job_pagination_incomplete")
        jobs.extend(page_jobs)
        page += 1
    if len(jobs) != total:
        raise ValueError("workflow_job_total_mismatch")
    ids = [canonical_github_id(job.get("id")) for job in jobs]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_workflow_job_id")
    return tuple(jobs)


def verify_online(
    client: httpx.Client,
    *,
    repository: str,
    repository_id: str,
    artifact_id: str,
    expected_artifact_name: str,
    workflow_run_id: str,
    subject_commit_sha: str,
    expected_protected_ref: str,
    expected_environment_name: str,
    expected_workflow_path: str,
    expected_workflow_id: str | None = None,
    downloader: Callable[..., bytes] = download_archive,
) -> VerificationResult:
    repository_id = canonical_github_id(repository_id)
    artifact_id = canonical_github_id(artifact_id)
    workflow_run_id = canonical_github_id(workflow_run_id)
    subject_commit_sha = canonical_commit_sha(subject_commit_sha)
    if not expected_protected_ref.startswith("refs/heads/"):
        raise ValueError("expected_protected_ref_invalid")
    artifact = _api_json(client, f"/repos/{repository}/actions/artifacts/{artifact_id}")
    if canonical_github_id(artifact.get("id")) != artifact_id or artifact.get("expired") is not False:
        raise ValueError("artifact_metadata_invalid")
    if artifact.get("name") != expected_artifact_name:
        raise ValueError("artifact_name_mismatch")
    artifact_run = artifact.get("workflow_run")
    if not isinstance(artifact_run, dict):
        raise ValueError("artifact_workflow_run_missing")
    for field in ("repository_id", "head_repository_id"):
        if canonical_github_id(artifact_run.get(field)) != repository_id:
            raise ValueError(f"artifact_{field}_mismatch")
    if canonical_github_id(artifact_run.get("id")) != workflow_run_id:
        raise ValueError("artifact_workflow_run_id_mismatch")
    if canonical_commit_sha(artifact_run.get("head_sha")) != subject_commit_sha:
        raise ValueError("artifact_commit_mismatch")
    digest = artifact.get("digest")
    size = artifact.get("size_in_bytes")
    if (
        not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or len(digest) != 71
        or type(size) is not int
        or size < 1
    ):
        raise ValueError("artifact_digest_metadata_invalid")

    run = _api_json(client, f"/repos/{repository}/actions/runs/{workflow_run_id}")
    if canonical_github_id(run.get("id")) != workflow_run_id:
        raise ValueError("workflow_run_id_mismatch")
    for field in ("repository", "head_repository"):
        value = run.get(field)
        if not isinstance(value, dict) or canonical_github_id(value.get("id")) != repository_id:
            raise ValueError(f"workflow_run_{field}_mismatch")
    if (
        run.get("event") != "workflow_dispatch"
        or canonical_github_id(run.get("run_attempt")) != "1"
        or canonical_commit_sha(run.get("head_sha")) != subject_commit_sha
        or run.get("head_branch") != expected_protected_ref[11:]
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
    ):
        raise ValueError("workflow_run_origin_invalid")
    workflow_id = canonical_github_id(run.get("workflow_id"))
    if expected_workflow_id is not None and workflow_id != canonical_github_id(expected_workflow_id):
        raise ValueError("workflow_id_mismatch")
    allowed_run_paths = {
        expected_workflow_path,
        f"{expected_workflow_path}@{expected_protected_ref}",
    }
    if run.get("path") not in allowed_run_paths:
        raise ValueError("workflow_run_path_mismatch")
    workflow = _api_json(client, f"/repos/{repository}/actions/workflows/{workflow_id}")
    if canonical_github_id(workflow.get("id")) != workflow_id or workflow.get("path") != expected_workflow_path:
        raise ValueError("workflow_identity_mismatch")
    current_state = workflow.get("state")
    if not isinstance(current_state, str) or not current_state:
        raise ValueError("workflow_current_state_missing")

    jobs = _attempt_jobs(client, repository=repository, run_id=workflow_run_id)
    if len(jobs) != len(REQUIRED_PHASE14_JOBS) or {job.get("name") for job in jobs} != set(
        REQUIRED_PHASE14_JOBS
    ):
        raise ValueError("workflow_job_allowlist_mismatch")
    archive = downloader(
        client,
        repository=repository,
        artifact_id=artifact_id,
        expected_size=size,
        expected_digest=digest[7:],
    )
    envelope, formal, attestation = _safe_formal_files(extract_formal_archive(archive))
    if (
        envelope.subject_commit_sha != subject_commit_sha
        or envelope.repository_id != repository_id
        or envelope.workflow_run_id != workflow_run_id
        or envelope.workflow_run_attempt != "1"
        or formal.workflow_id != workflow_id
        or formal.workflow_path != expected_workflow_path
        or attestation.protected_ref != expected_protected_ref
        or attestation.environment_name != expected_environment_name
    ):
        raise ValueError("formal_origin_binding_mismatch")
    identities = {item.job_name: item for item in formal.required_jobs}
    for job in jobs:
        name = job.get("name")
        identity = identities.get(name)
        check_url = job.get("check_run_url")
        if identity is None or not isinstance(check_url, str):
            raise ValueError("formal_job_identity_missing")
        if (
            canonical_github_id(job.get("run_id")) != workflow_run_id
            or canonical_commit_sha(job.get("head_sha")) != subject_commit_sha
            or job.get("status") != "completed"
            or job.get("conclusion") != "success"
            or canonical_github_id(job.get("id")) != identity.workflow_job_id
            or canonical_github_id(check_url.rstrip("/").rsplit("/", 1)[-1])
            != identity.check_run_id
        ):
            raise ValueError(f"workflow_job_origin_invalid:{name}")
    return VerificationResult(
        artifact_content_valid=True,
        github_artifact_origin_verified=True,
        authoritative_closeout_verified=True,
        formal_artifact_provenance="valid",
        workflow_identity_valid=True,
        workflow_state_at_closeout_valid=True,
        workflow_current_state=current_state,
    )


def verify_offline(path: Path) -> VerificationResult:
    _safe_formal_files(load_offline_directory(path))
    return VerificationResult(
        artifact_content_valid=True,
        github_artifact_origin_verified=False,
        authoritative_closeout_verified=False,
        formal_artifact_provenance="unverified",
    )
