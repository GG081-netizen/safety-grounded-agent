# Procurement Planning Demo

## 场景

为80名研发人员规划办公工作站采购，通过安全策略编排系统生成配置建议。

## 请求路径

```
POST /v1/qa  (demo mode, no auth, task_override=qa)
  → PolicyEngine: SAFE
  → IntentRouter: intent detection
  → TaskRouter + task_override: qa
  → DeterministicRagClient: procurement knowledge query
  → ResponseProjector: AgentResponse assembly
```

## 证明能力

- Policy 放行正常业务请求
- 确定性任务编排（通过 /v1/qa endpoint 的 task_override）
- RAG 返回结构化引用和证据
- 全链路 Trace 完整记录
- 零网络访问的确定性生成

## 生成命令

```bash
uv run python scripts/generate_portfolio_examples.py
uv run python scripts/generate_portfolio_examples.py --check
```
