from pathlib import Path
import re
import tomllib

import pytest


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PYPROJECT = ROOT / "pyproject.toml"
REPOSITORY = "https://github.com/GG081-netizen/safety-grounded-agent"


def test_readme_uses_fixed_portfolio_section_order() -> None:
    text = README.read_text(encoding="utf-8")
    headings = [
        "## Product",
        "## Capabilities",
        "## Architecture",
        "## Examples",
        "## Evaluation",
        "## Quick Start",
        "## Limitations",
        "## Engineering History",
    ]

    positions = [text.index(heading) for heading in headings]

    assert positions == sorted(positions)
    assert re.findall(r"^## .+$", text, flags=re.MULTILINE) == headings
    assert text.startswith("# 企业采购销售安全编排系统\n# Safety-Grounded Enterprise Agent")
    assert "Phase 15-E 才会生成" in text
    assert "examples/procurement-planning" in text


def test_readme_current_links_use_renamed_repository() -> None:
    text = README.read_text(encoding="utf-8")

    assert f"git clone {REPOSITORY}.git" in text
    assert f"{REPOSITORY}/actions/workflows/ci.yml" in text
    assert f"[{REPOSITORY.split('/')[-1]}]" not in text
    assert "GG081-netizen/crispy-fortnight-baseline-2" in text
    assert "该仓库后来" in text


def test_readme_relative_links_resolve_inside_repository() -> None:
    text = README.read_text(encoding="utf-8")
    relative_targets = [
        target
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text)
        if "://" not in target and not target.startswith("#")
    ]

    assert relative_targets
    for target in relative_targets:
        path = (ROOT / target.split("#", 1)[0]).resolve()
        assert path.is_relative_to(ROOT.resolve())
        assert path.exists(), target


def test_pyproject_exposes_current_repository_metadata() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]

    assert project["readme"] == "README.md"
    assert project["urls"] == {
        "Homepage": REPOSITORY,
        "Repository": REPOSITORY,
        "Documentation": f"{REPOSITORY}/tree/main/docs",
        "Issues": f"{REPOSITORY}/issues",
    }
    assert "Safety-grounded orchestration" in project["description"]
