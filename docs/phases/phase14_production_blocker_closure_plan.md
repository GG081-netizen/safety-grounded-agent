# Phase 14 生产阻断项关闭计划

Phase 14（第 14 阶段）在不修改 PostgreSQL Schema（数据库模式）和 Alembic（数据库迁移工具）revision（版本号）`0001` 的前提下关闭三个实现层面的阻断项：

1. Secret（密钥）配置、结构化日志脱敏、仓库作用域（Repository Scopes）、Gitleaks（密钥泄露扫描工具）功能控制和分发制品卫生。
2. 共享 Coordinator（协调器）中的请求/追踪/会话/策略隔离。
3. 企业 Policy（安全策略）规范化、逐出现位置的风险候选（RiskCandidate）、候选级别的立场判断（Stance）和带版本号的决策矩阵。

可信执行路径为：

```text
RequestContext（请求上下文）
-> OrchestrationRequestMetadata projection（编排请求元数据投影）
-> stateless Coordinator（无状态协调器）
```

```text
Normalized Input（规范化输入）
-> RiskCandidateDetector（风险候选检测器）
-> DeterministicStanceResolver（确定性立场解析器）
-> PolicyResolver（策略解析器）
-> PolicyDecision（策略决策）
```

仓库作用域被刻意分离：

```text
current tree（当前工作树）!= tracked files（已跟踪文件）!= Git history（Git 历史）
```

只有当前工作树作用域可以在没有 Git 元数据的情况下运行。事故关闭还需要提供商侧的凭据证据，以及一份绑定到已验证 Commit（提交）的受保护 Runtime Attestation（运行时证明）。任何仓库文件或普通 Pull Request 测试夹具都无法提供这种可信度。

本阶段不会增加以下内容：完整的 ExecutionContext（执行上下文）、RAG ACL（检索增强生成访问控制列表）、Tool Runtime（工具运行时）、人工审批、Worker Queue（工作队列）、Outbox（发件箱）、Telemetry（遥测）、Planner（规划器）或外部 CRM Connector（客户关系管理连接器）。
