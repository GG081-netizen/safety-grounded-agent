# test_demo（安全可信的智能体编排项目）深度理解与面试复盘手册

> Phase 0-13（阶段 0 至阶段 13）架构演进、代码实现、执行链路、评测指标与技术取舍

## 0. 如何使用这份文档

这不是产品宣传页，而是源码复习手册。建议先读第 1、3、4 章形成全局图，再按附录 A 的顺序对照源码，最后使用附录 B 做口述自测。

本文使用四类事实标记：

- **[源码确认]**：当前代码直接实现。
- **[测试确认]**：当前 pytest（Python 自动化测试框架）、CLI（命令行界面）或 deterministic evaluation（确定性评测）已验证。
- **[演进归纳]**：根据现有模块对 Phase 0-10（阶段 0 至阶段 10）做的合理复盘，不代表精确提交历史。
- **[未来方向]**：尚未实现，只是生产化或优化路径。

英文源码字段保留原名，并在首次定义、字段表或紧邻文本中说明中文实际含义；类名、函数名、源码路径、CLI（命令行界面）命令和环境变量保持英文，以便直接回查代码。

## 1. 先用一句话讲清楚项目

### 1.1 一句话定位

`test_demo`（当前智能体编排项目）是面向 B2B（企业对企业）IT（信息技术）设备和企业办公采购售前场景的 **Safety-Grounded Agent Orchestration Layer（以安全策略为基础的智能体编排层）**：它在业务任务执行前完成安全判断，再进行语义路由、任务路由、业务执行、外部 RAG（检索增强生成）调用与可追踪输出。

### 1.2 三十秒定位

项目把企业 Agent（智能体）分成 Policy（安全策略）、Routing（路由）、Coordinator（协调器）、Business Workflow（业务工作流）、RAG Adapter（RAG 适配器）和 Trace（执行追踪）。高风险请求先由 Business Safety Firewall（业务安全防火墙）拦截；安全请求再由 IntentRouter（意图路由器）理解意图、TaskRouter（任务路由器）选择执行任务。知识问答通过 `RagClient`（RAG 客户端协议）调用外部 `RAG_demo`（企业知识引擎），故障时可降级到本地关键词 RAG，并明确降低置信度、记录 warning（警告）和两步 trace。

### 1.3 项目不是什么

- 不处理企业采购、销售、售前和知识问答之外的行业业务规则。
- 不是完整 RAG 引擎；检索、rerank（重排序）、grounded generation（基于证据的生成）和 answerability（可回答性判断）属于 `RAG_demo`。
- 不是让多个 LLM（大语言模型）Agent 自由对话的系统；主链路由确定性 `Coordinator` 编排。
- 不是生产级 API（应用程序接口）服务；当前主要入口是 Click CLI（Click 命令行界面），存储是本地 JSON（结构化数据格式）。
- 不是“当前自动化测试通过所以已经生产可用”的系统。

### 1.4 test_demo 与 RAG_demo 的边界

| 系统 | 负责 | 不负责 |
|---|---|---|
| `test_demo` | Policy（安全策略）、Intent/Task Routing（意图与任务路由）、Coordinator（协调器）、Sales/Writer Workflow（销售与写作工作流）、Trace（执行追踪）、RAG Adapter（RAG 适配器）、Evaluation（评测） | 向量检索、rerank（重排序）、完整知识库治理 |
| `RAG_demo` | Retrieval（检索）、Rerank（重排序）、Grounded QA（基于证据的问答）、Citation（引用）、Answerability（可回答性判断） | 当前 Agent（智能体）的业务路由、安全硬门和销售流程 |

## 2. 业务场景与需求来源

### 2.1 为什么选择 B2B（企业对企业）IT（信息技术）采购售前

该场景同时存在结构化客户资料、长周期销售动作、企业知识查询、内容生成和高风险承诺，因此适合验证“安全先行 + 确定性编排 + 外部知识引擎”的组合。

### 2.2 系统用户与任务

主要用户是销售、售前和采购支持人员。典型任务包括客户进展分析、采购规则问答、周报、跟进邮件、播客脚本和客户资料查询。

### 2.3 业务痛点

- 业务事实散落，回答缺乏来源。
- 销售人员容易给出绝对交付、库存或 SLA（服务等级协议）承诺。
- 隐私查询、敏感属性推断和专业判断不应进入生成链路。
- 外部知识服务失败时需要可解释降级，而不是静默失败。
- 面试和审计需要知道每一步做了什么，而不仅是最终文本。

### 2.4 与目标 JD（岗位职责说明）的技术映射

| JD（岗位职责说明）能力 | 项目证据 |
|---|---|
| Agent（智能体）编排 | `Coordinator`（协调器）、IntentRouter（意图路由器）、TaskRouter（任务路由器）、task modules（任务模块） |
| RAG（检索增强生成）接入 | `RagClient`（RAG 客户端协议）、External/Local/Fallback clients（外部、本地与降级客户端） |
| 意图防火墙 | Policy-first（策略优先）、BLOCKED hard gate（阻断前置硬门） |
| 可解释性 | PolicyDecision（安全策略决策）、diagnostics（调用诊断）、AgentStep trace（执行步骤追踪） |
| 多任务 | QA（问答）、sales analysis（销售分析）、weekly report（周报）、podcast（播客脚本）、email（邮件） |
| 工程评测 | `rag-adapter`、`policy-boundary` |

## 3. 从输入到输出的完整主链路

```text
Click CLI（Click 命令行入口）
  -> Coordinator.run()（协调器主入口）
  -> PolicyEngine.decide()（安全策略决策）
     -> BLOCKED: immediate return（阻断并立即返回）
     -> SAFE/UNCERTAIN: continue（安全或不确定状态继续执行）
  -> IntentRouter.route()（识别用户意图）
  -> TaskRouter.route()（选择执行任务）
  -> optional task_override（可选的显式任务覆盖）
  -> task executor（任务执行器）
  -> RAG / Sales / Writer module（知识问答、销售分析或内容写作模块）
  -> AgentStep trace aggregation（聚合智能体步骤形式的执行追踪）
  -> OrchestrationResult（编排结果对象）
  -> to_public_dict() / human-readable output（公共字典或人类可读输出）
```

**[源码确认]** `task_override` 发生在 Policy 和两个 Router 之后，因此 `convagent qa` 能固定执行 QA，但不能绕过安全硬门。

### 3.1 HTTP API（超文本传输协议应用接口）入口

**[源码确认]** M1.2（里程碑 1.2）的同步服务链为 FastAPI Route（Web 接口路由）→ RequestContextBuilder（请求上下文构建器）→ ChatService（对话应用服务）→ Coordinator（协调器）。`/v1/chat`（通用对话接口）执行外部 UserRequest（用户请求）；`/v1/qa`（知识问答接口）额外强制 `task_override=qa`（任务覆盖为知识问答）。

请求中不能携带 `tenant_id`（租户标识）、`roles`（角色集合）或 `permissions`（权限集合）。`request_id`（请求标识）、`trace_id`（追踪标识）、Principal（可信主体）和 RuntimeVersionSnapshot（运行版本快照）由服务端创建。当前主体是开发占位身份，不是 OIDC（开放身份连接协议）认证结果。

**[源码确认]** ChatService 构建完整 RequestContext（请求上下文），但为了保持旧 Coordinator 接口兼容，目前只向 `Coordinator.run()` 传递 `text`（用户文本）、`session_id`（会话标识）和任务覆盖。Principal、版本快照和幂等键保留在 ApplicationResult（应用结果）的上下文中，尚未参与 Coordinator 决策。

| 阶段 | 输入对象 | 输出对象 | 关键变化 | 失败行为 |
|---|---|---|---|---|
| FastAPI Route | JSON（结构化请求数据） | UserRequest（用户请求） | 拒绝额外身份和权限字段 | 校验失败返回 `422`（请求验证失败） |
| RequestContextBuilder | 服务端主体、版本和请求元数据 | RequestContext（请求上下文） | 生成请求、追踪、会话标识和 UTC（协调世界时）时间 | Contract 不变量失败时不进入协调器 |
| ChatService | UserRequest 与 RequestContext | ApplicationResult（应用结果） | 应用服务映射任务与会话到协调器 | 内部异常包装为 `500`（应用执行失败） |
| Coordinator | 用户文本、会话标识、任务覆盖 | OrchestrationResult（编排结果） | 先执行 Policy，再路由和执行模块 | BLOCKED（阻断执行）立即返回 |
| Public serialization（公共序列化） | ApplicationResult | AgentResponse（智能体响应） | 加入请求/追踪标识并默认移除 `raw_response`（原始响应） | 不输出内部异常对象 |

**[测试确认]** `test_api.py`（应用接口测试）证明 `/v1/qa` 的服务端任务约束高于客户端覆盖、身份字段伪造返回 `422`、公共 RAG 结果不含 `raw_response`，且应用异常消息不泄露内部详情。业务 BLOCKED API（阻断接口请求）的真实冒烟结果只包含 `policy_engine`（策略引擎步骤）。

### 3.2 CLI（命令行界面）入口

`src/conversation_agent/cli/main.py` 的 `_setup_context()` 创建 `Coordinator`。`chat` 自动路由；`qa` 传 `task_override="qa"`；`report weekly` 传 `weekly_report`。JSON 输出统一经过 `_orchestration_to_public_dict()`。

### 3.3 Policy-first（策略优先）

`Coordinator.run()` 首先调用 `PolicyEngine.decide()` 并立即写入 `policy_engine` trace。BLOCKED 直接构造 `OrchestrationResult` 返回；SAFE 和 UNCERTAIN 才继续。

### 3.4 双 Router（双路由器）

- `IntentRouter` 回答“用户想干什么”，输出 `IntentResult`。
- `TaskRouter` 回答“系统如何执行”，输出 `TaskRoute`。

二者是串行关系。TaskRouter 同时读取文本和可选 IntentResult；任务显式覆盖只替换 TaskRoute，不替换 IntentResult。

### 3.5 Task Execution（任务执行）

`_execute_task()`（任务分发函数）根据 task（任务类型）分派到 QA（问答）、周报、播客、邮件或默认销售分析。当前不是通用 Planner（规划器），也没有动态生成任意 DAG（有向无环图）。

### 3.6 Trace（执行追踪）与输出

每个模块追加 `AgentStep`。最终 `OrchestrationResult` 同时保存 policy、intent、task、RAG 结果、citations、confidence、trace 和 metadata。普通 CLI 只展示摘要；JSON 展示结构化结果。

## 4. 七条必须掌握的执行链路

### 4.1 正常 external QA（外部知识问答）

```text
convagent qa（知识问答命令） -> Coordinator（协调器） -> Policy SAFE（策略安全放行） -> IntentRouter（意图路由器） -> TaskRouter（任务路由器）
-> task_override=qa（强制知识问答任务） -> RagClient（RAG 客户端协议） -> ExternalRagClient（外部 RAG 客户端） -> POST /query（发送知识查询请求）
-> response mapping（响应映射） -> RagResult（RAG 结果） -> diagnostics（调用诊断） -> AgentStep（执行步骤） -> public output（公共输出）
```

| 阶段 | 输入 | 输出/字段变化 | 失败与后续 |
|---|---|---|---|
| CLI | question | `Coordinator.run(..., task_override="qa")` | 空输入退出 |
| Policy | text | `PolicyDecision(SAFE)` | BLOCKED 时停止 |
| Router（路由阶段） | text（用户文本）+ intent（用户意图） | IntentResult（意图结果）、TaskRoute（任务路由结果） | override（显式覆盖）最终设为 qa（问答任务） |
| External（外部调用阶段） | question（知识问题） | POST（HTTP 提交请求）`{question}` | timeout/connection/http/json/schema/missing answer（超时、连接、HTTP、JSON、结构和缺少回答错误）转结构化错误 |
| Mapping（响应映射阶段） | response dict（外部响应字典） | `RagResult(provider="external")`，即结果提供方为外部 RAG | citation（引用）宽松归一化 |
| Trace | `diagnostics`（调用诊断列表） | `external_rag_query`（外部 RAG 查询）步骤 | 无 diagnostics 时使用 `rag_query`（通用 RAG 查询）步骤 |
| Output（输出阶段） | OrchestrationResult（编排结果） | 默认隐藏 `raw_response`（外部原始响应） | 配置开启才公开 |

