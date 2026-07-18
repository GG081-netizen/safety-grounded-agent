# Safety-Grounded Agent Orchestration Layer（安全可信的智能体编排层）：设计说明

本文解释当前架构为什么这样设计、替代方案是什么、代价在哪里。源码细节和面试复习见 [项目深度理解与面试复盘手册](project-walkthrough.md)。

阅读约定：英文源码字段保留原名，并在首次定义或所属表格中附上中文实际含义；类名、函数名、源码路径和环境变量保持原样，避免与代码失去对应关系。

## 1. 设计目标

项目服务于 B2B（企业对企业）IT（信息技术）设备和企业办公采购售前场景。目标不是构造一个会自由规划一切的 Agent（智能体），而是建立一条可预测、可测试、可降级、可追踪的业务执行链：

```text
Input（用户输入）
-> Business Safety Policy（业务安全策略）
-> IntentRouter（意图路由器）
-> TaskRouter（任务路由器）
-> Coordinator task execution（协调器任务执行）
-> RAG / Sales / Writer（检索增强生成、销售分析与内容写作模块）
-> Response + Evidence + Confidence + Trace（回答、证据、置信度与执行追踪）
```

设计原则：安全判断先于业务执行；语义理解与执行决策分层；知识能力通过协议解耦；降级必须显式；评测结论不得超出其样本和边界。

## 2. 系统职责边界

```text
test_demo（当前智能体编排项目）
  Policy / Routing / Coordinator / Business Workflow（安全策略、路由、协调器与业务工作流）
  Trace / RAG Adapter / Evaluation（执行追踪、RAG 适配器与评测）

RAG_demo（外部企业知识引擎）
  Retrieval / Rerank / Grounded QA（检索、重排序与基于证据的问答）
  Citation / Answerability（引用与可回答性判断）
```

### 2.1 为什么不把 RAG_demo 合并进来

- Agent（智能体）编排和知识引擎的变更周期不同。
- 外部协议可以让同一 Coordinator（协调器）替换 RAG provider（RAG 结果提供方）。
- 当前项目可以集中验证 safety（安全控制）、routing（路由）、fallback（降级）和 trace（执行追踪）。
- 避免在两个项目重复实现向量检索、rerank（重排序）和 answerability（可回答性判断）。

替代方案是把 RAG（检索增强生成）全部内嵌。它减少一次 HTTP（超文本传输协议）调用，但会扩大部署单元、耦合依赖，并模糊“编排可靠性”和“知识质量”的评测边界。

当前代价是依赖外部服务可用性和响应 schema（数据结构约束），因此实现了结构化错误、本地 fallback（降级）和输出映射。

## 3. 为什么采用 Policy-first（策略优先）

> 源码入口：`src/conversation_agent/orchestration/coordinator.py::Coordinator.run`、`src/conversation_agent/policy/engine.py::PolicyEngine`

`Coordinator.run()`（协调器主执行入口）在任何 Router（路由器）或任务执行前调用 `PolicyEngine.decide()`（安全策略决策入口）。BLOCKED（阻断执行）请求立即返回，trace（执行追踪）只有 `policy_engine`（策略引擎步骤）。

这不是生成后过滤。生成后过滤已经让风险输入进入 RAG（检索增强生成）、LLM（大语言模型）或工具，可能发生隐私传播、无效成本或副作用。前置硬门保证已识别的风险不进入后续模块。

`blocked_no_rag_call_rate`（被阻断请求未调用 RAG 的比例）使用 counting fake client（可计数的模拟客户端）验证 RAG 调用次数为零。它能证明控制流硬门有效，但不能证明 Policy 能识别所有风险表达。

### 3.1 SAFE、UNCERTAIN、BLOCKED 的真实控制流

| 状态 | 中文含义 | 是否继续 Router | 是否执行任务/RAG | 最终回答 | Confidence（置信度） |
|---|---|---|---|---|---|
| SAFE | 安全放行 | 是 | 是 | 业务结果 | 使用任务结果置信度 |
| UNCERTAIN | 不确定，但可继续提供有限协助 | 是 | 是 | `uncertain_message`（不确定状态提示）前置到业务结果 | `min(task_confidence, policy_confidence)`，即任务置信度与策略置信度的较小值 |
| BLOCKED | 阻断执行 | 否 | 否 | `blocked_message`（阻断提示） | Policy confidence（策略决策置信度） |

UNCERTAIN 不是弱拒答。它会继续经过 IntentRouter、TaskRouter 和任务执行；Router trace（路由追踪步骤）会携带 uncertain warning（不确定状态警告）；任务完成后，Coordinator 把提示放在业务结果之前，并限制最终 confidence（置信度）。当前没有独立人工审批或专门的降级 workflow（工作流）。

BLOCKED 则在 Policy 后立即返回，不调用 IntentRouter、TaskRouter、RAG 或其他任务模块，因此 trace（执行追踪）只有 `policy_engine`（策略引擎步骤）。

## 4. Business Safety Firewall（业务安全防火墙）

> 源码入口：`src/conversation_agent/policy/engine.py::PolicyEngine`、`normalization.py`、`candidates.py`、`stance.py`、`resolver.py`

### 4.1 企业业务安全边界

当前规则只覆盖企业采购、销售、售前和知识问答边界：隐私越权、私人敏感属性推断、法律或金融最终判断、虚假销售承诺、编造业务事实，以及需要证据或授权确认的业务不确定性。

