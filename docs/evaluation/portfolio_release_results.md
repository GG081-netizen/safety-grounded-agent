# Phase 15-E Portfolio Evaluation Snapshot（作品集发布评测快照）

## 1. Executive Summary（执行摘要）

本快照记录了 Phase 15-E-R2 在权威 main SHA 上的完整评测结果。

**关键声明：**

- 本快照是固定确定性评测集（fixed deterministic evaluation set）的结果
- 不是生产流量 Benchmark（production traffic benchmark）
- 不是通用安全认证（general security certification）
- 不是 Production Readiness 证明
- 不是 Phase 14 Incident Closure

| 状态项 | 值 |
|---|---|
| Phase 15-E 状态 | `completed` |
| 发布就绪状态 | `in_progress` |
| 剩余门禁 | Phase 15-F（Clean Clone 验证）、Phase 15-G（正式 Portfolio Release） |

## 2. Evidence Identity（证据身份）

| 项目 | 值 |
|---|---|
| 项目名称 | Safety-Grounded Enterprise Agent（企业采购销售安全编排系统） |
| 评测日期 | 2026-07-20 |
| evaluation_subject_sha | `363c328e9f13963066542c37be1e4dfc6e30f67a` |
| evaluation_subject_main_ci_run_id | `29733462264` |
| evaluation_subject_main_ci_event | `push` |
| database_revision（数据库版本号） | `0001` |

**历史运维证据引用（Operational Evidence Reference）：**

| 项目 | 值 |
|---|---|
| operational_evidence_subject_sha | `88a01225a79b4a6b2be26b855e6604ae1ff63505` |
| operational_release_gate_run_id | `29701719900` |
| destructive-postgres | success |
| operational-postgres | success |

**说明：**

- Main CI `29733462264` 直接锚定 `evaluation_subject_sha`（`363c328e`）
- Release Gate `29701719900` 锚定 `88a01225`（E-R1 合并后 SHA）
- 两个 SHA 之间只有 PR #7 的 10 个文档翻译文件，无任何 Runtime、Workflow、Database 变更
- 因此 Operational Gate 结果作为继承的历史运维证据引用，不声称 Release Gate 在 `363c328e` 上重新运行

## 3. Evaluation Scope（评测范围）

| 评测项 | 说明 |
|---|---|
| Policy Boundary | 策略边界确定性评测（18 个固定 case） |
| RAG Adapter | RAG 适配器确定性评测（6 个固定 case） |
| Production Blockers（implementation scope） | 生产阻断项实现范围检查 |
| Deterministic Portfolio Examples | 三个零网络确定性作品集示例 |
| Repository Hygiene | 仓库源码树与已跟踪文件卫生检查 |
| Package Distribution | 包构建与分发内容检查 |
| Portfolio CI | 五项 Required Job 全部通过 |
| PostgreSQL Integration | 非破坏性集成测试（CI 内） |
| Operational Release Gate | 破坏性 + 运维 PostgreSQL 测试（历史引用） |

## 4. Metric Summary（指标摘要）

| 指标 | 值 | 分子/分母 | 数据源 |
|---|---|---|---|
| blocked_request_no_rag_rate | **1.00** | 7/7 | Policy Boundary |
| fallback_activation_accuracy | **1.00** | 1/1 scenario | rag-fallback trace |
| fallback_contract_checks | **1.00** | 7/7 checks | rag-fallback trace |
| citation_coverage | **0.60** | 3/5 | RAG Adapter |
| trace_completeness | **1.00** | 3/3 scenarios | 三个 trace.json |
| policy_category_coverage | **1.00** | 6/6 categories | Policy Boundary |

## 5. Policy Boundary Results（策略边界结果）

### 5.1 执行

```bash
uv run --frozen --extra dev convagent --json eval policy-boundary --strict
```

数据源：`/tmp/phase15_e_r2/policy-boundary.json`

### 5.2 结果

| 指标 | 值 |
|---|---|
| 状态 | **PASS** |
| case_count（用例总数） | 18 |
| blocked_detection_rate | 1.00（7/7） |
| uncertain_detection_rate | 1.00（5/5） |
| safe_pass_rate | 1.00（6/6） |
| blocked_no_rag_call_rate | 1.00（7/7） |
| business_boundary_coverage | 6/6 |

### 5.3 覆盖类别

| 类别（category） | 中文含义 |
|---|---|
| `business_uncertain` | 业务不确定 |
| `legal_financial_final_judgment` | 法律/财务终局判断 |
| `privacy_overreach` | 隐私越权 |
| `sales_misrepresentation` | 销售不实陈述 |
| `sensitive_attribute_inference` | 敏感属性推断 |
| `unsupported_business_claim` | 不支持的业务声明 |