### 4.2 External failure（外部失败）后的 local fallback（本地降级）

```text
ExternalRagClient error（外部客户端错误） -> RagClientError（结构化 RAG 客户端异常） -> FallbackRagClient（降级客户端）
-> LocalKeywordRagClient（本地关键词客户端） -> provider=fallback（结果路径标记为降级）
-> min(local confidence, 0.55)（限制本地结果置信度上限） -> warning（加入降级警告）
-> external failed + local succeeded diagnostics（外部失败与本地成功两条诊断） -> two trace steps（两个执行追踪步骤）
```

| 对象 | 关键变化 |
|---|---|
| `RagClientError` | 保存 `error_type`（结构化错误类型）和人类可读消息 |
| `RagResult.provider`（RAG 结果提供路径） | `local`（本地结果）被包装器改为 `fallback`（本地降级结果） |
| `confidence`（RAG 结果置信度） | `min(local_confidence, 0.55)`，即本地结果置信度与 0.55 的较小值 |
| `warnings`（警告列表） | 首项加入 external unavailable（外部服务不可用）提示 |
| `diagnostics`（调用诊断列表） | external failed（外部调用失败）在前，local step（本地步骤）重命名为 `local_rag_fallback`（本地 RAG 降级） |
| Coordinator trace | 每个 diagnostic 转成一个 AgentStep |

**[测试确认]** `tests/test_rag_external.py` 和 `tests/test_evaluation.py` 验证 provider、0.55 上限、warning 和 diagnostics。

### 4.3 BLOCKED 业务安全请求

```text
input（用户输入） -> PolicyEngine（安全策略引擎） -> matched PolicyRule（命中的策略规则） -> BLOCKED（阻断执行）
-> blocked template（阻断提示模板） -> immediate return（立即返回）
-> no IntentRouter（不调用意图路由器） -> no TaskRouter（不调用任务路由器） -> no RagClient（不调用 RAG 客户端）
-> trace only policy_engine（执行追踪仅含策略引擎步骤）
```

| 阶段 | 输入 | 输出/状态 | 后续行为 |
|---|---|---|---|
| CLI | 风险文本 | command request | 调 Coordinator |
| PolicyEngine | text | `PolicyDecision(status="BLOCKED")`，即安全状态为阻断 | 选择最高优先级/严重度规则 |
| Coordinator | decision | rejection response | 立即返回 |
| Trace | policy result | 仅 `policy_engine` | JSON 输出 |

**[测试确认]** counting fake client 的 `call_count == 0`，证明这是生成前硬门，不是生成后过滤。

### 4.4 UNCERTAIN 降级协助请求

**[源码确认]** UNCERTAIN 不是拒绝。它继续路由并执行任务；Router step（路由步骤）带 uncertain warning（不确定状态警告），最终回答前加 uncertain template（不确定提示模板），最终 confidence（置信度）取业务结果置信度与 policy confidence（策略置信度）的较小值。

| 阶段 | 状态变化 |
|---|---|
| Policy | `status=UNCERTAIN`（策略状态为不确定）、规则 `confidence`（策略置信度）当前为 0.78 |
| Routing（路由阶段） | 正常生成 IntentResult（意图识别结果）和 TaskRoute（任务路由结果） |
| Execution | 正常进入 QA/Sales/Writer |
| Final response（最终回答） | 前置“可协助整理，但不能替代专业判断”的提示 |
| Confidence（最终置信度） | `min(execution_confidence, policy_confidence)`，即执行结果置信度与策略置信度的较小值 |

### 4.5 SAFE 销售、周报和邮件

- sales analysis：按客户名/别名/ID 选择 profile，输出阶段、成交分和健康分。
- weekly report：汇总客户、已评分、高潜和高风险数量。
- email draft：生成固定、稳妥的跟进邮件模板。
- podcast：先走 QA 获取素材，再生成脚本，因此可能触发 RAG fallback。

### 4.6 RAG Adapter Evaluation（RAG 适配器评测）

执行六个 deterministic cases，不访问真实 RAG_demo。每个 case 经 Coordinator 观察 provider、citation、evidence、confidence、raw exposure 和 trace。

### 4.7 Policy Boundary Evaluation（策略边界评测）

执行 18 个固定 case：7 BLOCKED、5 UNCERTAIN、6 SAFE。每个 case 使用 counting RagClient；BLOCKED case 额外观察 RAG call count。

## 5. 核心数据结构与 Contract（数据协议）

阅读约定：本章是全篇英文字段的统一中文释义来源。后续章节再次使用同名字段时，含义均以本章为准；类名、方法名和源码路径保持英文，方便直接定位代码。

### 5.1 PolicyRule（安全策略规则）

| 字段 | 中文实际含义 |
|---|---|
| `rule_id` | 规则唯一标识，用于诊断、评测和配置迁移 |
| `category` | 风险类别，用于归类命中规则和统计业务边界覆盖 |
| `status` | 规则命中后的策略状态，当前规则表只使用 BLOCKED 或 UNCERTAIN |
| `priority` | 规则优先级，多规则命中时优先选择数值更高者 |
| `patterns` | 触发当前规则的关键词或短语集合 |
| `negative_patterns` | 仅使当前规则跳过的排除短语，不是全局安全白名单 |
| `reason` | 规则命中后写入安全决策的原因 |
| `examples` | 便于维护者理解规则的示例，不参与实际匹配 |

### 5.2 PolicyDecision（安全策略决策）

| 字段/属性 | 中文实际含义 |
|---|---|
| `status` | 归一化安全状态：SAFE（安全放行）、UNCERTAIN（不确定但继续协助）或 BLOCKED（阻断执行） |
| `reason` | 本次安全决策原因，来自最高优先级规则或 classifier（分类器） |
| `matched_rules` | 当前文本命中的全部规则，格式为风险类别加命中短语 |
| `warnings` | classifier 失败等策略降级警告 |
| `classifier_used` | 本次决策是否实际调用了 optional classifier（可选分类器） |
| `confidence` | 安全决策的启发式置信度，不是统计概率 |
| `is_blocked` | `status` 是否为 BLOCKED 的便捷控制流属性 |
| `is_uncertain` | `status` 是否为 UNCERTAIN 的便捷控制流属性 |

### 5.3 IntentResult（意图识别结果）与 TaskRoute（任务路由结果）

**IntentResult（意图识别结果）**

| 字段 | 中文实际含义 |
|---|---|
| `intent` | 识别出的用户语义意图 |
| `confidence` | 意图识别置信度 |
| `reasoning` | 得出当前意图的规则或信号说明 |
| `alternative_intents` | 存在歧义时保留的备选意图列表 |

**TaskRoute（任务路由结果）**

| 字段 | 中文实际含义 |
|---|---|
| `task` | 系统最终选择的可执行任务 |
| `confidence` | 任务路由置信度 |
| `reason` | 选择该任务的原因 |

`task` 的可选协议值为 `qa`（企业知识问答）、`sales_analysis`（销售分析）、`weekly_report`（销售周报）、`podcast_script`（播客脚本）和 `email_draft`（跟进邮件草稿）。

### 5.4 AgentStep（执行追踪步骤）与 OrchestrationResult（编排结果）

**AgentStep（单个执行追踪步骤）**

| 字段 | 中文实际含义 |
|---|---|
| `step_name` | 执行步骤名称，例如策略引擎或外部 RAG 查询 |
| `input_summary` | 当前步骤输入内容的精简摘要 |
| `output_summary` | 当前步骤输出结果的精简摘要 |
| `confidence` | 当前步骤结果置信度；失败步骤可为 0 或空值 |
| `latency_ms` | 当前步骤执行耗时，单位为毫秒 |
| `tool_calls` | 当前步骤发生的工具或外部接口调用列表 |
| `warnings` | 当前步骤产生的警告列表 |

**OrchestrationResult（一次完整编排结果）**

| 字段 | 中文实际含义 |
|---|---|
| `session_id` | 当前交互会话标识 |
| `user_input` | 用户原始输入文本 |
| `policy` | 当前输入对应的 PolicyDecision（安全决策） |
| `intent_result` | IntentResult（意图识别结果）；BLOCKED 时为空 |
| `task_route` | TaskRoute（任务路由结果）；BLOCKED 时为空 |
| `final_response` | 最终面向用户的回答文本 |
| `rag_result` | RagResult（知识问答结果）；非 RAG 任务时为空 |
| `citations` | 从 RAG sources 提取的引用来源列表 |
| `confidence` | 最终编排结果置信度 |
| `trace` | 按执行顺序保存的 AgentStep 列表 |
| `metadata` | 会话意图、工具调用、耗时和模型调用统计等运行元数据 |
| `timestamp` | 编排结果生成的 UTC（协调世界时）时间 |

`to_public_dict()`（转换为公共输出字典）会委托 RagResult 过滤 `raw_response`（外部原始响应）。

### 5.5 RagResult（RAG 结果）、RagEvidence（RAG 证据）与 RagCallDiagnostic（RAG 调用诊断）

**RagResult（统一 RAG 查询结果）**

| 字段 | 中文实际含义 |
|---|---|
| `answer` | 最终知识问答文本 |
| `evidence` | 归一化后的结构化证据条目列表 |
| `sources` | 面向展示的引用来源列表 |
| `confidence` | 当前知识问答结果的启发式置信度，不是严格统计概率 |
| `warnings` | 面向用户和调用方的降级或错误警告 |
| `provider` | 实际结果提供路径：external（外部结果）、local（直接本地结果）、fallback（本地降级结果）或 none（无可用结果） |
| `diagnostics` | 每次 RAG provider 调用的结构化诊断列表 |
| `raw_response` | 外部 RAG 服务原始响应，仅用于受控调试，默认不公开 |

**RagEvidence（归一化证据条目）**

| 字段 | 中文实际含义 |
|---|---|
| `source_id` | 证据来源唯一标识 |
| `title` | 证据标题 |
| `source_path` | 证据原始文件路径或服务内资源路径 |
| `text` | 作为回答依据的证据文本 |
| `score` | 外部相关度或本地排序分值；不同 provider 的量纲不保证一致 |
| `metadata` | 证据附加元数据 |

**RagCallDiagnostic（单次 RAG 调用诊断）**

| 字段 | 中文实际含义 |
|---|---|
| `step_name` | 调用步骤名称，例如 external_rag_query 或 local_rag_fallback |
| `provider` | 本次实际调用的 RAG 提供方 |
| `success` | 本次调用是否成功 |
| `error_type` | 结构化错误类别；成功时为空 |
| `message` | 面向 Trace 和调试的诊断说明 |
| `latency_ms` | 本次 RAG 调用耗时，单位为毫秒 |

`warnings`（人类可读警告）与 `diagnostics`（机器可追踪诊断）职责不同，不能互相替代。

### 5.6 CustomerProfile（客户档案）与 InteractionRecord（互动记录）

**CustomerProfile（客户当前状态档案）**

| 字段 | 中文实际含义 |
|---|---|
| `customer_id` | 客户唯一标识 |
| `customer_name` | 客户名称 |
| `aliases` | 客户别名列表 |
| `schema_version` | 数据结构版本 |
| `version` | 当前客户档案业务版本 |
| `source` | 客户线索或数据来源 |
| `industry` | 客户所属行业 |
| `company_size` | 客户企业规模 |
| `procurement_department` | 客户采购部门 |
| `contacts` | 客户联系人列表 |
| `procurement_items` | 客户计划采购的产品或服务列表 |
| `budget_range` | 客户预算范围 |
| `procurement_cycle` | 客户采购周期 |
| `timeline` | 项目或采购时间线 |
| `competitors` | 当前竞争对手列表 |
| `decision_makers` | 客户侧决策人列表 |
| `sales_stage` | 当前销售阶段 |
| `status` | 当前客户状态 |
| `deal_score` | 当前成交评分结果 |
| `health_score` | 当前客户关系健康评分结果 |
| `risks` | 已识别业务风险列表 |
| `next_actions` | 建议下一步行动列表 |
| `tags` | 客户标签列表 |
| `created_at` | 档案创建时间 |
| `updated_at` | 档案最后更新时间 |

