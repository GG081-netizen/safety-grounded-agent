# Safety-Grounded Agent Orchestration Layer

一个面向 B2B IT 设备与企业办公采购售前场景的 Agent 编排项目。它负责安全策略、意图与任务路由、业务执行、外部 RAG 适配、降级控制和 Trace；完整知识检索由独立的 `RAG_demo` Knowledge Engine 承担。

**当前阶段：Phase 14 技术修复和 Phase 14-F 正式关闭链已实现，事故关闭仍 BLOCKED。** M1.4 已完成持久化、持久幂等和运维恢复边界。Phase 14 又完成 SecretStr（敏感配置类型）、日志脱敏、工作树/发布包检查、Coordinator 并发隔离，以及 Candidate + Stance + Resolver Policy 架构。Phase 14-F 增加 provenance-bound Evidence Envelope（来源绑定证据包络）、Protected incident candidate（受保护事故候选证据）和 downstream formal closeout（下游权威关闭）。供应商凭据撤销/轮换、真实 Git 全历史及 fresh GitHub.com protected run 尚无可信证明，因此不能将 Phase 14 标记为完全关闭。

## What It Is

```text
test_demo = Policy / Routing / Coordinator / Workflow / Trace / RAG Adapter / Evaluation
RAG_demo  = Retrieval / Rerank / Grounded QA / Citation / Answerability
```

项目支持企业知识问答、销售分析、周报、邮件和播客脚本。主链路由确定性 `Coordinator` 编排，不是多个 LLM Agent 自由对话。

## Business Scenario

企业采购售前同时涉及客户资料、采购知识、长期销售动作和高风险业务承诺。系统需要在回答问题或执行任务前阻止隐私越权、敏感属性推断、虚假承诺、编造事实和高风险专业判断，并对外部知识服务故障进行可解释降级。

## Why This Architecture

- **Policy-first**：高风险请求在 Router、RAG 和业务模块之前停止。
- **双 Router**：IntentRouter 理解“想做什么”，TaskRouter 决定“如何执行”。
- **确定性 Coordinator**：主链路可预测、可测试、可追踪。
- **RAG 解耦**：Agent 层不重复实现完整 RAG，通过统一 `RagClient` 使用外部知识引擎。
- **显式降级**：fallback 降低 confidence，并记录 warning、diagnostics 和 trace。

## Architecture

```text
User / CLI / HTTP API
    |
    v
Business Safety Policy
    |-- BLOCKED ------> Rejection + policy-only trace
    |
    `-- SAFE / UNCERTAIN
            |
            v
      IntentRouter -> TaskRouter
            |
            v
        Coordinator
       /     |      \
   Sales   Writer   RagClient
                     |
             External RAG_demo
                     |
              failure + enabled
                     v
             Local keyword fallback
                     |
                     v
Response + Evidence + Confidence + Trace
```

## M1.4 Request Chain

```text
RequestMetadata (request_id, trace_id, received_at)
 → BearerTokenParser (单 Bearer 凭证提取，否则 demo placeholder)
 → JWTVerifier (JOSE Header Policy → JWKS → 签名验证 → Claims 验证)
 → VerifiedClaims (不可变签名后声明)
 → PrincipalMappingPolicy (VerifiedClaims → Principal)
 → AuthorizationService (Principal + Route permissions → AuthorizationDecision)
 → RequestContextBuilder (Metadata + Principal + Authorization → RequestContext)
 → IdempotencyKeyParser (原始 ASGI Header → 严格幂等键)
 → RequestExecutionGateway (无键持久执行或有键幂等执行)
 → PostgreSQL Transaction A (请求接收与 Claim 提交)
 → ChatService (RequestContext + UserRequest → Coordinator，事务外执行)
 → Coordinator (Policy → Routing → Task Execution → OrchestrationResult)
 → PostgreSQL Transaction B (Run、Audit 与终态提交)
 → ResponseProjector (ApplicationResult → PublicAgentResponse)
