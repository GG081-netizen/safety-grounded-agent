"""Generate or verify the M1.1 pre-existing pytest node ID baseline."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
M1_1_BASELINE = ROOT / "tests" / "baselines" / "m1_1_preexisting_test_nodeids.txt"
M1_4_B_BASELINE = ROOT / "tests" / "baselines" / "m1_4_b_pre_r1_nodeids.txt"
M1_4_B_R1_BASELINE = ROOT / "tests" / "baselines" / "m1_4_b_r1_pre_c_nodeids.txt"
M1_4_C_BASELINE = ROOT / "tests" / "baselines" / "m1_4_c_pre_d_nodeids.txt"
M1_4_D_BASELINE = ROOT / "tests" / "baselines" / "m1_4_d_pre_e_nodeids.txt"
M1_4_E_BASELINE = ROOT / "tests" / "baselines" / "m1_4_e_pre_f_nodeids.txt"
PHASE14_BASELINE = ROOT / "tests" / "baselines" / "phase14_prechange_nodeids.txt"
RENAMES = ROOT / "tests" / "baselines" / "m1_1_nodeid_renames.json"
PHASE14_RENAMES = ROOT / "tests" / "baselines" / "phase14_nodeid_renames.json"


def apply_phase14_renames(nodeids: set[str]) -> set[str]:
    renames = json.loads(PHASE14_RENAMES.read_text(encoding="utf-8"))
    expected: set[str] = set()
    for nodeid in nodeids:
        mapping = renames.get(nodeid)
        if mapping is None:
            expected.add(nodeid)
            continue
        if not isinstance(mapping, dict):
            raise ValueError("Phase 14 rename entries must be objects")
        replacements = mapping.get("replacement")
        reason = mapping.get("reason")
        if (
            not isinstance(replacements, list)
            or not replacements
            or not all(isinstance(item, str) and item for item in replacements)
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError("Phase 14 rename entries require replacements and reason")
        expected.update(replacements)
    return expected


def collect_nodeids() -> tuple[str, ...]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    nodeids = {
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip().startswith("tests/") and "::" in line
    }
    return tuple(sorted(nodeids))


def write_baseline(path: Path, nodeids: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(nodeids) + "\n", encoding="utf-8", newline="\n")
    if not RENAMES.exists():
        RENAMES.write_text("{}\n", encoding="utf-8", newline="\n")


def verify_m1_1(nodeids: tuple[str, ...]) -> int:
    baseline = {
        line.strip()
        for line in M1_1_BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    renames = json.loads(RENAMES.read_text(encoding="utf-8"))
    if not isinstance(renames, dict) or not all(
        isinstance(old, str) and isinstance(new, str)
        for old, new in renames.items()
    ):
        raise ValueError("Node ID rename mapping must be a JSON object of strings")

    expected = apply_phase14_renames({renames.get(nodeid, nodeid) for nodeid in baseline})
    missing = sorted(expected - set(nodeids))
    print(f"m1_1_baseline_nodeids={len(baseline)}")
    print(f"current_nodeids={len(nodeids)}")
    print(f"rename_mappings={len(renames)}")
    print(f"missing_m1_1_nodeids={len(missing)}")
    for nodeid in missing:
        print(nodeid)
    return 1 if missing else 0


def verify_m1_4_b(nodeids: tuple[str, ...]) -> int:
    baseline = {
        line.strip()
        for line in M1_4_B_BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    missing = sorted(apply_phase14_renames(baseline) - set(nodeids))
    print(f"m1_4_b_baseline_nodeids={len(baseline)}")
    print(f"missing_m1_4_b_nodeids={len(missing)}")
    for nodeid in missing:
        print(nodeid)
    return 1 if missing else 0


def verify_m1_4_b_r1(nodeids: tuple[str, ...]) -> int:
    baseline = {
        line.strip()
        for line in M1_4_B_R1_BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    missing = sorted(apply_phase14_renames(baseline) - set(nodeids))
    print(f"m1_4_b_r1_baseline_nodeids={len(baseline)}")
    print(f"missing_m1_4_b_r1_nodeids={len(missing)}")
    for nodeid in missing:
        print(nodeid)
    return 1 if missing else 0


def verify_m1_4_c(nodeids: tuple[str, ...]) -> int:
    baseline = {
        line.strip()
        for line in M1_4_C_BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    missing = sorted(apply_phase14_renames(baseline) - set(nodeids))
    print(f"m1_4_c_baseline_nodeids={len(baseline)}")
    print(f"missing_m1_4_c_nodeids={len(missing)}")
    for nodeid in missing:
        print(nodeid)
    return 1 if missing else 0


def verify_m1_4_d(nodeids: tuple[str, ...]) -> int:
    baseline = {
        line.strip()
        for line in M1_4_D_BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    missing = sorted(apply_phase14_renames(baseline) - set(nodeids))
    print(f"m1_4_d_baseline_nodeids={len(baseline)}")
    print(f"missing_m1_4_d_nodeids={len(missing)}")
    for nodeid in missing:
        print(nodeid)
    return 1 if missing else 0


def verify_m1_4_e(nodeids: tuple[str, ...]) -> int:
    baseline = {
        line.strip()
        for line in M1_4_E_BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    missing = sorted(apply_phase14_renames(baseline) - set(nodeids))
    print(f"m1_4_e_baseline_nodeids={len(baseline)}")
    print(f"missing_m1_4_e_nodeids={len(missing)}")
    for nodeid in missing:
        print(nodeid)
    return 1 if missing else 0


def verify_phase14(nodeids: tuple[str, ...]) -> int:
    baseline = {
        line.strip()
        for line in PHASE14_BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    renames = json.loads(PHASE14_RENAMES.read_text(encoding="utf-8"))
    if not isinstance(renames, dict):
        raise ValueError("Phase 14 rename mapping must be a JSON object")
    expected = apply_phase14_renames(baseline)

    missing = sorted(expected - set(nodeids))
    print(f"phase14_baseline_nodeids={len(baseline)}")
    print(f"phase14_rename_mappings={len(renames)}")
    print(f"missing_phase14_nodeids={len(missing)}")
    for nodeid in missing:
        print(nodeid)
    return 1 if missing else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--write-m1-4-b", action="store_true")
    parser.add_argument("--write-m1-4-b-r1", action="store_true")
    parser.add_argument("--write-m1-4-c", action="store_true")
    parser.add_argument("--write-m1-4-d", action="store_true")
    parser.add_argument("--write-m1-4-e", action="store_true")
    parser.add_argument("--write-phase14", action="store_true")
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    nodeids = collect_nodeids()
    if args.write:
        write_baseline(M1_1_BASELINE, nodeids)
        print(f"Wrote {len(nodeids)} node IDs to {M1_1_BASELINE.relative_to(ROOT)}")
        return 0
    if args.write_m1_4_b:
        write_baseline(M1_4_B_BASELINE, nodeids)
        print(f"Wrote {len(nodeids)} node IDs to {M1_4_B_BASELINE.relative_to(ROOT)}")
        return 0
    if args.write_m1_4_b_r1:
        write_baseline(M1_4_B_R1_BASELINE, nodeids)
        print(
            f"Wrote {len(nodeids)} node IDs to "
            f"{M1_4_B_R1_BASELINE.relative_to(ROOT)}"
        )
        return 0
    if args.write_m1_4_c:
        write_baseline(M1_4_C_BASELINE, nodeids)
        print(
            f"Wrote {len(nodeids)} node IDs to "
            f"{M1_4_C_BASELINE.relative_to(ROOT)}"
        )
        return 0
    if args.write_m1_4_d:
        write_baseline(M1_4_D_BASELINE, nodeids)
        print(
            f"Wrote {len(nodeids)} node IDs to "
            f"{M1_4_D_BASELINE.relative_to(ROOT)}"
        )
        return 0
    if args.write_m1_4_e:
        write_baseline(M1_4_E_BASELINE, nodeids)
        print(
            f"Wrote {len(nodeids)} node IDs to "
            f"{M1_4_E_BASELINE.relative_to(ROOT)}"
        )
        return 0
    if args.write_phase14:
        write_baseline(PHASE14_BASELINE, nodeids)
        PHASE14_RENAMES.write_text("{}\n", encoding="utf-8", newline="\n")
        print(
            f"Wrote {len(nodeids)} node IDs to "
            f"{PHASE14_BASELINE.relative_to(ROOT)}"
        )
        return 0
    status = max(
        verify_m1_1(nodeids),
        verify_m1_4_b(nodeids),
        verify_m1_4_b_r1(nodeids),
        verify_m1_4_c(nodeids),
        verify_m1_4_d(nodeids),
        verify_m1_4_e(nodeids),
        verify_phase14(nodeids),
    )
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(
                {
                    "schema_version": "phase14_nodeid_report_v1",
                    "status": "pass" if status == 0 else "fail",
                    "current_node_ids": len(nodeids),
                    "required_node_ids_missing": 0 if status == 0 else 1,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