**InteractionRecord（单次客户交互记录）**

| 字段 | 中文实际含义 |
|---|---|
| `interaction_id` | 交互记录唯一标识 |
| `customer_id` | 该交互所属客户标识 |
| `date` | 交互发生时间 |
| `type` | 交互类型，例如会议、电话、邮件或备注 |
| `raw_text` | 未加工的原始交互文本 |
| `summary` | 交互内容摘要 |
| `key_quotes` | 需要保留的关键原话列表 |
| `extracted_facts` | 从交互中抽取的结构化事实 |
| `procurement_signals` | 从交互中识别的采购信号 |
| `risks` | 本次交互暴露的业务风险 |
| `next_actions` | 本次交互产生的后续行动建议 |
| `created_at` | 交互记录创建时间 |

CustomerProfile 描述客户当前汇总状态；InteractionRecord 描述一次历史业务事件，两者不可混用。

### 5.7 DealScore（成交评分）与 HealthScore（客户健康评分）

**DealScore（成交评分结果）**

| 字段 | 中文实际含义 |
|---|---|
| `score` | 0-100 的最终成交评分，不是成交概率 |
| `level` | 成交评分对应的等级 |
| `need_clarity` | 客户需求清晰度得分 |
| `budget_fit` | 预算匹配度得分 |
| `decision_maker_access` | 接触决策人的程度得分 |
| `urgency` | 项目紧迫度得分 |
| `engagement` | 客户互动积极度得分 |
| `risk_penalty` | 从加权结果中扣除的风险罚分 |
| `confidence` | 评分维度覆盖等级，为 high（高覆盖）、medium（中等覆盖）或 low（低覆盖），不是概率 |
| `filled_dimensions` | 已获得有效信息的评分维度数量 |
| `total_dimensions` | 评分维度总数 |
| `missing_dimensions` | 当前缺失信息的评分维度列表 |
| `reasoning` | 各维度得分的解释 |
| `summary` | 成交评分摘要 |

**HealthScore（客户关系健康评分结果）**

| 字段 | 中文实际含义 |
|---|---|
| `health_score` | 0-100 的客户关系健康总分，与人体健康无关 |
| `status` | 客户关系健康状态 |
| `recent_contact` | 最近联系情况得分，范围 0-20 |
| `responsiveness` | 客户响应积极度得分，范围 0-20 |
| `decision_maker_involvement` | 决策人参与程度得分，范围 0-20 |
| `need_clarity` | 客户需求清晰度得分，范围 0-20 |
| `budget_timeline_clarity` | 预算和时间线清晰度得分，范围 0-20 |
| `summary` | 客户关系健康评分摘要 |

两类评分都是确定性业务规则结果，不是机器学习概率。

### 5.8 ToolResult（工具执行结果）

| 字段/属性 | 中文实际含义 |
|---|---|
| `success` | 工具调用是否成功 |
| `tool_name` | 被调用工具名称 |
| `data` | 工具返回的结构化或文本数据 |
| `errors` | 导致工具失败的错误列表 |
| `warnings` | 不阻止成功、但需要调用方关注的警告列表 |
| `summary` | 工具执行结果摘要 |
| `is_partial` | 是否属于“成功但带 warning”的部分成功结果 |

### 5.9 M1.2（里程碑 1.2）服务 Contract（数据协议）

| 模型/字段 | 中文实际含义 |
|---|---|
| `UserRequest.text` | 外部调用方提交的业务文本 |
| `UserRequest.task_override` | 外部调用方可选的任务覆盖值；知识问答接口会由服务端强制覆盖为 `qa`（知识问答） |
| `UserRequest.session_id` | 外部调用方可选的会话标识；缺失时由服务端生成 |
| `RequestContext.request_id` | 服务端为本次 HTTP 请求生成的唯一请求标识 |
| `RequestContext.trace_id` | 服务端为本次调用链生成的追踪标识 |
| `RequestContext.session_id` | 已校验或生成后的内部必填会话标识 |
| `RequestContext.principal` | 服务端提供的可信主体；当前仅为开发占位主体 |
| `RequestContext.versions` | 本次执行使用的模型注册表、策略、应用和连接器版本快照 |
| `RequestContext.received_at` | 服务端接收请求的 UTC（协调世界时）时间 |
| `RequestContext.idempotency_key` | 从 HTTP Header（请求头）读取的可选幂等键；M1.2 只传递，不提供持久幂等语义 |
| `AgentResponse.request_id` | 公共响应中的请求标识，与 `X-Request-ID`（请求标识响应头）一致 |
| `AgentResponse.trace_id` | 公共响应中的追踪标识，与 `X-Trace-ID`（追踪标识响应头）一致 |
| `AgentResponse.session_id` | 公共响应中的会话标识 |
| `AgentResponse.result` | 经过公共序列化过滤的 OrchestrationResult（编排结果） |
| `APIErrorResponse.code` | 稳定、可供调用方判断的错误代码 |
| `APIErrorResponse.message` | 不暴露内部异常详情的公共错误消息 |
| `APIErrorResponse.details` | 请求校验错误的字段级详情列表 |

## 6. 核心模块逐一拆解

以下模块卡统一覆盖职责、位置、接口、异常、测试、追问和边界；路径均相对 `src/conversation_agent/`。

### 6.1 Policy Engine（安全策略引擎）

- **问题/独立性**：在昂贵或高风险执行前给出统一决策；独立于业务模块才能形成硬门。
- **位置/文件**：Coordinator 首步；`policy/engine.py`，`PolicyEngine.decide()`。
- **输入输出/逻辑**：文本 -> PolicyDecision；规则优先，未命中才 classifier。
- **交互/异常**：classifier 失败按配置 SAFE 或 UNCERTAIN，并写 warning。
- **测试**：`test_policy.py`、`test_orchestration.py`。
- **追问/回答**：是否只是关键词？规则是确定性底座且集中管理，语义边界由 optional classifier 扩展。
- **边界**：不能声称覆盖所有改写和规避表达。

### 6.2 Business Safety Rule Table（业务安全规则表）

- **问题/独立性**：把风险内容从 engine 控制代码中移出；`policy/rules.py`。
- **接口/逻辑**：不可变 `PolicyRule` tuple（元组）；全部命中被保留，最高 priority（优先级）、同级最高 severity（严重程度）决策。
- **异常**：negative pattern 只取消当前 rule，其他风险规则仍可命中。
- **测试**：rule id 唯一、多命中、tie-break、局部 negative。
- **边界**：仍依赖 pattern，不是完整语义分类器。

### 6.3 Policy Templates（策略提示模板）

- **问题/位置**：统一 BLOCKED 和 UNCERTAIN 口径；`policy/templates.py`。
- **输入输出**：PolicyDecision -> 用户可读提示。
- **交互**：Coordinator 对 blocked 直接返回，对 uncertain 前置到业务结果。
- **边界**：模板不负责分类，也不替代专业建议。

### 6.4 IntentRouter（意图路由器）

- **问题/文件**：理解用户想做什么；`sales/intent_router.py`。
- **输入输出**：文本 -> IntentResult；基于规则和信号计算意图与 confidence。
- **交互**：输出交给 TaskRouter；不会直接执行工具。
- **测试/边界**：`test_intent_router.py`；不是通用 NLU（自然语言理解）模型。

### 6.5 TaskRouter（任务路由器）

- **问题/文件**：把语义意图映射为可执行任务；`orchestration/task_router.py`。
- **输入输出**：文本 + IntentResult -> TaskRoute。
- **逻辑**：报告/播客/QA 信号优先，邮件依赖 intent，默认 sales analysis。
- **测试/边界**：`test_task_router.py`；规则映射不是动态 Planner。

### 6.6 Coordinator（协调器）

- **问题/文件**：提供唯一编排入口；`orchestration/coordinator.py`。
- **输入输出**：文本、session、可选 override -> OrchestrationResult。
- **逻辑**：Policy -> Routing -> Execution -> Trace -> Result。
- **异常**：blocked 提前返回；RAG 细节委托 RagClient。
- **测试**：`test_orchestration.py`。
- **边界**：确定性 pipeline，不是 LangGraph runtime 或自由自治 Agent。

### 6.7 ExternalRagClient（外部 RAG 客户端）

- **问题/独立性/位置**：隔离 HTTP 与外部 schema；位于 RagClient primary provider；`rag/external_client.py`。
- **接口**：question、trace_id、metadata -> RagResult；当前 HTTP body 只包含 question。
- **逻辑/交互**：POST `/query`，归一化 answer、citation、score 和 raw response，成功 diagnostic 为 `external_rag_query`。
- **异常**：timeout、connection、HTTP、JSON、schema、missing answer 转对应 RagClientError。
- **测试**：`test_rag_external.py` 的成功、字符串/dict citation 和六类错误。
- **追问/回答/边界**：为何宽松映射？兼容外部 schema 差异；但这不是 schema versioning，也不评价召回质量。

### 6.8 LocalKeywordRagClient（本地关键词 RAG 客户端）

- **问题/独立性/位置**：提供零外部依赖的本地 provider 和降级路径；`rag/local_client.py`。
- **接口**：统一 query contract -> provider=local 的 RagResult。
- **逻辑/交互**：调用 `rag/module.py` 的 retrieve（检索候选证据）、rank_and_filter（排序并过滤证据）、generate_with_citations（生成带引用回答）。
- **异常**：无证据时返回低 confidence 且不伪造 citation。
- **测试**：`test_rag.py` 和 local client regression。
- **追问/回答/边界**：为何简单？它只保证有限连续性；不能称为企业向量 RAG。

### 6.9 FallbackRagClient（RAG 降级客户端）

- **问题/独立性/位置**：把 provider 故障策略从 Coordinator 移出；`rag/factory.py`。
- **接口**：primary + optional fallback，保持相同 query contract。
- **逻辑/交互**：捕获 RagClientError，调用 local，改 provider、cap confidence、插 warning、合并 diagnostics。
- **异常**：fallback 关闭时返回 provider=none、confidence=0.15；非 RagClientError 不被吞掉。
- **测试**：`test_rag_external.py`、`test_evaluation.py`。
- **追问/回答/边界**：为何不是 retry/circuit breaker？当前只实现单次降级，生产韧性仍是未来方向。

### 6.10 RAG Factory（RAG 客户端工厂）

- **问题/独立性/位置**：集中解释 `RagServiceConfig`；`rag/factory.py:create_rag_client()`。
- **接口**：config -> RagClient。
- **逻辑/交互**：local 配置直接返回 local；external 配置组装 external 和可选 fallback。
- **异常**：配置合法性由 Pydantic（数据模型与校验框架）承担；不在 Coordinator 写 provider if/else（结果提供方条件分支）。
- **测试**：factory/local/external regression tests。
- **追问/回答/边界**：Factory 负责装配，不负责调用时观测或业务路由。

### 6.11 CustomerStore（客户档案存储）

- **问题/独立性/位置**：持久化客户当前状态；`memory/customer_store.py`，供 Coordinator、CLI、tools 使用。
- **接口**：CustomerProfile/标识 -> save/load/list/search 结果。
- **逻辑/交互**：JSON 文件、alias 和版本更新；模型负责字段校验。
- **异常**：损坏或缺失数据按 Store contract 处理，测试使用临时目录。
- **测试**：`test_stores.py`、customer CLI tests。
- **追问/回答/边界**：JSON 便于演示和检查，但无高并发事务、租户和数据库约束。

### 6.12 InteractionStore（客户互动存储）

- **问题/独立性/位置**：将历史交互与当前 CustomerProfile 分离；`memory/interaction_store.py`。
- **接口**：InteractionRecord -> save/list；按 customer 定位记录。
- **逻辑/交互**：每客户目录保存 JSON，供记忆和分析读取。
- **异常/测试**：坏 JSON 容错由 `test_stores.py` 在 tmp_path（pytest 临时目录）验证。
- **追问/回答/边界**：这是事件记录雏形，不是 event sourcing 或审计数据库。

