# Rollback Runbook（回滚运行手册）

M1.4-F（里程碑 1.4-F）不修改 Schema（数据库结构），代码回滚通常保持 `0001`。回滚前检查 Replay Snapshot Version（重放快照版本）、Persistence Config（持久化配置）和 HTTP Header Contract（请求头契约）；旧代码必须能够读取 Replay TTL（重放有效期）内仍存在的所有 Writer Version（写入版本）。

先移除实例 Readiness（就绪状态），等待请求排空，再回滚应用并验证 `/readyz`、首次执行、Replay（重放）与 Policy BLOCKED（策略阻断）。不得切换 NULL（空持久化）、删除 Idempotency Record（幂等记录）、自动修复 Row（数据行）或盲目执行 `alembic downgrade`。若 Snapshot Reader（快照读取器）不兼容，应阻止回滚并升级处理。

未来结构变更必须遵循 expand / migrate / contract（扩展/迁移/收缩）；数据库 downgrade（降级）仅在独立批准、备份完成且恢复演练通过后考虑。
