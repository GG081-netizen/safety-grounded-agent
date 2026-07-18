# M1.4-D Persistent Idempotency Closeout（持久幂等阶段收尾）

## 1. Status（阶段状态）

`M1.4-D`（里程碑 1.4-D）已实现 component-level persistent idempotency（组件级持久幂等）、Replay（结果重放）和 Fencing（执行权隔离）。当前组件尚未接入 FastAPI（Web 应用接口框架），也不解析 `Idempotency-Key`（HTTP 幂等键请求头）。

真实执行链为：

```text
IdempotentDurableApplicationService（幂等持久化应用服务）
→ Transaction A（事务 A）：数据库时间原子 Claim（声明）+ AgentRequest（智能体请求）+ AuditEvent（审计事件）
→ No-Transaction Zone（无事务区）：ChatService（对话服务）→ Coordinator（协调器）
→ Transaction B（事务 B）：Fencing（执行权校验）+ AgentRun（智能体运行）+ Request Finalization（请求终结）+ Audit + Replay Snapshot（重放快照）
```

## 2. Implemented Contracts（已实现契约）

- `IdempotencyRepository`（幂等仓储协议）与 `ExecutionRepository`（执行仓储协议）保持接口隔离。
- `SQLAlchemyIdempotencyRepository`（SQLAlchemy 幂等仓储）使用 PostgreSQL `clock_timestamp()`（事务内数据库当前时间）计算 Claim、Lease（租约）和 Terminal TTL（终态有效期）。
- `SQLAlchemyIdempotentExecutionUnitOfWork`（SQLAlchemy 幂等执行工作单元）让执行仓储和幂等仓储共享同一短事务。
- `IdempotencyStateValidator`（幂等状态校验器）对非法 ACTIVE（执行中）、COMPLETED（已完成）和 FAILED（已失败）记录 fail closed（安全失败），不自动修复。
- `ReplaySnapshotMapper`（重放快照映射器）使用严格字段允许列表、canonical JSON（规范 JSON）、UTF-8 byte length（UTF-8 字节长度）和版本校验。
- `IdempotentDurableApplicationService`（幂等持久化应用服务）处理 ACQUIRED（取得执行权）、RECLAIMED（回收过期执行权）、REPLAY（重放）、IN_PROGRESS（执行中冲突）、CONFLICT（指纹冲突）和 PREVIOUS_FAILURE（先前失败）。

## 3. Frozen Semantics（冻结语义）

幂等 Scope（作用域）由 `tenant_id`（租户标识）、`organization_id`（组织标识）、`principal_user_id`（主体用户标识）、`operation`（操作名称）和 `idempotency_key_hash`（幂等键哈希）共同确定。原始 Key（幂等键）按原字符串 UTF-8 编码计算 SHA-256（安全哈希算法），不 trim（去除空白）、不 lower（转小写）、不做 Unicode normalization（Unicode 规范化），且不持久化。

Fingerprint Version（请求指纹版本）不一致时，在记录有效期内安全失败；只有 terminal TTL（终态有效期）到期后才能按新请求重新 Claim，`claim_version`（执行权版本）继续递增。

ACTIVE（执行中）Lease 到期且 Fingerprint（请求指纹）相同时允许 Reclaim（回收）。旧 Request（请求）被终结为 failed（失败），并创建固定 `failure_code=idempotency_lease_reclaimed`（失败码：幂等租约被回收）的管理型 Run（运行记录）。

连续 Replay 始终通过 `completed_run_record_id`（完成运行记录标识）到 `AgentRun.original_request_id`（原始执行请求标识）确定 canonical source（规范来源），不会形成 Replay chain（重放链）。Replay 使用当前 `RequestContext`（请求上下文）和当前 `AuthorizationDecision`（授权决定），不恢复旧安全 Trace（追踪）、授权或调试数据。

Cancellation（任务取消）直接向上传播，不写 failed finalization（失败终结）；ACTIVE Claim 保留至 Lease 到期。线程桥显式使用 `abandon_on_cancel=true`（取消时放弃等待线程结果）。

