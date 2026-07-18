"""Validate bounded pytest JUnit evidence without freezing suite growth."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--minimum-tests", type=int, required=True)
    parser.add_argument("--allowed-skip", action="append", default=[])
    parser.add_argument("--required-test", action="append", default=[])
    parser.add_argument("--suite-identity", required=True)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()

    root = ET.parse(args.report).getroot()
    testcases = root.findall(".//testcase")
    skipped_names = {
        case.get("name", "") for case in testcases if case.find("skipped") is not None
    }
    skipped = len(skipped_names)
    failures = sum(case.find("failure") is not None for case in testcases)
    errors = sum(case.find("error") is not None for case in testcases)
    test_names = {case.get("name", "") for case in testcases}

    print(f"tests={len(testcases)}")
    print(f"skipped={skipped}")
    print(f"failures={failures}")
    print(f"errors={errors}")

    assert len(testcases) >= args.minimum_tests
    unexpected_skips = sorted(skipped_names - set(args.allowed_skip))
    missing_allowed_skips = sorted(set(args.allowed_skip) - skipped_names)
    assert not unexpected_skips
    assert not missing_allowed_skips
    assert failures == 0
    assert errors == 0
    missing_required = sorted(set(args.required_test) - test_names)
    print(f"missing_required_tests={len(missing_required)}")
    for name in missing_required:
        print(name)
    assert not missing_required
    payload = {
        "schema_version": "phase14_junit_validation_v1",
        "suite_identity": args.suite_identity,
        "exit_code": 0,
        "tests_collected": len(testcases),
        "tests_passed": len(testcases) - skipped - failures - errors,
        "tests_skipped": skipped,
        "failures": failures,
        "errors": errors,
        "unexpected_skips": len(unexpected_skips),
        "required_node_ids_missing": len(missing_required),
    }
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
