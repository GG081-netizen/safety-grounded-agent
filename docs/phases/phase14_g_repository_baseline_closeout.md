# Phase 14-G Repository Baseline Closeout

## Current Status

```text
implementation_status = pass
implementation_freeze = true
candidate_manifest_status = git_aware_pending_generation
bootstrap_round_status = phase14_g_r2_pre_push_retry
git_root_commit_status = not_created
github_push_status = not_started
discovery_run_status = not_started
formal_run_status = not_started
online_verifier_status = not_run
phase14_g_repository_baseline_status = blocked
phase14_authoritative_phase_status = blocked
database_revision = 0001
```

This document is updated only with results that were actually observed. A
successful local preflight does not prove GitHub origin, protected approval, or
workflow completion.

## Implemented Preflight Contracts

- Git-aware Candidate Manifest generation using Git's native ignore engine,
  with exact Git-index equivalence verification.
- Separate non-authoritative Discovery evidence and post-upload artifact
  binding.
- Six-job Formal workflow with protected approval and explicit job-time
  semantics.
- Fixed three-file baseline artifact contract.
- Offline diagnostic and GitHub-bound online verification modes.
- One-shot post-push invalidation boundary.

## Remaining Gates

Before a repository baseline can pass, the implementation must complete all
local regression gates, install and authenticate the approved GitHub CLI,
create and push the unique root commit, validate branch and Environment
protection, run Discovery, and start a fresh Formal workflow. Execution must
pause while `baseline-approval` waits for the required reviewer.

After approval, the same run must complete and the independent online verifier
must validate GitHub artifact metadata, the raw archive, all six jobs, root
commit and tree, Candidate Manifest, Discovery binding, and approval event.
Until then, Phase 14-G and Phase 14 overall remain blocked.

## Local Preflight Results

```text
workflow_yaml_parse = pass
legacy_node_ids_missing = 0
policy_boundary = PASS
rag_adapter = PASS
production_blockers_implementation = PASS
source_tree_secret_count = 0
approved_local_secret_store_status = pass
ignored_sensitive_files_status = pass
superseded_invalid_candidate_manifest_entries = 306
superseded_invalid_candidate_manifest_sha256 = 3c79050ce49aadfd26ec42938c646a66c202ec0ea1647bd1b8d902829a3527b2
distribution_archives = 2
distribution_forbidden_members = 0
distribution_build_command = uv build
postgres_non_destructive = 78 passed, 2 skipped
postgres_destructive = 80 passed
operational_integration = 6 passed
database_revision = 0001 (head)
gh_release_tag = v2.93.0
gh_binary_version = 2.93.0
gh_archive_sha256 = 02d1290eba130e0b896f3709ffff22e1c75a51475ddb70476a85abc6b5807af0
gh_official_checksum_match = true
gh_install_prefix = ~/.local/opt/gh_2.93.0
```

The superseded filesystem-only Candidate Manifest incorrectly included one local
Claude configuration file and nine ignored test/demo backup files. The Index
equivalence gate rejected it before commit. That manifest is
`superseded_invalid` and must never be reused. Git is initialized on unborn
`main`, the Index has been cleared, no commit exists, and no remote mutation has
occurred. The corrected implementation uses Git's native ignore engine and has
passed nested-rule, negation-rule, directory-rule, global-exclude isolation,
info-exclude rejection, symlink rejection, and Candidate/Index equivalence
tests.

## R1 Pre-Push Retry

The first local root candidate was rejected before push because the Gitleaks
all-refs gate reported six findings. No remote mutation occurred. The rejected
history is preserved outside the repository as a verified complete Git bundle
and isolated Git metadata.

```text
rejected_root_commit = 3be530e4a8351233d8f0228c9c5eea78091547f1
rejected_root_tree = ff1e1a197a85cf9870b9b8c6c89168d5dc93951e
rejected_root_finding_count = 6
rejected_root_push_performed = false
rejected_root_status = archived_not_rewritten
```

The DashScope custom rule now permits horizontal whitespace only and cannot
cross LF or CRLF after an empty assignment. Token-like test constants are
constructed from short runtime fragments, and no path or line-number allowlist
was added. The new root candidate must independently pass checksum, dual
Canary, and zero-finding all-refs gates before its first ordinary push.

## R2 Evidence Consistency Retry

The R1 root candidate was rejected before push because its Gitleaks summary
reported a passing, zero-finding all-refs scan while leaving
`gitleaks_real_repository_scan_passed` false. No remote mutation occurred, and
that history is separately preserved as a verified complete Git bundle and
isolated Git metadata.

```text
r1_rejected_root_commit = eee9f5b3a98cc8167c8afff177bb9628429ea759
r1_rejected_root_tree = 9821edd3a56b77b747d5821db6316772f9669274
r1_rejected_root_push_performed = false
r1_rejected_root_status = archived_not_rewritten
```

R2 uses one strict repository-scan result contract. Process return code and
finding count derive the all-refs status; that status, return code, and finding
count derive the boolean. Contradictory evidence is rejected by generation,
Formal consumption, and Production Blockers evaluation.
