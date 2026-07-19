# M1.3（里程碑 1.3）— OIDC（OpenID Connect）兼容 JWT（JSON Web Token）Bearer 认证与授权收尾

## 概述（Summary）

M1.3 实现了 OIDC-compatible JWT Bearer 资源服务器认证边界和确定性 RBAC/ABAC 授权，接入 FastAPI 主链。Coordinator 继续保持纯业务编排职责，不感知 HTTP Header、JWT、JWKS、OIDC 配置或权限策略。

## Implementation Scope

- 实现内容：JWT Bearer Token 解析、JOSE Header 验证、异步 Static/Remote JWKS Provider、JWT 签名与 Claims 验证、Principal 映射、确定性 RBAC/ABAC 授权、AuthN/AuthZ/Policy 三层分层 Trace、ResponseProjector 安全投影
- 明确不实现：OIDC 登录、Discovery、Token Introspection、Refresh Token、实时撤销、持久审计、真实企业 IdP 联调

## Key Implementation Changes

| 变更 | 位置 |
|---|---|
| `RequestMetadata` 中间件与 `RequestSecurityService` | `api/app.py`, `api/security.py` |
| `BearerTokenParser` — 严格单 Bearer 凭证解析 | `identity/authentication.py` |
| `JWTVerifier` — JOSE Header Policy + JWT 签名/Claims 验证 | `identity/authentication.py` |
| `StaticJwksProvider` — 验证后内存 JWKS | `identity/jwks.py` |
| `RemoteJwksProvider` — 异步流式 JWKS + 事务刷新 + single-flight + negative cache + generation | `identity/jwks.py` |
| `JwkSecurityValidator` / `JwksDocumentParser` — 严格 JWK 校验 | `identity/jwks.py` |
| `VerifiedClaims` — 签名后不可变声明 | `identity/authentication.py` |
| `PrincipalMappingPolicy` — VerifiedClaims → Principal | `identity/authentication.py` |
| `Principal` — 服务端可信主体模型 | `identity/models.py` |
| `AuthorizationService` — 确定性 RBAC/ABAC 权限并集 | `authorization/service.py` |
| `AuthorizationDecision` — 不可变授权快照 | `authorization/models.py` |
| `ResponseProjector` — 安全投影 + 双重 Gate | `api/projector.py` |
| OIDC 配置模型 | `config.py::OIDCConfig` |
| 安全测试套件（27 项） | `tests/test_security.py` |

## JWT / JOSE Security Boundary

- 算法硬编码 `RS256`，不信任 Token Header
  - `alg` 必须为 `RS256`
  - 拒绝 `jku`、`x5u`、`jwk`、`x5c`、`crit` 等未批准 JOSE Header
  - 拒绝 `alg: none` 和对称算法
- RSA 公钥下限 2048 bit，拒绝私钥字段（`d`、`p`、`q`、`dp`、`dq`、`qi`、`oth`）
- 严格 NumericDate 类型检查：`iat`、`exp`、`nbf` 必须为 Python `int`
- Claims 严格验证：issuer、audience、organization_id、token_use、roles、groups、enabled
- 重复 audience 值拒绝、int audience 成员拒绝
- `max_token_lifetime_seconds` 限制 Token 有效期上限

## JWKS Provider — Caching & Refresh

- **StaticJwksProvider**：验证后内存提供，适用于测试和离线部署
- **RemoteJwksProvider**：
  - 异步流式读取，限制响应大小（`jwks_max_response_bytes`）
  - 事务式刷新：完整下载→解析→验证→一次性替换 immutable key map
  - Single-flight：并发请求同一 kid 时只有一次实际 fetch
  - Negative-kid cache：按 generation 绑定，TTL 后可重新发现
  - Generation 机制：每次刷新递增，失效所有旧 negative entries
  - 过期后 fail-closed：不使用 stale key 继续验证
  - 禁止 redirect

## Principal Mapping & Authorization

- `PrincipalMappingPolicy` 将 `VerifiedClaims` 映射为 `Principal`，tenant/organization 由服务端配置决定
- `AuthorizationService.authorize()` 执行确定性 RBAC/ABAC：
  - 角色→权限映射表 `ROLE_PERMISSIONS` 集中定义
  - 有效权限是所有已知角色权限的确定性并集
  - `disabled` Principal 统一拒绝
- `debug_viewer` 角色只拥有 `raw_response:view` 权限，不获得 chat/qa 调用权限；不是超级角色

## HTTP Semantics

