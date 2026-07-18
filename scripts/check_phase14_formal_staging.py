"""Fail closed unless the Phase 14 upload staging directory is exact."""

from __future__ import annotations

import argparse
from pathlib import Path

from conversation_agent.evaluation.phase14_artifact import FORMAL_FILES


def validate_staging(path: Path) -> None:
    if not path.is_dir() or path.is_symlink():
        raise RuntimeError("formal_staging_directory_invalid")
    entries = list(path.iterdir())
    if {entry.name for entry in entries} != FORMAL_FILES:
        raise RuntimeError("formal_staging_file_set_mismatch")
    for entry in entries:
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_file():
            raise RuntimeError("formal_staging_entry_invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    validate_staging(args.directory)
    print("formal_staging_status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
