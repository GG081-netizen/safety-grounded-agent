# Phase 14-F 事件关闭与正式结项（Incident Closure And Formal Closeout）

Phase 14-F 不新增任何业务能力（business capability），不修改数据库结构（database Schema）或 Alembic 迁移版本（Alembic Revision）`0001`。它将外部事件证据（external incident evidence）转化为一条可审计、故障关闭（fail-closed）的 GitHub.com 工作流（workflow）。

## 信任链（Trust Chain）

```text
fresh workflow_dispatch, run_attempt=1
-> test / secret-scan / postgres-integration / operational-postgres
-> protected incident-closure
-> non-authoritative incident evidence
-> formal-closeout
-> authoritative Phase resolution
-> successful protected Artifact upload
```

每个生产者报告（Producer report）包装在一个 `EvidenceEnvelope`（证据包络）中，包含主体提交（subject commit）、仓库（repository）、工作流运行（workflow run）、尝试次数（attempt）、生产者工作流作业 ID（Producer Workflow Job ID）、检查运行 ID（Check Run ID）、生成时间（generation time）及规范载荷哈希（canonical payload hash）。生产者（Producer）仅在其自身处于 `queued` 或 `in_progress` 且结论为 null 时绑定其正在运行的作业（Job）；它从不自证 `completed/success`。

`incident-closure` 作业（Job）验证 GitHub 受保护环境（GitHub Environment）、审批历史（approval history）、受保护引用（protected ref）及其自身的 `job.check_run_id`。它只能输出 `incident_evidence_status` 和 `phase_candidate_status`；不能输出权威的 Phase PASS。

`incident-closure` 验证四个已完成的生产者作业（Producer Job），同时将自身绑定为仍在运行中。`formal-closeout` 验证这四个作业加上 `incident-closure` 均为 `completed/success`，同样将自身绑定为运行中。只有之后的上线验证器（Online Verifier，在线验真器），在工作流（Workflow）完成后，才要求工作流运行（Workflow Run）及全部六个作业（Job）均为 `completed/success`。

正式作业（Formal Job）还验证工作流定义 ID 及路径匹配 `.github/workflows/ci.yml`，且其状态在结项时为 `active`。独立验证器（independent verifier）将该历史状态保留在正式载荷（Formal Payload）中；后续管理性禁用（administrative disable）不会使原本真实的制品（Artifact）失效。

权威制品暂存目录（Artifact staging directory）恰好包含三个常规文件。独立验证器（independent verifier）绑定 GitHub 制品元数据（Artifact metadata）、工作流运行（Workflow Run）、Attempt 1 作业（Job）、工作流身份（Workflow identity）、原始 ZIP 摘要（digest）及内部合约（Contracts）。离线目录验证（offline directory verification）仅用于诊断，永远不能关闭 Phase。

## 平台边界（Platform Boundary）

正式关闭（Formal closure）仅限于 GitHub.com，且要求 `github.server_url == "https://github.com"`。GitHub Enterprise Server（GitHub 企业服务器）不暴露同样可信的 `job.check_run_id` 绑定，在未经新的身份绑定审查（identity-binding review）之前不得复用此设计。

所有 GitHub ID 在比较或哈希之前规范化为正整数的十进制 ASCII（无前导零）。主体提交（subject commit）为小写十六进制。环境名称（Environment name）使用 API 值进行精确比较。

## 当前状态（Current State）

本地实现和测试可以验证合约（Contracts）及故障关闭行为（fail-closed behavior），但不能审批该事件。供应商凭证吊销/轮换（credential revocation/rotation）、完整 Git 历史扫描及受保护的 GitHub 工作流（workflow）仍为外部门禁（external gates）。

```text
preflight_hardening = pass        # 预检加固 = 通过
implementation_status = pass      # 实现状态 = 通过
incident_closure_status = blocked # 事件关闭状态 = 阻塞
phase_status = blocked            # Phase 状态 = 阻塞
database_revision = 0001          # 数据库修订版本 = 0001
```
