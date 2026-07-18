# M1.4-F Operational Readiness（运维就绪）Closeout（收尾记录）

## 状态

```text
status = completed
m1_4f_operational_readiness = implemented_and_locally_validated
schema_revision = 0001
schema_changed = false
next_roadmap_stage = ready_not_started
```

M1.4-F 冻结并验证 M1.4 的 PostgreSQL（关系数据库）持久化运行边界。本阶段没有修改 `Coordinator`（协调器）、Policy（安全策略）、RAG（检索增强生成）、AuthN/AuthZ（认证与授权）、四表 Schema（数据库结构）或 `0001` Migration（初始迁移）。

## 冻结主链

```text
FastAPI（Web 接口框架）
-> AuthN/AuthZ（认证与授权）
-> RequestContext（可信请求上下文）
-> Idempotency-Key（幂等键）解析
-> Transaction A（请求接收事务）
-> Coordinator（协调器，事务外执行）
-> Transaction B（终态事务与执行权隔离）
-> ResponseProjector（公开响应投影器）
```

Transaction A（请求接收事务）原子写入 Request（请求记录）、ACTIVE Claim（活动声明）和 `request_accepted`（请求已接收）审计。Transaction B（终态事务）原子写入 Run（运行记录）、Request 终态、终态 Audit（审计事件）和 Idempotency（幂等）终态。Replay（结果重放）不创建新 Run，且始终通过完成 Run 的 `original_request_id`（原始请求标识）指向首次真实执行请求。

## 故障与恢复

| 场景 | 已验证结果 |
|---|---|
| Transaction A ambiguous commit（提交结果不确定） | 不执行 Coordinator，返回安全不可用；后续请求按数据库真实状态判断 |
| Transaction B ambiguous commit（提交结果不确定） | 不返回成功、不改写 FAILED（失败）；后续请求按数据库状态 Replay 或继续 ACTIVE |
| 进程在 Transaction A 后崩溃 | ACTIVE 与 in-progress Request（处理中请求）保留，Lease（租约）到期后请求驱动 Reclaim（回收） |
| 两实例并发 Claim | 共享 PostgreSQL 只产生一个合法 Owner（所有者） |
| Stale Owner（过期所有者）终态提交 | `claim_version`（声明版本）与 `owner_request_id`（所有者请求标识）执行 Fencing（执行权隔离） |
| 数据库网络中断 | `/healthz`（存活探针）保持进程信号，`/readyz`（就绪探针）失败，业务不降级到非持久路径 |
| 数据库网络恢复 | 新连接恢复，readiness（就绪状态）恢复，后续请求可继续执行 |
| Response projection（响应投影）失败 | 数据库保留 COMPLETED（已完成），同 Key 重试从 Snapshot（快照）重放并再次投影 |
| Cancellation（协程取消） | 正常向上传播，不写普通失败终态；ACTIVE 等待租约回收 |

数据库 COMMIT（提交）连接错误不等同于 rollback（回滚）。本进程不自动重试结果不确定的提交，以避免重复执行业务逻辑。

## Doctor 与完整性检查

`convagent ops persistence-doctor`（持久化诊断命令）是有界、只读诊断工具。Quick mode（快速模式）使用短 statement timeout（语句超时）；Full mode（完整模式）必须显式启用，按总 Deadline（截止时间）返回 `complete`（扫描完整性）。部分扫描不能报告完整 PASS（通过）。

Doctor 当前检查：

- connectivity（连通性）、数据库名、Schema 与 `search_path`（对象查找路径）；
- PostgreSQL TimeZone（数据库时区）、数据库时间、应用 UTC（协调世界时）Clock drift（时钟漂移）；
- 四表的 `timestamptz`（带时区时间戳）列契约；
- `0001` Revision（迁移版本）、四张业务表与 ORM/Alembic metadata diff（对象映射与迁移元数据差异）；
- TLS（传输层安全）连接策略；
- Request/Run、Replay lineage（重放血缘）、Audit 与 Idempotency 状态机完整性。

本地验收曾发现一个旧测试库虽标记为 `0001`，Replay 自引用外键仍保留 R1 前的 PostgreSQL 自动名称；执行冻结的 downgrade/upgrade（降级/升级）循环后，全新 `0001` 生成稳定命名约定名称并通过严格约束名断言。这证明 Revision 检查和 `compare_metadata` 不能替代手工 Schema signature（结构签名），恢复后两层检查都必须运行。

输出不包含 DSN（数据库连接串）、密码、原始 Key、Key Hash（键哈希）、Fingerprint（请求指纹）、Prompt（提示词）、Snapshot 内容或异常原文。

## Retention 与 Prune

`convagent ops idempotency-prune`（幂等终态清理命令）默认 dry-run（试运行）。实际删除必须显式 `--apply` 并确认数据库和环境。Prune（清理）使用数据库时间、`FOR UPDATE SKIP LOCKED`（跳过已锁行）、短事务、小批次和删除前二次状态校验；永不删除 ACTIVE，也不删除 Request、Run 或 Audit 历史。

