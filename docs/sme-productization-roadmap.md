# 面向中小企业内部部署的智能体产品化路线

## 1. 定位

本路线面向单个中小企业、多部门和约 50-500 名内部用户，将当前命令行原型逐步演进为 `Safety-Grounded Agent Orchestration Platform`（以安全策略为基础的智能体编排平台）。目标是 `single-tenant internal deployment`（单企业内部部署），不是 `multi-tenant SaaS`（多租户软件即服务平台），也不是自由对话式 `Multi-Agent`（多智能体）系统。

当前 `Coordinator`（协调器）继续负责安全策略、意图理解、任务路由和模块执行。未来身份、授权、事务、幂等、审计和审批由 `Application Service`（应用服务层）承担，不塞入协调器。

## 2. M1.1 当前交付

`M1.1`（里程碑 1.1）只建立测试基线、模型运行边界和可信数据协议：

- `DashScopeClient`（阿里云百炼模型客户端）通过非流式兼容接口调用模型。
- `standard`（标准档）绑定 `qwen3-8b`，是当前唯一可由运行时选择的模型档位。
- `advanced`（高级档）和 `evaluator`（评测档）已经配置，但 `runtime_selectable=false`（运行时不可选择）。
- `lightweight`（轻量档）使用 `not_configured`（未配置）哨兵，当前禁用。
- `ModelRouteDecision`（模型路由决定）只是不可变数据协议，动态 `ModelRouter`（模型路由器）尚未实现。
- `UserRequest`（用户请求）、`Principal`（可信主体）、`AuthorizationDecision`（授权决定）、`RequestContext`（请求上下文）和 `RuntimeVersionSnapshot`（运行版本快照）已定义；其中用户请求和运行上下文已在 M1.2（里程碑 1.2）由应用服务层消费，授权决定仍未接入运行链。

当前真实模型链路为：

```text
RealAgent（真实模型智能体）
→ create_llm_client（创建大语言模型客户端）
→ standard profile（标准模型档位）
→ DashScopeClient（阿里云百炼模型客户端）
→ qwen3-8b（当前默认模型）
→ stream=false（关闭流式返回）
→ enable_thinking=false（关闭思考模式）
```

## 3. 后续里程碑

### 3.1 可部署只读平台

后续 `M1.2-M1.7`（里程碑 1.2 至 1.7）计划引入 `FastAPI`（Web 应用接口框架）、`PostgreSQL`（关系数据库）、企业身份、组织权限、检索前文档权限过滤、客户关系管理只读连接器、审计、可观察性和恢复测试。

旧 `JSON Store`（JSON 文件存储）迁移遵循：数据盘点、只读试运行、幂等导入、事务提交、校验报告、旧存储只读和最终归档。迁移命令名称留到服务化命令树确定后再冻结。

### 3.2 受治理操作提案

第二里程碑计划增加 `ActionProposal`（操作提案）、`ApprovalPolicy`（审批策略）、`ApprovalRequest`（审批请求）、不可变审批决定和 `MockActionExecutor`（模拟操作执行器）。此阶段仍不直接写真实客户关系管理系统。

### 3.3 受控写入框架

第三里程碑只开放少量低风险写操作，并要求 `idempotency`（幂等）、`Transactional Outbox`（事务发件箱）、资源版本检查、对账、暂停开关和完整审计。没有真实企业凭据、审批制度和试点证据时，不描述为生产上线。

## 4. 当前边界

`M1.1`（里程碑 1.1）没有实现动态模型选择、8B 升级 14B、评测模型自动调用、本地轻量模型、流式输出、应用服务层、身份认证、数据库或真实客户关系管理写入。共享百炼地址只用于开发联调；未来生产部署应使用企业工作空间专属地址。

## 5. M1.1 Closeout（里程碑 1.1 收尾记录）

以下记录固化本阶段实际验收结果，不将测试通过解释为生产级可靠性：

```text
status = completed                              # 阶段状态：已完成
legacy_nodeids = 347                            # 实施前保留的测试节点标识数量
current_nodeids = 384                           # 实施后收集到的测试节点标识数量
missing_legacy_nodeids = 0                      # 缺失的旧测试节点标识数量
unit_passed = 383                               # 通过的单元测试数量
unit_duration_seconds = 1.58                    # 单元测试墙钟耗时，单位为秒
full_passed = 383                               # 通过的全量测试数量
full_skipped = 1                                # 跳过的全量测试数量，即可选真实百炼冒烟测试
full_duration_seconds = 1.68                    # 全量测试墙钟耗时，单位为秒
policy_boundary = PASS                          # 业务安全策略边界评测：通过
rag_adapter = PASS                              # 检索增强生成适配器评测：通过
default_provider = dashscope                    # 默认模型供应商：阿里云百炼
default_profile = standard                      # 默认模型档位：标准档
default_model = qwen3-8b                        # 默认模型：千问 3 八十亿参数模型
model_router_runtime_status = not_implemented   # 模型路由器运行状态：尚未实现
```