### 6.13 DealScorer（成交评分器）

- **问题/独立性/位置**：把成交判断变成可解释确定性评分；`sales/scorer.py`。
- **接口**：ProcurementSignals（采购信号集合）/业务维度 -> DealScore（成交评分）。
- **逻辑/交互**：维度加权、风险罚分、0-100 clamp、coverage confidence 和 reasoning。
- **异常/测试**：缺失维度降低 coverage；`test_scorer.py` 验证权重、等级和边界。
- **追问/回答/边界**：不是训练出的成交概率，score 与 confidence 含义不同。

### 6.14 HealthScorer（客户健康评分器）

- **问题/独立性/位置**：衡量客户关系活跃和清晰度；`sales/health.py`。
- **接口**：客户与互动信号 -> HealthScore。
- **逻辑/交互**：五个 0-20 维度汇总并映射 health status。
- **异常/测试**：模型拒绝维度总和超过 100；`test_scorer.py` 验证时间衰减和状态。
- **追问/回答/边界**：它是规则化客户关系活跃度评分，不代表客户私人属性或真实心理状态。

### 6.15 Tool Registry（工具注册表）

- **问题/文件**：统一工具注册、查找和执行；`tools/registry.py`、`tools/base.py`。
- **输入输出**：tool name + kwargs -> ToolResult。
- **异常/测试**：未知工具和执行失败归一化；`test_tools.py`。
- **边界**：Coordinator 当前业务路径不依赖 LLM 动态选工具。

### 6.16 MockAgent（模拟智能体）与 RealAgent（真实模型智能体）

- **问题/文件**：保留确定性测试 Agent（智能体）和 LLM tool-loop（大语言模型工具循环）实现；`agent.py`。
- **输入输出**：用户文本 -> Interaction。
- **异常/测试**：LLM（大语言模型）客户端失败通过 `is_terminal_client_failure()`（判断终止型客户端失败）集中停止循环；`test_agent.py`、`test_dashscope_client.py`。
- **关键边界**：CLI 当前注入的是 Coordinator，RealAgent 不是主执行入口。

### 6.17 DashScopeClient（阿里云百炼模型客户端）

- **问题/独立性/位置**：提供当前 RealAgent（真实模型智能体）的默认模型适配；`llm/dashscope_client.py`、`llm/factory.py`、`llm/models.py`。
- **接口/逻辑**：`standard`（标准档）绑定 `qwen3-8b`，执行非流式 HTTP（超文本传输协议）调用；HTTP client factory（HTTP 客户端工厂）、monotonic clock（单调时钟）、UTC wall clock（协调世界时现实时间）、sleeper（等待函数）和 jitter source（随机抖动来源）均可注入。
- **异常/测试**：区分 `missing_api_key`（缺少接口密钥）、`invalid_tool_schema`（请求工具定义无效）、`invalid_tool_call`（响应工具调用无效）、`http_error`（HTTP 错误）、`timeout`（超时）和 `invalid_response`（无效响应）；默认单元测试使用 `httpx.MockTransport`（HTTPX 模拟传输层），不访问网络。
- **能力边界**：`advanced`（高级档）和 `evaluator`（评测档）只配置不接运行路由；`lightweight`（轻量档）未配置；动态 ModelRouter（模型路由器）尚未实现。

### 6.18 AnthropicClient（Anthropic 模型客户端）

- **问题/独立性/位置**：把 Anthropic SDK（Anthropic 软件开发工具包）映射到 BaseLLMClient（基础大语言模型客户端协议）；`llm/anthropic_client.py`，供 RealAgent 使用而非 Coordinator 主链路。
- **接口/逻辑**：统一 message/tool schema、token usage 和 retry 配置。
- **异常/测试**：SDK 调用与响应使用 mock（模拟对象）；`test_llm.py`。
- **追问/回答/边界**：该 client（客户端）是显式配置后才使用的历史适配器，不是默认模型路径。

### 6.19 DeepSeekClient（DeepSeek 模型客户端）

- **问题/独立性/位置**：提供 OpenAI-compatible（兼容 OpenAI 协议的）DeepSeek provider（DeepSeek 模型提供方）；`llm/deepseek_client.py`。
- **接口/逻辑**：实现同一 BaseLLMClient contract，隔离认证和响应格式。
- **异常/测试**：HTTP/响应映射由 `test_llm.py` mock 验证。
- **追问/回答/边界**：它是显式配置后才使用的可选供应商适配，不是当前 Coordinator（协调器）的 Policy classifier（安全策略分类器）。

### 6.20 CLI（命令行界面）

- **问题/文件**：提供可演示、可脚本化入口；`cli/main.py`。
- **输入输出**：参数/stdin -> human text 或 JSON。
- **逻辑**：JSON 必须经过 public serialization；eval 提供摘要和完整 report。
- **测试**：`test_cli.py`。
- **边界**：不是 HTTP API，也没有用户认证和租户隔离。

### 6.21 Evaluation Modules（评测模块）

- **文件**：`evaluation/rag_adapter.py`、`evaluation/policy_boundary.py`。
- **输入输出**：固定 case + fake clients -> summary + cases。
- **逻辑**：指标聚合后执行 pass/warning/fail Gate。
- **测试**：`test_evaluation.py`、CLI tests。
- **边界**：证明 adapter 和 policy contract，不证明真实线上质量。

### 6.22 RequestContextBuilder（请求上下文构建器）

- **问题/文件**：隔离外部请求与内部可信上下文；`runtime/builder.py`。
- **输入输出**：可选会话标识、请求标识、追踪标识和幂等键 → 不可变 RequestContext（请求上下文）。
- **逻辑/异常**：身份和版本快照来自构造器依赖，不从请求体反序列化；Pydantic（数据校验模型库）继续验证长度、时区和空值。
- **测试/边界**：`test_application_service.py` 验证服务端生成字段。当前 `trusted_context_generation=implemented`（可信上下文生成已实现），但 `principal_mode=development_placeholder`（主体模式为开发占位）且真实认证、授权均未实现。

### 6.23 ChatService（对话应用服务）

- **问题/文件**：把 HTTP（超文本传输协议）用例映射到现有 Coordinator（协调器），避免协调器承担协议职责；`application/service.py`。
- **输入输出**：UserRequest（用户请求）和服务端调用元数据 → ApplicationResult（应用结果）。
- **逻辑/异常**：认证与授权成功后构建上下文，再把用户文本、会话标识和最终任务覆盖同步映射给协调器；内部异常包装为 ApplicationExecutionError（应用执行错误），公共消息不包含原始异常详情。完整 RequestContext 当前不传入 Coordinator。
- **测试/边界**：`test_application_service.py` 验证任务覆盖、标识贯穿和异常隔离。授权已在进入服务前完成，但当前仍没有数据库事务或审计仓库。

### 6.24 FastAPI Service（FastAPI 服务层）

- **问题/文件**：提供 `/healthz`（进程存活接口）、`/v1/chat`（同步对话接口）和 `/v1/qa`（同步知识问答接口）；`api/app.py`、`api/models.py`。
- **输入输出**：JSON（结构化数据格式）请求和可选 Idempotency-Key（幂等键请求头）→ AgentResponse（智能体响应）或 APIErrorResponse（应用接口错误响应）。
- **逻辑/异常**：中间件生成请求与追踪标识；Bearer（持有者令牌）格式错误返回 400，无效 Token（令牌）返回 401，权限不足返回 403，JWKS（JSON Web 密钥集）不可用返回 503，请求校验错误返回 422，应用执行错误返回 500；业务结果由 ResponseProjector（响应投影器）过滤原始响应。
- **测试/边界**：`test_api.py` 使用 ASGITransport（应用服务器网关接口进程内传输器）测试，不监听端口、不访问网络。`/healthz` 不是依赖健康检查或生产 readiness（就绪状态）证明。

M1.3（里程碑 1.3）运行状态：

```text
http_service_boundary = implemented             # HTTP 服务边界：已实现
request_context_builder = implemented           # 请求上下文构建器：已实现
trusted_context_source = server_generated       # 可信上下文来源：服务端生成
principal_mode = verified_claims_or_demo_placeholder  # 主体模式：Token 验证或 demo 占位
policy_first_execution = implemented            # 策略优先执行：已实现
qa_route_enforcement = implemented              # 知识问答路由强制约束：已实现
raw_response_default_exposure = false            # 原始响应默认暴露：否
raw_response_gate = config_and_permission        # 原始响应门控：配置与权限双重
authentication = implemented                    # JWT Bearer 认证：已实现
authorization = implemented                     # 确定性 RBAC/ABAC：已实现
authorization_strategy = conservative_route_union
persistent_idempotency = not_implemented         # 持久幂等：尚未实现
database_persistence = not_implemented           # 数据库持久化：尚未实现
production_readiness = not_implemented           # 生产就绪能力：尚未实现
```

## 7. Phase 0-13（阶段 0 至阶段 13）演进复盘

Phase 0-10 均为 **[演进归纳]**，不是精确开发日志。

| Phase | 解决的问题与实现 | 当前状态 | 面试一句话 |
|---|---|---|---|
| 0 | 明确采购销售场景和输入输出 | 保留 | 先固定业务问题，再选择 Agent 形态 |
| 1 | 建立 Agent/Interaction/ToolResult contract | 保留兼容层 | 先统一运行协议和错误表达 |
| 2 | 建立 CustomerProfile 与 JSON Store | 保留 | 用可观察本地存储验证业务模型 |
| 3 | 增加 InteractionRecord 和客户记忆 | 保留 | 把一次对话与长期客户状态分开 |
| 4 | 增加 Deal/Health 确定性评分 | 保留 | 评分结果包含维度和解释 |
| 5 | 增加 IntentRouter | 保留 | 先理解用户想做什么 |
| 6 | 引入工具注册与客户/评分工具 | 保留 | 用统一 ToolResult 管理工具边界 |
| 7 | 引入真实 LLM client 与 RealAgent | 非主链路 | 验证供应商抽象和 tool loop |
| 8 | 收敛为 Coordinator 与结构化 trace | 主链路 | 用确定性编排提升可测性 |
| 9 | 引入 Policy-first 和多任务路由 | 保留 | 风险请求必须先于业务执行处理 |
| 10 | 压扁本地 RAG 为三函数模块 | fallback 保留 | 本地 RAG 只承担轻量 grounded fallback |
| 11 | 外部 RAG Adapter、错误分类、fallback、raw control | 保留 | Agent 层与 Knowledge Engine 解耦 |
| 12 | 两类 deterministic evaluation 和发布清理 | 保留 | 从“能跑”升级到“可证明” |
| 13 | 集中化 Business Safety Rule Table | 被 Phase 14 分层 Catalog 取代 | 从单表匹配升级为候选、立场和矩阵治理 |
| 14 | Secret Governance、并发隔离与 Policy Hotfix | 实现范围完成；事故关闭 blocked | 技术修复与外部事故证明分开计状态 |

## 8. Business Safety Firewall（业务安全防火墙）深度理解

### 8.1 六类风险

| 类别 | 典型风险 | 当前决策 |
|---|---|---|
| `privacy_overreach` | 私人住址、私人联系方式、家庭成员或私人财务数据 | BLOCKED |
| `sensitive_attribute_inference` | 宗教信仰、政治倾向、族群身份或私人财务属性推断 | BLOCKED |
| `legal_financial_final_judgment` | 法律、金融和合同绝对定性 | BLOCKED |
| `sales_misrepresentation` | 100% 交付、保证中标、免责承诺 | BLOCKED |
| `unsupported_business_claim` | 编造案例、库存或 SLA | BLOCKED |
| `business_uncertain` | 法务确认、数据营销、库存/SLA 待确认 | UNCERTAIN |

### 8.2 Phase 14 决策算法

