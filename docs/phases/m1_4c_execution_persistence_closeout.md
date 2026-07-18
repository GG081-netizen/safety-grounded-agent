# M1.4-C Execution Persistence Closeout（执行持久化阶段收尾）

## 1. 状态

```text
status = completed
schema_revision = 0001
schema_changed = no
runtime_idempotency = not_implemented
fastapi_database_wiring = not_implemented
m1_4d_status = next_not_started
```

M1.4-C（里程碑 1.4-C）实现执行路径的 Repository（仓储）、Unit of Work（工作单元）、持久化 Mapper（映射器）和两段短事务应用组件。它没有把数据库接入 FastAPI Route（FastAPI 路由），也没有实施 M1.4-D 的 idempotency（幂等）、Fencing（执行权隔离）或 Replay（重放）。

## 2. 源码入口

| 源码入口 | 中文实际含义 |
|---|---|
| `application/durable_service.py::DurableApplicationService` | 以两个独立短事务包围同步业务执行的持久化应用组件 |
| `application/persistence_mappers.py` | 将可信应用 Contract 映射为最小化数据库记录与版本化快照 |
| `database/repository.py::ExecutionRepository` | M1.4-C 所需的窄执行仓储协议 |
| `database/unit_of_work.py::ExecutionUnitOfWork` | 执行持久化工作单元协议 |
| `database/sqlalchemy_repository.py::SQLAlchemyExecutionRepository` | 基于 SQLAlchemy 异步会话的执行仓储实现 |
| `database/sqlalchemy_uow.py::SQLAlchemyExecutionUnitOfWork` | 管理提交、回滚和会话关闭的 SQLAlchemy 工作单元 |
| `database/fake_execution.py` | 支持事务暂存和故障注入的内存测试实现 |

字段与逐步调用细节继续由 [Project Walkthrough（项目走读）](../project-walkthrough.md) 承担。

## 3. 事务语义

Transaction A（事务 A）只创建 `AgentRequest`（智能体请求）与 `request_accepted`（请求已接受）审计。提交成功后才生成 `run_id`（运行标识）和 `run_started_at`（运行开始时间），并在线程中调用 `ChatService.execute_with_context()`（使用既有上下文执行对话服务）。Coordinator（协调器）返回或失败后生成 `run_completed_at`（运行完成时间），Transaction B（事务 B）再原子写入 `AgentRun`（智能体运行）、请求终态与完成/阻断/失败审计。

BLOCKED（策略阻断）只由 `ApplicationResult.orchestration.policy.is_blocked`（应用结果中的结构化策略阻断标志）判断。没有根据回答文本、拒绝文案、HTTP 状态、citation（引用）或 RAG 调用情况推断。

## 4. 数据最小化

- `user_text_length`（用户文本长度）使用 Python `len(user_text)` 的 Unicode code point（Unicode 代码点）数量。
- `user_text_hash`（用户文本哈希）使用传入文本 UTF-8 编码后的 SHA-256。
- Mapper（映射器）不执行 trim/lower/normalize（去空白/转小写/规范化）。既有 `UserRequest`（用户请求）会在进入服务前按公开 Contract 去除首尾空白，因此数据库记录基于应用层验证后的文本。
- result snapshot（结果快照）仅保存回答长度、回答哈希、引用数量、结果类型和状态，不保存回答正文。
- trace snapshot（追踪快照）仅保存阶段名称、策略结果、规则标识、路由任务和 RAG provider（检索结果提供路径），不保存 Prompt（提示词）、原始响应或工具载荷。
- audit details（审计详情）只保存批准的版本化结构字段。

## 5. 错误与时间边界

`SQLAlchemyExecutionUnitOfWork.commit()`（工作单元提交）失败时尝试 rollback（回滚），保留安全异常链并关闭 Session（数据库会话）；失败后的工作单元不能再次提交。`__aexit__()`（异步退出边界）始终返回 false（不吞异常）。公开错误不包含 SQL statement（SQL 语句）、parameters（参数）或数据库 URL（地址）。

所有持久化日期时间必须是 timezone-aware UTC datetime（带时区的 UTC 日期时间），由注入 Clock（时钟）产生。同一次状态转换复用同一个事件时间，Mapper 不自行读取当前时间。

## 6. 验收证据

```text
collected_nodeids = 573
legacy_m1_1_nodeids = 347
legacy_m1_4_b_nodeids = 529
pre_m1_4c_nodeids = 530
missing_legacy_nodeids = 0

unit_passed = 522
unit_deselected = 51
default_passed = 522
default_skipped = 51
postgres_non_destructive_passed = 48
postgres_non_destructive_skipped = 2
postgres_destructive_passed = 50
postgres_destructive_skipped = 0
final_revision = 0001 (head)
policy_boundary = PASS
rag_adapter = PASS
uv_lock_changed = no
```

真实 PostgreSQL 17 验证覆盖事务 A 的独立可见性、未提交回滚、事务 B 原子性、唯一约束与状态冲突、安全失败记录、敏感字段排除、运行 SQL 不引用幂等表，以及两项 destructive migration（破坏性迁移）循环。

## 7. CI 与环境

```text
postgres_ci_job = implemented
postgres_ci_workflow_static_validation = PASS
postgres_ci_runtime_execution = not_run_in_current_environment
local_equivalent_postgres_ci_gate = PASS
task_postgres_container_created = yes
task_postgres_container_and_volume_cleaned = yes
git_diff_gate = unavailable
git_worktree_clean = not_asserted
```

CI Job（持续集成任务）的 YAML（配置语法）、精确 JUnit（测试报告）计数与关键测试节点结构已静态验证。本环境没有真实 GitHub Actions Run（GitHub 自动化运行），不能声称 CI Job 已实际运行通过。

## 8. 下一阶段

M1.4-D 可以开始设计原子 idempotency claim（幂等声明）、Fencing（执行权隔离）、冲突和 Replay（重放），但本轮没有开始这些能力。M1.4-E 的 FastAPI 数据库接入同样未实施。