其中 `full_skipped`（全量测试跳过数量）对应默认关闭的真实 DashScope smoke test（阿里云百炼冒烟测试）；它需要显式开关和专用测试密钥，不属于默认离线验收门。

## 6. M1.2 Delivery（里程碑 1.2 当前交付）

**[源码确认]** M1.2（里程碑 1.2）已经开始消费 M1.1（里程碑 1.1）建立的可信 Contract（数据协议），没有继续横向扩展 LLM layer（大语言模型层）。当前调用链为：

```text
FastAPI Route（Web 应用接口路由）
→ RequestContextBuilder（请求上下文构建器）
→ Application Service（应用服务层）
→ Coordinator（现有协调器）
```

当前已实现：

- FastAPI service skeleton（FastAPI 服务骨架）和 `/healthz`（进程存活接口）。
- 同步 `/v1/chat`（对话接口）和 `/v1/qa`（知识问答接口）。
- 统一 API error model（应用接口错误模型），隔离内部执行异常详情。
- 外部 UserRequest（用户请求）到内部 RequestContext（请求上下文）的可信映射。
- Application Service（应用服务层）调用现有 Coordinator（协调器），不把服务外围职责写入协调器。
- 服务端生成 `request_id`（请求标识）和 `trace_id`（追踪标识），并通过响应头和响应体返回。
- 公开编排结果继续执行 raw response filtering（原始响应过滤）。

当前明确没有同时引入 PostgreSQL（关系数据库）、Redis（内存数据服务）、OIDC（开放身份连接协议）或 JSON Store migration（JSON 文件存储迁移）。开发主体是服务端固定占位身份，不等于真实认证或授权；Idempotency-Key（幂等键）当前只进入上下文，不提供持久防重语义。

## 7. M1.2 Closeout（里程碑 1.2 收尾记录）

```text
status = completed                              # 阶段状态：已完成
legacy_nodeids = 347                            # M1.1 实施前保留的测试节点标识数量
current_nodeids = 394                           # M1.2 完成后收集到的测试节点标识数量
missing_legacy_nodeids = 0                      # 缺失的旧测试节点标识数量
unit_passed = 393                               # 通过的单元测试数量
unit_duration_seconds = 1.45                    # pytest 报告的单元测试耗时，单位为秒
unit_wall_seconds = 1.93                        # 单元测试进程墙钟耗时，单位为秒
full_passed = 393                               # 通过的全量测试数量
full_skipped = 1                                # 跳过的可选真实百炼冒烟测试数量
full_duration_seconds = 1.53                    # pytest 报告的全量测试耗时，单位为秒
full_wall_seconds = 1.98                        # 全量测试进程墙钟耗时，单位为秒
policy_boundary = PASS                          # 业务安全策略边界评测：通过
rag_adapter = PASS                              # 检索增强生成适配器评测：通过
http_api = healthz,chat,qa                      # 已实现接口：存活检查、同步对话、同步知识问答
http_service_boundary = implemented             # HTTP 服务边界：已实现
request_context_builder = implemented           # 请求上下文构建器：已实现
trusted_context_source = server_generated       # 可信上下文来源：服务端生成
principal_mode = development_placeholder        # 主体模式：开发占位主体
policy_first_execution = implemented            # 策略优先执行：已实现
qa_route_enforcement = implemented              # 知识问答路由强制约束：已实现
raw_response_default_exposure = false            # 原始响应默认暴露：否
authentication = not_implemented               # 身份认证状态：尚未实现
authorization = placeholder_only                # 授权状态：仅有占位 Contract，未接运行链
persistent_idempotency = not_implemented         # 持久幂等状态：尚未实现
database_persistence = not_implemented           # 数据库持久化状态：尚未实现
production_readiness = not_implemented           # 生产就绪状态：尚未实现
```

M1.2（里程碑 1.2）完成只证明最小同步服务边界、可信字段隔离和错误映射可重复测试，不证明高并发、多实例部署、真实企业认证、持久幂等或生产可用性。

## 8. M1.3（里程碑 1.3）认证与授权交付 — COMPLETED