```

Authentication ≠ Authorization ≠ Policy：
- **Authentication**：验证 Bearer Token 签名和 Claims → `VerifiedClaims`
- **Authorization**：Principal roles → permissions 并集，与 Route 权限比较 → `AuthorizationDecision`
- **Policy**（Coordinator 内部）：业务安全规则 BLOCKED/UNCERTAIN/SAFE，后于 AuthN/AuthZ

## Key Features

- 集中化、类别化的 Business Safety Rule Table。
- SAFE、UNCERTAIN、BLOCKED 三态策略和 optional classifier fallback。
- QA、sales analysis、weekly report、podcast script、email draft 五类任务。
- External/Local/Fallback RAG clients 和结构化错误分类。
- Citation、evidence、confidence、warnings、diagnostics 和 AgentStep trace。
- 默认隐藏外部 RAG `raw_response`。
- 两套 deterministic evaluation 和 CLI/JSON 输出。
- FastAPI `/v1/chat`、`/v1/qa`、`/healthz`（存活探针）与 `/readyz`（就绪探针），统一错误响应 400/401/403/409/422/500/503。
- PostgreSQL 模式下的 durable request（持久请求）、Run（运行记录）、Audit（审计事件）和 HTTP Replay（结果重放）。
- 原始 ASGI Header 重复检测、`optional`（可选）/`required`（必需）模式与 `Idempotency-Status`（幂等结果状态）响应头。
- Production fail-closed（生产故障关闭）：启动时验证连接和 Alembic revision（迁移版本），运行期故障不降级到非持久执行。
- **OIDC-compatible JWT Bearer 认证**（RS256）：JOSE Header Policy、JWKS（Static/Remote async）、single-flight、negative-kid cache、transactional refresh。
- **确定性 RBAC/ABAC 授权**：角色权限并集、route-union 策略、Principal 禁用拒绝。
- **Response Projection**：`raw_response` 通过 config + permission 双重 Gate。
- 服务端生成 RequestContext，HTTP 请求体不能提交 tenant、role 或 permission 等可信字段。
- Coordinator 纯业务编排：不感知 HTTP、JWT、JWKS、OIDC 或权限策略。

## M1.4 HTTP API

当前 API 已接入 JWT Bearer 认证和确定性授权。

启动：

```bash
uvicorn conversation_agent.api.app:app --host 127.0.0.1 --port 8000
```

调用 Chat 或强制 QA（demo 模式无 Authorization Header 使用开发占位身份）：

```bash
curl -X POST http://127.0.0.1:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"text":"生成本周销售周报"}'

curl -X POST http://127.0.0.1:8000/v1/qa \
  -H "Content-Type: application/json" \
  -d '{"text":"笔记本批量采购需要注意什么？"}'
```

Bearer 认证调用：

```bash
curl -X POST http://127.0.0.1:8000/v1/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"text":"query"}'
```

HTTP 语义：
- 400：Malformed Bearer Header
- 401：无效 Token（签名失败、Claims 违规、组织不匹配）
- 403：有效身份但权限不足
- 503：JWKS 不可用
- 409：幂等冲突、正在执行、历史执行失败或执行权丢失
- 200 + Policy BLOCKED：已认证已授权，但业务安全策略拒绝内容

PostgreSQL 模式下，首次成功的有键请求返回 `Idempotency-Status: executed`（已执行），同键同 DTO（数据传输对象）重试返回 `Idempotency-Status: replayed`（已重放）。该响应头只出现在成功 2xx 响应；错误响应不携带它。

`X-Request-ID` 和 `X-Trace-ID` 由服务端生成并写入响应头与响应体。`/v1/qa` 在服务端强制 QA 路由。客户端不能注入 tenant、role、permission 等可信字段。Demo 模式只在完全没有 Authorization Header 时才使用开发占位身份。

详细请求场景见 [Walkthrough](docs/project-walkthrough.md)，架构边界见 [Design](docs/project-design.md)。

## DashScope LLM Runtime

`RealAgent` 默认通过 `DashScopeClient` 使用 `standard/qwen3-8b`，请求固定为非流式并关闭 Thinking。模型注册表同时保留 `advanced/qwen3-14b` 和 `evaluator/qwen3-14b` 配置，但它们当前不可由运行时选择；`lightweight` 尚未配置。动态 `ModelRouter` 仍是未来能力，不参与 Coordinator 主链路。

无凭据时，旧 Demo 入口可明确降级为 `MockAgent`；test/production 模式会 fail-fast。单元测试使用可注入 Fake Client 和 `httpx.MockTransport`，默认禁止网络访问。

## Business Safety Firewall

当前规则覆盖：

- 隐私越权和敏感属性推断。
- 法律、金融和合同绝对定性等高风险专业判断。
- 100% 交付、保证中标等虚假销售承诺。
- 编造案例、库存或 SLA 等无依据业务事实。
- 需要法务、合规或交付确认的 UNCERTAIN 场景。

Phase 14 将规则升级为 `Normalization -> RiskCandidate -> Stance -> PolicyResolver`。业务动作、语言立场模式和决策阈值分别版本化；每个 occurrence 独立判断，`UNKNOWN` 与 classifier 故障均 fail-closed 为 `UNCERTAIN`。弃用的 `negative_patterns` 仅为构造兼容保留，Engine 不再读取。

## External RAG Integration

`Coordinator` 只依赖统一接口：

```python
rag_client.query(question, trace_id=request_metadata.trace_id, metadata={...})
```

Provider 含义：

| Provider | 含义 |
|---|---|
| `external` | 外部 `RAG_demo /query` 成功 |
| `local` | 配置为直接使用本地关键词 RAG |
| `fallback` | 外部失败后使用本地关键词 RAG |
| `none` | 外部失败且未启用 fallback |

Fallback 执行 `min(local_confidence, 0.55)`，加入明确 warning，并记录 external failed 与 local succeeded 两条 diagnostics。0.55 是工程降级上限，不是统计概率。

## Evaluation

| Command | 评估对象 | 不评估什么 |
|---|---|---|
| `convagent eval rag-adapter` | 外部调用、fallback、citation/evidence contract、安全硬门、raw output control | 真实 RAG_demo 召回和答案质量 |
| `convagent eval policy-boundary` | 固定业务安全分类、blocked hard gate、风险类别覆盖 | 所有语义变体和线上误报漏报 |

核心安全 Gate：BLOCKED 请求不得调用 RAG；默认公共输出不得暴露 `raw_response`。

## Quick Demo

安装：

```bash
python3 -m pip install -e .
```

运行：

```bash
convagent qa "笔记本批量采购需要注意什么？"

