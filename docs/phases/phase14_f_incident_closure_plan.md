# Phase 14-F 事故关闭与正式收尾

Phase 14-F（第 14-F 阶段）不增加任何业务能力，不修改数据库 Schema（数据库结构）和 Alembic Revision（迁移版本号）`0001`。它将外部事故证据转化为可审计、故障即关闭（Fail-Closed）的 GitHub.com Workflow（工作流）。

## 信任链

```text
fresh workflow_dispatch, run_attempt=1（全新手动触发工作流，首次运行）
-> test / secret-scan / postgres-integration / operational-postgres（测试/密钥扫描/PostgreSQL集成/运维PostgreSQL）
-> protected incident-closure（受保护的事故关闭 Job）
-> non-authoritative incident evidence（非权威事故证据）
-> formal-closeout（正式收尾 Job）
-> authoritative Phase resolution（权威阶段决议）
-> successful protected Artifact upload（受保护制品上传成功）
```

每个 Producer（生产者）报告被包装在 `EvidenceEnvelope`（证据包络）中，包含：主题 Commit（提交）、仓库、Workflow Run（工作流运行）、运行 Attempt（尝试次数）、Producer Workflow Job ID（工作流任务标识）、Check Run ID（检查运行标识）、生成时间和规范有效载荷哈希。Producer 仅在其状态为 `queued`（排队中）或 `in_progress`（进行中）且结论为空时绑定自身正在运行的 Job；它从不自我声明为 `completed/success`（已完成/成功）。

`incident-closure` Job 验证 GitHub Environment（受保护环境）、审批历史、受保护引用（Protected Ref）及其自身的 `job.check_run_id`。它只能输出 `incident_evidence_status`（事故证据状态）和 `phase_candidate_status`（阶段候选状态）；不能输出权威的 Phase PASS。

`incident-closure` 验证四个已完成的 Producer Job，同时将自身绑定为正在运行。`formal-closeout` 验证那四个 Job 加上 `incident-closure` 为已完成/成功，同时同样将自身绑定为正在运行。只有后续的 Online Verifier（在线验真器）在工作流完成后，才要求 Workflow Run 及其全部六个 Job 均已完成/成功。

Formal Job 还验证 Workflow 定义 ID 和路径是否与 `.github/workflows/ci.yml` 匹配，以及其在收尾时的状态是否为 `active`（活跃）。独立验真器将该历史状态保存在 Formal Payload（正式载荷）中；后续的管理性禁用不会使其他方面真实的历史 Artifact（制品）失效。

权威 Artifact 暂存目录恰好包含三个常规文件。独立验真器绑定 GitHub Artifact 元数据、Workflow Run、Attempt 1 各 Job、Workflow 身份、原始 ZIP 摘要和内部 Contracts（契约）。离线目录验证仅为诊断用途，永远不能关闭 Phase。

## 平台边界

正式收尾仅限于 GitHub.com，要求 `github.server_url == "https://github.com"`。GitHub Enterprise Server（GitHub 企业服务器）不暴露相同的受信任 `job.check_run_id` 绑定，在没有经过新的身份绑定审查之前不得复用本设计。

所有 GitHub ID 在比较或哈希之前被规范化为正整数、无前导零的十进制 ASCII 格式。主题 Commit 使用小写十六进制。Environment 名称使用 API 返回的值进行精确比较。

## 当前状态

本地实现和测试可以验证 Contracts（契约）和故障即关闭行为，但不能批准事故。供应商凭据撤销/轮转、完整 Git 历史扫描和受保护的 GitHub Workflow 仍然是外部门禁。

```text
preflight_hardening = pass（预检加固：通过）
implementation_status = pass（实现状态：通过）
incident_closure_status = blocked（事故关闭状态：阻塞）
phase_status = blocked（阶段状态：阻塞）
database_revision = 0001（数据库版本号）
```
