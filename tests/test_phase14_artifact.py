from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from conversation_agent.evaluation.phase14_artifact import (
    FORMAL_FILES,
    download_archive,
    extract_formal_archive,
    verify_offline,
    verify_online,
)
from conversation_agent.evaluation.phase14_evidence import (
    FormalCloseoutPayloadV1,
    RequiredJobIdentity,
    RuntimeAttestationV2,
    create_evidence_envelope,
    payload_sha256,
)

pytestmark = pytest.mark.unit
SHA = "a" * 40
NOW = datetime.now(timezone.utc)
JOBS = (
    "test",
    "secret-scan",
    "postgres-integration",
    "operational-postgres",
    "incident-closure",
    "formal-closeout",
)


def formal_files() -> dict[str, bytes]:
    attestation = RuntimeAttestationV2(
        provider="dashscope",
        credential_fingerprint_prefix="".join(("0123", "4567", "89ab")),
        credential_revocation_verified=True,
        credential_rotation_verified=True,
        provider_usage_review_status="reviewed_no_anomaly",
        discovered_at=NOW - timedelta(days=3),
        revocation_verified_at=NOW - timedelta(days=2),
        rotation_verified_at=NOW - timedelta(days=2),
        provider_usage_reviewed_at=NOW - timedelta(days=1),
        attestation_generated_at=NOW - timedelta(hours=1),
        attestation_source="protected_environment",
        subject_commit_sha=SHA,
        protected_ref="refs/heads/main",
        environment_name="phase14-incident-closure",
        environment_id="30",
        deployment_identifier="github-environment:30:workflow-job:5:check-run:15",
        verified_by_role="security-reviewer",
        approval_event_verified=True,
        approval_event_count=1,
        approval_environment_id="30",
        approval_reference="apr_" + "b" * 64,
    )
    identities = tuple(
        RequiredJobIdentity(job_name=name, workflow_job_id=str(index), check_run_id=str(index + 10))
        for index, name in enumerate(JOBS, start=1)
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
        workflow_id="40",
        workflow_path=".github/workflows/ci.yml",
        workflow_state_at_closeout="active",
        workflow_state_verified_at=NOW - timedelta(minutes=30),
        formal_workflow_job_id="6",
        formal_check_run_id="16",
        runtime_attestation_payload_sha256=payload_sha256(attestation.model_dump(mode="json")),
        formal_input_manifest_payload_sha256="c" * 64,
        required_jobs=identities,
    )
    envelope = create_evidence_envelope(
        report_type="formal-closeout",
        subject_commit_sha=SHA,
        repository_id="10",
        workflow_run_id="20",
        workflow_run_attempt="1",
        producer_job_name="formal-closeout",
        producer_workflow_job_id="6",
        producer_check_run_id="16",
        generated_at=NOW,
        payload=payload.model_dump(mode="json"),
    )
    return {
        "phase14-formal-closeout.json": envelope.model_dump_json(indent=2).encode(),
        "phase14-formal-closeout.md": (
            "# Phase 14 Formal Closeout\n\n- phase_status: pass\n"
            "- database_revision: 0001\n"
        ).encode(),
        "phase14_incident_attestation.json": attestation.model_dump_json(indent=2).encode(),
    }


def archive_bytes(files: dict[str, bytes] | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in (files or formal_files()).items():
            archive.writestr(name, content)
    return output.getvalue()


def test_offline_verification_is_never_authoritative(tmp_path: Path):
    for name, content in formal_files().items():
        (tmp_path / name).write_bytes(content)
    result = verify_offline(tmp_path)
    assert result.artifact_content_valid is True
    assert result.github_artifact_origin_verified is False
    assert result.authoritative_closeout_verified is False


@pytest.mark.parametrize("name", ["../escape", ".hidden", "extra.json"])
def test_archive_rejects_unsafe_or_unknown_member(name):
    files = formal_files()
    files[name] = b"unsafe"
    with pytest.raises(ValueError):
        extract_formal_archive(archive_bytes(files))


class NetworkStream:
    def __init__(self, address="93.184.216.34"):
        self.address = address

    def get_extra_info(self, name):
        if name == "server_addr":
            return (self.address, 443)
        return None


class RawStream(httpx.SyncByteStream):
    def __init__(self, body):
        self.body = body

    def __iter__(self):
        yield self.body


def download_clients(body, *, encoding=None, peer="93.184.216.34"):
    def api_handler(request):
        return httpx.Response(302, headers={"location": "https://artifact.example/file.zip"})

    def download_handler(request):
        headers = {
            "content-type": "application/zip",
            "content-length": str(len(body)),
        }
        if encoding is not None:
            headers["content-encoding"] = encoding
        return httpx.Response(
            200,
            headers=headers,
            stream=RawStream(body),
            extensions={"network_stream": NetworkStream(peer)},
        )

    api = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(api_handler))
    factory = lambda: httpx.Client(transport=httpx.MockTransport(download_handler))
    return api, factory