## 4. Security And Atomicity（安全与原子性）

Replay Snapshot（重放快照）禁止 NaN（非数字）、Infinity（无穷值）、原始供应商响应、JWT（JSON Web 令牌）、Claims（令牌声明）、授权快照、调试载荷和内部 Trace。超过 `max_replay_snapshot_bytes`（最大重放快照字节数）时，整个 Finalization Transaction（终结事务）回滚，不提交截断结果。

Audit Payload（审计载荷）只记录安全的 idempotency outcome（幂等结果）、`claim_version`（执行权版本）、`reclaimed`（是否回收）、`replayed`（是否重放）和 `expired_reuse`（是否过期复用）。不记录原始 Key、Key Hash（键哈希）、Fingerprint、`owner_request_id`（执行权所有者请求标识）或 Snapshot 内容。

Fencing 条件同时校验 `owner_request_id`（执行权所有者请求标识）、`claim_version`（执行权版本）和 ACTIVE 状态。旧 Owner（所有者）不能执行 complete（完成终结）或 fail（失败终结）。

## 5. PostgreSQL Evidence（PostgreSQL 证据）

真实 PostgreSQL 17（关系数据库第 17 版）验证覆盖：数据库权威时间、五维 Scoped Unique（作用域唯一约束）、事务回滚、指纹冲突、版本不匹配、完成重放、连续重放、失败保留、结构化 Policy BLOCKED（安全策略阻断）、Lease Reclaim、旧 Owner Fencing、非法状态、并发首个 Claim、服务级并发只执行一次、Terminal TTL 复用和 Snapshot 超限回滚。

```text
collected_nodeids = 613                         # 当前收集的测试节点数量
legacy_m1_1_nodeids = 347                       # M1.1 历史测试节点数量
legacy_m1_4_b_nodeids = 529                     # M1.4-B 历史测试节点数量
legacy_m1_4_b_r1_nodeids = 530                  # M1.4-B-R1 历史测试节点数量
legacy_m1_4_c_nodeids = 573                     # M1.4-C 历史测试节点数量
missing_legacy_nodeids = 0                      # 缺失历史测试节点数量
postgres_non_destructive = 64 passed, 2 skipped # 非破坏性 PostgreSQL 结果
postgres_destructive = 66 passed                # 完整 PostgreSQL 结果
full_with_postgres = 612 passed, 1 skipped      # 启用 PostgreSQL 的全量回归结果
final_revision = 0001 (head)                    # 最终迁移版本
```

上述数字以本阶段最终 Regression Gate（回归验收门）的实际输出为准。PostgreSQL CI Job（PostgreSQL 持续集成任务）已更新并完成静态结构验证；当前环境未运行真实 GitHub Actions（GitHub 自动化运行），因此不声称远程 CI 已通过。

## 6. Boundaries（能力边界）

```text
persistent_idempotency_component = implemented      # 持久幂等组件：已实现
scoped_atomic_claim = implemented                    # 作用域原子声明：已实现
request_fingerprint_conflict = implemented           # 请求指纹冲突：已实现
claim_fencing = implemented                          # 执行权隔离：已实现
completed_result_replay = implemented                 # 完成结果重放：已实现
request_driven_reclaim = implemented                  # 请求驱动租约回收：已实现
fastapi_idempotency_wiring = not_implemented          # FastAPI 幂等接线：尚未实现
idempotency_key_header_parsing = not_implemented      # 幂等请求头解析：尚未实现
production_database_lifespan = not_implemented        # 生产数据库生命周期：尚未实现
external_side_effect_exactly_once = not_guaranteed    # 外部副作用严格一次：不保证
claim_heartbeat = not_implemented                     # 执行权心跳：尚未实现
orphan_sweeper = not_implemented                      # 孤儿记录清理器：尚未实现
```

M1.4-D 在 component boundary（组件边界）停止。下一阶段 `M1.4-E`（里程碑 1.4-E）可以消费该组件并设计 FastAPI 幂等接线，但本阶段未开始该工作。
