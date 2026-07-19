# RAG Fallback Demo

## 场景

确定性外部 RAG 适配器超时模拟 → 本地 Fallback → 置信度上限。
本场景不涉及真实外部服务故障。

## 请求路径

```
POST /v1/qa  (demo mode, no auth, task_override=qa)
  → PolicyEngine: SAFE
  → IntentRouter → TaskRouter: qa task
  → TimeoutRagClient (deterministic stub): RagTimeoutError
  → FallbackRagClient: activate local fallback
  → confidence capped at 0.55
  → warning: "External RAG unavailable; used local keyword fallback."
```

## 证明能力

- External RAG 超时时的优雅降级
- Fallback 置信度上限 0.55
- Provider 标记为 fallback（非 external）
- Warning 对用户可见
- 不存在伪造的 External Citation
- 全流程零网络访问

## 生成命令

```bash
uv run python scripts/generate_portfolio_examples.py
uv run python scripts/generate_portfolio_examples.py --check
```
