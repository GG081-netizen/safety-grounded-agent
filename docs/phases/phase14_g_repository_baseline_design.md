# Phase 14-G 仓库基线设计

## 目的

Phase 14-G（第 14-G 阶段）为 `GG081-netizen/crispy-fortnight-baseline-2`（Bootstrap 2，引导阶段 2）建立首个可信的 Git 和 GitHub 基线。它是一次性引导契约（One-Shot Bootstrap Contract），而非普通发布工作流。

历史命名说明：在 Phase 14-G 被关闭为 blocked（阻塞状态）后，该仓库重命名为 `GG081-netizen/safety-grounded-agent`。仓库 ID（标识符）和保留的 Phase 14-G 审计事实未发生变更。本文件保留原始名称，因为它描述的是 Bootstrap 2 执行时的状态。

所需身份为：

```text
frozen local implementation（冻结的本地实现）
= Candidate Manifest（候选清单）
= root commit tree（根提交树）
= GitHub main（GitHub 主分支）
= Discovery subject（发现阶段主题）
= Formal subject（正式阶段主题）
= verified baseline artifact subject（已验证基线制品主题）
```

Candidate Manifest（候选清单）在空的、未出生的 Git 仓库中生成，仅存储在 Git 忽略的 `tmp/` 目录下。候选路径来自 Git 的原生忽略引擎，等效于 `git -c core.excludesFile=/dev/null ls-files --others --exclude-standard -z`。`.git/info/exclude` 中的有效非注释规则会被拒绝。这种方式隔离了全局排除规则，同时保留了嵌套、否定和目录级 `.gitignore` 语义。

清单列出了规范路径、Git 模式、字节大小和文件 SHA-256（安全哈希算法 256 位）值。它不包含时间戳、绝对路径、本地 Secret Store（密钥存储）、Git 元数据、被忽略的本地备份文件或自身。符号链接和工作树之外的路径被拒绝。在 `git add --all` 之后，从 Git 索引重建的相同表示必须精确匹配。

引导顺序是固定的：

```text
implementation freeze（实现冻结）
-> empty/unborn Git repository（空/未出生 Git 仓库）
-> Git-aware Candidate Manifest（Git 感知候选清单）
-> git add --all（添加全部文件）
-> Index Manifest（索引清单）
-> exact equivalence gate（精确等价门禁）
-> root commit（根提交）
```

## 证据层

Discovery（发现阶段）明确是非权威的。其内部证据仅包含 Artifact 上传前已知的事实。Artifact ID、摘要、大小和来源在事后从 GitHub API 获取，并记录在 Git 忽略的 `tmp/` 下的外部 `DiscoveryArtifactBindingV1` 中。

Discovery Artifact 成员集合是精确的：

```text
phase14-discovery-evidence.json（发现阶段证据）
phase14-candidate-manifest.json（候选清单）
```

未知、重复、隐藏、非普通文件、目录或符号链接成员被拒绝。

Formal（正式阶段）执行将该外部 GitHub 元数据绑定到原始下载的 ZIP 文件，然后再绑定到内部 Discovery 证据。最终基线 Artifact 恰好包含三个文件：

```text
phase14-baseline-closeout.json（基线收尾 JSON）
phase14-baseline-closeout.md（基线收尾 Markdown）
phase14-candidate-manifest.json（候选清单）
```

该 Artifact 不包含其自身的 ID、摘要、大小、下载 URL 或 GitHub 来源声明。这些事实只能由独立在线验真器（Online Verifier）在工作流完成后建立。

## Job 时间语义

正在运行的 Job 无法证明自身成功完成。Producer Job 在其状态为 `queued`（排队中）或 `in_progress`（进行中）且结论为空时，绑定其 Workflow-Job 和 Check-Run ID。`baseline-approval` 验证四个 Producer，而 `baseline-closeout` 验证这些 Job 加上审批 Job。每个 Job 仍然只记录自身的运行状态。只有在线验真器能证明全部六个 Job 已成功完成。

受保护 Environment（环境）为 `phase14-baseline-closeout`。审批必须来自 `toshibanino6-creator`，不同于 Workflow 触发 Actor，并通过防自审（Prevent-Self-Review）强制执行。

## 一次性故障边界

所有源代码、测试、工作流、验证脚本和文档在根提交之前被冻结。如果在首次 Push 后发现已提交文件的缺陷，该基线尝试即告失效。根提交永远不会被修改或重写，第二个提交不能被呈现为同一根基线。恢复需要全新批准的空仓库或新的带版本号的后续基线契约（Successor Baseline Contract）。

`GG081-netizen/crispy-fortnight` 中的 Bootstrap 1 在其首次 Discovery 运行暴露了已提交的 Discovery/Formal Job 身份契约混淆后已失效。其根提交和失败运行保留为不可变的审计证据；Bootstrap 2 使用独立的 `DiscoveryJobIdentity` 和 `FormalBaselineJobIdentity` 契约。

Phase 14-G 成功并不关闭 DashScope 事故：

```text
phase14_g_repository_baseline_status = pass（Phase 14-G 仓库基线状态：通过）
phase14_implementation_status = pass（Phase 14 实现状态：通过）
phase14_incident_closure_status = blocked（Phase 14 事故关闭状态：阻塞）
phase14_authoritative_phase_status = blocked（Phase 14 权威阶段状态：阻塞）
database_revision = 0001（数据库版本号）
```
