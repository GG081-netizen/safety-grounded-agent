# 企业采购销售安全编排系统
# Safety-Grounded Enterprise Agent

[![Portfolio CI](https://github.com/GG081-netizen/safety-grounded-agent/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/GG081-netizen/safety-grounded-agent/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB)](https://www.python.org/)
[![PostgreSQL 17](https://img.shields.io/badge/PostgreSQL-17-4169E1)](https://www.postgresql.org/)

## Product

这是一个面向企业采购、企业销售、售前支持和企业知识问答的安全编排系统。它把身份认证、授权、业务安全策略、确定性任务路由、RAG 适配、持久幂等和可追踪执行放在同一条可测试主链上。

系统不是让多个 Agent 自由对话。`Coordinator` 负责确定性编排，Policy 在 Router 和 RAG 之前阻断高风险请求，外部知识服务故障则通过显式 fallback、置信度上限和 warning 呈现。

```text
HTTP / CLI
  -> Authentication + Authorization
  -> Business Safety Policy
  -> IntentRouter + TaskRouter
  -> Coordinator
  -> Domain execution / RagClient
  -> Evidence + Confidence + Trace
```

## Capabilities

- **安全边界**：OIDC-compatible RS256 JWT Bearer、异步 JWKS、确定性 RBAC/ABAC 和敏感响应投影。
- **业务 Policy**：`Normalization -> RiskCandidate -> Stance -> PolicyResolver`，输出 `SAFE / UNCERTAIN / BLOCKED`。
- **确定性编排**：支持企业知识问答、销售分析、周报、邮件和播客脚本任务。
- **RAG 适配**：统一 External、Local 和 Fallback Client，保留 citation、evidence、diagnostics 与 trace。
- **可靠执行**：PostgreSQL Request/Run/Audit、持久幂等、Replay、Lease Reclaim 和 Fencing。
- **HTTP 契约**：`/v1/chat`、`/v1/qa`、`/healthz`、`/readyz`，以及安全的 400/401/403/409/422/500/503 响应。
- **工程门禁**：五项 Portfolio CI、Secret/Source-tree 检查、发布包检查和 PostgreSQL 17 集成测试。

## Architecture

```text
RequestMetadata
  -> Bearer / JWT / JWKS
  -> PrincipalMappingPolicy
  -> AuthorizationService
  -> RequestContext
  -> Idempotency Gateway
  -> PostgreSQL Transaction A
  -> ChatService
  -> Coordinator
       -> Policy hard gate
       -> IntentRouter / TaskRouter
       -> Domain task / RagClient
  -> PostgreSQL Transaction B
  -> ResponseProjector
```

关键边界：

- `RequestContext` 是可信请求身份和 Trace 的唯一来源。
- `Coordinator` 不感知 HTTP、JWT、JWKS、OIDC 或数据库 Session。
- `BLOCKED` 请求不进入 Router、RAG 或业务执行模块。
- 外部 RAG 失败不会伪造来源；fallback 会降低 confidence 并显示 warning。
- 幂等 Lease 与 TTL 使用 PostgreSQL 时间，业务事件时间使用注入的 UTC Clock。

更完整的组件责任和请求流见 [架构设计](docs/project-design.md) 与 [项目 Walkthrough](docs/project-walkthrough.md)。

## Examples

现有 CLI 和 HTTP 入口可用于手工探索：

```bash
convagent qa "笔记本批量采购需要关注哪些供应商与交付风险？"
convagent --json chat "检查这封销售邮件是否包含未经证实的客户承诺"
convagent eval policy-boundary --strict
convagent eval rag-adapter --strict
```

```bash
uv run uvicorn conversation_agent.api.app:app --host 127.0.0.1 --port 8000

curl -X POST http://127.0.0.1:8000/v1/qa \
  -H "Content-Type: application/json" \
  -d '{"text":"比较办公电脑供应商报价时需要核对哪些信息？"}'
```

Phase 15-D 将在后续阶段提供三个固定、零网络的作品集示例；本阶段只保留入口说明，不包含生成脚本或预生成输出。

## Evaluation

项目提供三个独立评测入口：

| 命令 | 评测范围 |
|---|---|
| `convagent eval policy-boundary --strict` | Policy 分类、混合立场、Unicode 对抗输入和 BLOCKED hard gate |
| `convagent eval rag-adapter --strict` | External/Fallback RAG、citation/evidence 和 raw response 边界 |
| `convagent eval production-blockers --scope implementation --strict` | Secret、并发隔离和 Policy 技术修复；不表示事故已关闭 |

默认分支由以下五个稳定 Check 保护：

```text
Portfolio CI / unit-and-contract
Portfolio CI / policy-and-rag-evaluation
Portfolio CI / secret-and-source-tree
Portfolio CI / package-build
Portfolio CI / postgres-integration
```

Phase 15-E 才会生成作品集 Evaluation Snapshot。本 README 不预写该阶段的最终指标。

## Quick Start

```bash
git clone https://github.com/GG081-netizen/safety-grounded-agent.git
cd safety-grounded-agent

uv sync --frozen --extra dev
uv run convagent qa "整理服务器采购需求"
```

运行测试和评测：

```bash
uv run --frozen --extra dev python -m pytest -m unit -q
uv run --frozen --extra dev convagent eval policy-boundary --strict
uv run --frozen --extra dev convagent eval rag-adapter --strict
```

Demo 模式在完全没有 `Authorization` Header 时使用开发身份。Production 必须配置有效 OIDC 和 PostgreSQL；配置示例见 [`.env.example`](.env.example)，真实凭据不得提交到仓库。

## Limitations

- 当前定位是 OIDC-compatible JWT Bearer 资源服务器，不包含完整 OIDC Login、Session、Introspection 或实时 Token 撤销。
- Policy 是确定性安全边界，不声称覆盖所有自然语言变体或替代法务、合规与人工审批。
- Local RAG 是低置信度 fallback；真实知识质量仍取决于外部 Knowledge Engine 和数据治理。
- 尚未实现 Redis/Celery Worker、流式输出、动态 Model Router、Lease heartbeat 或后台 orphan sweeper。
- PostgreSQL 幂等不保证数据库之外的副作用 exactly-once；无 Idempotency-Key 请求不具备客户端级重复抑制。
- 本地 backup/restore drill 不等于生产加密备份、PITR 或 WAL Archive 已部署。
- Phase 14 的供应商凭据事故和权威 Git 历史证明仍为 `blocked`，不得表述为生产事故已经关闭。

## Engineering History

项目已完成 M1.1-M1.4 的编排、HTTP 信任边界、OIDC-compatible 认证授权、PostgreSQL 持久化、幂等和运维恢复工作。Phase 14 完成 Secret 配置保护、日志脱敏、Coordinator 并发隔离和 Policy 热修复，但 Incident Closure 缺少外部可信证明，因此保持 `blocked`。

Phase 14-G 的 Bootstrap 1 与 Bootstrap 2 均在停止门触发后永久失效；Bootstrap 3 被禁止。Bootstrap 2 使用的历史仓库名为 `GG081-netizen/crispy-fortnight-baseline-2`，该仓库后来在不改变 repository ID 和 main commit 的情况下重命名为 [`GG081-netizen/safety-grounded-agent`](https://github.com/GG081-netizen/safety-grounded-agent)。这些名称在历史审计文档和 Fixture 中按原事实保留。

- [架构设计 / Project Design](docs/project-design.md)
- [项目 Walkthrough](docs/project-walkthrough.md)
- [产品化路线 / Productization Roadmap](docs/sme-productization-roadmap.md)
- [部署 Runbook](docs/operations/deployment_runbook.md)
- [M1.4 Final Closeout](docs/phases/m1_4_final_closeout.md)
- [Phase 14 Production Blocker Closeout](docs/phases/phase14_production_blocker_closure_closeout.md)
- [Phase 14-G Final Closeout](docs/phases/phase14_g_final_closeout.md)
