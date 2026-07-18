from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
import httpx
from pydantic import ValidationError

from conversation_agent.evaluation.phase14_baseline import (
    BASELINE_FILES,
    BASELINE_JOBS,
    DISCOVERY_FILES,
    ApprovalAttestationSummary,
    BaselineArtifactDocumentV1,
    BaselineCloseoutPayloadV1,
    BaselineFormalInputManifestV1,
    CandidateManifestV1,
    DiscoveryArtifactBindingV1,
    DiscoveryEvidenceV1,
    DiscoveryJobIdentity,
    FormalBaselineJobIdentity,
    build_candidate_manifest,
    build_index_manifest,
    candidate_manifest_sha256,
    validate_completed_formal_job,
    validate_discovery_runtime_self_job,
    validate_formal_runtime_self_job,
)

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
TREE = "b" * 40
NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)


def init_unborn_repository(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def job(name: str, *, status="in_progress", conclusion=None, number=1):
    return {
        "id": number,
        "run_id": 20,
        "name": name,
        "head_sha": SHA,
        "status": status,
        "conclusion": conclusion,
        "check_run_url": f"https://api.github.com/repos/acme/repo/check-runs/{number + 100}",
    }


def test_candidate_manifest_is_deterministic_and_excludes_secret_store(tmp_path: Path):
    init_unborn_repository(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "workflow.yml").write_text("name: test\n")
    (tmp_path / ".env").write_text("SECRET=not-read")
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "manifest.json").write_text("self")
    (tmp_path / ".gitignore").write_text(".env\ntmp/\n", encoding="utf-8")

    first = build_candidate_manifest(tmp_path)
    second = build_candidate_manifest(tmp_path)

    assert first == second
    assert [entry.path for entry in first.entries] == [
        ".github/workflow.yml",
        ".gitignore",
        "src/a.py",
    ]
    assert candidate_manifest_sha256(first) == candidate_manifest_sha256(second)


def test_candidate_manifest_rejects_symlink(tmp_path: Path):
    init_unborn_repository(tmp_path)
    target = tmp_path / "target"
    target.write_text("x")
    (tmp_path / "link").symlink_to(target)
    with pytest.raises(ValueError, match="candidate_manifest_symlink"):
        build_candidate_manifest(tmp_path)


def test_candidate_manifest_uses_git_ignore_negation_and_nested_rules(tmp_path: Path):
    init_unborn_repository(tmp_path)
    (tmp_path / ".gitignore").write_text(
        "ignored/*\n!ignored/keep.txt\ncache/\n", encoding="utf-8"
    )
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "drop.txt").write_text("drop", encoding="utf-8")
    (tmp_path / "ignored" / "keep.txt").write_text("keep", encoding="utf-8")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "drop.txt").write_text("drop", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / ".gitignore").write_text(
        "*.tmp\n!keep.tmp\n", encoding="utf-8"
    )
    (tmp_path / "nested" / "drop.tmp").write_text("drop", encoding="utf-8")
    (tmp_path / "nested" / "keep.tmp").write_text("keep", encoding="utf-8")

    manifest = build_candidate_manifest(tmp_path)

    assert [entry.path for entry in manifest.entries] == [
        ".gitignore",
        "ignored/keep.txt",
        "nested/.gitignore",
        "nested/keep.tmp",
    ]


