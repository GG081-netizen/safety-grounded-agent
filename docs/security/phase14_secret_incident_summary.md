# Phase 14 凭据事故摘要

在 Phase 14 基线审查期间，工作树中发现了一个已暴露的 DashScope（阿里云大模型服务平台）凭据。该凭据值不会保留在本文件、测试、日志或 Fixture（测试夹具）中。

本仓库仅包含 Runtime Attestation V2 Schema（运行时证明结构 V2 版本）和这份经脱敏的事故摘要。只有由 GitHub.com 受保护的 `incident-closure`（事故关闭）Job 重新生成运行时证明，并由下游 `formal-closeout`（正式收尾）Job 成功上传权威 Artifact（制品）后，凭据撤销、轮转和提供商使用审查才能被信任。本地文件、Pull Request 的测试夹具、角色标签和 Workflow Actor（工作流执行者）不构成审批证据。

仓库修复可以移除当前树中的凭据值，但无法证明提供商侧的撤销、轮转、使用审查、Tracked-File（已跟踪文件）历史记录和全引用历史扫描。这些条件需要受保护的 `incident-closure` Job 和授权审批人。该 Job 会生成绑定到 `github.sha` 的运行时证明；仓库中的 Markdown 文件、Schema（模式）定义和普通 Pull Request 测试夹具不被视为可信事故证据。

当前本地状态：

```text
credential_revocation_verified = false（凭据撤销未验证）
credential_rotation_verified = false（凭据轮转未验证）
provider_usage_review_status = unverified（提供商使用审查：未验证）
tracked_files_scan_status = blocked（已跟踪文件扫描：阻塞）
git_history_scan_status = blocked（Git 历史扫描：阻塞）
incident_closure_status = blocked（事故关闭状态：阻塞）
```
