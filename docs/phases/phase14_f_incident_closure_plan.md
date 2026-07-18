# Phase 14-F Incident Closure And Formal Closeout

Phase 14-F adds no business capability and does not change database Schema（数据库结构）or Alembic Revision（迁移版本）`0001`. It turns external incident evidence into an auditable, fail-closed GitHub.com workflow.

## Trust Chain

```text
fresh workflow_dispatch, run_attempt=1
-> test / secret-scan / postgres-integration / operational-postgres
-> protected incident-closure
-> non-authoritative incident evidence
-> formal-closeout
-> authoritative Phase resolution
-> successful protected Artifact upload
```

Each Producer report is wrapped in an `EvidenceEnvelope`（证据包络）containing the subject commit, repository, workflow run, attempt, Producer Workflow Job ID, Check Run ID, generation time and canonical payload hash. A Producer binds its own running Job only while it is `queued` or `in_progress` with a null conclusion; it never self-attests `completed/success`.

The `incident-closure` Job validates the GitHub Environment（受保护环境）, approval history, protected ref and its own `job.check_run_id`. It can only emit `incident_evidence_status` and `phase_candidate_status`; it cannot emit an authoritative Phase PASS.

`incident-closure` verifies the four completed Producer Jobs while binding itself as still running. `formal-closeout` verifies those four Jobs plus `incident-closure` as completed/success while likewise binding itself as running. Only a later Online Verifier（在线验真器）, after the Workflow finishes, requires the Workflow Run and all six Jobs to be completed/success.

The Formal Job also verifies that the Workflow definition ID and path match `.github/workflows/ci.yml` and that its state is `active` at closeout time. The independent verifier preserves that historical state in the Formal Payload; a later administrative disable does not invalidate an otherwise authentic historical Artifact.

The authoritative Artifact staging directory contains exactly three regular files. The independent verifier binds GitHub Artifact metadata, Workflow Run, Attempt 1 Jobs, Workflow identity, raw ZIP digest and internal Contracts. Offline directory verification is diagnostic only and can never close the Phase.

## Platform Boundary

Formal closure is restricted to GitHub.com and requires `github.server_url == "https://github.com"`. GitHub Enterprise Server（GitHub 企业服务器）does not expose the same trusted `job.check_run_id` binding and must not reuse this design without a new identity-binding review.

All GitHub IDs are normalized to positive, no-leading-zero decimal ASCII before comparison or hashing. The subject commit is lowercase hexadecimal. Environment names are compared exactly using the API value.

## Current State

Local implementation and tests can validate Contracts and fail-closed behavior, but cannot approve the incident. Supplier credential revocation/rotation, full Git history scanning and the protected GitHub workflow remain external gates.

```text
preflight_hardening = pass
implementation_status = pass
incident_closure_status = blocked
phase_status = blocked
database_revision = 0001
```
