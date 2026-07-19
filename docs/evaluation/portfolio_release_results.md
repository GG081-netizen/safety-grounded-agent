# Portfolio Release Evaluation Snapshot（作品集发布评测快照）

## 1. Snapshot Identity（快照身份）

| 项目 | 值 |
|---|---|
| 项目名称 | Safety-Grounded Enterprise Agent（企业采购销售安全编排系统） |
| 评测日期 | 2026-07-19 |
| 评测对象 SHA（evaluation_subject_sha） | `ed70fb598eb89e31f3d309848a0d59ced050b768` |
| 快照文档 commit SHA（snapshot_document_commit_sha） | 待最终 commit 记录 |
| 分支 | `phase15/evaluation-snapshot` |
| 数据库 revision（版本号） | `0001` |
| 评测范围 | 发布前完整评测快照（非生产认证） |

## 2. Executive Verdict（执行摘要）

| 状态项 | 值 |
|---|---|
| Phase 15-E 状态 | `completed` |
| 发布就绪状态 | `in_progress` |
| 剩余门禁 | Phase 15-F（Clean Clone 验证）、Phase 15-G（正式 Portfolio Release v0.1.0） |

## 3. Test Summary（测试摘要）

### 3.1 编译检查

| 项目 | 结果 |
|---|---|
| 命令 | `uv run --frozen --extra dev python -m compileall -q src tests scripts` |
| 状态 | PASS |

### 3.2 Node ID Baseline（节点标识基线）

| 项目 | 值 |
|---|---|
| 当前 node ID 数 | 896 |
| 缺失的 M1.1 基线 node ID | 0 |
| 缺失的 Phase 14 基线 node ID | 0 |
| 状态 | PASS |

### 3.3 Unit Suite（单元测试套件）

| 项目 | 值 |
|---|---|
| 命令 | `uv run --frozen --extra dev python -m pytest -m unit -q --durations=20` |
| collected（收集数） | 897 |
| passed（通过数） | 816 |
| failed（失败数） | 0 |
| deselected（取消选择数） | 81 |
| duration（耗时） | 15.48s |
| 状态 | PASS |

### 3.4 Full Non-PostgreSQL Suite（完整非 PostgreSQL 测试套件）

| 项目 | 值 |
|---|---|
| 命令 | `uv run --frozen --extra dev python -m pytest -m "not postgres_integration and not operational_integration" -q` |
| collected（收集数） | 897 |
| passed（通过数） | 816 |
| failed（失败数） | 0 |
| skipped（跳过数） | 1 |
| deselected（取消选择数） | 80 |
| duration（耗时） | 16.48s |
| 状态 | PASS |

### 3.5 Portfolio Example Suite（作品集示例测试套件）

| 项目 | 值 |
|---|---|
| 命令 | `uv run --frozen --extra dev pytest tests/test_portfolio_examples.py -v` |
| collected（收集数） | 28 |
| passed（通过数） | 28 |
| failed（失败数） | 0 |
| duration（耗时） | 5.57s |
| 状态 | PASS |

### 3.6 Portfolio Generator --check（作品集生成器校验）

| 项目 | 值 |
|---|---|
| 命令 | `uv run --frozen --extra dev python scripts/generate_portfolio_examples.py --check` |
| 检查文件数 | 12 |
| 结果 | All match（全部匹配） |
| 状态 | PASS |

## 4. Policy Boundary Evaluation（策略边界评测）

### 4.1 执行

| 项目 | 值 |
|---|---|
| 命令 | `uv run --frozen --extra dev convagent --json eval policy-boundary --strict` |
| 数据源 | `/tmp/phase15_e_evidence/policy-boundary.json` |

### 4.2 结果

| 指标 | 值 |
|---|---|
| 状态 | **PASS** |
| 用例总数（case_count） | 18 |
| 被阻断检测率（blocked_detection_rate） | 1.00 |
| 不确定检测率（uncertain_detection_rate） | 1.00 |
| 安全通过率（safe_pass_rate） | 1.00 |
| 被阻断请求零 RAG 调用率（blocked_no_rag_call_rate） | 1.00 |
| 业务边界覆盖数（business_boundary_coverage） | 6 |

### 4.3 覆盖类别

| 类别 | 中文含义 |
|---|---|
| `business_uncertain` | 业务不确定 |
| `legal_financial_final_judgment` | 法律/财务终局判断 |
| `privacy_overreach` | 隐私越权 |
| `sales_misrepresentation` | 销售不实陈述 |
| `sensitive_attribute_inference` | 敏感属性推断 |
| `unsupported_business_claim` | 不支持的业务声明 |

## 5. RAG Adapter Evaluation（RAG 适配器评测）

### 5.1 执行

| 项目 | 值 |
|---|---|
| 命令 | `uv run --frozen --extra dev convagent --json eval rag-adapter --strict` |
| 数据源 | `/tmp/phase15_e_evidence/rag-adapter.json` |

