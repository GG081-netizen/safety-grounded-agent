# Idempotency Client Contract（幂等客户端契约）

每个逻辑操作生成一个不含个人信息或业务秘密的 Opaque Key（不透明键）。重试必须使用同一 Key 与同一解析后 DTO（数据传输对象）；同 Key 不得发送不同 Payload（负载），也不得永久复用。

`409 idempotency_request_in_progress`（请求处理中）应遵循 `Retry-After`（重试等待）；`409 idempotency_key_conflict`（键冲突）需要新逻辑操作；`409 idempotency_previous_failure`（历史失败）不应立即自动重试。`503` 或首次 `response_projection_failed`（响应投影失败）后可使用同 Key 重试，数据库真实状态会决定 IN_PROGRESS（处理中）、Replay（重放）或重新 Claim（声明）。成功的 `Idempotency-Status: executed|replayed`（幂等状态：已执行/已重放）都表示业务结果已完成。

Reverse Proxy / Ingress（反向代理/入口）不得合并重复 `Idempotency-Key`，不得将两个 Header（请求头）改成逗号值；应允许该请求头、暴露 `Idempotency-Status` 和 `Retry-After`，并在 Access Log（访问日志）中脱敏。OPTIONS（预检）、`/healthz`、`/readyz`、文档端点不进入 Durable（持久化）或 Idempotency（幂等）主链。

Ambiguous Commit（提交结果不确定）时服务返回安全 `503`，不在同一进程自动重试。Transaction A 不确定时不得执行 Coordinator（协调器）；Transaction B 不确定时不得返回成功或改写 FAILED（失败）。后续同 Key 请求必须读取数据库真实状态。
