# Backup And Restore Runbook（备份与恢复运行手册）

本项目区分三层能力：Local Logical Backup Drill（本地逻辑备份演练）、Encrypted Production Backup（加密生产备份）与 PITR/WAL Archive（时间点恢复/预写日志归档）。本地演练不代表生产备份基础设施、磁盘加密、RPO（恢复点目标）或 RTO（恢复时间目标）已经部署。

本地演练使用 `scripts/postgres_backup_restore_drill.py`（备份恢复脚本），要求 `pg_dump`、`pg_restore`、`createdb` 和 `dropdb` 官方客户端工具。脚本只接受名称含 `test` 的源数据库，使用 Custom Format（自定义格式）、权限 `0600` 的临时文件、全新恢复数据库、进程超时与 `finally` 清理；凭据仅通过子进程环境传递。

```bash
python scripts/run_with_postgres_test_env.py -- python scripts/postgres_backup_restore_drill.py
```

恢复期间必须停止写流量。恢复完成后先运行 Persistence Doctor（持久化诊断器）和 Integrity Checker（完整性巡检器），再恢复业务。恢复点之后已经发生的 CRM、邮件、订单或支付副作用必须对账；数据库 Restore（恢复）不保证外部系统 exactly-once（严格一次）。不得批量删除恢复出的 ACTIVE（执行中）记录，它们继续遵循 Lease（租约）语义。

生产备份必须加密、限制访问、设置独立保留期并周期性恢复验证；恢复后重新应用 Owner（所有者）、Grant（授权）、Default Privilege（默认权限）和 Sequence Permission（序列权限）。