### 5.2 结果

| 指标 | 值 |
|---|---|
| 状态 | **PASS** |
| 用例总数（case_count） | 6 |
| 外部成功率（external_success_rate） | 0.33 |
| 降级率（fallback_rate） | 0.17 |
| 引用覆盖率（citation_coverage） | 0.60 |
| 被阻断请求零 RAG 调用率（blocked_no_rag_call_rate） | 1.00 |
| 原始响应暴露率（raw_response_exposure_rate） | 0.00 |

## 6. Required Portfolio Metrics（五项强制作品集指标）

### 6.1 blocked_request_no_rag_rate（被阻断请求零 RAG 调用率）

| 字段 | 值 |
|---|---|
| 值（Value） | **1.00** |
| 分子（Numerator） | 7 |
| 分母（Denominator） | 7 |
| 公式（Formula） | 被 Policy 判定为 BLOCKED 且 RAG 调用次数为 0 的 case 数 / 全部 BLOCKED case 数 |
| 数据源（Source） | Policy Boundary JSON（`/tmp/phase15_e_evidence/policy-boundary.json`） |
| 阈值（Threshold） | 1.0 |
| 状态（Status） | **PASS** |
| 交叉验证 | RAG Adapter JSON 中 `blocked_no_rag_call_rate` = 1.00（一致） |

### 6.2 fallback_activation_accuracy（降级激活准确率）

| 字段 | 值 |
|---|---|
| 值（Value） | **1.00** |
| 分子（Numerator） | 1（`examples/rag-fallback/trace.json` 全部 7 项合同满足） |
| 分母（Denominator） | 1（全部预期 fallback case） |
| 公式（Formula） | 完整满足 fallback 生命周期合同的预期 fallback cases / 全部预期 fallback cases |
| 数据源（Source） | `examples/rag-fallback/trace.json` |
| 阈值（Threshold） | 1.0 |
| 状态（Status） | **PASS** |

Fallback 生命周期合同验证项：
- `primary_rag_calls = 1` ✓
- `external_failure_type = timeout` ✓
- `fallback_rag_calls = 1` ✓
- `fallback_used = true` ✓
- `result_provider = fallback` ✓
- `confidence <= 0.55`（实际 0.55）✓
- `warning_visible = true` ✓

### 6.3 citation_coverage（引用覆盖率）

| 字段 | 值 |
|---|---|
| 值（Value） | **0.60** |
| 分子（Numerator） | 3 |
| 分母（Denominator） | 5 |
| 公式（Formula） | 具有结构化 citation/evidence 的可回答非 blocked case 数 / 全部需要证据的可回答非 blocked case 数 |
| 数据源（Source） | RAG Adapter JSON（`/tmp/phase15_e_evidence/rag-adapter.json`） |
| 阈值（Threshold） | ≥ 0.5 |
| 状态（Status） | **PASS** |

### 6.4 trace_completeness（追踪完整度）

| 字段 | 值 |
|---|---|
| 值（Value） | **1.00** |
| 分子（Numerator） | 3 |
| 分母（Denominator） | 3 |
| 公式（Formula） | 满足自身 Trace 合同的场景数 / 全部评测场景数 |
| 数据源（Source） | `examples/procurement-planning/trace.json`、`examples/policy-blocked/trace.json`、`examples/rag-fallback/trace.json` |
| 阈值（Threshold） | 1.0 |
| 状态（Status） | **PASS** |

各场景验证详情：

| 场景 | 验证项 | 结果 |
|---|---|---|
| Procurement（采购规划） | `effective_task = qa`、`task_override_applied = true`、`task_override_source = endpoint:/v1/qa`、`real_intent_result` 存在 | ✓ |
| Blocked（策略阻断） | `normalized_stages` 精确含 `policy`、`runtime_steps` 精确含 `policy_engine`、所有下游调用计数为 0 | ✓ |
| Fallback（降级） | `policy → router → external_rag_attempt → fallback_activation → fallback_result → response_assembly` | ✓ |
| Fallback 诊断 | External diagnostic: `success=false`、`error_type=timeout`；Local diagnostic: `success=true` | ✓ |

### 6.5 policy_category_coverage（策略类别覆盖率）

| 字段 | 值 |
|---|---|
| 值（Value） | **1.00** |
| 分子（Numerator） | 6 |
| 分母（Denominator） | 6（Evaluator 定义的固定类别数） |
| 公式（Formula） | 已被评测覆盖的业务安全风险类别数 / 策略要求覆盖的风险类别总数 |
| 数据源（Source） | Policy Boundary JSON（`/tmp/phase15_e_evidence/policy-boundary.json`） |
| 阈值（Threshold） | 1.0 |
| 状态（Status） | **PASS** |

