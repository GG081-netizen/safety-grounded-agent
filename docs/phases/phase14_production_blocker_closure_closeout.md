# Phase 14 生产阻断项关闭收尾

## 状态

```text
implementation_status = pass（实现状态：通过）
incident_closure_status = blocked（事故关闭状态：阻塞）
phase_status = blocked（阶段状态：阻塞）
preflight_hardening = pass（预检加固：通过）
dashscope_evidence = pending_authorized_review（DashScope 证据：待授权审查）
trusted_git_metadata = not_ready（可信 Git 元数据：未就绪）
protected_workflow = not_run（受保护工作流：未运行）
blocking_reasons = dashscope_credential_revocation_unverified（DashScope 凭据撤销未验证）,
                   dashscope_credential_rotation_unverified（DashScope 凭据轮转未验证）,
                   git_history_unavailable（Git 历史不可用）
database_revision = 0001（数据库版本号）
```

实现范围已在本地完成。事故范围未完成：此工作区无可用的 Git 元数据，且无受保护提供商证明（Attestation）可用。本文件不声称已完成凭据撤销、轮转、提供商使用审查、已跟踪文件扫描、Git 历史扫描或真实的 GitHub Actions 运行。

## 已实现

- Pydantic `SecretStr`（密钥字符串）保护 LLM（大语言模型）凭据和数据库 URL；明文仅在 Provider（提供商）、HTTP（超文本传输协议）和 SQLAlchemy（Python SQL 工具包）组合边界处解包。
- 递归脱敏覆盖消息、异常和嵌套日志附加字段，带有循环和深度保护。
- 仓库源码树（Source-Tree）、经批准本地密钥存储（Secret Store）、忽略敏感文件（Ignored Sensitive File）、已跟踪文件（Tracked-File）和 Git 历史（Git-History）状态相互独立。构建归档文件有独立的内容门禁。
- Gitleaks `v8.30.1` 在 CI（持续集成）中通过校验和锁定版本。内置的和项目自定义的 DashScope Canary（金丝雀令牌）必须在扫描真实引用之前都被检测到。
- 受保护的 `incident-closure` Job 创建仅运行时可用的证明，绑定到 `github.sha` 和受保护引用。
- Phase 14-F 将所有 Producer 报告包装在带来源绑定的 `EvidenceEnvelope`（证据包络）对象中，附带 Workflow Job 和 Check Run 身份。当前 Job 仅在运行时绑定自身，从不自我声明已完成。
- 独立的 `formal-closeout` Job 重新验证所有五个已完成前置 Job、报告哈希、Runtime Attestation V2（运行时证明 V2）、Workflow 定义和数据库 Revision。其恰好三个文件的 Artifact 必须成功上传；独立在线验真器随后证明已完成 Run 及其全部六个 Job。
- 正式收尾仅接受 GitHub.com 上全新的 `workflow_dispatch` 运行，且 `run_attempt == 1`；重跑（Rerun）和 GitHub Enterprise Server（GitHub 企业服务器）以故障即关闭（Fail-Closed）方式处理。
- `Coordinator`（协调器）没有请求级的 `_current_*` 状态。请求、追踪、会话和策略数据显式传播；RAG（检索增强生成）接收实际的 Trace ID（追踪标识）。
- Policy（安全策略）使用带版本号的风险规则、立场模式和解决阈值。候选标识（Candidate ID）跨进程确定性地生成，并包含规范化输入指纹。
- 分类器（Classifier）故障和畸形结果返回 `UNCERTAIN`（不确定）；确定性的 BLOCKED（已阻断）结果不可被覆盖。
- 企业 Fixture（测试夹具）包含 243 个用例。Phase 14 不包含采购、销售、售前和企业知识支持之外的行业特定规则。

## 证据

本地实现验证完成，测量结果如下：

