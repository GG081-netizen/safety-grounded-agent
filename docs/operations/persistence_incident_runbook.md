# Persistence Incident Runbook（持久化故障运行手册）

## 通用边界

先停止或减少写流量，保留 `/healthz`（存活探针）与只读诊断；使用 Persistence Doctor（持久化诊断器）和 Integrity Checker（完整性巡检器），禁止输出 DSN（连接串）、Key（键）、JWT（令牌）、Snapshot（快照）或用户正文。禁止直接修改生产 Row（数据行）、删除 ACTIVE（执行中）记录、绕过 Authorization（授权）、切换 NULL（空持久化）、调用 `create_all()` 或盲目 downgrade（降级）。

## 症状与处置

| 症状 | 安全诊断 | 恢复与验证 | 升级条件 |
|---|---|---|---|
| PostgreSQL unavailable（数据库不可用）或 `/readyz=503` | 检查网络、TLS、连接池、Revision（版本）和脱敏日志 | 恢复数据库后等待 `pool_pre_ping`（连接预检），验证 readiness 与新请求 | 连续失败或疑似数据损坏 |
| Transaction A 503（事务 A 不可用） | 确认 Coordinator 未执行；检查连接、锁和语句超时 | 同 Key 可重试，由数据库状态裁决 | Ambiguous Commit（提交不确定）持续出现 |
| Transaction B finalization failure（事务 B 终结失败） | 不返回成功、不改写 FAILED；检查 ACTIVE 与 Owner（所有者） | Lease 到期后请求驱动 Reclaim（回收） | 外部副作用可能已发生 |
| Projection failure（投影失败） | 确认数据库是否 COMPLETED（已完成） | 同 Key Replay，仍经过当前 Projector（投影器） | Snapshot Reader（快照读取器）不兼容 |
| 大量 409 in-progress/conflict（处理中/冲突） | 聚合 Operation/Outcome（操作/结果），不记录 Key | 检查 Lease、客户端 Key 复用和超时协调 | stale ACTIVE 持续增长 |
| Invalid persisted state（持久状态非法） | 运行完整 Doctor，不自动修复 | 停写、备份、人工审查与恢复 | 任一不变量计数非零 |
| Pool exhaustion / lock timeout（连接池耗尽/锁超时） | 检查连接预算、idle transaction（空闲事务）和慢查询 | 排空实例，修复容量或查询后恢复 | 连接泄漏或重复发生 |
| TLS / permission failure（传输/权限失败） | 检查证书有效期、Role Grant（角色授权）和 `search_path` | 轮换凭据或重新应用角色模板 | 明文降级请求必须拒绝 |
| Suspected credential leak（疑似凭据泄露） | 停止输出并封存脱敏证据 | 部署新凭据、滚动实例、排空旧连接、撤销旧凭据 | 立即安全升级 |
| Restore / corruption（恢复/损坏） | 停止写流量，验证备份与恢复点 | 恢复后先 Doctor，再对账外部副作用 | 任何完整性检查不完整或失败 |

恢复后的 ACTIVE 记录按 Lease 处理，不批量删除。外部系统副作用需要 Reconciliation（对账），数据库恢复本身不提供 exactly-once（严格一次）。