### 4.2 为什么使用分层、版本化 Catalog

`RiskRule` 将受监管业务动作集中在 `policy/rules.py`；Stance Pattern（立场模式）和 Resolution Threshold（决策阈值）分别位于版本化 Catalog。Engine 不包含散落的中文风险词、否定词或固定字符窗口。

| 字段 | 中文实际含义 |
|---|---|
| `rule_id` | 规则唯一标识，用于定位、诊断和后续配置迁移 |
| `category` | 风险类别，用于归类命中结果和统计边界覆盖 |
| `action` | 项目批准治理的业务动作，而不是自由文本标签 |
| `severity` | LOW、MEDIUM、HIGH 或 CRITICAL |
| `status_hint` | 规则对 Resolver 的状态提示，不直接替代立场决策 |
| `priority` | 规则优先级，多规则命中时优先选择数值更高者 |
| `detectors` | normalized 或 compact 召回规格；语义判断仍回到 normalized span |
| `reason` | 返回给 PolicyDecision 的决策原因 |

`PolicyDecision`（安全决策结果）的字段含义如下：

| 字段 | 中文实际含义 |
|---|---|
| `status` | 归一化安全状态：SAFE、UNCERTAIN 或 BLOCKED |
| `reason` | 本次安全决策原因 |
| `matched_rules` | 当前输入命中的全部规则列表 |
| `warnings` | 分类器失败或策略降级产生的警告列表 |
| `classifier_used` | 本次决策是否实际调用了可选分类器 |
| `confidence` | 当前安全决策的启发式置信度，不是统计概率 |

Engine 只负责串联 Normalize、Candidate Detector、Stance Resolver、Policy Resolver 和可选 classifier fallback（分类器兜底）。Candidate ID 包含输入指纹、Catalog、Rule、Action 和 normalized span，跨进程稳定且不暴露明文 span。

相较散落的 `if keyword in text`：

- 规则可定位、可测试、可统计 category coverage（风险类别覆盖范围）。
- 同一执行算法适用于所有业务类别。
- 后续可以迁移到 JSON/YAML（JSON 或 YAML 配置格式）或配置中心。

当前仍然是可解释的 deterministic guardrail（确定性安全护栏），不能声称具备完整自然语言理解。

### 4.3 多候选、立场与决策矩阵

Detector 保留每个 occurrence，Stance Resolver 对每个候选独立给出 REQUEST、PROHIBIT、AUDIT、DISCUSS、QUOTE 或 UNKNOWN。任一 HIGH/CRITICAL REQUEST 都使结果为 BLOCKED；UNKNOWN 固定为 UNCERTAIN；PROHIBIT/AUDIT 及完整作用域内达到版本化阈值的 DISCUSS/QUOTE 可以 SAFE。

### 4.4 不再存在全局排除模式

旧 `PolicyRule.negative_patterns` 仅为构造兼容保留，内置值为空且 Engine 不读取。安全前缀不能豁免同一句或后续分句中的风险 REQUEST。

### 4.5 optional classifier fallback（可选分类器兜底）

无候选时才调用可选 classifier（分类器）。异常、非法结构、非法状态、NaN/Infinity 或越界 confidence 全部 fail-closed 为 UNCERTAIN；warning 只记录安全 reason code，不包含异常原文或供应商响应。确定性 BLOCKED 不允许 classifier 覆盖。

默认 Coordinator 创建的 PolicyEngine 没有配置 classifier。`fallback_to_uncertain` 只为旧构造签名兼容保留，不能恢复 fail-open 行为。

替代方案是纯 LLM Policy。其语义覆盖更强，但输出可能漂移、延迟和成本更高，也更难稳定回归。当前采用 rule-first + optional classifier 平衡确定性与扩展性。

## 5. Routing（路由）与 Coordinator（协调器）

> 源码入口：`src/conversation_agent/sales/intent_router.py::IntentRouter`、`src/conversation_agent/orchestration/task_router.py::TaskRouter`、`src/conversation_agent/orchestration/coordinator.py::Coordinator`

### 5.1 为什么保留两个 Router（路由器）

- IntentRouter 理解用户“想干什么”，输出语义 `IntentResult`（意图识别结果）：`intent` 表示识别出的用户意图，`confidence` 表示意图识别置信度，`reasoning` 表示识别理由，`alternative_intents` 表示备选意图。
- TaskRouter 决定系统“怎么执行”，输出可运行 `TaskRoute`（任务路由结果）：`task` 表示具体执行任务，`confidence` 表示任务路由置信度，`reason` 表示选择该任务的原因。

例如“生成本周销售周报”：Intent（用户意图）是 report（报告）类语义，Task（执行任务）是 `weekly_report`（生成销售周报）。拆分后可独立测试语义分类和执行策略，也允许同一 intent（意图）在不同系统能力下映射不同 task（任务）。

代价是多一个 contract 和 trace 概念，但职责比单一混合 Router 更清晰。

### 5.2 为什么是确定性 Coordinator（协调器）

当前分支少且业务边界明确。显式 Python 控制流可以直接证明 Policy 顺序、task override 位置、fallback trace 和 blocked early return。

没有选择 LLM 自由控制全流程，是因为它会增加路径不确定性、测试复杂度、延迟和安全审计难度。

当前系统不是 Multi-Agent：Policy、RAG、Sales 和 Writer 是由同一个 Coordinator 调用的模块职责，并不各自拥有独立目标、持久状态、模型循环，也不会彼此发送消息或协商计划。代码中虽然保留 `MockAgent`、`RealAgent` 和多个 LLM client，但 CLI 主链路注入的是 Coordinator，这些类的存在不改变当前运行架构。

