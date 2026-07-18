# Data Retention And Capacity（数据保留与容量）

TTL（有效期）到期不等于物理删除。`idempotency_records`（幂等记录）会随唯一 Key（键）增长；Replay Snapshot（重放快照）可能包含用户可见答案和 Citation Metadata（引用元数据），数据库备份也会包含这些内容。

`convagent ops idempotency-prune`（幂等清理命令）默认 dry-run（试运行）。实际执行要求 `--apply`、数据库名确认、环境确认和非零 Safety Margin（安全余量）；它使用 PostgreSQL 数据库时间、`FOR UPDATE SKIP LOCKED`（跳过已锁行）、小批次短事务与删除时状态复验，只删除过期的 COMPLETED/FAILED（已完成/已失败）终态记录，永不删除 ACTIVE（执行中）、AgentRequest（请求）、AgentRun（运行）或 AuditEvent（审计事件）。Production（生产）必须使用 Maintenance Role（维护角色），Application Role（应用角色）没有 DELETE（删除）权限。

OPTIONAL（可选）模式的无 Key 请求没有客户端级重复抑制；崩溃后重试会形成独立执行。Request-driven Reclaim（请求驱动回收）只适用于带 Key 请求；存在外部副作用的操作应配置 REQUIRED（必需）。

容量监控关注各表行数、每日增长、stale ACTIVE（过期执行中）数、expired terminal（过期终态）数、Snapshot（快照）总体积与最大值、Audit（日审计）增长。Tenant、Principal、Key、Fingerprint（租户、主体、键、指纹）不得作为 Metrics Label（指标标签）。

DELETE（删除）不会立即释放数据库文件；应监控 autovacuum/analyze（自动清理/统计）、dead tuples（死元组）和表膨胀。大批量清理后评估 Bloat（膨胀），不得自动执行 `VACUUM FULL`。