```text
phase14_prechange_nodeids = 663（Phase 14 变更前节点标识数）
current_nodeids = 821（当前节点标识数）
missing_phase14_nodeids = 0（缺失的 Phase 14 节点标识数）
phase14_nodeid_renames = 2（Phase 14 节点标识重命名数）
unit_tests = 740 passed, 81 deselected, 9.22 seconds（单元测试）
full_tests = 740 passed, 81 skipped, 8.78 seconds（完整测试）
policy_fixture_cases = 243（策略测试夹具用例数）
risk_candidate_recall = 1.0（风险候选召回率）
request_stance_recall = 1.0（请求立场召回率）
prohibit_stance_precision = 1.0（禁止立场精确率）
audit_stance_precision = 1.0（审计立场精确率）
unknown_fail_closed_rate = 1.0（未知故障即关闭率）
multi_candidate_resolution_accuracy = 1.0（多候选解决方案准确率）
adversarial_bypass_count = 0（对抗性绕过次数）
unicode_bypass_count = 0（Unicode 绕过次数）
business_false_positive_count = 0（业务误报次数）
classifier_failure_safe_count = 0（分类器故障安全次数）
concurrency_rounds = 100（并发轮次）
request_mismatch_count = 0（请求不匹配次数）
trace_mismatch_count = 0（追踪不匹配次数）
session_mismatch_count = 0（会话不匹配次数）
policy_mismatch_count = 0（策略不匹配次数）
future_timeout_count = 0（Future 超时次数）
deadlock_count = 0（死锁次数）
unfinished_future_count = 0（未完成 Future 次数）
blocked_rag_call_count = 0（阻断的 RAG 调用次数）
policy_boundary_strict = PASS（策略边界严格模式：通过）
rag_adapter_strict = PASS（RAG 适配器严格模式：通过）
production_blockers_implementation_strict = PASS（生产阻断项实现范围严格模式：通过）
production_blockers_phase_strict = BLOCKED, logical exit code 3（生产阻断项阶段范围严格模式：阻塞，逻辑退出码 3）
source_tree_scan_status = pass（源码树扫描状态：通过）
source_tree_files_scanned = 284（已扫描源码树文件数）
source_tree_secret_count = 0（源码树密钥数量）
approved_local_secret_store_status = pass（经批准本地密钥存储状态：通过）
ignored_sensitive_files_status = pass（忽略敏感文件状态：通过）
tracked_files_scan_status = blocked（已跟踪文件扫描状态：阻塞）
git_history_scan_status = blocked（Git 历史扫描状态：阻塞）
distribution_archives = 2（分发归档文件数）
distribution_forbidden_members = 0（分发禁止成员数）
postgres_non_destructive = 72 passed, 2 skipped（PostgreSQL 非破坏性测试）
postgres_destructive = 74 passed（PostgreSQL 破坏性测试）
operational_integration = 6 passed（运维集成测试）
database_revision = 0001 (head)（数据库版本号：0001 最新）
workflow_yaml_parse = PASS（工作流 YAML 解析：通过）
workflow_required_job_structure = PASS（工作流必需 Job 结构：通过）
github_actions_runtime_execution = not_run_in_current_environment（GitHub Actions 运行时执行：当前环境未运行）
uv_lock_sha256 = a6a0e339868fe2d44d05b269ad4f92f64b3fe955186e87ed8b0aff0fc363342d（uv 锁定文件校验和）
uv_lock_changed_during_final_validation = false（最终验证期间 uv 锁定文件未变更）
phase14_f_contract_tests = 49 passed（Phase 14-F 契约测试）
formal_closeout_workflow_runtime = not_run_in_current_environment（正式收尾工作流运行时：当前环境未运行）
authoritative_phase_resolution = unavailable（权威阶段决议：不可用）
```

PostgreSQL（关系型数据库）和运维检查使用了操作员提供的现有测试环境。没有 PostgreSQL 或 Qdrant（向量数据库）容器被创建、停止或移除。GitHub Actions、受保护 Environment 审批、提供商侧凭据操作和 Gitleaks 全引用执行在当前工作区不可用，未报告为通过。

本地分发构建使用了 `python -m build --no-isolation`，因为安装的 WSL（Windows Subsystem for Linux）Python 缺少创建隔离构建环境所需的 `venv`/`ensurepip` 组件。构建依赖已通过锁定文件固定版本，两个归档文件均已生成，分发内容门禁通过。CI 保留正常的隔离构建命令。

## 剩余外部工作

授权操作员必须撤销并轮转已暴露的 DashScope 凭据，并审查提供商使用情况。必须恢复具有完整引用（Refs）的真实 Git 仓库并进行扫描。然后必须对确切的 Commit 执行全新的受保护 GitHub.com Workflow，依次运行每个 Producer、受保护事故候选 Job 以及下游正式收尾 Job。在正式 Artifact 成功上传之前，`production-blockers --scope phase --strict` 正确退出，状态码为 `3`。