convagent --json chat "帮我查一下客户的私人住址和健康状况"

convagent eval policy-boundary

convagent eval rag-adapter

convagent --json eval policy-boundary

convagent --json eval rag-adapter
```

默认外部 RAG 地址为 `http://127.0.0.1:8001`。常用环境变量：

```text
CONVAGENT_RAG_PROVIDER
CONVAGENT_RAG_BASE_URL
CONVAGENT_RAG_TIMEOUT_SECONDS
CONVAGENT_RAG_FALLBACK_TO_LOCAL
CONVAGENT_RAG_INCLUDE_RAW_RESPONSE
```

## Test Result

```text
Final validation:
- python3 -m compileall -q src tests scripts: passed
- uv run --frozen --extra dev python -m pytest --collect-only -q: 663 collected
- uv run --frozen --extra dev python -m pytest -m unit -q: 582 passed, 81 deselected
- default full suite: 582 passed, 81 skipped
- main suite with real PostgreSQL: 656 passed, 1 skipped, 6 operational deselected
- PostgreSQL 17 non-destructive contract: 72 passed, 2 skipped
- PostgreSQL 17 destructive persistence contract: 74 passed, 0 skipped
- PostgreSQL 17 operational contract: 6 passed, 0 skipped
- logical backup / fresh restore drill: PASS
- convagent eval policy-boundary: Status PASS
- convagent eval rag-adapter: Status PASS
- M1.1 legacy Node IDs missing: 0
- M1.4-B pre-R1 Node IDs missing: 0
- M1.4-B-R1 pre-C Node IDs missing: 0
- M1.4-C pre-D Node IDs missing: 0
- M1.4-D pre-E Node IDs missing: 0
- M1.4-E pre-F Node IDs missing: 0
```

这些结果证明当前自动化回归、本地 PostgreSQL 17 Schema Contract（结构契约）和 M1.4 运维恢复门已通过。GitHub Actions Job（远程持续集成任务）仅完成实现与静态验证，本环境没有真实运行；本地逻辑备份演练也不等于生产备份基础设施已部署。

## Project Boundary

当前已交付 M1.1、M1.2、M1.3 和完整 M1.4。M1.4-F 在既有持久 HTTP 主链上验证了进程崩溃 Reclaim（回收）、共享 PostgreSQL Claim（声明）协调、数据库中断恢复、连接池 Soak（稳定性运行）、最小权限角色、终态清理以及逻辑备份恢复。

**当前尚未实现：**
- 自动迁移、lease heartbeat（租约心跳续期）和后台 orphan sweeper（孤儿记录清理器）
- Redis、Celery 与外部 CRM/邮件副作用 exactly-once（严格一次执行）保证
- 外部副作用 exactly-once（严格一次执行）、Claim heartbeat（执行权心跳）和 orphan sweeper（孤儿记录清理器）
- 动态 ModelRouter 和模型升级
- 流式输出
- 完整 OIDC 登录、真实企业 IdP 联调
- 实时 Token 撤销、Token Introspection
- Redis / Celery / 分布式工作进程取消
- 管理后台 / 复杂报表
- 限流 / 高并发治理 / 生产 SLO

本地 JSON Store 不适合高并发事务。LocalKeywordRagClient 是低置信度 fallback。Policy rule-first 不能覆盖所有语义改写。RealAgent 与 LLM clients 存在，但不是 Coordinator 主执行入口。

## Further Reading

- [架构设计理由](docs/project-design.md) — 长期架构边界、组件责任与不变量
- [项目深度理解与面试复盘手册](docs/project-walkthrough.md) — 请求场景与实际流转
- [中小企业内部部署产品化路线](docs/sme-productization-roadmap.md) — 阶段状态与 M1.4 路线
- [M1.4 PostgreSQL 计划与 Closeout](docs/phases/m1_4_postgresql_persistence_plan.md) — Schema、迁移、R1、M1.4-C 与 M1.4-D 验收证据
- [M1.4-C Closeout](docs/phases/m1_4c_execution_persistence_closeout.md) — 执行持久化组件、事务边界和真实 PostgreSQL 证据
- [M1.4-D Closeout](docs/phases/m1_4d_persistent_idempotency_closeout.md) — 持久幂等、重放、执行权隔离和并发证据
- [M1.4-F Closeout](docs/phases/m1_4f_operational_readiness_closeout.md) — 运维恢复、最小权限、清理和备份恢复证据
- [M1.4 Final Closeout](docs/phases/m1_4_final_closeout.md) — PostgreSQL 持久化里程碑总收尾
- [M1.3 Closeout](docs/phases/m1_3_oidc_jwt_authorization_closeout.md) — 阶段验收证据与限制
