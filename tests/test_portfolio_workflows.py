from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_PATH = ROOT / ".github" / "workflows" / "portfolio-release-gates.yml"
EXPERIMENTAL_PATHS = (
    ROOT / ".github" / "workflows" / "phase14-baseline-discovery.yml",
    ROOT / ".github" / "workflows" / "phase14-baseline-closeout.yml",
)
PORTFOLIO_JOBS = {
    "unit-and-contract",
    "policy-and-rag-evaluation",
    "secret-and-source-tree",
    "package-build",
    "postgres-integration",
}
REQUIRED_CHECK_CONTEXTS = {
    f"Portfolio CI / {job}" for job in PORTFOLIO_JOBS
}


def _triggers(document: dict) -> dict:
    return document.get("on", document.get(True))


def test_portfolio_ci_has_exact_stable_check_contract() -> None:
    document = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))

    assert document["name"] == "Portfolio CI"
    assert set(document["jobs"]) == PORTFOLIO_JOBS
    assert set(_triggers(document)) == {"push", "pull_request", "workflow_dispatch"}
    actual_contexts = {
        f"{document['name']} / {job['name']}"
        for job in document["jobs"].values()
    }
    assert actual_contexts == REQUIRED_CHECK_CONTEXTS
    assert all("strategy" not in job for job in document["jobs"].values())


def test_default_ci_contains_no_phase14_or_operational_chain() -> None:
    text = CI_PATH.read_text(encoding="utf-8")

    assert "phase14" not in text.lower()
    assert "incident-closure" not in text
    assert "formal-closeout" not in text
    assert "operational-postgres" not in text
    assert "CONVAGENT_ALLOW_DESTRUCTIVE_DB_TESTS: \"true\"" not in text
    assert "environment:" not in text


def test_gitleaks_runtime_files_are_isolated_to_runner_temp() -> None:
    text = CI_PATH.read_text(encoding="utf-8")

    assert text.count('$RUNNER_TEMP/phase15-gitleaks') == 4
    assert "--output gitleaks.tar.gz" not in text
    assert "tar --extract --gzip --file gitleaks.tar.gz" not in text
    assert "--temporary-directory \"$GITLEAKS_DIR\"" in text
    assert "source-tree-before.json" in text
    assert "source-tree-after.json" in text
    assert 'cmp "$GITLEAKS_DIR/source-tree-before.json"' in text


def test_release_gates_are_separate_and_not_required_checks() -> None:
    document = yaml.safe_load(RELEASE_PATH.read_text(encoding="utf-8"))

    assert document["name"] == "Portfolio Release Gates"
    assert set(_triggers(document)) == {"workflow_dispatch", "schedule"}
    assert set(document["jobs"]) == {"destructive-postgres", "operational-postgres"}
    text = RELEASE_PATH.read_text(encoding="utf-8")
    assert "environment:" not in text
    assert "0001 (head)" in text
    assert "--suite-identity postgres-destructive" in text
    assert "--suite-identity operational-postgres" in text


def test_phase14_workflows_are_manual_only_experiments() -> None:
    for path in EXPERIMENTAL_PATHS:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        text = path.read_text(encoding="utf-8")
        assert document["name"].startswith("[Experimental] Phase 14")
        assert set(_triggers(document)) == {"workflow_dispatch"}
        assert document["permissions"] == {"contents": "read"}
        assert "environment:" not in text
