# Phase 14-G Bootstrap 1 作废记录（Bootstrap 1 Invalidation）

Bootstrap 1 是永久性失败证据（permanent failed evidence），不可作为可复用基线（baseline）。

```text
repository = GG081-netizen/crispy-fortnight                          # 仓库
root_commit = 3fa489c70c199c936a5b0c4c6b5d645a434ffaf6              # 根提交
root_tree = b023bdece5f58bd02a9277f4f73c4800329cfb2e                # 根树
discovery_run_id = 29657780089                                       # 发现运行 ID
discovery_run_attempt = 1                                            # 发现运行尝试次数
discovery_conclusion = failure                                       # 发现结论 = 失败
formal_started = false                                               # 正式流程已启动 = 否
failure_reason_code = discovery_job_identity_not_allowed_by_contract # 失败原因 = 发现作业身份不被合约允许
```

已推送的根提交（root commit）、失败的工作流运行（workflow run）、分支保护（branch protection）及环境（Environment）作为审计证据（audit evidence）保留。Bootstrap 1 不得接收修复提交（repair commit）、重写历史（rewritten history）、另一次权威发现运行（authoritative Discovery run）或正式运行（Formal run）。

Bootstrap 2 使用新的空仓库 `GG081-netizen/crispy-fortnight-baseline-2`，并将发现身份（Discovery identity）与六个正式作业身份（Formal job identity）保持分离。