### 5.3 与 LangGraph（状态图编排框架）的关系

当前没有使用 LangGraph。若未来出现并行任务、持久 checkpoint（状态检查点）、人工审批、长任务恢复或复杂循环，可把 Policy（安全策略）、Router（路由）、Task（任务）和 Result（结果）映射成 StateGraph（状态图）节点。现在引入会增加依赖，收益有限。

### 5.4 DashScope（阿里云百炼模型服务）默认路径与模型档位

> 源码入口：`src/conversation_agent/llm/models.py::ModelRegistryConfig`（模型注册表配置）、`src/conversation_agent/llm/factory.py::create_llm_client`（创建模型客户端）、`src/conversation_agent/llm/dashscope_client.py::DashScopeClient`（百炼模型客户端）、`src/conversation_agent/agent.py::RealAgent`（真实模型智能体）。

`RealAgent`（真实模型智能体）未注入客户端时，只能通过 `standard`（标准档）创建 `qwen3-8b` 客户端；请求固定 `stream=false`（关闭流式返回），并使用档位绑定的 `enable_thinking=false`（关闭思考模式）。`advanced`（高级档）和 `evaluator`（评测档）配置可由注册表手动解析，但 `runtime_selectable=false`（运行时不可选择）；`lightweight`（轻量档）当前未配置。

`configured`（配置完整）与 `runtime_selectable`（运行时可选择）刻意分开，避免把“存在配置”误写成“已经接入任务路由”。当前没有动态 `ModelRouter`（模型路由器），也没有 8B 到 14B 的自动升级。

模型能力来自项目批准表，而不是根据模型名称推断。`STRUCTURED_OUTPUT`（结构化输出能力）仅表示通过项目当前协议验证，不表示供应商原生保证任意 JSON Schema（JSON 结构约束）。

## 6. External RAG Integration（外部 RAG 集成）

> 源码入口：`src/conversation_agent/rag/base.py::RagClient`、`src/conversation_agent/rag/factory.py`、`src/conversation_agent/rag/external_client.py::ExternalRagClient`

### 6.1 RagClient（RAG 客户端）抽象

Coordinator 只调用：

```python
rag_client.query(question, trace_id=session_id, metadata={...})
```

其中 `question`（知识查询问题）是必填输入，`trace_id`（跨模块追踪标识）用于关联链路，`session_id`（会话标识）作为当前追踪值传入，`metadata`（调用上下文元数据）为后续任务和策略上下文预留。

它不知道 external（外部）、local（本地）或 fallback（降级）分支。Factory（客户端工厂）根据配置组装 client（客户端），FallbackRagClient（RAG 降级客户端）管理降级。这保持了编排层的职责纯度。

### 6.2 Provider（结果提供路径）与错误边界

| Provider | 中文含义 | 产生条件 |
|---|---|---|
| external | 外部知识服务结果 | 外部 `/query`（知识查询接口）成功并完成映射 |
| local | 本地关键词结果 | 配置直接使用 LocalKeywordRagClient |
| fallback | 外部失败后的本地降级结果 | 外部产生 `RagClientError` 后本地成功 |
| none | 没有可用 RAG 结果 | 外部失败且未启用 fallback |

ExternalRagClient（外部 RAG 客户端）区分 timeout（请求超时）、connection error（连接错误）、HTTP error（HTTP 状态错误）、invalid JSON（响应不是有效 JSON）、schema error（响应结构不符合协议）和 missing answer（缺少回答）。FallbackRagClient 只捕获 `RagClientError`（RAG 客户端结构化异常）；其他程序错误不会被吞成 fallback（降级），会继续向上抛出。

### 6.3 为什么 fallback（降级结果）降低 confidence（置信度）

本地关键词检索能力弱于完整 RAG_demo。FallbackRagClient 执行 `min(local_confidence, 0.55)`，其中 `local_confidence` 表示本地 RAG 结果置信度；同时添加固定 warning（降级警告）。0.55 是工程降级上限，不是统计校准概率。

替代方案是直接失败，语义更严格但业务连续性差；或保持原 confidence，会误导用户。当前选择“可用但显式低可信”。

### 6.4 diagnostics（调用诊断）、trace（执行追踪）与 `raw_response`（外部原始响应）

Fallback（降级）结果保留 external failed（外部调用失败）和 local succeeded（本地调用成功）两条 diagnostics（调用诊断）。Coordinator（协调器）将每条 diagnostic 转换成 AgentStep（执行追踪步骤），因此 trace（执行追踪）能解释降级路径。

统一 `RagResult`（RAG 查询结果）的关键字段含义如下：

| 字段 | 中文实际含义 |
|---|---|
| `answer` | 最终知识问答文本 |
| `evidence` | 结构化证据条目集合 |
| `sources` | 面向展示的引用来源集合 |
| `confidence` | 当前 RAG 结果的启发式置信度，不是严格概率 |
| `warnings` | 面向用户和调用方的降级或风险提示 |
| `provider` | 实际提供结果的路径：external、local、fallback 或 none |
| `diagnostics` | 面向 Trace 和调试的逐次 RAG 调用诊断记录 |
| `raw_response` | 外部 RAG 服务的原始响应，仅用于受控调试 |

