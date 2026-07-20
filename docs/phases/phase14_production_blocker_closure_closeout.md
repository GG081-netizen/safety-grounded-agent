# Phase 14 生产阻塞项关闭结项（Production Blocker Closure Closeout）

## 状态（Status）

```text
implementation_status = pass                      # 实现状态 = 通过
incident_closure_status = blocked                 # 事件关闭状态 = 阻塞
phase_status = blocked                            # Phase 状态 = 阻塞
preflight_hardening = pass                        # 预检加固 = 通过
dashscope_evidence = pending_authorized_review    # DashScope 证据 = 待授权审查
trusted_git_metadata = not_ready                  # 受信任 Git 元数据 = 未就绪
protected_workflow = not_run                      # 受保护工作流 = 未运行
blocking_reasons = dashscope_credential_revocation_unverified,  # 阻塞原因 = DashScope 凭证吊销未验证,
                   dashscope_credential_rotation_unverified,     #             DashScope 凭证轮换未验证,
                   git_history_unavailable                       #             Git 历史不可用
database_revision = 0001                          # 数据库修订版本 = 0001
```

实现范围（implementation scope）在本地已完成。事件范围（incident scope）未完成：本工作区没有可用的 Git 元数据（metadata），也没有可用的受保护提供商证明（protected provider attestation）。本文档不声称凭证吊销（credential revocation）、轮换（rotation）、提供商用量审查（provider usage review）、已跟踪文件扫描（tracked-file scanning）、Git 历史扫描（Git history scanning）或真实的 GitHub Actions 运行已完成。

## 已实现（Implemented）

- Pydantic `SecretStr` 保护 LLM 凭证（credential）和数据库 URL；明文（plaintext）仅在提供商（provider）、HTTP 和 SQLAlchemy 组合边界（composition boundary）处解包。
- 递归脱敏（recursive redaction）覆盖消息（message）、异常（exception）和嵌套日志附加数据（nested logging extras），并带有循环引用和深度保护（cycle and depth protection）。
- 仓库源树（repository source-tree）、已批准的本地密钥存储（approved local Secret Store）、已忽略敏感文件（ignored-sensitive-file）、已跟踪文件（tracked-file）及 Git 历史（Git-history）状态相互独立。构建归档（build archive）具有独立的内容门禁（content gate）。
- Gitleaks `v8.30.1` 在 CI 中以校验和锁定（checksum pinned）。内置和项目级 DashScope 金丝雀（Canary）必须均被检测到后，才会扫描真实引用（real refs）。
- 受保护（protected）的 `incident-closure` 作业（Job）创建一条仅运行时有效的证明（runtime-only attestation），绑定到 `github.sha` 和受保护引用（protected ref）。
- Phase 14-F 将所有生产者报告（Producer report）包装在来源绑定的 `EvidenceEnvelope` 对象（证据包络）中，包含工作流作业（Workflow Job）和检查运行（Check Run）身份。当前作业（Job）仅在运行中绑定，绝不自我证明已完成（completion）。
- 独立的 `formal-closeout` 作业（Job）重新验证全部五个已完成的前置作业、报告哈希（report hash）、运行时证明 V2（Runtime Attestation V2）、工作流定义（Workflow definition）和数据库修订版本（database Revision）。其恰好三文件的制品（Artifact）必须成功上传；独立在线验证器（independent online verifier）随后证明已完成运行（Run）及全部六个作业（Job）。
- 正式关闭（Formal closure）仅接受全新的 GitHub.com `workflow_dispatch` 运行（run），且 `run_attempt == 1`；重新运行（rerun）和 GitHub Enterprise Server 故障关闭（fail closed）。
- `Coordinator`（协调器）没有请求级别的 `_current_*` 状态。请求（request）、追踪（trace）、会话（session）和策略数据（policy data）被显式传播；RAG 接收实际的追踪 ID（trace ID）。
- 策略（Policy）使用版本化风险规则（versioned risk rule）、立场模式（stance pattern）和解析阈值（resolution threshold）。候选 ID（Candidate ID）在跨进程间是确定性的，并包含规范化的输入指纹（normalized-input fingerprint）。
- 分类器（Classifier）故障和格式错误的结果返回 `UNCERTAIN`（不确定）；确定性的 BLOCKED 结果不可被覆盖。
- 企业固件（Enterprise fixture）包含 243 个用例。Phase 14 不包含采购（procurement）、销售（sales）、售前（presales）和企业知识支持（enterprise knowledge support）之外的行业特定规则。

## 证据（Evidence）

本地实现验证（validation）完成，测量结果如下：