**[源码确认]** M1.3 已将 `RequestMetadata`（请求元数据）、JWT（JSON Web 令牌）验证、JWKS（JSON Web 密钥集）缓存、`Principal`（可信主体）映射和 `AuthorizationDecision`（授权决定）接入 FastAPI（Web 应用接口框架）主链。Coordinator（协调器）仍然只消费经过应用层映射的业务输入，不接触 HTTP Header（请求头）、Token（令牌）或权限表。

**受保护请求链：**

```text
RequestMetadata
→ BearerTokenParser
→ JOSE Header Policy
→ Async JWKS (Static/Remote + single-flight + negative-kid cache + transactional refresh)
→ JWT Signature & Claims Verification
→ VerifiedClaims
→ PrincipalMappingPolicy
→ AuthorizationService (RBAC/ABAC v1)
→ AuthorizationDecision
→ RequestContextBuilder
→ ChatService
→ Coordinator
→ ResponseProjector
```

**关键交付：**
- JWT Bearer Token 解析（严格单 Header、ASCII 凭证）
- JOSE Header Policy（alg=RS256 硬编码，拒绝 jku/x5u/jwk/x5c/crit）
- 异步 Static/Remote JWKS Provider（流式、事务刷新、single-flight、negative-kid cache、generation）
- JWT 签名 + Claims 严格验证（NumericDate 类型、audience 去重、organization_id 匹配、token_use 过滤）
- PrincipalMappingPolicy（VerifiedClaims → Principal，tenant/org 由服务端决定）
- 确定性 RBAC/ABAC（角色权限并集、Principal disabled 拒绝、conservative_route_union）
- AuthN/AuthZ/Policy 三层分层 Trace
- ResponseProjector（config + permission 双重 Gate）

**HTTP 语义：**

```text
Malformed Bearer（格式错误的 Bearer 请求）→ 400 → Coordinator 未调用
Invalid Token（无效令牌）→ 401 → Coordinator 未调用
JWKS Unavailable（签名密钥服务不可用）→ 503 → Coordinator 未调用
Authorization Denied（授权拒绝）→ 403 → Coordinator 未调用
Policy BLOCKED（业务安全阻断）→ HTTP 200 业务拒绝 → 不执行后续任务
```

`demo` mode 仅在无 Authorization Header 时使用开发占位身份；提交无效 Token 不回退。`test`/`production` mode 禁止占位身份。

`debug_viewer`（调试查看角色）只附加 `raw_response:view`（查看原始响应权限），不会获得 chat/qa（对话/问答）调用权限。只有业务角色与调试角色并存、且 `include_raw_response`（允许原始响应配置）开启时，公开响应才包含 `debug.rag_raw_response`（RAG 原始响应调试载荷）。

**Authentication ≠ Authorization ≠ Policy：**
- **Authentication**：验证 Token 签名和 Claims → VerifiedClaims（401 时 Coordinator 未调用）
- **Authorization**：Principal roles → permissions 并集，与 Route 权限比较 → AuthorizationDecision（403 时 Coordinator 未调用）
- **Policy**（Coordinator 内部）：业务安全规则 BLOCKED/UNCERTAIN/SAFE，已认证已授权但业务拒绝 → HTTP 200

## 9. M1.3 Closeout（里程碑 1.3 收尾记录）

```text
status = completed                              # 阶段状态：已完成
legacy_nodeids = 347                            # M1.1 保存的旧测试节点数量
current_nodeids = 424                           # M1.3 完成后的测试节点数量
missing_legacy_nodeids = 0                      # 缺失旧测试节点数量
unit_passed = 423                               # 通过的单元测试数量
full_passed = 423                               # 通过的全量测试数量
full_skipped = 1                                # 跳过的可选真实网络测试数量
policy_boundary = PASS                          # 业务安全边界评测：通过
rag_adapter = PASS                              # RAG 适配器评测：通过
uv.lock_changed_during_validation = no          # 验证期间 lockfile 未变化
uv.lock_sha256 = E1A8D1F1555F492AE0280A12527E0B4505158D1FEB624E671AE0BF193D4A38D7

authentication_runtime_status = implemented     # JWT Bearer 认证：已实现
authorization_runtime_status = implemented      # 确定性 RBAC/ABAC 授权：已实现
authorization_strategy = conservative_route_union
supported_jwt_algorithm = RS256
jwks_provider = static_and_remote_async
jwks_single_flight = implemented
jwks_stale_if_error = disabled
tenant_mode = single_tenant
organization_mode = single_controlled_organization
demo_placeholder_principal = no_header_only
production_placeholder_principal = forbidden
raw_response_default_exposure = false
raw_response_gate = config_and_permission

oidc_login_status = not_implemented
enterprise_idp_integration = not_implemented
token_introspection = not_implemented
real_time_revocation = not_implemented
persistent_audit = not_implemented
```