每条 `RagCallDiagnostic`（RAG 调用诊断）中，`step_name` 表示调用步骤名称，`provider` 表示被调用的提供方，`success` 表示调用是否成功，`error_type` 表示结构化错误类型，`message` 表示诊断说明，`latency_ms` 表示调用耗时毫秒数。

`raw_response` 可能含内部 chunk、路径和 debug trace。它可存在于内存 `RagResult`，但公共输出按以下顺序过滤：

```text
RagResult.to_public_dict(include_raw_response=False)（RAG 结果公共序列化，默认排除原始响应）
-> OrchestrationResult.to_public_dict(...)（编排结果公共序列化）
-> CLI _orchestration_to_public_dict(...)（命令行最终公共输出过滤）
```

CLI 从 `get_config().rag_service.include_raw_response` 读取开关，默认不公开。该措施只控制公共序列化输出，不等同完整隐私治理或日志审计。

### 6.5 RAG 配置边界

> 配置入口：`src/conversation_agent/config.py::RagServiceConfig`

| 配置字段 | 中文实际含义 | 环境变量 | 默认值 |
|---|---|---|---|
| `provider` | RAG 提供方模式，选择 external 或 local | `CONVAGENT_RAG_PROVIDER` | `external` |
| `base_url` | 外部 RAG 服务基础地址 | `CONVAGENT_RAG_BASE_URL` | `http://127.0.0.1:8001` |
| `timeout_seconds` | 外部 RAG 请求超时时间，单位为秒 | `CONVAGENT_RAG_TIMEOUT_SECONDS` | `30.0` |
| `fallback_to_local` | 外部 RAG 失败后是否允许使用本地关键词降级 | `CONVAGENT_RAG_FALLBACK_TO_LOCAL` | `true` |
| `include_raw_response` | 公共 JSON 输出是否包含外部原始响应 | `CONVAGENT_RAG_INCLUDE_RAW_RESPONSE` | `false` |

## 7. Evaluation（评测）设计

> 源码入口：`src/conversation_agent/evaluation/rag_adapter.py`、`src/conversation_agent/evaluation/policy_boundary.py`

| Evaluation | 中文含义 | 证明什么 | 不证明什么 |
|---|---|---|---|
| `rag-adapter` | RAG 适配器可靠性评测 | adapter mapping（响应映射）、provider（结果提供路径）、fallback（降级）、citation/evidence contract（引用与证据协议）、hard gate（前置硬门）、raw output control（原始输出控制） | 真实召回率、rerank（重排序）质量、答案事实正确性、线上 SLA（服务等级目标） |
| `policy-boundary` | 业务安全边界评测 | 固定 BLOCKED/UNCERTAIN/SAFE case（样例）、hard gate（前置硬门）、类别触达 | 所有语义变体、对抗绕过、线上误报漏报 |

### 7.1 为什么使用 fake clients（模拟客户端）

Evaluation（评测）关注编排边界，需要稳定复现 external success（外部调用成功）、timeout（请求超时）、fallback（降级）和泄露场景。Fake clients（模拟客户端）让每次执行具有同样输入和可观察结果。真实联调仍然必要，但属于另一类评测。

### 7.2 Gate（评测门槛）

**RAG Adapter Evaluation（RAG 适配器评测）**

| 指标要求 | 中文实际含义 | 不满足时 |
|---|---|---|
| `blocked_no_rag_call_rate == 1.0` | 被阻断请求未调用 RAG 的比例必须为 100% | FAIL（评测失败） |
| 默认 `raw_response_exposure_rate`（原始响应暴露率）`== 0.0` | 默认公共输出暴露原始响应的比例必须为 0 | FAIL（评测失败） |
| `citation_coverage`（引用覆盖率）`>= 0.5` | 非阻断回答包含引用来源的覆盖率至少为 50% | WARNING（评测警告） |
| `average_confidence`（平均结果置信度）`>= 0.3` | 非阻断结果的平均置信度至少为 0.3 | WARNING（评测警告） |

**Policy Boundary Evaluation（策略边界评测）**

| 指标要求 | 中文实际含义 | 不满足时 |
|---|---|---|
| `blocked_no_rag_call_rate == 1.0` | 被阻断请求未调用 RAG 的比例必须为 100% | FAIL（评测失败） |
| `blocked_detection_rate`（阻断识别率）`>= 0.9` | 预期阻断样例的正确识别率至少为 90% | FAIL（评测失败） |
| `uncertain_detection_rate`（不确定状态识别率）`>= 0.8` | 预期不确定样例的正确识别率至少为 80% | WARNING（评测警告） |
| `safe_pass_rate`（安全放行率）`>= 0.8` | 预期安全样例的正确放行率至少为 80% | WARNING（评测警告） |
| `business_boundary_coverage`（业务边界覆盖数量）`>= 4` | 评测触达的业务风险类别数量至少为 4 类 | WARNING（评测警告） |

这些 Gate 来自固定 case 的项目回归评测，不是生产 SLO（服务等级目标），也不证明真实 RAG 质量或完整语义安全覆盖。

## 8. 架构替代方案汇总

