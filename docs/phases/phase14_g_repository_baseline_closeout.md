# Phase 14-G 仓库基线收尾

## 当前状态

```text
implementation_status = pass（实现状态：通过）
implementation_freeze = true（实现冻结：是）
candidate_manifest_status = git_aware_pending_generation（候选清单状态：Git 感知待生成）
bootstrap_round_status = phase14_g_r2_pre_push_retry（引导轮次状态：R2 推送前重试）
git_root_commit_status = not_created（Git 根提交状态：未创建）
github_push_status = not_started（GitHub 推送状态：未开始）
discovery_run_status = not_started（发现阶段运行状态：未开始）
formal_run_status = not_started（正式阶段运行状态：未开始）
online_verifier_status = not_run（在线验真器状态：未运行）
phase14_g_repository_baseline_status = blocked（Phase 14-G 仓库基线状态：阻塞）
phase14_authoritative_phase_status = blocked（Phase 14 权威阶段状态：阻塞）
database_revision = 0001（数据库版本号）
```

本文件仅根据实际观察到的结果进行更新。本地预检成功不能证明 GitHub 来源、受保护审批或 Workflow 完成。

## 已实现的预检契约

- 使用 Git 原生忽略引擎的 Git 感知 Candidate Manifest（候选清单）生成，具有精确的 Git 索引等价验证。
- 独立的非权威 Discovery（发现阶段）证据和上传后 Artifact（制品）绑定。
- 六个 Job 的 Formal（正式阶段）Workflow，带有受保护审批和明确的 Job 时间语义。
- 固定的三文件基线 Artifact 契约。
- 离线诊断和 GitHub 绑定在线验证模式。
- Push 后一次性失效边界。

## 剩余门禁

在仓库基线能够通过之前，实现必须完成所有本地回归门禁、安装并认证经批准的 GitHub CLI（命令行工具）、创建并推送唯一的根提交、验证分支和 Environment 保护、运行 Discovery、并启动全新的 Formal Workflow。执行必须在 `baseline-approval`（基线审批）等待必需审批人期间暂停。

审批后，同一 Run 必须完成，独立在线验真器必须验证 GitHub Artifact 元数据、原始归档文件、全部六个 Job、根提交和根树、候选清单、Discovery 绑定和审批事件。在此之前，Phase 14-G 和 Phase 14 整体保持阻塞（blocked）状态。

## 本地预检结果

```text
workflow_yaml_parse = pass（工作流 YAML 解析：通过）
legacy_node_ids_missing = 0（遗留节点标识缺失数）
policy_boundary = PASS（策略边界：通过）
rag_adapter = PASS（RAG 适配器：通过）
production_blockers_implementation = PASS（生产阻断项实现范围：通过）
source_tree_secret_count = 0（源码树密钥数量）
approved_local_secret_store_status = pass（经批准本地密钥存储状态：通过）
ignored_sensitive_files_status = pass（忽略敏感文件状态：通过）
superseded_invalid_candidate_manifest_entries = 306（已废弃无效候选清单条目数）
superseded_invalid_candidate_manifest_sha256 = 3c79050ce49aadfd26ec42938c646a66c202ec0ea1647bd1b8d902829a3527b2（已废弃无效候选清单校验和）
distribution_archives = 2（分发归档文件数）
distribution_forbidden_members = 0（分发禁止成员数）
distribution_build_command = uv build（分发构建命令）
postgres_non_destructive = 78 passed, 2 skipped（PostgreSQL 非破坏性测试）
postgres_destructive = 80 passed（PostgreSQL 破坏性测试）
operational_integration = 6 passed（运维集成测试）
database_revision = 0001 (head)（数据库版本号：0001 最新）
gh_release_tag = v2.93.0（GitHub CLI 发布版本标签）
gh_binary_version = 2.93.0（GitHub CLI 二进制版本）
gh_archive_sha256 = 02d1290eba130e0b896f3709ffff22e1c75a51475ddb70476a85abc6b5807af0（GitHub CLI 归档文件校验和）
gh_official_checksum_match = true（GitHub CLI 官方校验和匹配）
gh_install_prefix = ~/.local/opt/gh_2.93.0（GitHub CLI 安装路径）
```

已废弃的仅文件系统 Candidate Manifest 错误地包含了一个本地 Claude 配置文件和九个被忽略的测试/演示备份文件。Index 等价门禁在提交前将其拒绝。该 Manifest 为 `superseded_invalid`（已废弃无效），永远不得复用。Git 在未出生的 `main` 分支上初始化，Index 已清除，不存在 Commit，未发生远程变更。修正后的实现使用 Git 的原生忽略引擎，并通过了嵌套规则、否定规则、目录规则、全局排除隔离、信息排除拒绝、符号链接拒绝和 Candidate/Index 等价测试。

## R1 推送前重试

第一个本地根候选在推送前被拒绝，因为 Gitleaks 全引用门禁报告了六条发现。未发生远程变更。被拒绝的历史记录作为经验证的完整 Git Bundle（Git 打包文件）和隔离的 Git 元数据保存在仓库外部。

```text
rejected_root_commit = 3be530e4a8351233d8f0228c9c5eea78091547f1（被拒绝的根提交）
rejected_root_tree = ff1e1a197a85cf9870b9b8c6c89168d5dc93951e（被拒绝的根目录树）
rejected_root_finding_count = 6（被拒绝的根提交发现数量）
rejected_root_push_performed = false（是否执行了推送：否）
rejected_root_status = archived_not_rewritten（被拒绝的根状态：已归档未重写）
```

DashScope 自定义规则现在仅允许水平空白字符，且不能在空赋值后跨越 LF（换行）或 CRLF（回车换行）。类似 Token 的测试常量由短运行时片段构造，未添加任何路径或行号白名单。新的根候选必须在首次普通推送前独立通过校验和、双 Canary（金丝雀令牌）和零发现全引用门禁。

## R2 证据一致性重试

R1 根候选在推送前被拒绝，因为其 Gitleaks 摘要报告了通过、零发现的全引用扫描，但同时将 `gitleaks_real_repository_scan_passed` 设为 false（假）。未发生远程变更，该历史记录作为经验证的完整 Git Bundle 和隔离的 Git 元数据单独保存。

```text
r1_rejected_root_commit = eee9f5b3a98cc8167c8afff177bb9628429ea759（R1 被拒绝的根提交）
r1_rejected_root_tree = 9821edd3a56b77b747d5821db6316772f9669274（R1 被拒绝的根目录树）
r1_rejected_root_push_performed = false（R1 是否执行了推送：否）
r1_rejected_root_status = archived_not_rewritten（R1 被拒绝的根状态：已归档未重写）
```

R2 使用一个严格的仓库扫描结果契约。进程返回码和发现数量推导出全引用状态；该状态、返回码和发现数量推导出布尔值。矛盾的证据被生成、Formal 消费和生产阻断项评估拒绝。