TTL（生存时间）只表示可清理资格，不等于物理空间立即释放。运维需监控 autovacuum（自动清理）、analyze（统计信息分析）、dead tuples（死亡元组）和表膨胀；禁止自动执行 `VACUUM FULL`（全表重写清理）。

## 权限与连接安全

真实 PostgreSQL 权限测试验证：

- Migration Role（迁移角色）拥有 Schema 对象；App Role（应用角色）不是 Owner（所有者）；
- App Role 可执行所需业务 DML（数据操作），不能执行 DDL（结构操作）、修改 `alembic_version`、更新或删除 Audit、删除历史业务记录；
- Maintenance Role（维护角色）可以执行受保护的终态 Idempotency 清理；
- PUBLIC（公共角色）无多余 Schema 创建权限；Sequence（序列）权限满足插入需求；
-未来对象需要显式 default privileges（默认权限），恢复后必须重新应用 Grants（授权）。

`DatabaseEngine`（数据库引擎）配置连接、statement、lock、idle transaction、readiness 和 shutdown 超时，固定受控 `search_path`。Production（生产模式）远程 PostgreSQL 禁止明文降级；TLS 证书和凭据轮换流程见部署 Runbook（运行手册）。

## Backup 与 Restore

本地 logical backup drill（逻辑备份演练）使用 PostgreSQL 17 官方 `pg_dump`/`pg_restore`，在独立源测试数据库中写入 completed、blocked、failed、Replay、ACTIVE 和 Reclaim 代际数据，再恢复到全新数据库。恢复后验证：

- Revision 为 `0001`；
- 四张业务表行数与源库一致；
- PersistenceIntegrityChecker（持久化完整性检查器）为 healthy/complete（健康且完整）；
- 临时源库、恢复库和备份文件均被清理。

该演练不等于 encrypted production backup（加密生产备份）或 PITR/WAL archive（时间点恢复/预写日志归档）已部署。生产备份必须加密、访问受控并持续执行恢复验证。

恢复边界：恢复期间停止写流量；恢复后先运行 Doctor 与 Integrity Checker；不批量删除恢复出的 ACTIVE；恢复点之后的外部副作用必须单独 reconciliation（对账）。数据库恢复不保证外部 CRM、邮件或订单 exactly-once（严格一次）。

## 本地验证证据

```text
collected = 663
unit = 582 passed, 81 deselected
non_postgres = 582 passed, 1 skipped, 80 deselected
postgres_non_destructive = 72 passed, 2 skipped
postgres_destructive = 74 passed, 0 skipped
operational = 6 passed
default_full = 582 passed, 81 skipped
main_with_real_postgres = 656 passed, 1 skipped, 6 operational deselected
policy_boundary = PASS
rag_adapter = PASS
final_revision = 0001 (head)
metadata_diff = 0
historical_nodeid_missing = 0
backup_restore = PASS
```

Operational tests（运维集成测试）覆盖真实子进程崩溃、两个独立 Engine（引擎）的并发声明、Prune 与 TTL Reacquire（到期重新声明）竞争、任务内 TCP proxy（传输控制协议代理）模拟中断、连接池 Soak（稳定性运行）和临时角色权限。每项都具有进程、事件或数据库操作超时，使用 monotonic clock（单调时钟）轮询，并在 `finally`（最终清理块）中只清理任务资源。

## CI 状态

```text
postgres_ci_job = implemented
postgres_ci_workflow_static_validation = PASS
postgres_ci_runtime_execution = not_run_in_current_environment
operational_ci_job = implemented
operational_ci_workflow_static_validation = PASS
operational_ci_runtime_execution = not_run_in_current_environment
local_equivalent_postgres_gate = PASS
local_operational_gate = PASS
```

Operational Job（运维持续集成任务）具有 6 项测试零跳过断言，并执行有超时和清理保护的逻辑备份/全新库恢复。当前目录没有有效 Git metadata（版本控制元数据），因此没有真实 GitHub Actions Run（远程自动化运行）。

## 已知限制

- Claim heartbeat（声明心跳）、background orphan sweeper（后台孤儿清理器）和 automatic retention scheduler（自动保留期调度器）未实现。
- Distributed worker cancellation（分布式工作进程取消）未实现；Reclaim 只隔离过期 Owner 的提交权，不能物理停止旧 Coordinator。
- OPTIONAL（可选幂等）无 Key 请求不具备客户端级重复抑制；有外部副作用的操作应使用 REQUIRED（强制幂等）。
- Redis（内存数据服务）、Celery（任务队列）、Saga（长事务协调）和 Transactional Outbox（事务发件箱）未实现。
- 外部副作用 exactly-once 不保证。
- 无生产 RPO/RTO（恢复点/恢复时间目标）保证，无生产备份基础设施部署声明。