| 当前选择 | 替代方案 | 当前选择理由 | 当前代价 | 何时考虑替代 |
|---|---|---|---|---|
| 确定性 Coordinator（协调器） | LangGraph（状态图编排框架） | 流程短、分支固定、易测试 | 缺少 checkpoint（状态检查点）和复杂图能力 | 出现长任务、并行、人工审批或恢复需求 |
| Rule-first（规则优先）+ optional classifier（可选分类器） | 纯 LLM Policy（大模型安全策略） | 稳定、可解释、可回归 | 语义变体召回有限 | 有校准分类器和完整语义评测集 |
| External RAG（外部知识引擎） | 内嵌 RAG | 职责和部署边界清晰 | 增加 HTTP 依赖 | 单体部署收益超过解耦收益 |
| Local fallback（本地降级） | 直接失败 | 保持有限业务连续性 | 答案能力较弱 | 高风险场景必须 fail-closed（失败时默认阻断） |
| 一个 Coordinator（协调器）+ Modules（功能模块） | Multi-Agent（多智能体）对话 | 延迟、成本和控制流更可控 | 专家自治能力有限 | 任务确实需要独立状态、目标和协商 |

## 9. Trace（执行追踪）与可解释性

> 源码入口：`src/conversation_agent/orchestration/models.py::AgentStep`、`src/conversation_agent/orchestration/coordinator.py::_run_qa`

Policy（安全策略）、Router（路由器）、RAG（检索增强生成）、Sales（销售分析）和 Writer（内容写作）都产生 AgentStep（执行追踪步骤）。RAG diagnostics（RAG 调用诊断）在 Coordinator（协调器）内转换为 trace（执行追踪），因此 provider（结果提供方）客户端不依赖 orchestration model（编排数据模型）。

当前 trace（执行追踪）适合 CLI（命令行界面）调试和测试，但没有分布式 trace backend（追踪后端）、跨服务 span（调用跨度）、持久审计或敏感字段脱敏。生产化应接入 OpenTelemetry（开放式遥测框架）或等价设施。

## 10. 当前边界与生产化

当前项目是结构完整的工程作品，不是商用生产服务：

- M1.2（里程碑 1.2）已提供同步 FastAPI（Web 应用接口框架）服务边界，但没有后台 worker（任务进程）、真实认证授权或持久运行仓库。
- JSON Store（JSON 数据存储）无数据库事务、并发锁和租户隔离。
- 没有真实高并发、长时间稳定性或故障注入结果。
- LocalKeywordRagClient（本地关键词 RAG 客户端）不是完整 RAG。
- rule-first（规则优先）不能覆盖全部语义改写，默认 classifier（分类器）未配置。
- Evaluation（评测）使用固定 cases（样例）和 fake clients（模拟客户端）。
- RealAgent（真实模型智能体）和 LLM clients（大语言模型客户端）存在，但不是 Coordinator（协调器）主入口。
- DashScopeClient（阿里云百炼模型客户端）是 RealAgent 的默认供应商适配器，但当前 CLI（命令行界面）业务主链路仍由 Coordinator 执行。
- ModelRouteDecision（模型路由决定）仍未接入动态路由；UserRequest（用户请求）和 RequestContext（请求上下文）已由最小 Application Service（应用服务层）消费。

### 10.1 M1.2（里程碑 1.2）已实现的可信 HTTP（超文本传输协议）服务边界

**源码入口**：`api/app.py::create_app`（创建 FastAPI 应用）、`runtime/builder.py::RequestContextBuilder`（构建服务端可信上下文）、`application/service.py::ChatService`（同步应用用例）和 `orchestration/coordinator.py::Coordinator.run`（协调器主执行入口）。详细字段、对象变化和测试映射见 `docs/project-walkthrough.md`（项目深度理解与面试复盘手册）。

#### 10.1.1 HTTP 服务职责

FastAPI（Web 应用接口框架）公开 `GET /healthz`（进程存活检查）、`POST /v1/chat`（同步通用对话）和 `POST /v1/qa`（同步知识问答）。HTTP Route（接口路由）只负责请求解析与 schema validation（数据结构校验）、调用应用服务、公开响应映射和错误映射，不直接执行 Policy（安全策略）、RAG（检索增强生成）、工具调用或 Coordinator（协调器）内部编排。

```text
FastAPI Route（Web 接口路由）
→ RequestContextBuilder（请求上下文构建器）
→ ChatService（对话应用服务）
→ Coordinator（协调器）
```

#### 10.1.2 可信 RequestContext（请求上下文）

外部请求只能提交 UserRequest（用户请求）的 `text`（用户文本）、`task_override`（可选任务覆盖）和 `session_id`（可选会话标识）。`request_id`（请求标识）、`trace_id`（追踪标识）、`received_at`（接收时间）、Principal（可信主体）和 RuntimeVersionSnapshot（运行版本快照）由服务端构造。`roles`（角色集合）、`permissions`（权限集合）、ResourceScope（资源范围）、`tenant_id`（租户标识）和 AuthorizationDecision（授权决定）不是请求体字段；同名输入会被 `extra="forbid"`（拒绝额外字段）规则拦截。

核心不变量是“外部请求不等于可信运行上下文”。`create_development_context_builder()`（创建开发上下文构建器）只用于 `demo`（演示）模式且请求完全没有 Authorization Header（授权请求头）的兼容路径；`test`（测试）和 `production`（生产）模式必须使用经过 JWT Bearer（JWT 持有者令牌）验证和确定性授权的 Principal（可信主体）。

#### 10.1.3 Policy-first（策略优先）兼容映射

ChatService（对话应用服务）构造并保留完整 RequestContext（请求上下文），再单向投影 frozen `OrchestrationRequestMetadata(request_id, trace_id, session_id)`，连同 `text` 和最终任务覆盖传给 `Coordinator.run()`。该投影不是第二套可信上下文，也不是完整 ExecutionContext；唯一可信源仍是 RequestContext。Coordinator 不依赖 FastAPI Request、HTTP Header、JWT、OIDC、数据库会话或 HTTP 状态码。

