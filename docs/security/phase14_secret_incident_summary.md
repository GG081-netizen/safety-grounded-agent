# Phase 14 密钥事件摘要（Phase 14 Secret Incident Summary）

在 Phase 14 基线审查（baseline review）期间，工作树（working tree）中发现了一个暴露的 DashScope 凭证（credential）。本文档、测试、日志及固件（fixtures）中均未保留该凭证值。

本仓库（repository）仅包含运行时证明 V2 结构（Runtime Attestation V2 Schema）和本脱敏摘要。只有在全新的 GitHub.com 受保护（protected）`incident-closure` 作业（Job）生成运行时证明（runtime attestation）、且下游 `formal-closeout` 成功上传权威制品（Artifact）时，凭证吊销（revocation）、轮换（rotation）及提供商用量审查（provider usage review）才能被信任。本地文件、拉取请求固件（pull-request fixture）、角色标签（role label）及工作流执行者（workflow actor）均不构成审批证据（approval evidence）。

仓库修复（repository remediation）可以从当前树（current tree）中移除该值，但无法证明提供商侧的吊销（revocation）、轮换（rotation）、用量审查（usage review）、已跟踪文件历史（tracked-file history）或全引用历史扫描（all-ref history scanning）已完成。这些条件需要一个受保护（protected）的 `incident-closure` 作业（Job）和一名授权审查人（authorized reviewer）。该作业创建一条绑定到 `github.sha` 的运行时证明（runtime attestation）；仓库 Markdown、结构定义（schema）及普通拉取请求固件（pull-request fixture）均不构成受信任的事件证据（incident evidence）。

当前本地状态：

```text
credential_revocation_verified = false    # 凭证吊销已验证 = 否
credential_rotation_verified = false      # 凭证轮换已验证 = 否
provider_usage_review_status = unverified # 提供商用量审查状态 = 未验证
tracked_files_scan_status = blocked       # 已跟踪文件扫描状态 = 阻塞
git_history_scan_status = blocked         # Git 历史扫描状态 = 阻塞
incident_closure_status = blocked         # 事件关闭状态 = 阻塞
```
