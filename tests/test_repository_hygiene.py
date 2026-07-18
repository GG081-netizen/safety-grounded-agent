from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_current_tree_scan_is_independent_and_finds_untracked_secret(
    tmp_path: Path,
) -> None:
    module = _load_script("check_repository_hygiene.py")
    module.ROOT = tmp_path
    unsafe = "sk-" + "A" * 32
    (tmp_path / "unsafe.txt").write_text(unsafe + "\n", encoding="utf-8")

    result = module.scan_current_tree()

    assert result.scope == "source-tree"
    assert result.status == "fail"
    assert result.findings[0].rule_id == "api_key"


def test_git_scopes_are_blocked_when_real_repository_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("check_repository_hygiene.py")

    def unavailable(*args, **kwargs):
        raise subprocess.CalledProcessError(128, args[0])

    monkeypatch.setattr(module.subprocess, "run", unavailable)

    assert module.scan_tracked_files().status == "blocked"
    assert module.git_history_status().status == "blocked"


def test_current_tree_scan_does_not_promote_git_scopes(tmp_path: Path) -> None:
    module = _load_script("check_repository_hygiene.py")
    module.ROOT = tmp_path
    (tmp_path / "safe.txt").write_text("safe\n", encoding="utf-8")

    current = module.scan_current_tree()
    history = module.git_history_status()

    assert current.status == "pass"
    assert history.status == "blocked"


def test_approved_local_secret_store_is_not_content_scanned(tmp_path: Path) -> None:
    module = _load_script("check_repository_hygiene.py")
    module.ROOT = tmp_path
    (tmp_path / ".gitignore").write_text(".env\n.env.local\n", encoding="utf-8")
    secret = tmp_path / ".env"
    secret.write_text("API_KEY=not-for-source-scan\n", encoding="utf-8")
    secret.chmod(0o600)

    assert module.scan_current_tree().status == "pass"
    assert module.approved_local_secret_store_status().status == "pass"