Phase 14 删除了 Coordinator 的 `_current_session_id` 和 `_current_policy_status` 请求级实例状态。metadata 与结构化 Policy 状态通过调用参数显式传播，Podcast 内部 QA 复用同一投影，RAG `trace_id` 使用请求真实 trace，而不是 session。共享 Coordinator 的 Barrier/Event 并发测试覆盖 SAFE/SAFE、SAFE/UNCERTAIN、BLOCKED/SAFE、异常隔离和同 session 不同 trace。

Coordinator 仍然首先执行 PolicyEngine（安全策略引擎）。`BLOCKED`（阻断执行）会立即返回受控业务结果，RAG、Writer（内容写作）、Sales（销售分析）和模型工具链不会继续执行；API 验收确认该链路的 trace（执行追踪）只有 `policy_engine`（策略引擎步骤）。这与未来的 `403`（已认证但无权限）不同：授权拒绝应在进入 Coordinator 前结束，Policy BLOCKED 则表示已获准调用系统，但业务安全策略拒绝处理内容。

#### 10.1.4 QA（知识问答）与服务端任务约束

`/v1/qa` 在应用服务调用前强制 `forced_task="qa"`（服务端强制知识问答任务），优先级高于请求体中的 `task_override`（客户端任务覆盖）和用户文本；客户端不能把该专用接口改写成 Writer 或 Sales 任务。`/v1/chat` 使用经过 TaskName（任务名称枚举）校验的可选任务覆盖，否则进入现有通用路由链。

FastAPI 层不承担 retrieval（检索）、rerank（重排）或 citation（引用）生成；QA 仍通过 Coordinator 和 RagClient（RAG 客户端协议）访问本地或外部知识能力。

#### 10.1.5 敏感响应与错误隔离

API 不对内部对象直接调用裸 `model_dump()`（模型完整序列化），而是沿用 OrchestrationResult.to_public_dict()（编排结果公共序列化方法）和 RagResult.to_public_dict()（RAG 结果公共序列化方法）。因此 `raw_response`（供应商原始响应）默认不公开；公开 trace 仅包含当前 AgentStep（执行步骤）的摘要字段，不包含 Python traceback（异常堆栈）、接口密钥或原始供应商载荷。

M1.2 统一处理 `422`（请求或 Contract 校验失败）和 `500`（应用执行失败）；M1.3 增加 `400`（Bearer 请求格式错误）、`401`（缺少或无效认证）、`403`（权限不足）和 `503`（认证材料服务不可用）。APIErrorResponse（应用接口错误响应）返回稳定错误码、公共消息、请求标识和追踪标识，不暴露原始内部异常。

#### 10.1.6 成熟度与限制

| 维度 | 当前状态 | 实现证据 | 当前限制 |
|---|---|---|---|
| HTTP 服务边界 | 已实现 | FastAPI Route 只负责解析、调用和公开响应映射 | `/healthz` 仅证明进程存活 |
| 可信 RequestContext | 已实现 | RequestContextBuilder 在服务端生成标识、时间、主体和版本快照 | — |
| JWT Bearer 认证 (M1.3) | 已实现 | BearerTokenParser + JWTVerifier + JOSE Header Policy + Claims 验证 | 仅 RS256；无 OIDC 登录/Discovery |
| JWKS Provider (M1.3) | 已实现 | Static/Remote async + single-flight + negative-kid + transactional refresh | 进程内缓存；无多实例协调 |
| Principal 映射 (M1.3) | 已实现 | PrincipalMappingPolicy: VerifiedClaims → Principal | 单租户；无实时撤销 |
| 确定性授权 (M1.3) | 已实现 | RBAC/ABAC conservative_route_union | 代码常量权限表；无动态后台 |
| Policy-first 编排 | 已实现 | Coordinator 首先执行 Policy，BLOCKED 后立即停止 | — |
| RAG/QA API | 已实现 | `/v1/qa` 在服务端强制 QA 路由 | 依赖现有 RAG Adapter |
| 服务端任务约束 | 已实现 | `forced_task` 优先于客户端 task_override | 无细粒度任务权限 |
| 敏感字段隔离 | 已实现 | ResponseProjector config + permission 双重 Gate | — |
| PostgreSQL Schema Contract（PostgreSQL 结构契约） | 已实现并真实验证 | ORM Metadata（对象关系映射元数据）、`0001` Migration（初始迁移）、`compare_metadata`（元数据差异比较）和手工 Schema signature（结构签名）共同验证 | Schema 仍冻结在 `0001`（初始迁移） |
| M1.4-C execution persistence（执行持久化） | 已实现、接线并真实验证 | `SQLAlchemyExecutionRepository`（SQLAlchemy 执行仓储）、`SQLAlchemyExecutionUnitOfWork`（SQLAlchemy 执行工作单元）和 `DurableApplicationService`（持久化应用服务）完成两段短事务 | 外部副作用不在数据库事务内 |
| M1.4-D persistent idempotency（持久幂等） | 已实现、接线并真实验证 | 原子 Scoped Claim（作用域声明）、数据库时间 Lease（租约）、Fingerprint Conflict（指纹冲突）、Replay（结果重放）、Fencing（执行权隔离）和请求驱动 Reclaim（回收） | 无心跳或后台孤儿清理器 |
| M1.4-F operational readiness（运维就绪） | 已实现并本地真实验证 | Doctor（诊断器）、Integrity Checker（完整性检查器）、受保护 Prune（清理）、进程崩溃、多实例、数据库中断、权限与备份恢复演练 | 远程 CI 未运行；生产备份基础设施未部署 |