def test_download_accepts_missing_content_encoding_and_raw_digest():
    body = archive_bytes()
    api, factory = download_clients(body)
    with api:
        downloaded = download_archive(
            api,
            repository="acme/repo",
            artifact_id="50",
            expected_size=len(body),
            expected_digest=hashlib.sha256(body).hexdigest(),
            resolver=lambda host, port: frozenset({"93.184.216.34"}),
            download_client_factory=factory,
        )
    assert downloaded == body


def test_download_rejects_encoded_body_and_peer_mismatch():
    body = archive_bytes()
    api, factory = download_clients(body, encoding="gzip")
    with api, pytest.raises(ValueError, match="content_encoding"):
        download_archive(
            api,
            repository="acme/repo",
            artifact_id="50",
            expected_size=len(body),
            expected_digest=hashlib.sha256(body).hexdigest(),
            resolver=lambda host, port: frozenset({"93.184.216.34"}),
            download_client_factory=factory,
        )
    api, factory = download_clients(body, peer="93.184.216.35")
    with api, pytest.raises(ValueError, match="peer_mismatch"):
        download_archive(
            api,
            repository="acme/repo",
            artifact_id="50",
            expected_size=len(body),
            expected_digest=hashlib.sha256(body).hexdigest(),
            resolver=lambda host, port: frozenset({"93.184.216.34"}),
            download_client_factory=factory,
        )


def api_payloads(*, workflow_state="disabled_manually", formal_status="completed"):
    jobs = []
    for index, name in enumerate(JOBS, start=1):
        jobs.append(
            {
                "id": index,
                "run_id": 20,
                "name": name,
                "head_sha": SHA,
                "status": formal_status if name == "formal-closeout" else "completed",
                "conclusion": (
                    "success"
                    if name != "formal-closeout" or formal_status == "completed"
                    else None
                ),
                "check_run_url": f"https://api.github.com/repos/acme/repo/check-runs/{index + 10}",
            }
        )
    return {
        "/repos/acme/repo/actions/artifacts/50": {
            "id": 50,
            "name": f"phase14-formal-closeout-10-20-1-{SHA}",
            "expired": False,
            "digest": "sha256:" + "d" * 64,
            "size_in_bytes": 123,
            "workflow_run": {
                "id": 20,
                "repository_id": 10,
                "head_repository_id": 10,
                "head_sha": SHA,
            },
        },
        "/repos/acme/repo/actions/runs/20": {
            "id": 20,
            "repository": {"id": 10},
            "head_repository": {"id": 10},
            "event": "workflow_dispatch",
            "run_attempt": 1,
            "head_sha": SHA,
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
            "workflow_id": 40,
            "path": ".github/workflows/ci.yml@refs/heads/main",
        },
        "/repos/acme/repo/actions/workflows/40": {
            "id": 40,
            "path": ".github/workflows/ci.yml",
            "state": workflow_state,
        },
        "/repos/acme/repo/actions/runs/20/attempts/1/jobs?per_page=100&page=1": {
            "total_count": 6,
            "jobs": jobs,
        },
    }


def client_for(payloads):
    def handler(request: httpx.Request):
        payload = payloads.get(request.url.raw_path.decode())
        if payload is None:
            return httpx.Response(404)
        return httpx.Response(200, json=payload)

    return httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))


def verify(payloads):
    with client_for(payloads) as client:
        return verify_online(
            client,
            repository="acme/repo",
            repository_id="10",
            artifact_id="50",
            expected_artifact_name=f"phase14-formal-closeout-10-20-1-{SHA}",
            workflow_run_id="20",
            subject_commit_sha=SHA,
            expected_protected_ref="refs/heads/main",
            expected_environment_name="phase14-incident-closure",
            expected_workflow_path=".github/workflows/ci.yml",
            downloader=lambda *args, **kwargs: archive_bytes(),
        )


def test_online_verifier_accepts_workflow_disabled_after_closeout():
    result = verify(api_payloads(workflow_state="disabled_manually"))
    assert result.authoritative_closeout_verified is True
    assert result.workflow_current_state == "disabled_manually"


def test_online_verifier_rejects_formal_job_still_running():
    with pytest.raises(ValueError, match="workflow_job_origin_invalid:formal-closeout"):
        verify(api_payloads(formal_status="in_progress"))


def test_online_verifier_rejects_workflow_path_change():
    payloads = api_payloads()
    payloads["/repos/acme/repo/actions/workflows/40"]["path"] = ".github/workflows/other.yml"
    with pytest.raises(ValueError, match="workflow_identity_mismatch"):
        verify(payloads)
