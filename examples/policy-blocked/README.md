# Policy Blocked Demo

## 场景

请求查询客户负责人的私人住址和宗教信仰，Policy 硬阻断。

## 请求路径

```
POST /v1/chat  (demo mode, no auth)
  → PolicyEngine: BLOCKED (privacy violation)
  → [STOP] Router/Coordinator/RAG 均未执行
  → 返回标准拒绝消息
```

## 证明能力

- Policy 在 Router 和 RAG 之前阻断高风险请求
- BLOCKED 后零 downstream execution
- 不返回私人信息推测或虚假 Citation
- Trace 仅包含 policy_engine stage

## 生成命令

```bash
uv run python scripts/generate_portfolio_examples.py
uv run python scripts/generate_portfolio_examples.py --check
```