M1.4 已完成 FastAPI durable wiring（FastAPI 持久化接线）、HTTP idempotency（HTTP 幂等）和本地 operational readiness（运维就绪）验证。后续方向包括真实 IdP（身份提供商）联调、Token introspection/revocation（令牌内省/撤销）、限流、PII（个人身份信息）治理、真实 RAG 联调、规则配置中心、classifier（分类器）校准和红队评测。完整路线见 [产品化路线](sme-productization-roadmap.md)。

## M1.4 持久 HTTP 主链与恢复边界

`RequestExecutionGateway`（请求执行网关）在当前 AuthN（身份认证）和 AuthZ（权限授权）成功后选择执行方式：无 `Idempotency-Key`（幂等键）的 `optional`（可选）请求进入 `DurableApplicationService`（持久应用服务）；有键请求进入 `IdempotentDurableApplicationService`（幂等持久应用服务）。`NULL`（空持久化）模式收到键时返回 `idempotency_unavailable`（幂等能力不可用），不会静默忽略。

```text
FastAPI Route（接口路由）
→ RequestContext（可信请求上下文）
→ IdempotencyKeyParser（幂等键解析器）
→ RequestExecutionGateway（请求执行网关）
→ Transaction A（请求接收短事务）
→ Coordinator（事务外编排）
→ Transaction B（结果终结短事务）
→ ResponseProjector（公开响应投影器）
```

`/healthz`（存活探针）只说明进程存活；`/readyz`（就绪探针）在 PostgreSQL 模式检查数据库连接和 `0001` Alembic revision（迁移版本）。启动不执行 `create_all`（自动建表）或 Alembic upgrade（自动迁移）。投影失败发生在 Transaction B 已提交之后时，数据库保持 `COMPLETED`（已完成），同键重试从 Snapshot（快照）重放并再次执行当前投影器。

## 11. 验证基线

```text
Final validation（最终验证）：
- python3 -m compileall -q src tests scripts: passed（源码、测试与脚本编译检查通过）
- uv run --frozen --extra dev python -m pytest --collect-only -q: 663 collected（收集 663 个测试节点）
- uv run --frozen --extra dev python -m pytest -m unit -q: 582 passed, 81 deselected（582 项单元测试通过，81 项未选择）
- default full suite（默认全量测试）: 582 passed, 81 skipped（582 项通过，81 项显式集成测试跳过）
- main suite with real PostgreSQL（包含真实数据库的主回归）: 656 passed, 1 skipped, 6 operational deselected（656 项通过，1 项联网冒烟测试跳过，6 项运维测试独立执行）
- PostgreSQL 17 non-destructive contract（非破坏性数据库契约）: 72 passed, 2 skipped（72 项通过，2 项破坏性用例跳过）
- PostgreSQL 17 destructive persistence contract（破坏性持久化契约）: 74 passed, 0 skipped（74 项全部通过）
- PostgreSQL 17 operational contract（运维契约）: 6 passed, 0 skipped（6 项全部通过）
- logical backup and fresh restore drill（逻辑备份与全新库恢复演练）: PASS（通过）
- convagent eval policy-boundary: Status PASS（策略边界评测状态通过）
- convagent eval rag-adapter: Status PASS（RAG 适配器评测状态通过）
- business blocked request trace only contains policy_engine（业务阻断请求的追踪仅含策略引擎步骤）
- RAG external unavailable falls back to local keyword RAG with warning and lowered confidence（外部 RAG 不可用时降级到本地关键词 RAG，并附带警告和较低置信度）
```

当前结果证明 M1.4 PostgreSQL 持久化链及本地恢复门通过，不等于生产备份、外部副作用 exactly-once（严格一次）或真实企业部署已经完成。M1.1 至 M1.4-E 的六套 Node ID（测试节点标识）基线均零缺失；当前收集 663 个节点。PostgreSQL 与 Operational CI Job（运维持续集成任务）已实现且 YAML（配置语言）静态验证通过，但本环境未运行真实 GitHub Actions。

## 12. M1.3（里程碑 1.3）认证与授权边界

### 12.1 完整受保护请求链

**[源码确认]** 当前受保护请求链为：

```text
RequestMetadata（服务端生成 request_id, trace_id, received_at）
  → BearerTokenParser（严格单 Bearer ASCII 凭证解析；无 Header → demo placeholder）
  → JOSE Header Policy（alg=RS256 硬编码，拒绝 jku/x5u/jwk/x5c/crit，验证 kid）
  → JWKS Lookup（Static/Remote async；single-flight；negative-kid cache；transactional refresh）
  → JWT Signature & Claims Verification（RS256 签名；iss/aud/sub/exp/iat/nbf 严格验证）
  → VerifiedClaims（不可变签名后声明）
  → PrincipalMappingPolicy（VerifiedClaims → Principal；tenant/org 由服务端配置决定）
  → AuthorizationService（Principal roles → permissions 并集；conservative_route_union；
     disabled principal → denied）
  → AuthorizationDecision（不可变授权快照：allowed/code/permissions/resource_scopes）
  → RequestContextBuilder（Metadata + Principal + Authorization + VersionSnapshot → RequestContext）
  → ChatService（RequestContext + UserRequest → Coordinator）
  → Coordinator（Policy → Routing → Task Execution → OrchestrationResult）
  → ResponseProjector（ApplicationResult → PublicAgentResponse；config + permission 双重 Gate）
```

