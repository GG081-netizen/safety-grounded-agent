# Persistence Doctor（持久化诊断器）

`convagent ops persistence-doctor`（持久化诊断命令）是只读、脱敏且有界的运维入口。默认 `quick mode`（快速模式）使用短 `statement timeout`（语句超时）和整体 `deadline`（截止时间）；`--full`（完整模式）显式提高预算，但仍有总超时。任何部分扫描或超时都返回 `incomplete`（未完整），不得报告完整 `PASS`（通过）。

诊断内容包括 PostgreSQL（数据库）连通性、`0001` Alembic Revision（迁移版本）、四张业务表、ORM/Alembic Metadata Diff（对象关系映射与迁移元数据差异）、`TimeZone`（时区）、数据库时间、应用 UTC Clock Drift（协调世界时时钟偏差）、`timestamptz`（带时区时间类型）、TLS（传输层安全）、`search_path`（对象搜索路径）以及 Request/Run/Audit/Idempotency（请求/运行/审计/幂等）不变量。

```bash
convagent --json ops persistence-doctor
convagent --json ops persistence-doctor --full
convagent --json ops persistence-integrity
```

退出码：`0` 健康；`2` 配置非法；`3` 连接不可用；`4` 版本不匹配；`5` 完整性异常或检查不完整；`6` 权限异常；`7` 传输安全异常。输出不包含 DSN（数据库连接串）、Key（密钥）、Hash（哈希）、Fingerprint（指纹）、Snapshot（快照）、JWT（令牌）、Principal（主体）或用户正文。

Lease（租约）和 Terminal TTL（终态有效期）只使用 PostgreSQL `clock_timestamp()`（数据库当前时间）判断；应用 UTC Clock（协调世界时时钟）只用于漂移诊断。