| HTTP Status | 条件 | Coordinator 调用 |
|---|---|---|
| 400 | Malformed Bearer Header（多余 Authorization、非 ASCII、超大） | call_count = 0 |
| 401 | 无效 Token（签名失败、JOSE Header 违规、Claims 违规、组织不匹配） | call_count = 0 |
| 403 | 有效身份但权限不足或 Principal 被禁用 | call_count = 0 |
| 503 | JWKS 不可用 | call_count = 0 |
| 200 | Policy BLOCKED（已认证已授权，但业务安全策略拒绝） | 调用但仅执行 policy_engine |

## Runtime Mode Identity Semantics

| Runtime Mode | 无 Authorization Header | 提交无效 Token |
|---|---|---|
| demo | 使用 development Principal | 401，不回退 development Principal |
| test | 401（禁止占位身份） | 401 |
| production | 401（禁止占位身份） | 401 |

## Response Projection

- `ResponseProjector.project()` 将内部 `ApplicationResult` 投影为公共 `AgentResponse`
- `raw_response` 默认不公开：`include_raw_response=false` 时 `debug` 为 `null`
- 公开需同时满足：
  1. `rag_service.include_raw_response = true`（配置开关）
  2. `raw_response:view` 在 `AuthorizationDecision.permissions` 中（权限 Gate）
- Trace 分层：authentication → authorization → orchestration

## Authentication ≠ Authorization ≠ Policy

- **Authentication**：验证 Bearer Token 的签名和 Claims，输出 `VerifiedClaims`
- **Authorization**：将 Principal 的 roles 映射为 permissions，与 Route 所需权限比较，输出 `AuthorizationDecision`
- **Policy**（Coordinator 内部）：对已认证已授权请求执行业务安全规则（BLOCKED/UNCERTAIN/SAFE），后于 AuthN/AuthZ

## Test Coverage

- `tests/test_security.py`：27 项安全测试
  - Token 解析、JOSE Header 拒绝（5 个禁止字段）、NumericDate 类型检查（3 个 claims）、Strict Claims（7 类畸形值）
  - Demo/Test/Production 模式身份语义、debug_viewer 权限语义、双重 Gate
  - JWKS 短 RSA 拒绝、4096-bit RSA 接受、私钥拒绝、重复 JSON 成员拒绝
  - Remote JWKS single-flight、negative cache、过期后 fail-closed、流式大小限制、key rotation 发现
  - Policy BLOCKED HTTP 200 trace 验证
- `tests/test_productization_contracts.py`：可信 Contract 不变性验证
- `tests/test_api.py`：API 边界测试
- `tests/test_application_service.py`：应用服务层测试

## Final Acceptance Results

```text
unit_passed = 423
full_passed = 423
full_skipped = 1    (requires dedicated API key, CI runs offline by default)
collected = 424
legacy_nodeids = 347
missing_legacy = 0
policy_boundary = PASS
rag_adapter = PASS
release_residues = 0
uv.lock_changed_during_validation = no
uv.lock_sha256 = E1A8D1F1555F492AE0280A12527E0B4505158D1FEB624E671AE0BF193D4A38D7
```

## State Snapshot

```text
authentication_runtime_status = implemented
authorization_runtime_status = implemented
authorization_strategy = conservative_route_union

supported_jwt_algorithm = RS256
jwks_provider = static_and_remote_async
jwks_single_flight = implemented
jwks_stale_if_error = disabled

tenant_mode = single_tenant
organization_mode = single_controlled_organization

demo_placeholder_principal = no_header_only
production_placeholder_principal = forbidden

raw_response_default_exposure = false
raw_response_gate = config_and_permission

oidc_login_status = not_implemented
enterprise_idp_integration = not_implemented
token_introspection = not_implemented
real_time_revocation = not_implemented
persistent_audit = not_implemented
```

## Known Limitations

- 不支持 RS256 以外的 JWT 算法
- JWKS 缓存仅在进程内，多实例部署不协调
- Principal 没有实时禁用/撤销机制（Token 签发后至过期前持续有效）
- 组织 ID 硬编码为单值；多组织场景需扩展 PrincipalMappingPolicy
- 权限表为代码常量，非动态管理后台
- 单租户、单受控组织模式
- 无 OIDC Login、Discovery、Token Introspection、Refresh Token
- 无持久审计

## Next Phase

→ M1.4: PostgreSQL Persistence & Idempotency

See [sme-productization-roadmap.md](../sme-productization-roadmap.md) for M1.4 scope and plan.