完整 closeout 细节见 [M1.3 Closeout](phases/m1_3_oidc_jwt_authorization_closeout.md)。

## 10. M1.4 — PostgreSQL Persistence & Idempotency（已完成）

### 目标

在现有认证、授权和服务边界之上引入 PostgreSQL 持久化存储、原子幂等防重、短事务状态迁移和应用层审计。Coordinator 不感知数据库。

### 修订调用链

以下是 M1.4 全阶段的目标链路，其中 idempotency claim（幂等声明）仍属于 M1.4-D，FastAPI 数据库接线仍属于 M1.4-E：

```text
FastAPI Route → AuthN/AuthZ → DurableApplicationService
  → Transaction A: Atomic Idempotency Claim + INSERT AgentRequest (in_progress) + Audit → COMMIT
  → [No DB] asyncio.to_thread(ChatService → Coordinator)
  → Transaction B: INSERT AgentRun + UPDATE request/idempotency + Audit → COMMIT
  → ResponseProjector → AgentResponse
```

M1.4-C 当前真实组件链路为：

```text
DurableApplicationService（持久化应用服务）
  → Transaction A（事务 A）：AgentRequest（智能体请求）+ request_accepted（请求已接受审计）→ COMMIT（提交）
  → No DB transaction（无数据库事务）：ChatService（对话应用服务）→ Coordinator（协调器）
  → Transaction B（事务 B）：AgentRun（智能体运行）+ request terminal state（请求终态）+ audit（审计）→ COMMIT（提交）
```

该组件尚未接入 FastAPI Route（FastAPI 路由），也不读取或写入 `idempotency_records`（幂等记录表）。

### 技术范围

| 组件 | 用途 |
|---|---|
| PostgreSQL 17 + SQLAlchemy 2 async + asyncpg | 当前真实验收使用的异步关系数据库组合 |
| Alembic | 数据库迁移（显式命令，不自动执行） |
| `agent_requests` | 每一次进入应用层的 HTTP 调用记录 |
| `agent_runs` | 每一次 Coordinator 真实执行记录 |
| `audit_events` | 应用层审计事件（request_accepted/replayed/blocked/completed/failed） |
| `idempotency_records` | 原子幂等 Claim，scoped unique key |

### 关键设计决策（已冻结）

- **原子幂等 Claim**：INSERT ON CONFLICT (scope, idempotency_key) DO NOTHING
- **Fencing**：claim_version + owner_request_id + lease_expires_at；Transaction B 条件 UPDATE 防止旧 owner 覆盖
- **Idempotency scope**：SHA-256(tenant || org || principal || operation)
- **Request fingerprint**：SHA-256(fingerprint_version || operation || session_id || user_text || task_override)
- **短事务**：Coordinator 执行在数据库事务外，不持有 AsyncSession
- **Replay**：保存 sanitized canonical result + schema_version，重放时重新投影（当前 metadata + 当前 auth）
- **Audit scope**：Plan A — 仅应用层审计
- **Data minimization**：默认 hash-only（user_text 不持久化），全文需 opt-in
- **Alembic**：显式 `alembic upgrade head`，仅 demo/test 可开启 auto_migrate
- **Fail-closed**：production 必须配置数据库，连接失败启动失败
- **Orphan**：无后台 sweeper；同 key 重试可原子 re-claim stale lease；旧 owner 被 fencing 排除
- **FK structure**：agent_runs.original_request_id → agent_requests.id (unique)；无循环 FK

### Delivery Batches

| Batch | 内容 | 关键 Gate | 状态 |
|---|---|---|---|
| M1.4-A | 依赖、DatabaseConfig（数据库配置）、DatabaseEngine（数据库引擎）、Null persistence（空持久化实现） | 当阶段既有回归通过 | ✅ **COMPLETED**（已完成） |
| M1.4-B | ORM Schema (4 tables + fencing)、Alembic、约束/索引 | upgrade/downgrade 循环 | ✅ **COMPLETED** |
| M1.4-B-R1 | Schema Contract stabilization（结构契约稳定化）、严格约束名、CI Job（持续集成任务） | PostgreSQL 17 两轮真实 Gate（验收门） | ✅ **COMPLETED** |
| M1.4-C | Repository（仓储）、UnitOfWork（工作单元）、DurableService（持久化服务）、短事务（无运行时幂等） | Repository/UoW PostgreSQL 验证 | ✅ **COMPLETED**（已完成） |
| M1.4-D | 原子 Claim（声明）、fingerprint（请求指纹）、fencing（执行权隔离）、conflict/replay（冲突/重放）、并发测试 | 并发仅执行一次 Coordinator（协调器）；旧 Owner（所有者）被隔离 | ✅ **COMPLETED**（已完成） |
| M1.4-E | FastAPI 接入、fail-closed（故障关闭）、Auth snapshot（授权快照）、端到端投影 | PostgreSQL 17 HTTP 执行、Replay（重放）、冲突、并发和失败映射通过 | completed（已完成） |
| M1.4-F | 恢复矩阵、Doctor（诊断器）、Integrity Checker（完整性检查器）、Prune（清理）、最小权限、备份恢复、运维 Runbook（运行手册）与 M1.4 总 Closeout（收尾） | 本地 PostgreSQL 17 Operational Gate（运维验收门）、备份恢复和全回归通过 | ✅ **COMPLETED**（已完成） |