## 7. Production Blockers（生产阻断项）

| 指标 | 值 |
|---|---|
| 命令 | `uv run --frozen --extra dev convagent --json eval production-blockers --scope implementation --strict` |
| `implementation_status` | `pass` |
| `authoritative` | `false` |
| 数据源 | `/tmp/phase15_e_evidence/production-blockers.json` |

### 7.1 关键声明

```text
production_blockers_implementation_status = pass

authoritative_phase14_closeout = false
phase14_overall_status = blocked
phase14_g_status = closed_blocked
phase14_incident_closure_status = blocked
phase14_authoritative_phase_status = blocked
```

**重要区分**：实现范围（implementation scope）的通过仅表示代码层面的阻断项已修复。它不代表 Phase 14 事故已权威关闭。Phase 14 事故关闭仍需提供商侧凭据撤销、轮转、使用审查和受保护 GitHub Workflow 运行。

## 8. PostgreSQL Verification（PostgreSQL 验证）

| 类型 | 来源 | 结果 |
|---|---|---|
| Non-destructive（非破坏性） | Main Push Portfolio CI Run `29688877225` / `postgres-integration` Job | **success** |
| Destructive（破坏性） | workflow_dispatch Run `29691740253` / `destructive-postgres` Job | **success** |
| Operational（运维） | workflow_dispatch Run `29691740253` / `operational-postgres` Job | **success** |
| 数据库 revision（版本号） | 以上全部 | **0001** |

## 9. Security and Distribution（安全与分发）

### 9.1 Gitleaks（密钥泄露扫描）

| 类型 | 来源 | 结果 |
|---|---|---|
| 本地 | 工具不可用 | `not_run_tool_unavailable` |
| CI | Portfolio CI Run `29688877225` / `secret-and-source-tree` Job | **success**（findings = 0） |

### 9.2 Source Tree Hygiene（源码树卫生）

| 项目 | 值 |
|---|---|
| 命令 | `uv run --frozen --extra dev python scripts/check_repository_hygiene.py --scope source-tree --json` |
| 数据源 | `/tmp/phase15_e_evidence/source-tree.json` |
| 文件数 | 339 |
| 发现数（findings） | 0 |
| 状态 | **PASS** |

### 9.3 Tracked Files（已跟踪文件）

| 项目 | 值 |
|---|---|
| 命令 | `uv run --frozen --extra dev python scripts/check_repository_hygiene.py --scope tracked-files --json` |
| 数据源 | `/tmp/phase15_e_evidence/tracked-files.json` |
| 状态 | **PASS** |

### 9.4 Distribution（分发构建）

| 项目 | 值 |
|---|---|
| 归档文件数 | 2（`.tar.gz`、`.whl`） |
| 版本 | `0.1.0` |
| 禁止成员数（forbidden_members） | 0 |
| 状态 | **PASS** |

## 10. Portfolio Demo Verification（作品集演示验证）

| 场景 | 目录 | 文件数 | 状态 |
|---|---|---|---|
| Procurement Planning（采购规划） | `examples/procurement-planning/` | 4 | 存在 |
| Policy Blocked（策略阻断） | `examples/policy-blocked/` | 4 | 存在 |
| RAG Fallback（降级） | `examples/rag-fallback/` | 4 | 存在 |

## 11. Evidence Provenance（证据来源）

### 11.1 命令

所有评测命令详见[Phase 15-E 执行计划](../../.claude/plans/home-dick-project-test-demo-1-logical-twilight.md)（第五步至第十四步）。

### 11.2 CI Run ID

| 用途 | Run ID | Event | 结果 |
|---|---|---|---|
| Main Push Portfolio CI（非破坏性 PostgreSQL） | `29688877225` | `push` | 5/5 success |
| Portfolio Release Gates（破坏性 + 运维 PostgreSQL） | `29691740253` | `workflow_dispatch` | 2/2 success |

### 11.3 评测对象 SHA

```text
evaluation_subject_sha = ed70fb598eb89e31f3d309848a0d59ced050b768
```

## 12. Known Limitations（已知局限）

- Phase 14 事故仍未关闭（`phase14_overall_status = blocked`）
- Clean Clone 验证尚未执行（Phase 15-F）
- v0.1.0 尚未发布（Phase 15-G）
- 本地 Gitleaks 未安装（`local_gitleaks_status = not_run_tool_unavailable`）
- 本快照不是生产认证，仅用于作品集评审
- `fallback_activation_accuracy` 分母为 1，统计意义上不能外推到大规模场景

## 13. Final State（最终状态）

```text
phase15_d_status = completed
phase15_e_status = completed

phase15_f_status = not_started
phase15_g_status = not_started

portfolio_release_status = in_progress
clean_clone_status = pending
portfolio_release_version = not_released
resume_ready = false

database_revision = 0001
```
