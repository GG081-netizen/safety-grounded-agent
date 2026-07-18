# Phase 14-G Bootstrap 1 Invalidation

Bootstrap 1 is permanent failed evidence and is not a reusable baseline.

```text
repository = GG081-netizen/crispy-fortnight
root_commit = 3fa489c70c199c936a5b0c4c6b5d645a434ffaf6
root_tree = b023bdece5f58bd02a9277f4f73c4800329cfb2e
discovery_run_id = 29657780089
discovery_run_attempt = 1
discovery_conclusion = failure
formal_started = false
failure_reason_code = discovery_job_identity_not_allowed_by_contract
```

The pushed root, failed workflow run, branch protection, and Environment are
retained as audit evidence. Bootstrap 1 must not receive a repair commit,
rewritten history, another authoritative Discovery run, or a Formal run.

Bootstrap 2 uses the new empty repository
`GG081-netizen/crispy-fortnight-baseline-2` and keeps Discovery identity
separate from the six Formal job identities.
