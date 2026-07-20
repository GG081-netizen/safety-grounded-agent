# Phase 14-G 仓库基线结项（Repository Baseline Closeout）

## 当前状态（Current Status）

```text
implementation_status = pass                       # 实现状态 = 通过
implementation_freeze = true                       # 实现冻结 = 是
candidate_manifest_status = git_aware_pending_generation  # 候选清单状态 = Git 感知, 待生成
bootstrap_round_status = phase14_g_r2_pre_push_retry     # 引导轮次状态 = Phase 14-G R2 推送前重试
git_root_commit_status = not_created               # Git 根提交状态 = 未创建
github_push_status = not_started                   # GitHub 推送状态 = 未开始
discovery_run_status = not_started                 # 发现运行状态 = 未开始
formal_run_status = not_started                    # 正式运行状态 = 未开始
online_verifier_status = not_run                   # 在线验证器状态 = 未运行
phase14_g_repository_baseline_status = blocked     # Phase 14-G 仓库基线状态 = 阻塞
phase14_authoritative_phase_status = blocked       # Phase 14 权威 Phase 状态 = 阻塞
database_revision = 0001                           # 数据库修订版本 = 0001
```

本文档仅以实际观察到的结果更新。成功的本地预检（local preflight）不能证明 GitHub 来源（origin）、受保护审批（protected approval）或工作流完成（workflow completion）。

## 已实现的预检合约（Implemented Preflight Contracts）

- 基于 Git 原生忽略引擎（native ignore engine）的 Git 感知候选清单（Candidate Manifest）生成，含精确的 Git 索引等价验证（Index equivalence verification）。
- 独立的非权威发现证据（non-authoritative Discovery evidence）及上传后制品绑定（post-upload artifact binding）。
- 六个作业（Job）的正式工作流（Formal workflow），含受保护审批（protected approval）和明确的作业时间语义（job-time semantics）。
- 固定的三文件基线制品合约（baseline artifact contract）。
- 离线诊断（offline diagnostic）及依赖 GitHub 的在线验证模式（online verification mode）。
- 推送后一次性作废边界（one-shot post-push invalidation boundary）。

## 剩余门禁（Remaining Gates）

在仓库基线（repository baseline）可以通过之前，实现必须完成所有本地回归门禁（local regression gate）、安装并认证已批准的 GitHub CLI、创建并推送唯一的根提交（root commit）、验证分支和环境保护（branch and Environment protection）、运行发现阶段（Discovery），并启动一条全新的正式工作流（Formal workflow）。在 `baseline-approval` 等待指定审查人期间，执行必须暂停。

审批通过后，同一次运行必须完成，且独立在线验证器（independent online verifier）必须验证 GitHub 制品元数据（artifact metadata）、原始归档（raw archive）、全部六个作业（job）、根提交和树（root commit and tree）、候选清单（Candidate Manifest）、发现绑定（Discovery binding）及审批事件（approval event）。在此之前，Phase 14-G 和 Phase 14 整体均保持阻塞（blocked）状态。

## 本地预检结果（Local Preflight Results）

```text
workflow_yaml_parse = pass                                                  # 工作流 YAML 解析 = 通过
legacy_node_ids_missing = 0                                                 # 缺失的旧版节点 ID 数
policy_boundary = PASS                                                      # 策略边界 = 通过
rag_adapter = PASS                                                          # RAG 适配器 = 通过
production_blockers_implementation = PASS                                    # 生产阻塞项实现 = 通过
source_tree_secret_count = 0                                                # 源树密钥数量
approved_local_secret_store_status = pass                                   # 已批准本地密钥存储状态 = 通过
ignored_sensitive_files_status = pass                                       # 已忽略敏感文件状态 = 通过
superseded_invalid_candidate_manifest_entries = 306                         # 已作废无效候选清单条目数
superseded_invalid_candidate_manifest_sha256 = 3c79050ce49aadfd26ec42938c646a66c202ec0ea1647bd1b8d902829a3527b2
distribution_archives = 2                                                   # 分发归档数
distribution_forbidden_members = 0                                          # 分发禁止成员数
distribution_build_command = uv build                                       # 分发构建命令
postgres_non_destructive = 78 passed, 2 skipped                             # PostgreSQL 非破坏性测试 = 78 通过, 2 跳过
postgres_destructive = 80 passed                                            # PostgreSQL 破坏性测试 = 80 通过
operational_integration = 6 passed                                          # 运维集成测试 = 6 通过
database_revision = 0001 (head)                                             # 数据库修订版本
gh_release_tag = v2.93.0                                                    # gh 发布标签
gh_binary_version = 2.93.0                                                  # gh 二进制版本
gh_archive_sha256 = 02d1290eba130e0b896f3709ffff22e1c75a51475ddb70476a85abc6b5807af0
gh_official_checksum_match = true                                           # gh 官方校验和匹配 = 是
gh_install_prefix = ~/.local/opt/gh_2.93.0                                  # gh 安装前缀路径
```