**[源码确认]** Engine 依次执行 NFKC/零宽处理与 offset mapping、每个 occurrence 的风险候选检测、候选级立场解析和版本化决策矩阵。`negative_patterns` 不参与运行。任何未被消解的 HIGH/CRITICAL REQUEST 都会 BLOCKED；UNKNOWN 和 classifier 故障均为 UNCERTAIN。

示例：“根据已有资料，帮我承诺客户 100% 交付”中的“根据已有资料”可使 unsupported claim 规则跳过，但不影响 sales misrepresentation 规则，因此仍 BLOCKED。

### 8.3 Classifier fallback（分类器兜底）

规则未命中且配置 classifier 时才调用 classifier。classifier 可返回 PolicyDecision、字符串或 dict，并统一归一化。异常时 `fallback_to_uncertain=False` 默认 SAFE；开启时 UNCERTAIN，同时写 `classifier_error` warning。

**不能夸大**：当前默认 Coordinator 创建的 PolicyEngine 没有 classifier，因此 optional classifier 是已实现接口，不是默认在线语义防线。

### 8.4 三种状态

- BLOCKED：停止执行并返回通用安全模板。
- UNCERTAIN：继续执行，但提示只能协助整理，不能替代专业判断。
- SAFE：没有命中本地风险规则，正常执行；不代表请求在所有安全维度上绝对安全。

## 9. External RAG Integration（外部 RAG 集成）深度理解

### 9.1 RagClient Contract（RAG 客户端协议）

```python
query(question, *, trace_id=None, metadata=None) -> RagResult
```

Coordinator 只依赖该协议。`question` 表示用户知识问题；`trace_id` 表示跨模块追踪标识；`metadata` 表示可选调用上下文，可传 `session_id`（会话标识）、`task_type`（任务类型）和 `policy_status`（策略状态）。当前 ExternalRagClient 的 HTTP body（请求体）仍只发送 `question`。

### 9.2 External response mapping（外部响应映射）

ExternalRagClient POST（外部 RAG 客户端发送 HTTP 提交请求）`{base_url}/query`，其中 `base_url` 表示外部 RAG 服务基础地址。回答缺失会触发 `missing_answer`（缺少回答）错误；citation（引用）支持字符串和 dict（字典），并进行以下兼容映射：

| 外部字段 | 中文实际含义 |
|---|---|
| `source_id` / `id` / `source` / `title` | 用于生成统一证据来源标识或标题 |
| `text` / `snippet` / `content` / `quote` | 用于生成统一证据文本 |
| `score` / `rerank_score` / `relevance_score` | 用于生成统一相关度或重排序分值 |

错误被归类为 `timeout`（请求超时）、`connection_error`（连接失败）、`http_error`（HTTP 状态错误）、`invalid_json`（响应不是有效 JSON）、`schema_error`（响应结构不合法）或 `missing_answer`（响应缺少回答）。Fallback 只捕获 RagClientError，不吞掉任意程序错误。

### 9.3 Provider（结果提供路径）产生条件

| `provider` 值 | 中文实际含义 | 产生条件 |
|---|---|---|
| `external` | 外部 RAG 结果 | 配置 external 且外部请求成功 |
| `local` | 直接本地关键词结果 | 配置 `CONVAGENT_RAG_PROVIDER=local`，直接本地查询 |
| `fallback` | 外部失败后的本地降级结果 | external 抛 RagClientError，且启用 local fallback 并成功 |
| `none` | 没有可用 RAG 结果 | external 失败且 fallback 关闭，返回低置信度不可用结果 |

### 9.4 Confidence（置信度）、diagnostics（调用诊断）与 raw response（原始响应）

fallback 的 0.55 是明确的工程降级上限，目的是不让关键词结果看起来与企业 RAG 等价；它不是模型校准或概率证明。`warnings`（警告列表）告诉用户发生降级，`diagnostics`（调用诊断列表）保存每次 provider（结果提供方）调用事实。

raw response 可留在内存中用于调试；`RagResult.to_public_dict(False)` 删除它，`OrchestrationResult.to_public_dict()` 传递该策略，CLI 读取 `CONVAGENT_RAG_INCLUDE_RAW_RESPONSE`。因此正确表述是“默认不公开”，不是“系统从不保存”。

## 10. Evaluation（评测）深度理解

### 10.1 两个 Evaluation（评测）的区别

| Evaluation | 中文实际含义 | 评估对象 | 不评估什么 |
|---|---|---|---|
| `rag-adapter` | RAG 适配器可靠性评测 | provider（结果提供方）调用、fallback（降级）、citation/evidence（引用/证据）保留、安全硬门、raw output control（原始输出控制） | 真实 RAG_demo 召回、rerank（重排序）、答案事实正确性 |
| `policy-boundary` | 业务安全边界评测 | 固定业务边界的分类、安全硬门、风险类别覆盖 | 所有语义变体、对抗绕过、线上误报漏报 |

### 10.2 RAG Adapter（RAG 适配器）指标

#### external_success_rate（外部 RAG 成功率）

- **定义/口径**：成功且 `provider=external`（结果提供方为外部 RAG）的 case（评测样例）数 / 全部 6 个 cases。
- **意义/异常**：观察 external 成功路径是否存在；过低说明 case 或映射退化。
- **不能证明**：不是真实服务 SLA。
- **面试怎么讲**：这是 adapter 场景覆盖率，不是线上成功率。

#### fallback_rate（本地降级率）

- **定义/口径**：`provider=fallback`（结果提供方为本地降级）的 case 数 / 全部 cases。
- **意义/异常**：证明降级路径被执行；本固定集应包含一次。
- **不能证明**：不代表生产 fallback 频率。
- **面试怎么讲**：它验证控制流，不做流量统计推断。

#### citation_coverage（引用覆盖率）

- **定义/口径**：有 `sources`（引用来源）的非 blocked（非阻断）case 数 / 全部非 blocked cases。
- **意义/异常**：观察 adapter 是否保留展示来源；低于 0.5 触发 warning。
- **不能证明**：不证明 citation 正确或 RAG_demo 召回率高。
- **面试怎么讲**：它是输出 contract 覆盖，不是知识质量评测。

#### no_evidence_rate（无证据结果比例）

- **定义/口径**：无 `evidence`（结构化证据）的非 blocked case 数 / 全部非 blocked cases。
- **意义/异常**：高值意味着 grounded evidence 缺失。
- **不能证明**：有 evidence 也不等于 evidence 支持答案。
- **面试怎么讲**：用于发现结构化证据丢失。

#### average_confidence（平均结果置信度）

- **定义/口径**：非 blocked case 的 `confidence`（RAG 结果置信度）算术平均。
- **意义/异常**：低于 0.3 触发 warning。
- **不能证明**：不是准确率或校准概率。
- **面试怎么讲**：它只检查降级策略和输出是否落在合理范围。

#### blocked_no_rag_call_rate（阻断请求未调用 RAG 的比例）

- **定义/口径**：RAG `call_count`（调用次数）`=0` 的 blocked case 数 / blocked cases。
- **意义/异常**：必须等于 1.0，否则安全硬门失效并 FAIL。
- **不能证明**：不证明 Policy 能识别所有风险。
- **面试怎么讲**：它证明已识别的 blocked 请求不会泄漏到知识服务。

#### raw_response_exposure_rate（原始响应暴露率）

- **定义/口径**：公共输出含 `raw_response`（外部原始响应）的 case 数 / 全部 cases。
- **意义/异常**：默认必须 0，否则 FAIL。
- **不能证明**：不证明内部内存或日志从未接触 raw data。
- **面试怎么讲**：这是输出边界测试，不是完整数据防泄漏审计。

### 10.3 Policy Boundary（策略边界）指标

#### blocked_detection_rate（阻断识别率）

- **定义/口径**：实际 BLOCKED 的预期 blocked cases / 7。
- **意义/异常**：低于 0.9 为 FAIL。
- **不能证明**：不覆盖未知表达和对抗输入。
- **面试怎么讲**：固定高风险回归集的检出率。

#### uncertain_detection_rate（不确定状态识别率）

- **定义/口径**：实际 UNCERTAIN 的预期 uncertain cases / 5。
- **意义/异常**：低于 0.8 为 WARNING。
- **不能证明**：不代表真实业务 uncertain 分布。
- **面试怎么讲**：防止模糊风险全部被误判 SAFE 或 BLOCKED。

#### safe_pass_rate（安全放行率）

- **定义/口径**：实际 SAFE 的预期 safe cases / 6。
- **意义/异常**：低于 0.8 为 WARNING，观察误伤。
- **不能证明**：SAFE case 在所有维度绝对安全。
- **面试怎么讲**：与风险检出共同观察 precision/utility 取舍。

#### blocked_no_rag_call_rate（阻断请求未调用 RAG 的比例）

- **定义/口径**：被正确 BLOCKED 且 call_count=0 的 blocked cases / 7 个预期 blocked cases。
- **意义/异常**：不等于 1.0 为 FAIL。
- **不能证明**：不评价 Router 后其他非 RAG side effects，因为 blocked 根本不进入后续链路。
- **面试怎么讲**：这是 Policy-first 的可执行证据。

#### business_boundary_coverage（业务边界覆盖数量）/ covered_categories（已覆盖类别列表）

- **定义/口径**：`matched_rules`（已命中规则列表）中出现的风险 `category`（风险类别）去重数量及排序列表。
- **意义/异常**：数量低于 4 为 WARNING；列表提供可解释性。
- **不能证明**：类别出现不代表该类别的所有表达都覆盖。
- **面试怎么讲**：coverage 是样例触达范围，不是语义完备度。

### 10.4 Gate（评测门槛）

RAG Eval 中硬门或默认 raw exposure 失败为 FAIL；citation/confidence 不足为 WARNING。Policy Eval 中 blocked 检出不足或硬门失败为 FAIL；uncertain、safe、coverage 不足为 WARNING。Gate 是项目回归门，不是生产 SLO。

## 11. 测试体系

当前测试覆盖模型、Store（数据存储）、评分、工具、Intent/Task Router（意图与任务路由器）、Agent/LLM（智能体与大语言模型）、Policy（安全策略）、RAG（检索增强生成）、Orchestration（编排）、Evaluation（评测）、DashScope Client（阿里云百炼模型客户端）和可信服务 Contract（数据协议）。

**[测试确认]** M1.1（里程碑 1.1）至 M1.4-E（里程碑 1.4-E）实施前共保存六套规范化 Node ID（测试节点标识），当前收集 663 个节点，全部历史基线缺失数量均为 0。Unit suite（单元测试集）为 582 passed（582 项通过）；真实 PostgreSQL 17 非破坏性契约为 72 passed（72 项通过）和 2 skipped（2 项跳过），开启安全开关后的完整契约为 74 passed（74 项通过）；Operational suite（运维测试集）为 6 passed（6 项通过）。测试通过仍不证明真实身份提供商联调、生产备份基础设施或外部副作用严格一次执行。

关键证据：

- blocked-no-RAG（阻断请求不调用 RAG）：counting fake client（可计数模拟客户端）断言 0 次调用。
- fallback（降级）：mock external error（模拟外部错误），断言 provider（结果提供路径）、cap（置信度上限）、warning（警告）和 diagnostics（调用诊断）。
- raw exposure（原始响应暴露）：默认 public dict（公共字典）不含 raw（原始响应）；显式开启时可含。
- bad JSON（无效 JSON）：测试在临时目录构造，不污染 demo data（演示数据）。
- CLI：Click runner（Click 命令测试运行器）验证 human/JSON（人类可读/JSON）命令和 eval（评测）输出。

### 11.1 M1.4-B-R1 PostgreSQL Schema Contract（PostgreSQL 结构契约）

**[源码确认]** `src/conversation_agent/database/models.py`（数据库 ORM 模型）使用 naming convention（命名约定）、PostgreSQL `JSONB`（二进制 JSON 类型）、具名约束和显式索引；`alembic/versions/0001_initial_schema.py`（初始迁移）保持 `revision="0001"`（迁移版本标识）与 `down_revision=None`（无前置迁移），并用 `op.f()`（已完成命名标记）防止约束名被二次加工。