### M1.4 暂不引入

- Redis / Celery / 多实例协调
- 管理后台 / 复杂报表
- 后台 orphan sweeper（同 key 重试可自愈）
- AuthN/AuthZ 持久审计（Plan A）
- agent_requests 自动删除/归档
- 限流 / 熔断 / 生产 SLO

### 状态

```text
m1_4_status = completed
m1_4a_status = completed
m1_4b_status = completed
m1_4b_r1_status = completed
m1_4c_status = completed
m1_4d_status = completed
plan_document = docs/phases/m1_4_postgresql_persistence_plan.md
m1_4b_closeout = docs/phases/m1_4_postgresql_persistence_plan.md#20-m14-b-closeout--completed

m1_4b_pre_r1_nodeids = 529
m1_4b_r1_current_nodeids = 530
m1_4b_r1_missing_nodeids = 0
m1_4b_unit_passed = 496
m1_4b_default_passed = 496
m1_4b_default_skipped = 34
m1_4b_r1_postgres_non_destructive = 31 passed, 2 skipped
m1_4b_r1_postgres_destructive = 33 passed, 0 skipped
m1_4b_r1_postgres_ci_job = implemented
m1_4b_r1_postgres_ci_workflow_static_validation = PASS
m1_4b_r1_postgres_ci_runtime_execution = not_run_in_current_environment
m1_4b_policy_boundary = PASS
m1_4b_rag_adapter = PASS
m1_4b_uv_lock_changed = no
m1_4b_uv_lock_sha256 = bdb8986c1832728d1fe3a19a3b30eb61927cc09df30f3ad0d81d20e5a2594fe5

m1_4c_current_nodeids = 573
m1_4c_pre_stage_nodeids = 530
m1_4c_missing_pre_stage_nodeids = 0
m1_4c_unit = 522 passed, 51 deselected
m1_4c_default = 522 passed, 51 skipped
m1_4c_postgres_non_destructive = 48 passed, 2 skipped
m1_4c_postgres_destructive = 50 passed, 0 skipped
m1_4c_repository_runtime_status = component_implemented
m1_4c_uow_runtime_status = component_implemented
m1_4c_durable_service_runtime_status = component_implemented
m1_4c_fastapi_database_wiring = not_implemented
m1_4d_current_nodeids = 613
m1_4d_pre_stage_nodeids = 573
m1_4d_missing_pre_stage_nodeids = 0
m1_4d_unit = 546 passed, 67 deselected
m1_4d_postgres_non_destructive = 64 passed, 2 skipped
m1_4d_postgres_destructive = 66 passed, 0 skipped
m1_4d_persistent_idempotency_component = implemented
m1_4d_scoped_atomic_claim = implemented
m1_4d_claim_fencing = implemented
m1_4d_completed_result_replay = implemented
m1_4d_fastapi_idempotency_wiring = not_implemented
m1_4d_external_side_effect_exactly_once = not_guaranteed
m1_4d_closeout = docs/phases/m1_4d_persistent_idempotency_closeout.md
m1_4e_status = completed
m1_4f_status = completed

phase14_implementation_status = implemented
phase14_incident_closure_status = blocked
phase14_phase_status = blocked
phase14_policy_architecture = candidate_stance_resolver
phase14_coordinator_request_state = isolated
phase14_current_tree_scan = implemented
phase14_tracked_files_scan = blocked_git_unavailable
phase14_git_history_scan = blocked_git_unavailable
phase14_runtime_attestation = protected_job_only
```
# Phase 14-G Repository Baseline

Phase 14-G is the next controlled gate after Phase 14-F preflight hardening. It
creates a one-shot trusted Git root baseline and binds it to a non-authoritative
Discovery run, a protected Formal run, and an independently verified GitHub
artifact. It does not close the DashScope credential incident and does not
change database revision `0001`.
