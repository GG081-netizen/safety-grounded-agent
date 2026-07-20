# Phase 14 生产阻塞项关闭计划（Production Blocker Closure Plan）

Phase 14 在不修改 PostgreSQL 数据库结构（Schema）或 Alembic 迁移版本（Alembic revision）`0001` 的前提下关闭三个实现阻塞项（implementation blockers）：

1. 密钥配置（secret configuration）、结构化日志脱敏（structured log redaction）、仓库作用域（repository scopes）、Gitleaks 功能控制与分发卫生（distribution hygiene）。
2. 共享协调器（Coordinator）中的请求/追踪/会话/策略隔离（Request/trace/session/policy isolation）。
3. 企业策略规范化（Enterprise Policy normalization）、逐次风险候选（per-occurrence risk candidate）、候选级立场（candidate-level stance）及版本化决策矩阵（versioned decision matrix）。

受信任的执行路径（trusted execution paths）如下：

```text
RequestContext
-> OrchestrationRequestMetadata projection
-> stateless Coordinator
```

```text
Normalized Input
-> RiskCandidateDetector
-> DeterministicStanceResolver
-> PolicyResolver
-> PolicyDecision
```

仓库作用域（repository scope）被有意分离：

```text
current tree != tracked files != Git history
# 当前树 != 已跟踪文件 != Git 历史
```

只有当前树作用域（current-tree scope）可以在不依赖 Git 元数据（metadata）的情况下运行。事件关闭（incident closure）另外要求提供商侧凭证证据（provider-side credential evidence）和一条绑定到已验证提交的受保护运行时证明（protected runtime attestation）。仓库中的任何文件或普通拉取请求固件（pull-request fixture）都无法提供这种信任。

本 Phase 不新增完整的执行上下文（ExecutionContext）、RAG ACL、工具运行时（tool runtime）、人工审批（human approval）、工作队列（worker queue）、发件箱（outbox）、遥测（telemetry）、计划器（planner）或外部 CRM 连接器（connector）。