## 6. RAG Adapter Results（RAG 适配器结果）

### 6.1 执行

```bash
uv run --frozen --extra dev convagent --json eval rag-adapter --strict
```

数据源：`/tmp/phase15_e_r2/rag-adapter.json`

### 6.2 结果

| 指标 | 值 |
|---|---|
| 状态 | **PASS** |
| case_count | 6 |
| external_success_rate | 0.33 |
| fallback_rate | 0.17 |
| citation_coverage | 0.60（3/5） |
| blocked_no_rag_call_rate | 1.00（1/1） |
| raw_response_exposure_rate | 0.00（0/6） |

### 6.3 解读

- 6 个固定场景覆盖 External 成功、External 失败→Fallback、External 失败无 Fallback、Policy 阻断等路径
- `citation_coverage = 3/5`：5 个可回答的非阻断 case 中 3 个具有结构化引用
- `raw_response_exposure_rate = 0.00`：原始外部响应从未直接暴露给调用方
- `blocked_no_rag_call_rate = 1.00`：被阻断的 case 不发起 RAG 调用
- Confidence 仅作为固定场景指标，不作为真实生产置信度标定

## 7. Fallback Contract（降级合同）

### 7.1 fallback_activation_accuracy

| 字段 | 值 |
|---|---|
| 值 | **1.00** |
| 分子 | 1（rag-fallback 场景） |
| 分母 | 1（全部预期 fallback 场景） |
| 解释 | 唯一的确定性 Fallback 场景完全满足生命周期合同 |

场景级验证（5 项）：

| 检查项 | 结果 |
|---|---|
| primary_rag_calls = 1 | ✓ |
| external_failure_type = timeout | ✓ |
| fallback_rag_calls = 1 | ✓ |
| fallback_used = true | ✓ |
| result_provider = fallback | ✓ |

### 7.2 fallback_contract_checks

| 字段 | 值 |
|---|---|
| 值 | **1.00** |
| 分子 | 7 |
| 分母 | 7 |

七项粒度检查：

| 检查 | 描述 | 结果 |
|---|---|---|
| A | `primary_rag_calls == 1` | ✓ |
| B | `external_failure_type == "timeout"` | ✓ |
| C | `fallback_rag_calls == 1 and fallback_used == true` | ✓ |
| D | `result_provider == "fallback"` | ✓ |
| E | `confidence_within_fallback_cap == true and confidence <= 0.55` | ✓ |
| F | `citation_sources_all_local == true and no_fake_external_citation == true` | ✓ |
| G | `warning_visible == true` | ✓ |

**重要区分：** `fallback_activation_accuracy = 1/1 scenario`（全或无，场景级），`fallback_contract_checks = 7/7 checks`（细粒度，检查项级）。两者是不同的指标维度。

## 8. Trace Completeness（追踪完整度）

| 指标 | 值 |
|---|---|
| 值 | **1.00** |
| 分子 | 3 |
| 分母 | 3 |
| 解释 | 三个固定作品集场景均满足各自的 Trace Stage Contract |

各场景验证：

| 场景 | 要求 Stage | 禁止 Stage | 额外检查 | 结果 |
|---|---|---|---|---|
| procurement-planning | policy_engine, router, rag_query | — | — | ✓ |
| policy-blocked | policy_engine | router, rag_query, external_rag_query, local_rag_fallback, response_assembly | downstream_task_execution_calls=0, primary_rag_calls=0, fallback_rag_calls=0 | ✓ |
| rag-fallback | policy_engine, router, external_rag_query | — | — | ✓ |

Trace Completeness 只衡量三个固定作品集场景的 Contract 完整度，不表示所有生产路径都已覆盖。

## 9. Deterministic Portfolio Examples（确定性作品集示例）

| 场景 | 目录 | 文件数 | 说明 |
|---|---|---|---|
| Procurement Planning | `examples/procurement-planning/` | 4 | Policy 放行 → QA RAG → 引用与证据 |
| Policy Blocked | `examples/policy-blocked/` | 4 | 隐私请求 → Policy BLOCKED → 下游不执行 |
| RAG Fallback | `examples/rag-fallback/` | 4 | 外部超时 → 本地 Fallback → 置信度上限 |

验证命令：`uv run python scripts/generate_portfolio_examples.py --check`

结果：`All match.`