**[测试确认]** `tests/integration/test_postgresql_migration.py`（PostgreSQL 集成测试）在 pytest-asyncio strict mode（严格异步模式）下运行，不依赖 `--asyncio-mode=auto`。`compare_metadata`（元数据比较）仅排除 `alembic_version`（迁移版本表）；手工 Schema signature（结构签名）继续精确检查类型、默认值、CHECK（检查约束）、唯一约束、外键和索引顺序。所有预期 `IntegrityError`（完整性错误）均从 asyncpg（异步 PostgreSQL 驱动）结构化异常中提取并精确匹配 `constraint_name`（约束名称）。

当前边界：Schema/Migration（结构与迁移）、执行持久化、持久幂等、Fencing（执行权隔离）、Replay（结果重放）和 FastAPI 数据库接线均已实现并经 PostgreSQL 17 真实验证。

### 11.2 M1.4-C Execution Persistence（执行持久化）

**[源码确认]** `DurableApplicationService.execute()`（持久化应用服务执行入口）先在 Transaction A（事务 A）写入 `AgentRequest`（智能体请求）和 `request_accepted`（请求已接受）审计事件并提交；之后生成 `run_id`（运行标识）和 `run_started_at`（运行开始时间），通过 `anyio.to_thread.run_sync()`（线程执行桥接）调用同步 `ChatService`（对话应用服务），不在事务外持有 ORM 对象或 `AsyncSession`（异步数据库会话）。Coordinator 返回或失败后，Transaction B（事务 B）再原子写入 `AgentRun`（智能体运行）、请求终态和完成/阻断/失败审计。

`BLOCKED`（策略阻断）只由 `result.orchestration.policy.is_blocked`（结构化策略阻断标志）判定，不读取回答文本、HTTP 状态或引用。`RequestPersistenceMapper`（请求持久化映射器）按当前 `UserRequest`（用户请求）Contract 提供的文本计算 `len(user_text)`（Unicode 代码点长度）及原始 UTF-8 SHA-256（原始文本 UTF-8 哈希）；所有事件时间由注入的 UTC Clock（协调世界时钟）生成，同一状态转换复用同一个事件时间。

**[测试确认]** 真实 PostgreSQL 测试证明 Transaction A 可在 Coordinator 执行期间独立可见、未提交工作单元自动回滚、Transaction B 原子提交、重复请求与非法状态转换映射为安全错误、失败路径仍记录真实运行时间，并确认运行 SQL 不引用 `idempotency_records`（幂等记录表）。当前组件尚未由 FastAPI Route（FastAPI 路由）调用。

### 11.3 M1.4-D Persistent Idempotency（持久幂等）

**[源码确认]** `IdempotentDurableApplicationService.execute()`（幂等持久化应用服务执行入口）先验证当前 `AuthorizationDecision`（授权决定），再以五维 Scope（作用域）和原始 Key（幂等键）的 SHA-256（安全哈希值）请求原子 Claim（声明）。`SQLAlchemyIdempotencyRepository`（SQLAlchemy 幂等仓储）在同一数据库事务内使用 PostgreSQL `clock_timestamp()`（数据库当前时间）计算 `claimed_at`（声明时间）、`lease_expires_at`（租约到期时间）和 `expires_at`（终态到期时间），避免不同应用实例时钟决定执行权。

Claim 可能返回 ACQUIRED（取得执行权）、RECLAIMED（回收过期执行权）、REPLAY（重放）、IN_PROGRESS（仍在执行）、CONFLICT（指纹冲突）或 PREVIOUS_FAILURE（先前失败）。Fingerprint Version（请求指纹版本）不一致时在有效期内 fail closed（安全失败）；terminal TTL（终态有效期）到期后才允许按新版本重新声明，且 `claim_version`（执行权版本）单调递增。

Replay Snapshot（重放快照）只包含项目批准的公开业务字段，使用 canonical JSON（规范 JSON）计算 UTF-8 byte length（UTF-8 字节长度），拒绝 NaN（非数字）、Infinity（无穷值）和敏感字段。重放请求使用当前 `RequestContext`（请求上下文）与授权结果，通过完成 Run（运行记录）回溯原始实际执行请求，不形成重放链，也不创建新的 AgentRun。

**[测试确认]** 真实 PostgreSQL 17 测试证明五维 Scope 隔离、服务级并发重复抑制、连续重放来源、结构化 Policy BLOCKED（策略阻断）、失败终态、过期 Lease 回收、旧 Owner（执行权所有者）Fencing、Terminal TTL 复用、非法状态安全失败和超限 Snapshot 整事务回滚。取消使用 `abandon_on_cancel=true`（取消时放弃等待线程结果），不写普通失败终态，ACTIVE Claim（执行中声明）留待租约到期后回收。

当前边界：该能力已接入 FastAPI 并解析 `Idempotency-Key`（幂等键请求头）；外部副作用不保证 exactly-once（严格一次执行），也没有 Claim heartbeat（执行权心跳）或 orphan sweeper（孤儿记录清理器）。完整组件证据见 [M1.4-D Closeout](phases/m1_4d_persistent_idempotency_closeout.md)，运行恢复证据见 [M1.4-F Closeout](phases/m1_4f_operational_readiness_closeout.md)。

## 12. CLI（命令行界面）与配置

安装：`python3 -m pip install -e .`。入口由 `pyproject.toml` 映射为 `convagent`。

主要配置：

| 环境变量 | 中文实际含义 | 默认值 |
|---|---|---|
| `CONVAGENT_RAG_PROVIDER` | RAG 提供方模式，可设为 external（外部）或 local（本地） | `external` |
| `CONVAGENT_RAG_BASE_URL` | 外部 RAG 服务基础地址 | `http://127.0.0.1:8001` |
| `CONVAGENT_RAG_TIMEOUT_SECONDS` | 外部 RAG HTTP 请求超时时间，单位为秒 | `30` |
| `CONVAGENT_RAG_FALLBACK_TO_LOCAL` | 外部 RAG 失败后是否允许本地关键词降级 | `true`（允许） |
| `CONVAGENT_RAG_INCLUDE_RAW_RESPONSE` | 公共 JSON 输出是否包含外部原始响应 | `false`（不包含） |
| `CONVERSATION_AGENT_DATA_DIR` | JSON Store（JSON 数据存储）的根目录 | `./data` |
| `CONVAGENT_LLM_PROVIDER` | 默认大语言模型供应商 | `dashscope`（阿里云百炼） |
| `CONVAGENT_RUNTIME_MODE` | 运行模式，可设为 demo（演示）、test（测试）或 production（生产） | `demo`（演示） |
| `CONVAGENT_DASHSCOPE_API_KEY` | 项目专用百炼接口密钥，空值会回退读取标准密钥变量 | 空 |
| `CONVAGENT_DASHSCOPE_BASE_URL` | 百炼兼容接口基础地址；生产环境应覆盖为工作空间专属地址 | 共享北京地域地址 |
| `CONVAGENT_LLM_DEFAULT_PROFILE` | 当前默认模型档位 | `standard`（标准档） |

常见故障：external 未启动会自动 fallback；关闭 fallback 后 provider=none；知识库为空会得到低 confidence；数据目录不可写会影响 Store；classifier 默认未配置不是故障。

## 13. 架构取舍

### 为什么自研 Coordinator（协调器），而不是 LangGraph（状态图编排框架）

当前流程短、分支固定，自研 Coordinator 更容易断言 hard gate（前置硬门）和 trace（执行追踪）。LangGraph 适合状态图、并行节点、持久 checkpoint（状态检查点）和复杂恢复；当前引入会增加依赖和调试成本。**[未来方向]** 当 workflow（工作流）真正变复杂时可迁移为 StateGraph（状态图）。

### 为什么不是 Multi-Agent（多智能体）互相对话

Policy（安全策略）、RAG（检索增强生成）、Writer（内容写作）更适合作为确定性模块和工具。自由对话会放大 latency（延迟）、成本和不可预测性。项目保留“多专家职责”，但实现是一个 Coordinator 加模块。

### 为什么采用 rule-first（规则优先）加 optional classifier（可选分类器）

明确风险需要稳定、可解释、可测试；纯 LLM Policy 存在漂移和不可复现。只用规则又会漏掉语义改写，因此保留 classifier 接口。当前边界是默认没有在线 classifier。

### 为什么 external RAG（外部 RAG）独立、local fallback（本地降级）简单

知识引擎与业务编排生命周期不同。外部服务承担完整 RAG，本地只在故障时提供有限建议；简单是刻意边界，不是完整能力缺失的伪装。

### 为什么使用 JSON Store（JSON 数据存储）和 fake evaluation（模拟评测）

JSON（结构化数据格式）便于 V1（第一版）检查和测试，但不提供并发事务。Fake clients（模拟客户端）让错误分支和 Gate（评测门槛）可重复，但不能替代真实联调、负载和质量评测。

## 14. 项目边界与生产化

当前是具有最小同步 FastAPI（Web 应用接口框架）边界的工程作品和 orchestration reference（编排参考实现），不是商用生产服务。

**[未来方向]** 生产化至少需要后台 worker（任务进程）、数据库与迁移、租户鉴权和 RBAC（基于角色的访问控制）、PII（个人可识别信息）处理、持久幂等和审计、连接池、重试/退避/熔断、限流、指标与 tracing（分布式追踪）、真实 RAG 联调集、规则配置审批、classifier（分类器）校准、红队测试和 SLO（服务等级目标）。

## 15. 面试问答与追问树

### 15.1 30 秒介绍

我实现了一个面向企业采购售前的 Safety-Grounded Agent Orchestration Layer（以安全策略为基础的智能体编排层）。系统先用 Business Safety Policy（业务安全策略）做前置硬门，再通过 IntentRouter（意图路由器）和 TaskRouter（任务路由器）执行销售、报告或知识问答任务。知识问答复用外部 RAG_demo（企业知识引擎），支持结构化错误、本地低置信度 fallback（降级）、citation（引用）、diagnostics（调用诊断）和 trace（执行追踪），并用两套 deterministic evaluation（确定性评测）验证编排可靠性和业务安全边界。

### 15.2 高频追问示例

**问题：这是不是关键词匹配？**

- 主回答：当前是 rule-first deterministic guardrail，但规则集中在结构化 PolicyRule table，Engine 只负责解释、优先级和 fallback。
- 二次追问：换种表达绕过怎么办？
- 二次回答：规则负责高确定性边界；未命中时可调用 classifier。当前默认 classifier 未配置，因此不能声称覆盖所有语义变体。
- 证据：`policy/engine.py`、`policy/rules.py`、`test_policy.py`。
- 不能说：已经解决所有安全语义问题。

**问题：为什么不用 LangGraph？**

- 主回答：当前图固定且短，自研 Coordinator 更直接、可测。
- 二次追问：何时会迁移？
- 二次回答：出现并行、checkpoint、人工审批、长任务恢复时。
- 不能说：LangGraph 没价值，或当前已经使用 LangGraph。

**问题：当前 tests（测试）全部通过是否等于生产可用？**

- 主回答：只证明当前测试集通过。
- 二次追问：还缺什么？
- 二次回答：真实依赖联调、并发/故障注入、安全红队、监控和 SLO。
- 不能说：测试数量本身证明可靠性等级。

## 16. 最终复习清单

- 能画出 Policy-first 主链路和 external fallback 链路。
- 能解释 IntentRouter 与 TaskRouter。
- 能逐字段解释 PolicyDecision、RagResult 和 AgentStep。
- 能说出四种 provider（结果提供路径）条件和六种 external error（外部调用错误）。
- 能解释 `negative_patterns`（排除模式）的局部作用域。
- 能解释两个 Evaluation 的分母、Gate 和局限。
- 能承认 RealAgent 非主入口、local RAG 非完整 RAG、系统非生产级。
- 能从附录 A 在一分钟内定位源码和测试。

## 附录 A：源码、测试与文档映射

