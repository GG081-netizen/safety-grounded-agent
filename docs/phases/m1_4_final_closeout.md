# M1.4 PostgreSQL Persistence（PostgreSQL 持久化）Final Closeout（最终收尾）

## 最终状态

```text
M1.4-A = completed
M1.4-B = completed
M1.4-B-R1 = completed
M1.4-C = completed
M1.4-D = completed
M1.4-E = completed
M1.4-F = completed
M1.4 PostgreSQL persistence milestone = completed
next roadmap stage = ready / not started
```

M1.4 将 M1.3 的可信 HTTP（超文本传输协议）边界扩展为真实 PostgreSQL 持久执行链：四表 Schema（数据库结构）、Alembic Migration（迁移）、Repository/UnitOfWork（仓储/工作单元）、两段短事务、持久 Idempotency（幂等）、Replay（重放）、Fencing（执行权隔离）、FastAPI 接线、readiness（就绪检查）和运维恢复工具均已实现并在本地 PostgreSQL 17 上验证。

## 阶段交付

| 阶段 | 交付 |
|---|---|
| M1.4-A | 数据库配置、Engine（引擎）生命周期与持久化抽象 |
| M1.4-B/R1 | 四表 ORM（对象关系映射）、`0001` Migration 与严格 Schema Contract（结构契约） |
| M1.4-C | Execution Repository（执行仓储）、UnitOfWork 与 Transaction A/B（两段事务） |
| M1.4-D | 数据库时间 Lease、Scoped Claim（限定范围声明）、Fingerprint、Replay、Reclaim 与 Fencing |
| M1.4-E | FastAPI durable wiring（持久化接线）、`Idempotency-Key` 协议、production fail-closed（生产故障关闭）与 `/readyz` |
| M1.4-F | Doctor、Integrity Checker（完整性检查器）、Prune、最小权限角色、恢复/多实例/Soak 演练、备份恢复与 Runbook |

## 保持不变

- `Coordinator` 仍只负责 Policy、Routing（路由）和任务执行，不感知数据库、JWT（签名令牌）或 HTTP Header（请求头）。
- Policy BLOCKED（策略阻断）仍是 HTTP 200 的业务拒绝；AuthN/AuthZ 失败在 Coordinator 前停止。
- 四张业务表、`0001` Revision（迁移版本）、五维 Idempotency Scope（幂等作用域）和 Fingerprint Version 2（指纹版本 2）保持冻结。
- 未创建 `0002`，未使用 `Base.metadata.create_all()`，应用启动不自动迁移。

## 生产恢复边界

Ambiguous commit（提交结果不确定）必须返回安全 503，不能假设连接错误已经回滚，也不能在同进程自动重试。备份恢复期间必须停止写流量；恢复后运行 Doctor 和 Integrity Checker，并对恢复点后的外部副作用执行对账。ACTIVE 记录继续按 Lease 语义处理，禁止批量删除。

未来滚动 Migration 应采用 expand/migrate/contract（扩展/迁移/收缩），当前严格 Revision Gate 需要在该阶段升级为兼容 Revision 集合。Alembic downgrade（降级）不是默认应用回滚方式。

## 证据与边界

完整测试数字、故障矩阵、角色权限、备份恢复和 CI（持续集成）状态见 [M1.4-F Operational Readiness Closeout](m1_4f_operational_readiness_closeout.md)。部署、回滚、事故处理、备份恢复、容量保留和客户端重试协议见 `docs/operations/`（运维文档目录）。

```text
m1_4f_operational_readiness = implemented_and_locally_validated
remote_github_actions_runtime = not_run_in_current_environment
production_backup_infrastructure = documented_and_locally_drilled_not_deployed
external_side_effect_exactly_once = not_guaranteed
heartbeat = not_implemented
automatic_orphan_sweeper = not_implemented
automatic_retention_scheduler = not_implemented
git_diff_gate = unavailable
git_worktree_clean = not_asserted
```

M1.4 至此停止。下一 Roadmap（路线图）阶段仅为 ready（可开始），本次未实施。