def test_candidate_manifest_isolates_configured_global_excludes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    init_unborn_repository(tmp_path)
    excluded = tmp_path / "global-ignore"
    excluded.write_text("global-only.txt\n", encoding="utf-8")
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text(
        f"[core]\n\texcludesFile = {excluded.as_posix()}\n", encoding="utf-8"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    (tmp_path / "global-only.txt").write_text("included", encoding="utf-8")

    manifest = build_candidate_manifest(tmp_path)

    assert "global-only.txt" in {entry.path for entry in manifest.entries}


def test_candidate_manifest_rejects_effective_info_exclude_rule(tmp_path: Path):
    init_unborn_repository(tmp_path)
    info_exclude = tmp_path / ".git" / "info" / "exclude"
    info_exclude.write_text("# comments are allowed\nprivate.txt\n", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("visible", encoding="utf-8")

    with pytest.raises(ValueError, match="candidate_manifest_info_exclude_not_empty"):
        build_candidate_manifest(tmp_path)


def test_candidate_and_index_manifests_are_identical(tmp_path: Path):
    init_unborn_repository(tmp_path)
    (tmp_path / ".gitignore").write_text("*.bak\n", encoding="utf-8")
    (tmp_path / "tracked.txt").write_text("tracked", encoding="utf-8")
    (tmp_path / "local.bak").write_text("ignored", encoding="utf-8")
    candidate = build_candidate_manifest(tmp_path)
    subprocess.run(
        ["git", "add", "--all"], cwd=tmp_path, check=True, capture_output=True
    )

    index = build_index_manifest(tmp_path)

    assert candidate == index
    assert candidate_manifest_sha256(candidate) == candidate_manifest_sha256(index)


@pytest.mark.parametrize(
    ("status", "conclusion", "accepted"),
    [
        ("queued", None, True),
        ("in_progress", None, True),
        ("completed", "success", False),
        ("completed", "failure", False),
    ],
)
def test_runtime_job_cannot_self_attest_completion(status, conclusion, accepted):
    value = job("test", status=status, conclusion=conclusion)
    if accepted:
        identity = validate_formal_runtime_self_job(
            value, expected_name="test", run_id="20", sha=SHA, check_run_id="101"
        )
        assert identity.workflow_job_id == "1"
    else:
        with pytest.raises(ValueError, match="baseline_illegal_self_attestation"):
            validate_formal_runtime_self_job(
                value, expected_name="test", run_id="20", sha=SHA, check_run_id="101"
            )


def test_completed_predecessor_requires_success():
    identity = validate_completed_formal_job(
        job("test", status="completed", conclusion="success"),
        expected_name="test",
        run_id="20",
        sha=SHA,
    )
    assert identity.check_run_id == "101"
    with pytest.raises(ValueError, match="baseline_required_job_not_successful"):
        validate_completed_formal_job(
            job("test"), expected_name="test", run_id="20", sha=SHA
        )


def test_discovery_runtime_identity_accepts_only_discovery():
    identity = validate_discovery_runtime_self_job(
        job("discovery"), run_id="20", sha=SHA, check_run_id="101"
    )
    assert identity == DiscoveryJobIdentity(
        job_name="discovery", workflow_job_id="1", check_run_id="101"
    )
    for name in BASELINE_JOBS:
        with pytest.raises(ValueError, match="baseline_illegal_self_attestation"):
            validate_discovery_runtime_self_job(
                job(name), run_id="20", sha=SHA, check_run_id="101"
            )


def test_formal_identity_rejects_discovery():
    with pytest.raises(ValidationError, match="literal_error"):
        FormalBaselineJobIdentity(
            job_name="discovery", workflow_job_id="1", check_run_id="101"
        )
    with pytest.raises(ValueError, match="baseline_illegal_self_attestation"):
        validate_formal_runtime_self_job(
            job("discovery"),
            expected_name="test",
            run_id="20",
            sha=SHA,
            check_run_id="101",
        )


def test_discovery_evidence_forbids_artifact_self_metadata():
    values = {
        "authoritative": False,
        "discovery_status": "pass",
        "repository": "GG081-netizen/crispy-fortnight-baseline-2",
        "repository_id": "1305192784",
        "workflow_id": "10",
        "workflow_path": ".github/workflows/phase14-baseline-discovery.yml",
        "workflow_run_id": "20",
        "workflow_run_attempt": "1",
        "subject_commit_sha": SHA,
        "subject_tree_sha": TREE,
        "candidate_manifest_sha256": "c" * 64,
        "producer_job_id": "30",
        "producer_check_run_id": "40",
        "generated_at": NOW,
        "artifact_id": "50",
    }
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DiscoveryEvidenceV1(**values)


def test_discovery_runtime_fixture_generates_exact_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module_path = ROOT / "scripts" / "create_phase14_baseline_evidence.py"
    spec = importlib.util.spec_from_file_location("baseline_evidence_creator", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = CandidateManifestV1(
        entries=(
            {
                "path": "README.md",
                "git_mode": "100644",
                "size_bytes": 1,
                "sha256": hashlib.sha256(b"x").hexdigest(),
            },
        )
    )
    manifest_path = tmp_path / "candidate.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    output = tmp_path / "artifact"
    for name, value in {
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REPOSITORY": "GG081-netizen/crispy-fortnight-baseline-2",
        "GITHUB_REPOSITORY_ID": "1305192784",
        "GITHUB_ACTOR": "GG081-netizen",
        "GITHUB_RUN_ID": "20",
        "GITHUB_SHA": SHA,
        "PHASE14_CURRENT_CHECK_RUN_ID": "101",
    }.items():
        monkeypatch.setenv(name, value)
    payloads = {
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/actions/runs/20/attempts/1/jobs?per_page=100&page=1": {
            "total_count": 1,
            "jobs": [job("discovery")],
        },
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/actions/runs/20": {
            "workflow_id": 11
        },
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/actions/workflows/11": {
            "path": ".github/workflows/phase14-baseline-discovery.yml",
            "state": "active",
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = payloads.get(request.url.raw_path.decode())
        return httpx.Response(200, json=payload) if payload else httpx.Response(404)

    client = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    )
    monkeypatch.setattr(module, "api_client", lambda: client)
    monkeypatch.setattr(module, "git_tree_sha", lambda: TREE)

    module.discovery(SimpleNamespace(candidate_manifest=manifest_path, output=output))

    assert {item.name for item in output.iterdir()} == DISCOVERY_FILES
    evidence = DiscoveryEvidenceV1.model_validate_json(
        (output / "phase14-discovery-evidence.json").read_text(encoding="utf-8")
    )
    assert evidence.producer_job_id == "1"
    assert evidence.producer_check_run_id == "101"


def sample_document() -> tuple[BaselineArtifactDocumentV1, CandidateManifestV1]:
    manifest = CandidateManifestV1(
        entries=(
            {
                "path": "README.md",
                "git_mode": "100644",
                "size_bytes": 1,
                "sha256": hashlib.sha256(b"x").hexdigest(),
            },
        )
    )
    binding = DiscoveryArtifactBindingV1(
        repository="GG081-netizen/crispy-fortnight-baseline-2",
        repository_id="1305192784",
        workflow_run_id="12",
        workflow_run_attempt="1",
        subject_commit_sha=SHA,
        artifact_id="50",
        artifact_name="discovery",
        artifact_digest="sha256:" + "d" * 64,
        artifact_size_in_bytes=500,
        artifact_content_verified=True,
        github_artifact_origin_verified=True,
    )
    approval = ApprovalAttestationSummary(
        approval_verified=True,
        approval_actor_differs_from_trigger=True,
        approval_environment_id="70",
        approval_event_count=1,
    )
    identities = tuple(
        FormalBaselineJobIdentity(
            job_name=name, workflow_job_id=str(index), check_run_id=str(index + 100)
        )
        for index, name in enumerate(BASELINE_JOBS, start=1)
    )
    payload = BaselineCloseoutPayloadV1(
        authoritative=False,
        repository_baseline_candidate_status="pass",
        phase14_authoritative_phase_status="blocked",
        repository="GG081-netizen/crispy-fortnight-baseline-2",
        repository_id="1305192784",
        workflow_id="80",
        workflow_path=".github/workflows/phase14-baseline-closeout.yml",
        workflow_run_id="90",
        workflow_run_attempt="1",
        subject_commit_sha=SHA,
        subject_tree_sha=TREE,
        candidate_manifest_sha256=candidate_manifest_sha256(manifest),
        discovery_binding=binding,
        approval_attestation_summary=approval,
        required_jobs=identities,
        baseline_closeout_job_id="6",
        baseline_closeout_check_run_id="106",
        baseline_closeout_runtime_status="in_progress",
        baseline_closeout_runtime_conclusion=None,
        database_revision="0001",
        generated_at=NOW,
    )
    document = BaselineArtifactDocumentV1(
        closeout_payload=payload,
        formal_input_manifest=BaselineFormalInputManifestV1(
            candidate_manifest_sha256=candidate_manifest_sha256(manifest),
            discovery_workflow_id="11",
            discovery_workflow_path=".github/workflows/phase14-baseline-discovery.yml",
            discovery_run_id="12",
            discovery_run_attempt="1",
            discovery_artifact_id="50",
            discovery_artifact_name="discovery",
            discovery_artifact_digest="sha256:" + "d" * 64,
        ),
        repository_baseline_attestation={},
        approval_attestation_summary=approval,
        producer_evidence_references=(),
    )
    return document, manifest


def test_formal_payload_forbids_own_artifact_metadata():
    document, _ = sample_document()
    values = document.closeout_payload.model_dump()
    values["formal_artifact_id"] = "500"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        BaselineCloseoutPayloadV1.model_validate(values)


def test_offline_verification_never_claims_github_origin(tmp_path: Path):
    module_path = ROOT / "scripts" / "verify_phase14_repository_baseline.py"
    spec = importlib.util.spec_from_file_location("baseline_verifier", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    document, manifest = sample_document()
    (tmp_path / "phase14-baseline-closeout.json").write_text(
        document.model_dump_json(), encoding="utf-8"
    )
    (tmp_path / "phase14-candidate-manifest.json").write_text(
        manifest.model_dump_json(), encoding="utf-8"
    )
    (tmp_path / "phase14-baseline-closeout.md").write_text("diagnostic")
    result = module.verify_offline(tmp_path)
    assert result == {
        "artifact_content_valid": True,
        "github_artifact_origin_verified": False,
        "authoritative_baseline_verified": False,
    }


def test_baseline_workflows_parse_and_freeze_job_contracts():
    discovery_path = ROOT / ".github" / "workflows" / "phase14-baseline-discovery.yml"
    formal_path = ROOT / ".github" / "workflows" / "phase14-baseline-closeout.yml"
    discovery = yaml.safe_load(discovery_path.read_text(encoding="utf-8"))
    formal = yaml.safe_load(formal_path.read_text(encoding="utf-8"))
    assert set(discovery["jobs"]) == {"discovery"}
    assert tuple(formal["jobs"]) == BASELINE_JOBS
    assert discovery["name"] == "[Experimental] Phase 14 Discovery"
    assert formal["name"] == "[Experimental] Phase 14 Formal Closeout"
    text = formal_path.read_text(encoding="utf-8")
    assert "environment:" not in text
    assert "CONVAGENT_DASHSCOPE_API_KEY" not in text
    assert "permissions: write-all" not in text
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in text
    for name in (
        "candidate_manifest_sha256",
        "expected_root_commit_sha",
        "expected_root_tree_sha",
        "discovery_workflow_id",
        "discovery_workflow_path",
        "discovery_run_id",
        "discovery_run_attempt",
        "discovery_artifact_id",
        "discovery_artifact_name",
        "discovery_artifact_digest",
    ):
        assert name in text


def test_baseline_archive_contract_is_exactly_three_files():
    assert BASELINE_FILES == {
        "phase14-baseline-closeout.json",
        "phase14-baseline-closeout.md",
        "phase14-candidate-manifest.json",
    }


def test_discovery_archive_contract_is_exactly_two_files():
    assert DISCOVERY_FILES == {
        "phase14-discovery-evidence.json",
        "phase14-candidate-manifest.json",
    }


def _zip(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


def _online_fixture():
    module_path = ROOT / "scripts" / "verify_phase14_repository_baseline.py"
    spec = importlib.util.spec_from_file_location("baseline_online_verifier", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    document, manifest = sample_document()
    discovery = DiscoveryEvidenceV1(
        authoritative=False,
        discovery_status="pass",
        repository="GG081-netizen/crispy-fortnight-baseline-2",
        repository_id="1305192784",
        workflow_id="11",
        workflow_path=".github/workflows/phase14-baseline-discovery.yml",
        workflow_run_id="12",
        workflow_run_attempt="1",
        subject_commit_sha=SHA,
        subject_tree_sha=TREE,
        candidate_manifest_sha256=candidate_manifest_sha256(manifest),
        producer_job_id="30",
        producer_check_run_id="40",
        generated_at=NOW,
    )
    discovery_archive = _zip(
        {
            "phase14-discovery-evidence.json": discovery.model_dump_json().encode(),
            "phase14-candidate-manifest.json": manifest.model_dump_json().encode(),
        }
    )
    formal_archive = _zip(
        {
            "phase14-baseline-closeout.json": document.model_dump_json().encode(),
            "phase14-baseline-closeout.md": b"diagnostic",
            "phase14-candidate-manifest.json": manifest.model_dump_json().encode(),
        }
    )
    formal_jobs = [
        {
            "id": index,
            "run_id": 90,
            "name": name,
            "head_sha": SHA,
            "status": "completed",
            "conclusion": "success",
            "check_run_url": f"https://api.github.com/repos/x/y/check-runs/{index + 100}",
        }
        for index, name in enumerate(BASELINE_JOBS, start=1)
    ]
    payloads = {
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/actions/artifacts/500": {
            "id": 500,
            "name": "formal-artifact",
            "expired": False,
            "digest": "sha256:" + "e" * 64,
            "size_in_bytes": len(formal_archive),
            "workflow_run": {
                "id": 90,
                "repository_id": 1305192784,
                "head_repository_id": 1305192784,
                "head_sha": SHA,
            },
        },
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/actions/artifacts/50": {
            "id": 50,
            "name": "discovery",
            "expired": False,
            "digest": "sha256:" + "d" * 64,
            "size_in_bytes": 500,
            "workflow_run": {
                "id": 12,
                "repository_id": 1305192784,
                "head_repository_id": 1305192784,
                "head_sha": SHA,
            },
        },
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/actions/runs/90": {
            "id": 90,
            "repository": {"id": 1305192784},
            "head_repository": {"id": 1305192784},
            "actor": {"login": "GG081-netizen"},
            "event": "workflow_dispatch",
            "run_attempt": 1,
            "head_sha": SHA,
            "head_branch": "main",
            "path": ".github/workflows/phase14-baseline-closeout.yml@refs/heads/main",
            "status": "completed",
            "conclusion": "success",
            "workflow_id": 80,
        },
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/actions/runs/12": {
            "id": 12,
            "repository": {"id": 1305192784},
            "head_repository": {"id": 1305192784},
            "actor": {"login": "GG081-netizen"},
            "event": "workflow_dispatch",
            "run_attempt": 1,
            "head_sha": SHA,
            "head_branch": "main",
            "path": ".github/workflows/phase14-baseline-discovery.yml@refs/heads/main",
            "status": "completed",
            "conclusion": "success",
            "workflow_id": 11,
        },
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/actions/workflows/80": {
            "id": 80,
            "path": ".github/workflows/phase14-baseline-closeout.yml",
            "state": "active",
        },
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/actions/workflows/11": {
            "id": 11,
            "path": ".github/workflows/phase14-baseline-discovery.yml",
            "state": "active",
        },
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/actions/runs/90/attempts/1/jobs?per_page=100&page=1": {
            "total_count": 6,
            "jobs": formal_jobs,
        },
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/actions/runs/12/attempts/1/jobs?per_page=100&page=1": {
            "total_count": 1,
            "jobs": [
                {
                    "id": 30,
                    "run_id": 12,
                    "name": "discovery",
                    "head_sha": SHA,
                    "status": "completed",
                    "conclusion": "success",
                    "check_run_url": "https://api.github.com/repos/x/y/check-runs/40",
                }
            ],
        },
        f"/repos/GG081-netizen/crispy-fortnight-baseline-2/git/commits/{SHA}": {
            "sha": SHA,
            "parents": [],
            "tree": {"sha": TREE},
        },
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/git/ref/heads/main": {
            "object": {"sha": SHA}
        },
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/branches?per_page=100": [
            {"name": "main"}
        ],
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/git/matching-refs/tags/": [],
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/environments/phase14-baseline-closeout": {
            "id": 70,
            "name": "phase14-baseline-closeout",
            "protection_rules": [
                {
                    "type": "required_reviewers",
                    "prevent_self_review": True,
                    "reviewers": [
                        {"reviewer": {"login": "toshibanino6-creator"}}
                    ],
                },
                {"type": "branch_policy"},
            ],
            "deployment_branch_policy": {
                "protected_branches": True,
                "custom_branch_policies": False,
            },
        },
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/environments/phase14-baseline-closeout/deployment_protection_rules": {
            "total_count": 0
        },
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/actions/runs/90/approvals": [
            {
                "state": "approved",
                "user": {"login": "toshibanino6-creator"},
                "environments": [
                    {"id": 70, "name": "phase14-baseline-closeout"}
                ],
            }
        ],
    }

    def handler(request: httpx.Request):
        payload = payloads.get(request.url.raw_path.decode())
        return httpx.Response(200, json=payload) if payload is not None else httpx.Response(404)

    client = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    )

    def downloader(*args, **kwargs):
        return discovery_archive if str(kwargs["artifact_id"]) == "50" else formal_archive

    return module, client, downloader, payloads


def test_online_verifier_requires_github_origin_jobs_and_approval():
    module, client, downloader, _ = _online_fixture()
    with client:
        result = module.verify_baseline_online(
            client,
            artifact_id="500",
            artifact_name="formal-artifact",
            run_id="90",
            sha=SHA,
            downloader=downloader,
        )
    assert result["phase14_g_repository_baseline_status"] == "pass"
    assert result["phase14_authoritative_phase_status"] == "blocked"
    assert result["authoritative_baseline_verified"] is True


def test_online_verifier_rejects_running_formal_job():
    module, client, downloader, payloads = _online_fixture()
    jobs = payloads[
        "/repos/GG081-netizen/crispy-fortnight-baseline-2/actions/runs/90/attempts/1/jobs?per_page=100&page=1"
    ]["jobs"]
    jobs[-1]["status"] = "in_progress"
    jobs[-1]["conclusion"] = None
    with client, pytest.raises(ValueError, match="workflow_job_not_successful"):
        module.verify_baseline_online(
            client,
            artifact_id="500",
            artifact_name="formal-artifact",
            run_id="90",
            sha=SHA,
            downloader=downloader,
        )
