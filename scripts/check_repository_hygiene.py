"""Bounded repository hygiene checks with explicit scan scopes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
}
_FORBIDDEN_TRACKED_PARTS = {
    ".env",
    ".env.local",
    ".env.production",
    ".claude/settings.local.json",
}
_APPROVED_LOCAL_SECRET_STORES = {".env", ".env.local"}
_SENSITIVE_IGNORED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".dump", ".backup"}
_TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_RULES = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b", re.IGNORECASE)),
    (
        "database_dsn",
        re.compile(
            r"postgres(?:ql)?(?:\+[a-z0-9_]+)?://[^\s:/@]+:[^\s@]+@[^\s]+",
            re.IGNORECASE,
        ),
    ),
    (
        "nonempty_secret_env",
        re.compile(
            r"^\s*[A-Z0-9_]*(?:API_KEY|AUTH_TOKEN|ACCESS_TOKEN|PASSWORD|SECRET|DATABASE_URL)\s*=\s*\S+"
        ),
    ),
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule_id: str
    fingerprint: str


@dataclass(frozen=True)
class ScanResult:
    scope: str
    status: str
    files_scanned: int
    findings: tuple[Finding, ...]
    blocking_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "status": self.status,
            "files_scanned": self.files_scanned,
            "finding_count": len(self.findings),
            "findings": [asdict(item) for item in self.findings],
            "blocking_reason": self.blocking_reason,
        }


def _is_obvious_test_value(line: str) -> bool:
    lowered = line.lower()
    return any(
        marker in lowered
        for marker in (
            "canary",
            "not-real",
            "test_only",
            "test-only",
            "localhost/test",
            "127.0.0.1",
            "invalid.token.value",
            "header.payload.signature",
            "secret-token-value",
            ":password@",
            ":secret@",
            ":pass@",
            "db.example",
        )
    )


def _scan_paths(paths: tuple[Path, ...], *, scope: str) -> ScanResult:
    findings: list[Finding] = []
    files_scanned = 0
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        if scope == "tracked-files" and relative in _FORBIDDEN_TRACKED_PARTS:
            digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:12]
            findings.append(Finding(relative, 0, "forbidden_tracked_path", digest))
        if path.suffix.lower() not in _TEXT_SUFFIXES or not path.is_file():
            continue
        if path.stat().st_size > 5_000_000:
            digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:12]
            findings.append(Finding(relative, 0, "oversized_unscanned_file", digest))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        files_scanned += 1
        for line_number, line in enumerate(text.splitlines(), start=1):
            if _is_obvious_test_value(line):
                continue
            for rule_id, pattern in _RULES:
                if rule_id == "nonempty_secret_env" and not (
                    path.name.startswith(".env")
                    or path.suffix.lower() in {".yaml", ".yml", ".toml"}
                ):
                    continue
                match = pattern.search(line)
                if match is None:
                    continue
                fingerprint = hashlib.sha256(
                    match.group(0).encode("utf-8")
                ).hexdigest()[:12]
                findings.append(Finding(relative, line_number, rule_id, fingerprint))
    return ScanResult(
        scope=scope,
        status="fail" if findings else "pass",
        files_scanned=files_scanned,
        findings=tuple(findings),
    )


def scan_current_tree() -> ScanResult:
    paths: list[Path] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in _EXCLUDED_DIRS for part in relative.parts):
            continue
        if relative.as_posix() in _APPROVED_LOCAL_SECRET_STORES:
            continue
        if path.is_symlink():
            continue
        if path.is_file():
            paths.append(path)
    return _scan_paths(tuple(sorted(paths)), scope="source-tree")


def approved_local_secret_store_status() -> ScanResult:
    findings: list[Finding] = []
    gitignore = ROOT / ".gitignore"
    ignored_patterns = set()
    if gitignore.is_file():
        ignored_patterns = {
            line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
    files = 0
    for name in sorted(_APPROVED_LOCAL_SECRET_STORES):
        path = ROOT / name
        if not path.exists():
            continue
        files += 1
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_symlink() or not path.is_file():
            findings.append(Finding(name, 0, "approved_secret_store_not_regular", hashlib.sha256(name.encode()).hexdigest()[:12]))
        if name not in ignored_patterns and ".env.*" not in ignored_patterns:
            findings.append(Finding(name, 0, "approved_secret_store_not_ignored", hashlib.sha256(name.encode()).hexdigest()[:12]))
        if mode != 0o600:
            findings.append(Finding(name, 0, "approved_secret_store_permissions", hashlib.sha256(f"{name}:{mode:o}".encode()).hexdigest()[:12]))
    return ScanResult(
        "approved-local-secret-store",
        "fail" if findings else "pass",
        files,
        tuple(findings),
    )


def ignored_sensitive_files_status() -> ScanResult:
    findings: list[Finding] = []
    files = 0
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in _EXCLUDED_DIRS for part in relative.parts) or not path.is_file():
            continue
        name = relative.as_posix()
        if name in _APPROVED_LOCAL_SECRET_STORES or name == ".env.example":
            continue
        sensitive = (
            path.suffix.lower() in _SENSITIVE_IGNORED_SUFFIXES
            or path.name.startswith(".env.")
        )
        if sensitive:
            files += 1
            findings.append(Finding(name, 0, "unapproved_sensitive_file", hashlib.sha256(name.encode()).hexdigest()[:12]))
    return ScanResult(
        "ignored-sensitive-files",
        "fail" if findings else "pass",
        files,
        tuple(findings),
    )


def scan_tracked_files() -> ScanResult:
    try:
        process = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ScanResult("tracked-files", "blocked", 0, (), "git_unavailable")
    paths = tuple(
        ROOT / item.decode("utf-8")
        for item in process.stdout.split(b"\0")
        if item
    )
    return _scan_paths(paths, scope="tracked-files")


def git_history_status() -> ScanResult:
    try:
        subprocess.run(
            ["git", "rev-list", "--all", "--count"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ScanResult("git-history", "blocked", 0, (), "git_unavailable")
    return ScanResult(
        "git-history",
        "blocked",
        0,
        (),
        "gitleaks_report_required",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scope",
        choices=(
            "current-tree",
            "source-tree",
            "approved-local-secret-store",
            "ignored-sensitive-files",
            "tracked-files",
            "git-history",
            "all",
        ),
        default="current-tree",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-untracked", action="store_true")
    args = parser.parse_args()
    del args.include_untracked

    scanners = {
        "current-tree": scan_current_tree,
        "source-tree": scan_current_tree,
        "approved-local-secret-store": approved_local_secret_store_status,
        "ignored-sensitive-files": ignored_sensitive_files_status,
        "tracked-files": scan_tracked_files,
        "git-history": git_history_status,
    }
    selected = (
        (
            "source-tree",
            "approved-local-secret-store",
            "ignored-sensitive-files",
            "tracked-files",
            "git-history",
        )
        if args.scope == "all"
        else (args.scope,)
    )
    results = [scanners[scope]() for scope in selected]
    if args.json:
        print(json.dumps([result.to_dict() for result in results], indent=2))
    else:
        for result in results:
            print(
                f"{result.scope}: status={result.status} "
                f"files={result.files_scanned} findings={len(result.findings)}"
            )
            for finding in result.findings:
                print(
                    f"{finding.path}:{finding.line} "
                    f"{finding.rule_id} {finding.fingerprint}"
                )
            if result.blocking_reason:
                print(f"blocking_reason={result.blocking_reason}")
    if any(result.status == "fail" for result in results):
        return 2
    if any(result.status == "blocked" for result in results):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