Coordinator（协调器）仍然只消费经过应用层映射的业务输入（text、session_id、task_override），不感知 HTTP Header、JWT、JWKS、OIDC 配置、Authorization Policy 或数据库 Session。

### 12.2 Authentication ≠ Authorization ≠ Policy

这是三个独立的安全层，依次执行：

**Authentication（认证）** — 验证 "你是谁"：
- 入口：`BearerTokenParser` → `JWTVerifier.verify()`
- 输入：ASGI raw headers → Bearer Token
- 输出：`VerifiedClaims`（签名验证后的不可变声明）
- 失败：400（malformed）或 401（invalid token）或 503（JWKS unavailable）
- 源码：`identity/authentication.py::BearerTokenParser`, `JWTVerifier`

**Authorization（授权）** — 决定 "你能调用什么"：
- 入口：`AuthorizationService.authorize()`
- 输入：`Principal`（来自 VerifiedClaims）+ Route required permissions
- 输出：`AuthorizationDecision`（allowed + effective permissions + resource_scopes）
- 失败：403（disabled principal 或 missing permissions）
- 策略：conservative_route_union — 所有角色权限的确定性并集
- 源码：`authorization/service.py::AuthorizationService`

**Policy（业务安全策略）** — 判断 "内容是否安全"：
- 入口：`PolicyEngine.decide()`（Coordinator.run() 的第一步）
- 输入：用户文本
- 输出：`PolicyDecision`（SAFE / UNCERTAIN / BLOCKED）
- 失败（BLOCKED）：HTTP 200 业务拒绝，不执行后续任务
- 源码：`policy/engine.py::PolicyEngine`, `policy/rules.py`

**关键区别：**
- Authenticated ≠ Authorized：认证成功但权限不足 → 403
- Authorized ≠ Policy-safe：授权通过但内容触发业务安全规则 → HTTP 200 + policy_engine trace
- Coordinator 不感知 AuthN/AuthZ 失败：401/403/503 在 `RequestSecurityService.secure()` 中抛出，Coordinator call_count = 0
- Policy BLOCKED ≠ HTTP 403：BLOCKED 是已认证已授权用户提交的风险内容被业务规则拒绝

### 12.3 长期架构不变量

| 不变量 | 实现方式 | 验证 |
|---|---|---|
| Coordinator 不感知 HTTP/JWT/JWKS/权限策略 | ChatService 在调用 Coordinator 前完成所有安全处理 | Coordinator.call_count = 0 测试 |
| RequestContext 只由服务端构建 | RequestContextBuilder 注入，请求体 extra="forbid" | test_api.py |
| 客户端不能注入 Principal 或 AuthorizationDecision | UserRequest 不存在这些字段 | test_productization_contracts.py |
| AuthN/AuthZ 失败时 Coordinator 不执行 | SecurityBoundaryError 在 secure() 抛出 | test_security.py |
| Policy BLOCKED 已进入 Coordinator 但不执行后续任务 | Coordinator.run() 在 Policy 后立即返回 | test_orchestration.py |
| raw_response 通过配置和权限双 Gate | ResponseProjector + AuthorizationDecision | test_security.py |
| 当前单租户、单受控组织 | OIDCConfig.tenant_id, expected_organization_id | PrincipalMappingPolicy |
| Demo 占位身份仅无 Header 时使用 | RequestSecurityService._runtime_mode == "demo" | test_security.py |
| 无效 Token 不回退开发身份 | AuthenticationFailure → SecurityBoundaryError | test_security.py |

### 12.4 技术决策

| 问题 | 当前方案 | 设计理由 | 当前边界 |
|---|---|---|---|
| JWT 算法 | 服务端固定 `RS256` | 不信任令牌头决定算法 | 暂不支持其他算法 |
| JWKS | 异步流式、大小限制、严格解析、事务式缓存替换 | 防阻塞、超量和半更新 | 进程内缓存 |
| 身份来源 | `VerifiedClaims` → `Principal` | 原始声明不能直接成为授权结果 | 无实时撤销 |
| 授权 | 固定版本 RBAC/ABAC | 稳定、可解释、可回归 | 无动态权限后台 |
| 调试输出 | config + permission 双重 Gate | 防止客户端自行开启敏感输出 | OpenAPI 显示可选 debug 字段 |

`demo` 模式只有在请求完全没有 `Authorization` Header 时才使用开发占位主体；一旦提交 Bearer Token，任何格式、签名或 JWKS 错误都不会回退为开发身份。`test` 和 `production` 模式禁止隐式占位主体。

授权拒绝发生在 Coordinator 之前；Policy `BLOCKED` 发生在已认证、已授权并进入 Coordinator 之后。因此 `403`（权限不足）与 Policy BLOCKED 保持不同语义。

M1.3 是 OIDC-compatible JWT Bearer resource server 边界，不是完整 OIDC 登录系统，也不代表真实企业 IdP 已经联调。当前系统是单租户、单受控组织模式；`oidc_login_status`、`enterprise_idp_integration`、`token_introspection`、`real_time_revocation`、`persistent_audit` 均为 `not_implemented`。
