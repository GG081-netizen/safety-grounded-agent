"""Generate or verify the deterministic Phase 14-G Candidate Manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from conversation_agent.evaluation.phase14_baseline import (
    CandidateManifestV1,
    build_candidate_manifest,
    build_index_manifest,
    candidate_manifest_sha256,
    verify_manifest_equal,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--mode", choices=("candidate", "index", "verify-index"), required=True)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--expect-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.mode == "candidate":
        manifest = build_candidate_manifest(args.root)
    elif args.mode == "index":
        manifest = build_index_manifest(args.root)
    else:
        if args.expected is None:
            raise RuntimeError("candidate_manifest_expected_required")
        expected = CandidateManifestV1.model_validate_json(
            args.expected.read_text(encoding="utf-8")
        )
        manifest = build_index_manifest(args.root)
        verify_manifest_equal(expected, manifest)

    if args.output is not None:
        write_json(args.output, manifest)
    digest = candidate_manifest_sha256(manifest)
    if args.expect_sha256 is not None and digest != args.expect_sha256:
        raise RuntimeError("candidate_manifest_sha256_mismatch")
    print(json.dumps({
        "status": "pass",
        "mode": args.mode,
        "entry_count": len(manifest.entries),
        "candidate_manifest_sha256": digest,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