说明：
- 通过真实 FastAPI ASGI 主链生成
- 使用确定性 Stub（零网络访问）
- `latency_ms = 0` 是 Stub 行为，不是性能 Benchmark

## 10. CI Evidence（CI 证据）

### 10.1 Main Portfolio CI

| 项目 | 值 |
|---|---|
| Run ID | `29733462264` |
| Event | `push` |
| Head SHA | `363c328e9f13963066542c37be1e4dfc6e30f67a` |
| Status | `completed` |
| Conclusion | `success` |

五项 Required Job：

| Job | 结果 |
|---|---|
| unit-and-contract | success |
| policy-and-rag-evaluation | success |
| secret-and-source-tree | success |
| package-build | success |
| postgres-integration | success |

### 10.2 Operational Release Gates（历史引用）

| 项目 | 值 |
|---|---|
| Run ID | `29701719900` |
| Subject SHA | `88a01225a79b4a6b2be26b855e6604ae1ff63505` |
| destructive-postgres | success |
| operational-postgres | success |
| database_revision | `0001` |

## 11. Security and Packaging Evidence（安全与打包证据）

| 检查项 | 命令 | 结果 |
|---|---|---|
| source-tree hygiene | `check_repository_hygiene.py --scope source-tree` | PASS（0 findings） |
| tracked-files hygiene | `check_repository_hygiene.py --scope tracked-files` | PASS（0 findings） |
| Gitleaks（CI） | CI `secret-and-source-tree` job | success（0 findings） |
| package-build | `python -m build` | success |
| distribution inspection | `check_distribution_contents.py` | PASS（0 forbidden members） |
| raw_response 暴露 | RAG Evaluator | 不暴露（rate = 0.00） |

## 12. Limitations and Blocked Boundaries（局限与阻断边界）

### 12.1 Phase 14 状态

```text
phase14_overall_status = blocked
authoritative_phase14_closeout = false
```

Phase 14 供应商凭据事故和权威 Git 历史证明仍未关闭。实现范围修复（Secret 配置保护、日志脱敏、Coordinator 并发隔离、Policy 热修复）已完成并通过 CI 验证，但事故关闭需要提供商侧凭据撤销、轮换、使用审查和受保护 GitHub Workflow 运行。

### 12.2 未覆盖范围

- 真实生产流量（本快照仅为固定确定性评测集）
- 外部 Knowledge Engine 知识质量
- 全语言 Policy 覆盖（当前为中文业务场景）
- 完整 OIDC Login / Session / Token Revocation
- Redis / Celery Worker
- 流式输出
- PITR / WAL Archive 部署
- 生产事故关闭
- 通用安全认证

### 12.3 已知局限

- `fallback_activation_accuracy` 分母为 1，统计意义上不能外推到大规模场景
- 本地 Gitleaks 未安装（`local_gitleaks_status = not_run_tool_unavailable`），CI Gitleaks 通过
- PostgreSQL 运维测试引用 E-R1 的历史 Release Gate Run（`29701719900`），未在 E-R2 SHA 上重新运行
- Confidence 指标来自固定 Stub 场景，不代表真实 LLM 置信度

## 13. Reproduction Commands（复现命令）

```bash
# 第一轮评测
uv run --frozen --extra dev convagent --json eval policy-boundary --strict
uv run --frozen --extra dev convagent --json eval rag-adapter --strict
uv run --frozen --extra dev convagent --json eval production-blockers --scope implementation --strict
uv run --frozen --extra dev python scripts/generate_portfolio_examples.py --check

# 完整性验证
uv lock --check
uv sync --frozen --extra dev
uv run --frozen --extra dev python -m compileall -q src tests scripts
uv run --frozen --extra dev python scripts/check_nodeid_baseline.py
uv run --frozen --extra dev python -m pytest -m unit -q
uv run --frozen --extra dev python -m pytest -m "not postgres_integration and not operational_integration" -q
uv run --frozen --extra dev python scripts/check_repository_hygiene.py --scope source-tree --json
uv run --frozen --extra dev python scripts/check_repository_hygiene.py --scope tracked-files --json
uv run --frozen --extra dev python -m build
uv run --frozen --extra dev python scripts/check_distribution_contents.py
```

## 14. Final State（最终状态）

```text
phase15_e_r2_status = completed
phase15_e_status = completed

phase15_f_status = not_started
phase15_g_status = not_started

portfolio_release_status = in_progress
portfolio_release_version = not_released

phase14_overall_status = blocked
authoritative_phase14_closeout = false
database_revision = 0001
```