```text
phase14_prechange_nodeids = 663                       # Phase 14 变更前节点 ID 数
current_nodeids = 821                                 # 当前节点 ID 数
missing_phase14_nodeids = 0                           # 缺失的 Phase 14 节点 ID 数
phase14_nodeid_renames = 2                            # Phase 14 节点 ID 重命名数
unit_tests = 740 passed, 81 deselected, 9.22 seconds   # 单元测试 = 740 通过, 81 已排除, 9.22 秒
full_tests = 740 passed, 81 skipped, 8.78 seconds      # 全量测试 = 740 通过, 81 跳过, 8.78 秒
policy_fixture_cases = 243                            # 策略固件用例数
risk_candidate_recall = 1.0                           # 风险候选召回率
request_stance_recall = 1.0                           # 请求立场召回率
prohibit_stance_precision = 1.0                       # 禁止立场精确率
audit_stance_precision = 1.0                          # 审计立场精确率
unknown_fail_closed_rate = 1.0                        # 未知故障关闭率
multi_candidate_resolution_accuracy = 1.0             # 多候选解析准确率
adversarial_bypass_count = 0                          # 对抗性绕过次数
unicode_bypass_count = 0                              # Unicode 绕过次数
business_false_positive_count = 0                     # 业务误报次数
classifier_failure_safe_count = 0                     # 分类器故障安全次数
concurrency_rounds = 100                              # 并发轮数
request_mismatch_count = 0                            # 请求不匹配次数
trace_mismatch_count = 0                              # 追踪不匹配次数
session_mismatch_count = 0                            # 会话不匹配次数
policy_mismatch_count = 0                             # 策略不匹配次数
future_timeout_count = 0                              # Future 超时次数
deadlock_count = 0                                    # 死锁次数
unfinished_future_count = 0                           # 未完成 Future 次数
blocked_rag_call_count = 0                            # 被阻止的 RAG 调用次数
policy_boundary_strict = PASS                         # 策略边界严格 = 通过
rag_adapter_strict = PASS                             # RAG 适配器严格 = 通过
production_blockers_implementation_strict = PASS       # 生产阻塞项实现严格 = 通过
production_blockers_phase_strict = BLOCKED, logical exit code 3  # 生产阻塞项 Phase 严格 = 阻塞, 逻辑退出码 3
source_tree_scan_status = pass                        # 源树扫描状态 = 通过
source_tree_files_scanned = 284                       # 源树已扫描文件数
source_tree_secret_count = 0                          # 源树密钥数量
approved_local_secret_store_status = pass             # 已批准本地密钥存储状态 = 通过
ignored_sensitive_files_status = pass                 # 已忽略敏感文件状态 = 通过
tracked_files_scan_status = blocked                   # 已跟踪文件扫描状态 = 阻塞
git_history_scan_status = blocked                     # Git 历史扫描状态 = 阻塞
distribution_archives = 2                             # 分发归档数
distribution_forbidden_members = 0                    # 分发禁止成员数
postgres_non_destructive = 72 passed, 2 skipped       # PostgreSQL 非破坏性测试 = 72 通过, 2 跳过
postgres_destructive = 74 passed                      # PostgreSQL 破坏性测试 = 74 通过
operational_integration = 6 passed                    # 运维集成测试 = 6 通过
database_revision = 0001 (head)                       # 数据库修订版本
workflow_yaml_parse = PASS                            # 工作流 YAML 解析 = 通过
workflow_required_job_structure = PASS                # 工作流必需作业结构 = 通过
github_actions_runtime_execution = not_run_in_current_environment  # GitHub Actions 运行时执行 = 当前环境未运行
uv_lock_sha256 = a6a0e339868fe2d44d05b269ad4f92f64b3fe955186e87ed8b0aff0fc363342d
uv_lock_changed_during_final_validation = false       # uv.lock 在最终验证期间变更 = 否
phase14_f_contract_tests = 49 passed                  # Phase 14-F 合约测试 = 49 通过
formal_closeout_workflow_runtime = not_run_in_current_environment  # 正式结项工作流运行时 = 当前环境未运行
authoritative_phase_resolution = unavailable          # 权威 Phase 决议 = 不可用
```

PostgreSQL 和运维检查使用了由运维人员（operator）提供的现有测试环境。未创建、停止或移除任何 PostgreSQL 或 Qdrant 容器。GitHub Actions、受保护环境审批（protected Environment approval）、提供商侧凭证操作（provider-side credential operation）及 Gitleaks 全引用执行（all-ref execution）在本工作区不可用，未报告为通过。

本地分发构建使用了 `python -m build --no-isolation`，因为已安装的 WSL Python 缺少 `venv`/`ensurepip` 组件，无法创建隔离构建环境。构建需求已被锁版本锁定，两个归档均已产出，分发内容门禁（distribution content gate）已通过。CI 保留常规的隔离构建命令。

## 剩余外部工作（Remaining External Work）

授权运维人员（authorized operator）必须吊销（revoke）和轮换（rotate）暴露的 DashScope 凭证（credential），并审查提供商用量（provider usage）。必须恢复并扫描一个具有完整引用（complete refs）的真实 Git 仓库（repository）。然后必须针对该确切提交，执行一条全新的受保护 GitHub.com 工作流（workflow），运行每个生产者（Producer）、受保护事件候选（protected incident candidate）及下游正式结项（downstream formal closeout）。在正式制品（formal Artifact）成功上传之前，`production-blockers --scope phase --strict` 正确地退出，状态码（status code）为 `3`。
