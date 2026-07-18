"""Create compact producer payloads from machine-readable CI outputs."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def junit_summary(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return {
        field: sum(int(suite.attrib.get(field, "0")) for suite in suites)
        for field in ("tests", "failures", "errors", "skipped")
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("test", "postgres", "operational"), required=True)
    parser.add_argument("--junit", type=Path, action="append", default=[])
    parser.add_argument("--database-revision-file", type=Path)
    parser.add_argument("--suite-validation", type=Path, action="append", default=[])
    parser.add_argument("--nodeid-report", type=Path)
    parser.add_argument("--source-tree-report", type=Path)
    parser.add_argument("--policy-evaluation", type=Path)
    parser.add_argument("--rag-evaluation", type=Path)
    parser.add_argument("--implementation-evaluation", type=Path)
    parser.add_argument("--distribution-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summaries = [junit_summary(path) for path in args.junit]
    if not summaries or any(item["failures"] or item["errors"] for item in summaries):
        raise RuntimeError("producer_test_result_not_passed")
    payload: dict[str, object] = {
        "producer_kind": args.kind,
        "status": "pass",
        "test_reports": summaries,
    }
    if args.kind == "test":
        paths = {
            "node_id": args.nodeid_report,
            "source_tree": args.source_tree_report,
            "policy_boundary": args.policy_evaluation,
            "rag_adapter": args.rag_evaluation,
            "production_blockers_implementation": args.implementation_evaluation,
            "distribution": args.distribution_report,
        }
        if any(path is None for path in paths.values()):
            raise RuntimeError("test_machine_report_missing")
        reports = {
            name: json.loads(path.read_text(encoding="utf-8"))
            for name, path in paths.items()
            if path is not None
        }
        if reports["node_id"].get("status") != "pass":
            raise RuntimeError("node_id_report_failed")
        source = reports["source_tree"]
        if not isinstance(source, list) or any(item.get("status") != "pass" for item in source):
            raise RuntimeError("source_tree_report_failed")
        for name in (
            "policy_boundary",
            "rag_adapter",
            "production_blockers_implementation",
        ):
            if reports[name].get("summary", {}).get("status") != "pass" and reports[name].get(
                "summary", {}
            ).get("implementation_status") != "pass":
                raise RuntimeError(f"evaluation_report_failed:{name}")
        if reports["distribution"].get("status") != "pass":
            raise RuntimeError("distribution_report_failed")
        payload.update(
            regression_status="pass",
            node_id_status="pass",
            distribution_status="pass",
            policy_boundary_status="pass",
            rag_adapter_status="pass",
            implementation_evaluation_status="pass",
            machine_report_types=tuple(paths),
        )
    if args.kind in {"postgres", "operational"}:
        expected_validations = 2 if args.kind == "postgres" else 1
        if len(args.suite_validation) != expected_validations:
            raise RuntimeError("suite_validation_report_count_mismatch")
        validations = [
            json.loads(path.read_text(encoding="utf-8")) for path in args.suite_validation
        ]
        if any(
            item.get("exit_code") != 0
            or item.get("failures") != 0
            or item.get("errors") != 0
            or item.get("unexpected_skips") != 0
            or item.get("required_node_ids_missing") != 0
            for item in validations
        ):
            raise RuntimeError("suite_validation_failed")
        payload["suite_validations"] = validations
        if args.database_revision_file is None:
            raise RuntimeError("database_revision_file_required")
        revision_text = args.database_revision_file.read_text(encoding="utf-8").strip()
        if revision_text not in {"0001", "0001 (head)"}:
            raise RuntimeError("database_revision_mismatch")
        payload["database_revision"] = "0001"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"producer_kind={args.kind}")
    print("producer_payload_status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