| 能力 | 核心源码 | 关键测试 | 章节 |
|---|---|---|---|
| Policy 主流程 | `policy/engine.py` | `test_policy.py` | 4.3、6.1、8 |
| 业务规则 | `policy/rules.py` | `test_policy.py` | 6.2、8 |
| Coordinator（协调器） | `orchestration/coordinator.py` | `test_orchestration.py` | 3、4、6.6 |
| HTTP API（超文本传输协议应用接口） | `api/app.py`, `api/models.py` | `test_api.py` | 3.1、5.9、6.24 |
| Request context（请求上下文） | `runtime/builder.py`, `runtime/models.py` | `test_application_service.py` | 3.1、5.9、6.22 |
| Application service（应用服务） | `application/service.py` | `test_application_service.py` | 3.1、6.23 |
| Task routing（任务路由） | `orchestration/task_router.py` | `test_task_router.py` | 3.4、6.5 |
| Intent routing（意图路由） | `sales/intent_router.py` | `test_intent_router.py` | 3.4、6.4 |
| External RAG（外部 RAG） | `rag/external_client.py` | `test_rag_external.py` | 4.1、9 |
| Local RAG（本地 RAG） | `rag/local_client.py`, `rag/module.py` | `test_rag.py` | 4.2、9 |
| Fallback（降级处理） | `rag/factory.py` | `test_rag_external.py` | 4.2、9 |
| Sales scoring（销售评分） | `sales/scorer.py`, `sales/health.py` | `test_scorer.py` | 5.7、6.14 |
| Memory（业务记忆存储） | `memory/` | `test_stores.py` | 5.6、6.11 |
| Tools（业务工具） | `tools/` | `test_tools.py` | 5.8、6.15 |
| Agent/LLM（智能体与大语言模型） | `agent.py`, `llm/` | `test_agent.py`, `test_llm.py`, `test_dashscope_client.py` | 6.16-6.19 |
| RAG Eval（RAG 评测） | `evaluation/rag_adapter.py` | `test_evaluation.py` | 4.6、10 |
| Policy Eval（策略评测） | `evaluation/policy_boundary.py` | `test_evaluation.py` | 4.7、10 |
| CLI（命令行界面） | `cli/main.py` | `test_cli.py` | 3.2、12 |

推荐阅读顺序：FastAPI Route（Web 接口路由） -> RequestContextBuilder（请求上下文构建器） -> ChatService（对话应用服务） -> Coordinator（协调器） -> Policy Engine（安全策略引擎） -> Rules（规则表） -> IntentRouter（意图路由器） -> TaskRouter（任务路由器） -> orchestration models（编排数据模型） -> RagClient protocol（RAG 客户端协议） -> Factory（客户端工厂） -> External（外部客户端） -> Local（本地客户端） -> Evaluation（评测） -> CLI（命令行界面） -> Sales/Memory/Tools（销售、记忆与工具模块） -> Agent/LLM（智能体与大语言模型）。

## 附录 B：口述自测题库

以下 32 题均应按“回答要点 -> 源码证据 -> 测试证据 -> 继续追问 -> 不能说”作答。括号给出最短导航。

### L1（一级）：一句话回答

| # / 问题 | 回答要点 | 源码 / 测试证据 | 继续追问 | 不能说 |
|---|---|---|---|---|
| 1. test_demo 是什么？ | 企业采购售前的安全编排层 | Coordinator；orchestration tests | 核心模块？ | 完整 RAG |
| 2. 与 RAG_demo 如何分工？ | 前者编排，后者知识引擎 | RagClient；external tests | 为何 HTTP 解耦？ | 当前代码 import RAG_demo |
| 3. Policy 为什么在 Router 前？ | 先阻止风险副作用 | Coordinator early return（提前返回）；`call_count=0`（RAG 调用次数为零） | 生成后过滤为何不够？ | 所有风险都能识别 |
| 4. 两个 Router 有何区别？ | `intent`（用户意图）是语义，`task`（执行任务）是动作 | intent/task router；各自 tests | 为何不合并？ | 二者并行 |
| 5. 四种 `provider`（结果提供路径）如何产生？ | external（外部）/local（本地）/fallback（降级）/none（无结果） | factory；rag external tests | 谁决定 provider？ | Coordinator 写 provider 分支 |
| 6. UNCERTAIN 是否拒绝？ | 不拒绝，继续执行并前置提示、限制 `confidence`（置信度） | Coordinator；policy eval | 与 BLOCKED 区别？ | UNCERTAIN 立即返回 |
| 7. `raw_response`（外部原始响应）默认如何处理？ | 内存可有，公共输出默认删 | RagResult/OrchestrationResult；public dict test | 哪个配置开启？ | 系统从不保存 raw data |
| 8. 两个 Eval 各评什么？ | adapter 可靠性；policy 边界 | evaluation modules；evaluation tests | 是否评真实召回？ | Eval 证明线上质量 |

### L2（二级）：解释设计理由

| # / 问题 | 回答要点 | 源码 / 测试证据 | 继续追问 | 不能说 |
|---|---|---|---|---|
| 9. 为什么不是 Agent 自由对话？ | 固定流程更可测、低延迟、易审计 | Coordinator；orchestration tests | 何时需要 Multi-Agent？ | 当前是自治多 Agent |
| 10. 为什么不用 LangGraph？ | 当前状态图短且固定 | Coordinator；trace tests | 何时迁移？ | LangGraph 没价值 |
| 11. 为什么不纯 LLM Policy？ | 明确风险需确定性和回归稳定 | engine/rules；classifier tests | 规则绕过怎么办？ | LLM 一定不安全 |
| 12. SAFE 为什么不进规则表？ | SAFE 是未命中风险后的默认结果 | rules/engine；safe tests | 如何防误伤？ | SAFE 是绝对安全认证 |
| 13. `negative_patterns`（排除模式）为什么不能全局放行？ | 只消除当前规则误报 | `_match_rules`；negative-scope test | 同句另有风险呢？ | 出现“根据资料”就全局 SAFE |
| 14. 为何 `provider`（结果提供路径）对 Coordinator 透明？ | 保持编排层只依赖 contract | RagClient/factory；client tests | 如何换 provider？ | Coordinator 无法观察结果 provider |
| 15. 为什么 `confidence`（降级结果置信度）上限为 0.55？ | 显式区分弱 fallback 与完整知识引擎 | factory；confidence assertion | 是校准概率吗？ | 0.55 有统计学证明 |
| 16. 为何 Eval 用 fake clients？ | 稳定复现错误和输出边界 | evaluation；evaluation tests | 真实联调怎么办？ | fake 等价真实服务 |

### L3（三级）：讲源码调用链

| # / 问题 | 回答要点 | 源码 / 测试证据 | 继续追问 | 不能说 |
|---|---|---|---|---|
| 17. QA 完整链路？ | CLI -> Policy -> Routers -> override -> RagClient -> trace -> public output | CLI/Coordinator；CLI tests | override 在哪生效？ | qa 绕过 Policy |
| 18. external timeout 后如何变化？ | error -> local -> fallback/cap/warning/two diagnostics | external/factory；fallback tests | fallback 也失败呢？ | 静默保持 external |
| 19. BLOCKED 为何一个 trace？ | policy step 后 immediate return | Coordinator；orchestration test | `metadata`（运行元数据）如何写？ | 先调用 RAG 再过滤 |
| 20. UNCERTAIN 如何影响结果？ | router warning（路由警告）、模板前置、`confidence`（置信度）取较小值 | Coordinator；policy eval | 是否有专门 workflow？ | 已有人工审批流 |
| 21. `diagnostics`（调用诊断）在哪里转 trace？ | Coordinator `_run_qa()` 循环转换 | coordinator；diagnostic test | 无 diagnostics 呢？ | client 直接创建 AgentStep |
| 22. citation 如何归一化？ | 兼容 `id`（标识）、`text`（证据文本）、`score`（相关度分值）等字段 | external client；citation tests | schema 变化怎么办？ | 映射证明 citation 正确 |
| 23. `task_override`（显式任务覆盖）能否绕过 Policy？ | 不能；在 Policy 与 Router 后替换 TaskRoute | Coordinator；blocked override test | 为何仍运行 Router？ | override 是安全后门 |
| 24. JSON 如何隐藏 raw？ | CLI helper -> orchestration public dict -> rag public dict | 三处源码；raw tests | human output 呢？ | 裸 model_dump 用于公开输出 |

### L4（四级）：压力追问

| # / 问题 | 回答要点 | 源码 / 测试证据 | 继续追问 | 不能说 |
|---|---|---|---|---|
| 25. pattern 被绕过怎么办？ | 未命中可接 classifier；默认仍有召回边界 | engine；classifier tests | 当前是否在线？ | 默认已覆盖全部语义 |
| 26. classifier 挂掉怎么办？ | catch exception，按配置 SAFE/UNCERTAIN，写 `warnings`（警告） | engine；failure test | 默认为何 SAFE？ | 故障没有风险 |
| 27. external 长期挂掉怎么办？ | 当前 local fallback；生产加重试、熔断、告警 | factory；fallback eval | local 是否足够？ | 已有生产熔断 |
| 28. local RAG 简单为何保留？ | 低成本连续性和可离线测试 | local/module；rag tests | 无证据呢？ | 它等价企业 RAG |
| 29. tests（测试）全部通过为何不等于生产？ | 缺真实负载、依赖、红队、长期运行 | 全部 tests（测试）；validation（验证） | 测试仍证明什么？ | 测试数量等于可靠等级 |
| 30. JSON Store 能否生产？ | 当前适合 demo；生产需 DB、事务、锁、权限 | memory；store tests | 如何迁移？ | JSON 支持高并发事务 |
| 31. `citation_coverage`（引用覆盖率）高代表正确吗？ | 只代表输出保留来源 | rag eval；metric tests | 如何评正确性？ | coverage 等于 grounded accuracy |
| 32. 业务增长先改哪里？ | 按观测瓶颈：并发先存储/服务，复杂流先编排，风险先 policy | 当前边界与 tests | 如何定优先级？ | 脱离指标给唯一答案 |

口述验收：随机抽取至少 20 题，不看答案说明实现、理由、代码位置、测试证据和边界；答不上时按附录 A 回到源码和测试。

## 17. M1.3（里程碑 1.3）认证与授权源码链

### 17.1 对象状态变化

| 阶段 | 输入 | 输出 | 失败行为 |
|---|---|---|---|
| `RequestMetadataMiddleware`（请求元数据中间件） | HTTP Request（HTTP 请求） | `RequestMetadata`（请求标识、追踪标识、接收时间） | 后续错误复用同一组标识 |
| `BearerTokenParser`（Bearer 令牌解析器） | ASGI raw headers（ASGI 原始请求头） | 单个 ASCII Token（ASCII 令牌）或演示模式空值 | 格式错误返回 400，不进入 Coordinator |
| `JWTVerifier`（JWT 验证器） | Token、JOSE Header（JOSE 令牌头）、可信公钥 | `VerifiedClaims`（签名后已验证声明） | 无效令牌返回 401；密钥服务失败返回 503 |
| `PrincipalMappingPolicy`（主体映射策略） | 已验证声明、服务端租户配置 | `Principal`（可信主体） | Token 不能覆盖租户和组织边界 |
| `AuthorizationService`（授权服务） | 主体、Route permissions（接口权限要求） | `AuthorizationDecision`（不可变授权快照） | 权限不足返回 403，Coordinator 调用数为 0 |
| `RequestContextBuilder`（请求上下文构建器） | 元数据、主体、授权决定、版本快照 | `RequestContext`（可信运行上下文） | Scope（资源范围）必须与主体租户和组织一致 |
| `ResponseProjector`（响应投影器） | 内部编排结果、授权快照 | `PublicAgentResponse`（公开智能体响应） | 默认不复制原始 RAG 载荷 |

### 17.2 JWKS（JSON Web 密钥集）刷新

`RemoteJwksProvider`（远程密钥提供器）使用共享 `httpx.AsyncClient`（异步 HTTP 客户端）流式读取解压后的响应字节。`JwksDocumentParser`（密钥文档解析器）拒绝重复 JSON（JavaScript 对象表示法）成员，`JwkSecurityValidator`（密钥安全校验器）拒绝短 RSA 密钥、私钥字段和非签名用途密钥。