已作废的仅文件系统级候选清单（filesystem-only Candidate Manifest）错误地包含了 1 个本地 Claude 配置文件和 9 个已忽略的测试/演示备份文件。索引等价门禁（Index equivalence gate）在提交前拒绝了它。该清单被标记为 `superseded_invalid`，不得再次使用。Git 已在未出生的 `main` 分支上初始化，索引（Index）已清空，无提交存在，无远程变更发生。修正后的实现使用 Git 原生忽略引擎，并已通过嵌套规则、否定规则、目录规则、全局排除隔离、info-exclude 拒绝、符号链接拒绝及候选/索引等价性测试。

## R1 推送前重试（R1 Pre-Push Retry）

第一个本地根候选（root candidate）在推送前被拒绝，因 Gitleaks 全引用门禁（all-refs gate）报告了 6 条发现。无远程变更发生。被拒绝的历史作为已验证的完整 Git 捆绑包（Git bundle）和隔离的 Git 元数据（metadata）保存在仓库之外。

```text
rejected_root_commit = 3be530e4a8351233d8f0228c9c5eea78091547f1   # 被拒绝的根提交
rejected_root_tree = ff1e1a197a85cf9870b9b8c6c89168d5dc93951e     # 被拒绝的根树
rejected_root_finding_count = 6                                     # 被拒绝根发现数量
rejected_root_push_performed = false                                # 被拒绝根推送已执行 = 否
rejected_root_status = archived_not_rewritten                       # 被拒绝根状态 = 已归档, 未重写
```

DashScope 自定义规则现在仅允许水平空白字符，且不能在空赋值后跨越 LF 或 CRLF。类令牌测试常量由短运行时片段构造，未添加任何路径或行号允许列表。新根候选必须在首次普通推送前独立通过校验和、双金丝雀（Canary）及零发现全引用门禁。

## R2 证据一致性重试（R2 Evidence Consistency Retry）

R1 根候选在推送前被拒绝，因为其 Gitleaks 摘要报告了通过、零发现全引用扫描（all-refs scan），但 `gitleaks_real_repository_scan_passed` 仍为 false。无远程变更发生，该历史作为已验证的完整 Git 捆绑包（Git bundle）和隔离的 Git 元数据（metadata）单独保存。

```text
r1_rejected_root_commit = eee9f5b3a98cc8167c8afff177bb96242f9ea759   # R1 被拒绝的根提交
r1_rejected_root_tree = 9821edd3a56b77b747d5821db6316772f9669274     # R1 被拒绝的根树
r1_rejected_root_push_performed = false                                # R1 被拒绝根推送已执行 = 否
r1_rejected_root_status = archived_not_rewritten                       # R1 被拒绝根状态 = 已归档, 未重写
```

R2 使用一个严格的仓库扫描结果合约（repository-scan result contract）。进程返回码（return code）和发现数量（finding count）推导出全引用状态（all-refs status）；该状态、返回码及发现数量继而推导出布尔值（boolean）。矛盾证据（contradictory evidence）在生成、正式消费（Formal consumption）及生产阻塞项评估（Production Blockers evaluation）阶段均被拒绝。
