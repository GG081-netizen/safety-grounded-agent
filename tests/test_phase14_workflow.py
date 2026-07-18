import importlib.util
import sys
from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def load_trust_module():
    path = ROOT / "scripts" / "verify_phase14_github_trust.py"
    spec = importlib.util.spec_from_file_location("phase14_github_trust_script", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workflow_yaml_parses_to_expected_jobs():
    parsed = yaml.safe_load(workflow_text())

    assert isinstance(parsed, dict)
    assert set(parsed["jobs"]) >= {
        "test",
        "secret-scan",
        "postgres-integration",
        "operational-postgres",
        "incident-closure",
        "formal-closeout",
    }


def test_phase14_formal_chain_is_fresh_github_com_bound():
    text = workflow_text()

    assert "if: github.event_name == 'workflow_dispatch' && github.run_attempt == 1" in text
    assert text.count("PHASE14_CURRENT_CHECK_RUN_ID: ${{ job.check_run_id }}") == 6
    assert "verify_phase14_github_trust.py --mode incident" in text
    assert "verify_phase14_github_trust.py --mode formal" in text
    assert "GITHUB_SERVER_URL" in (ROOT / "scripts" / "verify_phase14_github_trust.py").read_text(
        encoding="utf-8"
    )


def test_incident_job_is_candidate_only_and_formal_job_is_authoritative():
    text = workflow_text()
    incident_start = text.index("  incident-closure:")
    formal_start = text.index("  formal-closeout:")
    incident = text[incident_start:formal_start]
    formal = text[formal_start:]

    assert "--scope incident-evidence --strict" in incident
    assert "--scope phase --strict" not in incident
    assert "--scope phase --strict" in formal
    assert "Upload authoritative formal closeout" in formal
    assert formal.index("Upload authoritative formal closeout") < formal.index(
        "Publish authoritative Phase resolution"
    )


def test_required_jobs_and_minimum_permissions_are_declared():
    text = workflow_text()

    assert "permissions:\n  contents: read" in text
    assert "needs: [test, secret-scan, postgres-integration, operational-postgres]" in text
    assert (
        "needs: [test, secret-scan, postgres-integration, operational-postgres, incident-closure]"
        in text
    )
    assert "write-all" not in text
    assert "actions: write" not in text
    assert "contents: write" not in text


def test_formal_artifact_has_fail_closed_upload_contract():
    text = workflow_text()
    formal = text[text.index("  formal-closeout:") :]

    assert "if-no-files-found: error" in formal
    assert "retention-days: 90" in formal
    assert "phase14-formal-closeout-${{ github.repository_id }}-${{ github.run_id }}-${{ github.run_attempt }}-${{ github.sha }}" in formal
    assert "tmp/phase14-formal-artifact" in formal
    assert "check_phase14_formal_staging.py" in formal
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in formal
    assert "authoritative_resolution_source = formal-closeout" in formal


def test_github_enterprise_server_is_explicitly_unsupported(tmp_path, monkeypatch):
    module = load_trust_module()
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.enterprise.example")
    monkeypatch.setattr(
        sys,
        "argv",
        ["verify_phase14_github_trust.py", "--mode", "incident", "--output", str(tmp_path / "out.json")],
    )

    with pytest.raises(RuntimeError, match="unsupported_github_platform"):
        module.main()


def test_rerun_attempt_is_rejected_before_github_api_access(tmp_path, monkeypatch):
    module = load_trust_module()
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setattr(
        sys,
        "argv",
        ["verify_phase14_github_trust.py", "--mode", "formal", "--output", str(tmp_path / "out.json")],
    )

    with pytest.raises(RuntimeError, match="formal_closeout_requires_fresh_run"):
        module.main()


def test_required_job_commit_mismatch_is_rejected():
    module = load_trust_module()
    jobs = {
        "jobs": [
            {
                "id": 10,
                "run_id": 1,
                "name": "test",
                "status": "completed",
                "conclusion": "success",
                "head_sha": "b" * 40,
                "check_run_url": "https://api.github.com/repos/acme/repo/check-runs/20",
            }
        ]
    }

    with pytest.raises(RuntimeError, match="required_job_commit_mismatch:test"):
        module.verify_completed_jobs(
            jobs,
            ("test",),
            workflow_run_id="1",
            subject_commit_sha="a" * 40,
        )


def test_incident_and_formal_only_accept_completed_predecessors():
    module = load_trust_module()
    jobs = {
        "jobs": [
            {
                "id": 10,
                "run_id": 1,
                "name": "test",
                "status": "completed",
                "conclusion": "success",
                "head_sha": "a" * 40,
                "check_run_url": "https://api.github.com/repos/acme/repo/check-runs/20",
            }
        ]
    }
    identities = module.verify_completed_jobs(
        jobs,
        ("test",),
        workflow_run_id="1",
        subject_commit_sha="a" * 40,
    )
    assert identities[0]["workflow_job_id"] == "10"
    jobs["jobs"][0]["status"] = "in_progress"
    jobs["jobs"][0]["conclusion"] = None
    with pytest.raises(RuntimeError, match="required_job_not_successful:test"):
        module.verify_completed_jobs(
            jobs,
            ("test",),
            workflow_run_id="1",
            subject_commit_sha="a" * 40,
        )
