# M1.4-E FastAPI Durable Wiring Closeout（FastAPI 持久化接线收尾）

## Status（状态）

`M1.4-E = COMPLETED`（里程碑 1.4-E 已完成）。`M1.4-F = READY / NOT IMPLEMENTED`（下一阶段可开始但尚未实施）。

## Implemented（已实现）

- 单一 FastAPI composition root（FastAPI 组合根）和 worker-local lifespan（工作进程本地生命周期）。
- `POSTGRES`（PostgreSQL 持久化）、`NULL`（显式非持久化）与测试专用 `FAKE`（伪持久化）模式矩阵。
- `Idempotency-Key`（幂等键）原始 Header 检测、`optional`（可选）/`required`（必需）策略和长度/字符约束。
- `RequestExecutionGateway`（请求执行网关）对 durable execution（持久执行）与 idempotent execution（幂等执行）的选择。
- `v1.chat`（版本 1 通用对话）与 `v1.qa`（版本 1 问答）稳定操作名，以及包含解析后 `session_id`（会话标识）的 Fingerprint Version 2（请求指纹版本 2）。
- PostgreSQL startup connectivity gate（启动连通性门）和 Alembic revision gate（迁移版本门）。
- `/healthz`（存活探针）与 `/readyz`（就绪探针）分离。
- `Idempotency-Status: executed | replayed`（幂等状态：已执行/已重放）仅用于成功 2xx 响应。
- Projection failure（投影失败）发生在提交后时保留 COMPLETED（已完成）并允许后续 Replay（重放）。
- Client cancellation（客户端取消）不写普通 FAILED（失败终态），ACTIVE Claim（执行中声明）保留到 Lease（租约）到期。
- Snapshot Reader Version 1（快照读取器版本 1）在 Replay TTL（重放有效期）内保留；未知版本安全失败。

## Runtime Semantics（运行语义）

```text
AuthN / AuthZ（认证 / 授权）
→ RequestContext（可信请求上下文）
→ IdempotencyKeyParser（幂等键解析器）
→ RequestExecutionGateway（请求执行网关）
→ Transaction A（请求接收短事务）
→ Coordinator（事务外协调器）
→ Transaction B（结果终结短事务）
→ ResponseProjector（公开响应投影器）
→ HTTP Response（HTTP 响应）
```

Replay（重放）仍执行当前 AuthN/AuthZ 和当前 ResponseProjector，不恢复旧 Security Trace（安全追踪）或 Raw Response（原始响应）。`Idempotency-Status` 是本项目定义的响应 Header（响应头），不是外部通用标准。

## Safety And Data Minimization（安全与数据最小化）

原始 Key、Key Hash（键哈希）、Fingerprint（请求指纹）、JWT（JSON Web Token）、Claims（令牌声明）、数据库 URL 和 Replay Snapshot（重放快照）内容不进入公开响应。数据库 URL 在 Settings repr（配置表示）、ValidationError（校验错误）、启动错误和日志中保持脱敏。

## Validation（验收）

本阶段通过 unit tests（单元测试）、默认无数据库回归、真实 PostgreSQL 17 HTTP 集成、Policy Boundary Evaluation（策略边界评测）、RAG Adapter Evaluation（检索增强适配器评测）、历史 Node ID（测试节点标识）兼容检查和 `uv.lock`（依赖锁文件）哈希检查。最终数字以本文件 Closeout Record（收尾记录）和最终实施报告为准。

```text
status = completed
collected_nodeids = 638
unit_passed = 565
unit_deselected = 73
not_postgres_passed = 565
not_postgres_skipped = 1
not_postgres_deselected = 72
postgres_non_destructive_passed = 70
postgres_non_destructive_skipped = 2
postgres_destructive_passed = 72
postgres_destructive_skipped = 0
full_passed = 637
full_skipped = 1
policy_boundary = PASS
rag_adapter = PASS
final_revision = 0001 (head)
m1_1_missing_nodeids = 0
m1_4_b_missing_nodeids = 0
m1_4_b_r1_missing_nodeids = 0
m1_4_c_missing_nodeids = 0
m1_4_d_missing_nodeids = 0
uv_lock_sha256 = bdb8986c1832728d1fe3a19a3b30eb61927cc09df30f3ad0d81d20e5a2594fe5
```

## Not Implemented（未实现）

- automatic migration（自动迁移）与启动时 `create_all`（自动建表）。
- lease heartbeat（租约心跳续期）与 background orphan sweeper（后台孤儿记录清理器）。
- distributed worker cancellation（分布式工作进程取消）。
- Redis、Celery 和外部副作用 exactly-once（严格一次执行）保证。
- M1.4-F recovery matrix（恢复矩阵）、Runbook（运行手册）与 M1.4 总 Closeout（收尾）。

## CI Status（持续集成状态）

```text
postgres_ci_job = implemented
postgres_ci_workflow_static_validation = PASS
postgres_ci_runtime_execution = not_run_in_current_environment
local_equivalent_postgres_ci_gate = PASS
```

没有真实 GitHub Actions Run（GitHub 自动化运行）时，不将 CI runtime（持续集成运行时）描述为通过。
