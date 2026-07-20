# Phase 14-G 仓库基线设计（Repository Baseline Design）

## 目的（Purpose）

Phase 14-G 为 `GG081-netizen/crispy-fortnight-baseline-2`（Bootstrap 2）建立首个受信任的 Git 与 GitHub 基线（baseline）。这是一份一次性引导合约（one-shot bootstrap contract），而非普通的发布工作流（release workflow）。

历史命名说明：在 Phase 14-G 以阻塞（blocked）状态关闭之后，同一仓库已更名为 `GG081-netizen/safety-grounded-agent`。仓库 ID 和保留的 Phase 14-G 审计事实（audit facts）未发生变化。本文档保留原始名称，因为它描述的是当时 Bootstrap 2 的执行过程。

所要求的身份链（identity chain）为：

```text
frozen local implementation
= Candidate Manifest
= root commit tree
= GitHub main
= Discovery subject
= Formal subject
= verified baseline artifact subject
```

候选清单（Candidate Manifest）在一个空的、未出生的 Git 仓库（unborn repository）中生成，并仅存储在已被忽略的 `tmp/` 目录下。候选路径（candidate path）来自 Git 原生忽略引擎（native ignore engine），使用等价于 `git -c core.excludesFile=/dev/null ls-files --others --exclude-standard -z` 的命令。`.git/info/exclude` 中存在有效的非注释规则（non-comment rule）将被拒绝。这在保留嵌套、否定及目录级 `.gitignore` 语义的同时隔离了全局排除规则（global excludes）。

清单（manifest）列出规范路径（canonical path）、Git 模式（mode）、字节大小（byte size）及文件 SHA-256 值。它不包含时间戳（timestamp）、绝对路径（absolute path）、本地密钥存储（local Secret Store）、Git 元数据（metadata）、已忽略的本地备份文件（local backup file）或其自身。符号链接（symlink）和工作树外路径被拒绝。在 `git add --all` 之后，从 Git 索引（index）重建的相同表示（representation）必须完全匹配。

引导顺序（bootstrap ordering）固定如下：

```text
implementation freeze
-> empty/unborn Git repository
-> Git-aware Candidate Manifest
-> git add --all
-> Index Manifest
-> exact equivalence gate
-> root commit
```

## 证据层（Evidence Layers）

发现阶段（Discovery）明确是非权威的（non-authoritative）。其内部证据仅包含在制品上传（artifact upload）之前已知的事实。制品 ID（Artifact ID）、摘要（digest）、大小（size）及来源（origin）随后从 GitHub API 获取，并记录在已忽略 `tmp/` 下的外部 `DiscoveryArtifactBindingV1` 中。

发现制品成员集（Discovery artifact member set）精确如下：

```text
phase14-discovery-evidence.json
phase14-candidate-manifest.json
```

未知、重复、隐藏、非常规、目录或符号链接成员均被拒绝。

正式执行（Formal execution）将该外部 GitHub 元数据（metadata）绑定到原始下载的 ZIP 文件，再绑定到内部发现证据（Discovery evidence）。最终基线制品（baseline artifact）恰好包含三个文件：

```text
phase14-baseline-closeout.json
phase14-baseline-closeout.md
phase14-candidate-manifest.json
```

制品（artifact）不包含其自身的 ID、摘要（digest）、大小（size）、下载 URL 或 GitHub 来源声明。这些事实只能由独立的在线验证器（independent online verifier）在工作流（workflow）完成后建立。

## 作业时间语义（Job Time Semantics）

正在运行的作业无法证明其自身成功完成。生产者作业（Producer job）在其状态为 `queued` 或 `in_progress` 且结论为 null 时绑定其工作流作业 ID（workflow-job ID）和检查运行 ID（check-run ID）。`baseline-approval` 验证四个生产者（Producer），`baseline-closeout` 验证这些作业加上审批作业（approval job）。每个作业仍仅记录自身的运行状态。仅在线验证器（online verifier）能证明全部六个作业均已成功完成。

受保护环境（protected Environment）为 `phase14-baseline-closeout`。审批必须来自 `toshibanino6-creator`，且必须不同于工作流触发者（workflow trigger actor），并通过防止自我审查（prevent-self-review）强制执行。

## 一次性失败边界（One-shot Failure Boundary）

所有源码、测试、工作流、验证脚本及文档在根提交（root commit）之前冻结。如果在首次推送（push）后发现已提交文件的缺陷（defect），则该基线尝试（baseline attempt）作废。根提交（root commit）永不修改（amended）或重写（rewritten），第二个提交不能作为同一根基线（root baseline）呈现。恢复需要新批准的空白仓库，或一份新的版本化继任基线合约（Successor Baseline contract）。

`GG081-netizen/crispy-fortnight` 中的 Bootstrap 1 在其首次发现运行（Discovery run）暴露了已提交的发现/正式作业身份合约（Discovery/Formal job-identity contract）混淆问题后被作废。其根提交和失败运行仍为不可变审计证据（immutable audit evidence）；Bootstrap 2 使用独立的 `DiscoveryJobIdentity` 和 `FormalBaselineJobIdentity` 合约（contract）。

Phase 14-G 成功不关闭 DashScope 事件：

```text
phase14_g_repository_baseline_status = pass    # Phase 14-G 仓库基线状态 = 通过
phase14_implementation_status = pass            # Phase 14 实现状态 = 通过
phase14_incident_closure_status = blocked       # Phase 14 事件关闭状态 = 阻塞
phase14_authoritative_phase_status = blocked    # Phase 14 权威 Phase 状态 = 阻塞
database_revision = 0001                        # 数据库修订版本 = 0001
```
