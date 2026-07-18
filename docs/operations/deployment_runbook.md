# Deployment Runbook（部署运行手册）

## 部署前

1. 验证 `uv lock --check`（锁文件检查）、单元测试、PostgreSQL Gate（数据库验收门）和 Operational Gate（运维验收门）。当前无有效 Git Metadata（版本控制元数据）时必须记录限制，不能伪造 Revision（代码版本）。
2. 使用 Migration Role（迁移角色）执行 `alembic upgrade head`，再确认 `alembic current` 为 `0001 (head)`；应用启动不自动迁移，也不调用 `create_all()`（自动建表）。
3. 运行 Persistence Doctor（持久化诊断器）、Production Config Audit（生产配置审计）和 Snapshot Reader Compatibility（快照读取兼容检查）。
4. 确认 TLS Mode（传输层安全模式）、可信 `search_path`（对象搜索路径）、Application Role（应用角色）权限和备份恢复证据。
5. 连接预算按 `worker_count × (pool_size + max_overflow)`（工作进程数乘每进程连接上限）计算，并额外预留 Migration（迁移）、Readiness（就绪探针）、Operator（运维）和 PostgreSQL Administration（数据库管理）连接。该公式不是性能保证。
6. 运行 current-tree、tracked-files、Git history 和 distribution（发布包）四类 Secret Gate。文件系统扫描不能替代 tracked refs 或历史扫描。
7. Phase 14 事故关闭只能读取受保护 Environment Job 在运行时生成的 Attestation（证明）。`subject_commit_sha`、protected ref、授权 Reviewer 和 Gitleaks 双 Canary 报告必须绑定同一次提交；仓库中的 Markdown、Schema 或普通 PR Fixture 不构成证明。

## 发布与扩容

先发布一个 Canary Instance（金丝雀实例），依次检查 `/healthz`（存活探针）、`/readyz`（就绪探针）、无 Key（幂等键）请求、首次 `executed`（已执行）、同 Key `replayed`（已重放）、不同 Payload（负载）冲突和 Policy BLOCKED（策略阻断）。日志必须脱敏。

第二实例启动后验证 Shared PostgreSQL Claim Coordination（共享数据库声明协调）和连接总数，再逐步放量。不得切换 NULL Persistence（空持久化）隐藏故障。

## 平滑停止与轮换

先将 Readiness（就绪状态）置为 false，由上游停止新流量，再等待活动请求至 Graceful Timeout（平滑停止超时），最后释放 Engine（引擎）与连接池。强制终止时：Transaction A（事务 A）未提交则无记录；已提交则保留 ACTIVE（执行中）并由 Lease Reclaim（租约回收）恢复；Transaction B（事务 B）已提交则可 Replay（重放）。

凭据或 TLS Certificate（证书）轮换顺序：部署新凭据、滚动实例、验证 readiness、排空旧实例、关闭旧连接池、回收旧凭据。禁止明文降级，证书路径与私钥路径不得进入日志。

DashScope 凭据事故还必须由受权运维人员确认撤销、轮换和用量核查。应用侧 current-tree 清理不能证明供应商控制面操作已完成。

未来 Migration（迁移）采用 expand / migrate / contract（扩展/迁移/收缩）流程。当前严格 Revision Gate（版本门）限制滚动跨版本窗口，必须在未来迁移设计中显式调整；`alembic downgrade` 不作为默认应用回滚方式。
