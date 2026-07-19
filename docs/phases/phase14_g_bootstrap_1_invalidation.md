# Phase 14-G Bootstrap 1 失效记录

Bootstrap 1（引导阶段 1）为永久失败证据，不可作为可复用基线（Baseline）。

```text
repository = GG081-netizen/crispy-fortnight（仓库名称）
root_commit = 3fa489c70c199c936a5b0c4c6b5d645a434ffaf6（根提交）
root_tree = b023bdece5f58bd02a9277f4f73c4800329cfb2e（根目录树）
discovery_run_id = 29657780089（发现运行 ID）
discovery_run_attempt = 1（发现运行尝试次数）
discovery_conclusion = failure（发现结论：失败）
formal_started = false（正式阶段未启动）
failure_reason_code = discovery_job_identity_not_allowed_by_contract（失败原因：发现 Job 身份不符合契约要求）
```

已推送的根提交（Root Commit）、失败的 Workflow Run（工作流运行）、分支保护（Branch Protection）和 Environment（环境）作为审计证据保留。Bootstrap 1 不得接收修复提交、历史重写、另一次权威 Discovery 运行或 Formal 运行。

Bootstrap 2 使用新的空仓库 `GG081-netizen/crispy-fortnight-baseline-2`，并将 Discovery 身份与六个 Formal Job 身份分离。
