from pathlib import Path

import pytest

from scripts.check_phase14_formal_staging import validate_staging

pytestmark = pytest.mark.unit
NAMES = (
    "phase14-formal-closeout.json",
    "phase14-formal-closeout.md",
    "phase14_incident_attestation.json",
)


def populate(path: Path):
    for name in NAMES:
        (path / name).write_text("safe", encoding="utf-8")


def test_exact_staging_directory_passes(tmp_path):
    populate(tmp_path)
    validate_staging(tmp_path)


def test_extra_staging_file_fails(tmp_path):
    populate(tmp_path)
    (tmp_path / "extra.json").write_text("unsafe", encoding="utf-8")
    with pytest.raises(RuntimeError, match="formal_staging_file_set_mismatch"):
        validate_staging(tmp_path)


def test_staging_symlink_fails(tmp_path):
    populate(tmp_path)
    target = tmp_path / "phase14-formal-closeout.json"
    target.unlink()
    target.symlink_to(tmp_path / "phase14-formal-closeout.md")
    with pytest.raises(RuntimeError, match="formal_staging_entry_invalid"):
        validate_staging(tmp_path)
