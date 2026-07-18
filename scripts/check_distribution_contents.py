"""Verify wheel and sdist archives contain only distributable project files."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
_FORBIDDEN_PARTS = {
    ".env",
    ".venv",
    ".claude",
    ".pytest_cache",
    "__pycache__",
    "logs",
    "tmp",
    "backup",
    "backups",
    "data",
}
_FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".dump", ".backup", ".pem", ".key"}


def _unsafe(name: str) -> bool:
    path = Path(name)
    lowered = {part.lower() for part in path.parts}
    return bool(lowered & _FORBIDDEN_PARTS) or path.suffix.lower() in _FORBIDDEN_SUFFIXES


def _members(path: Path) -> tuple[str, ...]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return tuple(archive.namelist())
    with tarfile.open(path, "r:*") as archive:
        return tuple(member.name for member in archive.getmembers())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    archives = tuple(sorted(DIST.glob("*.whl"))) + tuple(
        sorted(DIST.glob("*.tar.gz"))
    )
    if not archives:
        print("distribution archives not found", file=sys.stderr)
        return 2
    failures: list[tuple[str, str]] = []
    for archive in archives:
        for member in _members(archive):
            if _unsafe(member):
                failures.append((archive.name, member))
    for archive, member in failures:
        print(f"{archive}: forbidden distribution member: {member}")
    print(f"distribution_archives={len(archives)}")
    print(f"forbidden_members={len(failures)}")
    payload = {
        "schema_version": "phase14_distribution_report_v1",
        "status": "fail" if failures else "pass",
        "archive_count": len(archives),
        "forbidden_count": len(failures),
        "archives": [
            {
                "name": archive.name,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "member_count": len(_members(archive)),
            }
            for archive in archives
        ],
    }
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