刷新不是边读边改缓存，而是完整下载、解析和验证后构造新的 immutable key map（不可变密钥映射），最后一次性替换。失败时 generation（缓存代际）、positive cache（正缓存）和 expires_at（过期时间）均保持不变。过期文档不使用 stale key（过期密钥）继续验证。

### 17.3 权限与敏感输出

`effective_permissions`（有效权限）是所有已知角色权限的确定性并集。`debug_viewer`（调试查看角色）只有 `raw_response:view`（查看原始响应权限），不能独立调用 `/v1/chat`（通用对话接口）或 `/v1/qa`（知识问答接口）。公开 `debug`（调试载荷）必须同时满足配置开关和权限 Gate（门控）。

**[测试确认]** 安全测试覆盖 Demo（演示模式）不回退、JOSE Header 拒绝、严格 Claim（声明）类型、RSA 密钥长度、JWKS 并发刷新、权限并集、Policy BLOCKED（业务安全阻断）和调试载荷双重门控。

**不能夸大：** 当前没有 OIDC Login（OIDC 登录）、Discovery（服务发现）、Token Introspection（令牌内省）、Refresh Token（刷新令牌）、实时撤销、真实企业 IdP（身份提供商）联调、持久 Audit（审计）或多实例 JWKS 协调。

### 17.4 M1.3 请求场景

以下八个场景展示一次 HTTP 请求从收到到返回的完整流转。所有场景中 `RequestMetadataMiddleware` 首先生成 `request_id` 和 `trace_id`（后续错误复用同一组标识）。

#### 场景 1：demo 模式无 Authorization Header

```text
POST /v1/chat {"text": "query"} (无 Authorization Header)
→ BearerTokenParser.parse() → None (无 Header)
→ runtime_mode="demo" → development_security_context()
  → Principal(tenant_id="single_tenant", user_id="local_api_user", roles=("agent_user",))
→ AuthorizationService.authorize() → AuthorizationDecision(allowed=True)
→ RequestContextBuilder.build() → RequestContext
→ ChatService.execute() → Coordinator.run()
→ ResponseProjector.project() → AgentResponse (HTTP 200)
```

**[测试]** `test_api.py::test_chat_returns_public_envelope_and_request_headers`

#### 场景 2：demo 模式提交无效 Token（不回退）

```text
POST /v1/chat (Authorization: Bearer invalid.token.value)
→ BearerTokenParser.parse() → "invalid.token.value"
→ JWTVerifier.verify() → AuthenticationFailure("invalid_access_token", 401)
→ SecurityBoundaryError(status_code=401)
→ Coordinator 未调用 (call_count = 0)
→ HTTP 401 + WWW-Authenticate: Bearer error="invalid_token"
```

**[测试]** `test_security.py::test_demo_invalid_token_never_falls_back_to_development_principal`

#### 场景 3：Malformed Bearer Header

```text
POST /v1/chat (两个 Authorization Header 或非 ASCII 或超大)
→ BearerTokenParser.parse() → AuthenticationFailure("invalid_request", 400)
→ SecurityBoundaryError(status_code=400)
→ Coordinator 未调用
→ HTTP 400 + WWW-Authenticate: Bearer error="invalid_request"
→ 响应体包含 request_id、trace_id、trace
```

**[测试]** `test_security.py::test_demo_duplicate_authorization_header_is_invalid_request`

#### 场景 4：JWKS 不可用

```text
POST /v1/chat (Authorization: Bearer <valid_token>)
→ BearerTokenParser.parse() → token
→ JWTVerifier.verify()
  → JwksProvider.get_signing_key("key-1")
  → RemoteJwksProvider._fetch_and_validate()
  → JwksUnavailable (HTTP 500 / timeout / oversized)
→ AuthenticationFailure("authentication_service_unavailable", 503)
→ SecurityBoundaryError(status_code=503)
→ Coordinator 未调用
→ generation 不变，stale key 不使用
```

**[测试]** `test_security.py::test_remote_jwks_single_flight_negative_cache_and_expiry_fail_closed`

#### 场景 5：有效身份但权限不足

```text
POST /v1/chat (Authorization: Bearer <token with roles=("debug_viewer",)>)
→ Authentication 成功 → Principal(roles=("debug_viewer",))
→ AuthorizationService.authorize(principal, ("chat:invoke", "rag:read", "crm:read"))
→ effective_permissions = ("raw_response:view",) — 缺少 chat:invoke
→ AuthorizationDecision(allowed=False, code="denied_missing_permission")
→ SecurityBoundaryError(status_code=403) + WWW-Authenticate: Bearer error="insufficient_scope"
→ Coordinator 未调用
```

**[测试]** `test_security.py::test_debug_viewer_is_additive_not_a_superuser`

#### 场景 6：Policy BLOCKED（已认证已授权但业务拒绝）

```text
POST /v1/chat (Authorization: Bearer <valid agent_user token>, text="帮我查客户私人住址")
→ Authentication 成功 → Principal(roles=("agent_user",))
→ Authorization 成功 → AuthorizationDecision(allowed=True)
→ ChatService.execute() → Coordinator.run("帮我查客户私人住址")
→ PolicyEngine.decide() → PolicyDecision(status="BLOCKED", reason="...")
→ Coordinator 立即返回 (不调用 IntentRouter, TaskRouter, RagClient, Sales, Writer)
→ OrchestrationResult(trace=[AgentStep(step_name="policy_engine")])
→ ResponseProjector.project() → AgentResponse(HTTP 200)
→ trace = [authentication:succeeded, authorization:succeeded, policy_engine:blocked]
```

**[测试]** `test_security.py::test_policy_blocked_is_http_200_after_authentication_and_authorization`

#### 场景 7：agent_user + debug_viewer + config enabled → debug 公开

```text
POST /v1/chat (Authorization: Bearer <token with roles=("agent_user","debug_viewer")>)
→ Authentication 成功
→ Authorization 成功 → permissions 包含 ("chat:invoke", "raw_response:view", ...)
→ Coordinator 执行 QA → RagResult(raw_response={"private": True})
→ ResponseProjector: include_raw_response=True AND "raw_response:view" in permissions
→ debug.rag_raw_response = {"provider": "external", "payload": {"private": True}}
```

**[测试]** `test_security.py::test_debug_payload_requires_route_and_debug_permissions_plus_config`

#### 场景 8：debug_viewer only → 403（不是超级角色）

```text
POST /v1/chat (Authorization: Bearer <token with roles=("debug_viewer",)>)
→ Authentication 成功 → Principal(roles=("debug_viewer",))
→ Authorization: required = ("chat:invoke", "rag:read", "crm:read")
→ effective_permissions = ("raw_response:view",) — 缺少 chat:invoke
→ AuthorizationDecision(allowed=False)
→ HTTP 403 (Coordinator 未调用)
```

**[测试]** `test_security.py::test_debug_viewer_is_additive_not_a_superuser`

### 17.5 Demo vs Test vs Production 身份语义

| 场景 | demo | test | production |
|---|---|---|---|
| 无 Authorization Header | development Principal | HTTP 401 | HTTP 401 |
| 有效 Token | 认证+授权 | 认证+授权 | 认证+授权（要求 HTTPS JWKS） |
| 无效 Token | HTTP 401（不回退） | HTTP 401 | HTTP 401 |
| Malformed Header | HTTP 400 | HTTP 400 | HTTP 400 |
| JWKS Unavailable | HTTP 503 | HTTP 503 | HTTP 503 |
| Permission Denied | HTTP 403 | HTTP 403 | HTTP 403 |

`AppConfig.runtime_mode` 是唯一运行模式来源。

## 18. M1.4-E 持久化 HTTP 场景

### 场景 1：首次执行与 Replay（结果重放）

```text
POST /v1/chat + Idempotency-Key（幂等键）
→ 当前 AuthN（身份认证）与 AuthZ（权限授权）成功
→ Transaction A（请求接收短事务）提交 ACTIVE Claim（执行中声明）
→ Coordinator（协调器）在事务外执行一次
→ Transaction B（结果终结短事务）提交 COMPLETED（已完成）
→ HTTP 200 + Idempotency-Status: executed（已执行）

同 Key + 同解析后 DTO（数据传输对象）
→ 当前 AuthN/AuthZ 再次执行
→ 读取 Snapshot（快照），不调用 Coordinator
→ 当前 ResponseProjector（响应投影器）再次投影
→ HTTP 200 + Idempotency-Status: replayed（已重放）
```

### 场景 2：并发、冲突和历史失败

ACTIVE（执行中）记录尚未完成时，同 Scope（作用域）、同 Key 请求返回 HTTP 409 `idempotency_request_in_progress`（幂等请求正在执行）。同 Key 但完整 Fingerprint（请求指纹）不同返回 HTTP 409 `idempotency_key_conflict`（幂等键冲突）。首次 Coordinator 执行失败沿用安全 HTTP 500 `application_execution_error`（应用执行错误）；有效 Terminal TTL（终态有效期）内再次请求返回 HTTP 409 `idempotency_previous_failure`（幂等历史失败），不会重新执行。

### 场景 3：投影失败与取消

Transaction B 已提交后若 ResponseProjector（响应投影器）失败，返回安全 HTTP 500 `response_projection_failed`（响应投影失败），数据库保持 COMPLETED；同 Key 重试进入 Replay。客户端 cancellation（取消）发生在 Transaction A 后时正常向上传播，不写 FAILED（失败终态），ACTIVE Claim 保留并等待 Lease（租约）到期后的请求驱动 Reclaim（回收）。

## 19. M1.4-F 故障恢复与运维场景

### 场景 1：真实进程崩溃后请求驱动 Reclaim（回收）

```text
子进程 A 提交 Transaction A（请求接收事务）
→ 数据库存在 ACTIVE Claim + in_progress Request + request_accepted Audit
→ Coordinator（协调器）阻塞，进程 A 被强制终止
→ Lease（租约）到期前，同请求返回 IN_PROGRESS（执行中）
→ Lease 到期后，进程 B 原子 Reclaim
→ claim_version（声明版本）递增，旧 Request 以固定管理失败码收尾
→ B 完成唯一有效 Run（运行记录）
```

该机制只回收数据库提交权，不能物理取消旧 Worker（工作进程）中的 Coordinator。旧 Owner（所有者）即使继续运行，也会被 Transaction B 的 Fencing（执行权隔离）拒绝。

### 场景 2：数据库中断与 Readiness（就绪状态）恢复

测试使用任务内 TCP proxy（传输控制协议代理）切断数据库网络，不停止或修改用户数据库：

```text
数据库可达 → /readyz = 200
代理切断 → /healthz 保持存活；/readyz = 503；业务请求不降级
代理恢复 → 连接池建立新连接；/readyz 恢复 200；新请求继续执行
```

### 场景 3：Prune（清理）与 TTL Reacquire（到期重新声明）竞争

Prune 使用数据库时间和 `FOR UPDATE SKIP LOCKED`（跳过已锁行）选择过期终态，并在删除前再次验证状态和 `expires_at`（到期时间）。若并发 Reacquire 已锁定并转换记录，Prune 跳过该行；若 Prune 先合法删除，后续请求创建新声明。两条路径只产生一个合法结果，ACTIVE 永不被删除。

### 场景 4：逻辑备份与全新数据库恢复

任务创建独立源数据库并写入 completed、blocked、failed、Replay、ACTIVE 和 Reclaim 代际数据，随后使用 PostgreSQL 17 `pg_dump`（逻辑备份工具）和 `pg_restore`（逻辑恢复工具）恢复到全新数据库。恢复后的 Revision（迁移版本）、四表行数和 Integrity Checker（完整性检查器）全部通过，临时数据库与备份文件在 `finally`（最终清理块）中删除。

这只证明 local logical backup drill（本地逻辑备份演练），不表示 encrypted production backup（加密生产备份）、PITR（时间点恢复）或 WAL archive（预写日志归档）已经部署。完整证据见 [M1.4-F Closeout](phases/m1_4f_operational_readiness_closeout.md)。
