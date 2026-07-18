# Phase 14-G Repository Baseline Design

## Purpose

Phase 14-G establishes the first trusted Git and GitHub baseline for
`GG081-netizen/crispy-fortnight-baseline-2` (Bootstrap 2). It is a one-shot bootstrap contract, not an
ordinary release workflow.

The required identity is:

```text
frozen local implementation
= Candidate Manifest
= root commit tree
= GitHub main
= Discovery subject
= Formal subject
= verified baseline artifact subject
```

The Candidate Manifest is generated in an empty, unborn Git repository and
stored only under ignored `tmp/`. Candidate paths come from Git's native ignore
engine using the equivalent of
`git -c core.excludesFile=/dev/null ls-files --others --exclude-standard -z`.
An effective non-comment rule in `.git/info/exclude` is rejected. This isolates
global excludes while preserving nested, negated, and directory `.gitignore`
semantics.

The manifest lists canonical paths, Git modes, byte sizes, and file SHA-256
values. It does not contain timestamps, absolute paths, local Secret Stores,
Git metadata, ignored local backup files, or itself. Symlinks and paths outside
the worktree are rejected. After `git add --all`, the same representation is
rebuilt from the Git index and must match exactly.

The bootstrap ordering is fixed:

```text
implementation freeze
-> empty/unborn Git repository
-> Git-aware Candidate Manifest
-> git add --all
-> Index Manifest
-> exact equivalence gate
-> root commit
```

## Evidence Layers

Discovery is explicitly non-authoritative. Its internal evidence contains only
facts known before artifact upload. Artifact ID, digest, size, and origin are
obtained afterward from the GitHub API and recorded in an external
`DiscoveryArtifactBindingV1` under ignored `tmp/`.

The Discovery artifact member set is exact:

```text
phase14-discovery-evidence.json
phase14-candidate-manifest.json
```

Unknown, duplicate, hidden, non-regular, directory, or symlink members are
rejected.

Formal execution binds that external GitHub metadata to the raw downloaded ZIP
and then to the internal Discovery evidence. The final baseline artifact has
exactly three files:

```text
phase14-baseline-closeout.json
phase14-baseline-closeout.md
phase14-candidate-manifest.json
```

The artifact does not contain its own ID, digest, size, download URL, or claim
of GitHub origin. Those facts can only be established by the independent online
verifier after the workflow has completed.

## Job Time Semantics

A running job cannot prove its own successful completion. Producer jobs bind
their workflow-job and check-run IDs while their state is `queued` or
`in_progress` and conclusion is null. `baseline-approval` verifies the four
Producers, while `baseline-closeout` verifies those jobs plus the approval job.
Each still records only its own running state. The online verifier alone proves
that all six jobs completed successfully.

The protected Environment is `phase14-baseline-closeout`. Approval must come
from `toshibanino6-creator`, differ from the workflow trigger actor, and be
enforced with prevent-self-review.

## One-shot Failure Boundary

All source, tests, workflows, verification scripts, and documentation are
frozen before the root commit. If a committed-file defect is discovered after
the first push, the baseline attempt is invalidated. The root commit is never
amended or rewritten, and a second commit cannot be presented as the same root
baseline. Recovery requires either a newly approved empty repository or a new,
versioned Successor Baseline contract.

Bootstrap 1 in `GG081-netizen/crispy-fortnight` was invalidated after its
first Discovery run exposed a committed Discovery/Formal job-identity contract
mix-up. Its root and failed run remain immutable audit evidence; Bootstrap 2
uses distinct `DiscoveryJobIdentity` and `FormalBaselineJobIdentity` contracts.

Phase 14-G success does not close the DashScope incident:

```text
phase14_g_repository_baseline_status = pass
phase14_implementation_status = pass
phase14_incident_closure_status = blocked
phase14_authoritative_phase_status = blocked
database_revision = 0001
```
