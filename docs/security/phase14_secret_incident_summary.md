# Phase 14 Secret Incident Summary

An exposed DashScope credential was found in the working tree during the Phase 14 baseline review. The value is not retained in this document, tests, logs, or fixtures.

This repository contains only the Runtime Attestation V2 Schema（运行时证明结构）and this redacted summary. Revocation, rotation and provider usage review can be trusted only when a fresh GitHub.com protected `incident-closure` Job generates the runtime attestation and downstream `formal-closeout` successfully uploads the authoritative Artifact. Local files, pull-request fixtures, role labels and workflow actors are not approval evidence.

Repository remediation can remove the value from the current tree, but it cannot prove provider-side revocation, rotation, usage review, tracked-file history, or all-ref history scanning. Those conditions require a protected `incident-closure` job and an authorized reviewer. The job creates a runtime attestation bound to `github.sha`; repository Markdown, schemas, and ordinary pull-request fixtures are not trusted incident evidence.

Current local status:

```text
credential_revocation_verified = false
credential_rotation_verified = false
provider_usage_review_status = unverified
tracked_files_scan_status = blocked
git_history_scan_status = blocked
incident_closure_status = blocked
```
