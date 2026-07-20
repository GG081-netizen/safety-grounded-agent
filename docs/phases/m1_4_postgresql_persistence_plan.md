# M1.4（里程碑 1.4）— PostgreSQL（关系型数据库）持久化与幂等性计划

## 1. 概述（Summary）

在 M1.3 认证/授权/服务边界之上引入 PostgreSQL 持久化存储、原子幂等防重、短事务状态迁移和应用层审计。Coordinator 完全不感知数据库。

**M1.4 不引入：** Redis、Celery、多实例协调、管理后台、复杂报表。

## 2. 修订后的请求链（Revised Request Chain）

```text
FastAPI Route (async)
  → Depends(secure_chat) / Depends(secure_qa)     # authN + authZ, unchanged
  → DurableApplicationService.execute()            # application boundary
      │
      │  Transaction A (short, async):
      │  ├── Atomic Idempotency Claim (INSERT ON CONFLICT)
      │  │   ├── fresh claim  → INSERT in_progress
      │  │   ├── replay       → return saved canonical result
      │  │   ├── conflict     → 409 idempotency_key_conflict
      │  │   └── in_progress  → 409 idempotency_in_progress
      │  ├── INSERT AgentRequestRecord (status=in_progress)
      │  ├── INSERT AuditEvent (request_accepted)
      │  └── COMMIT
      │
      ├── [NO DB SESSION HELD] asyncio.to_thread(ChatService.execute)
      │   └── Coordinator.run()                     # pure orchestration
      │       ├── PolicyEngine.decide()
      │       ├── IntentRouter → TaskRouter
      │       └── Task execution (RAG/Sales/Writer)
      │
      │  Transaction B (short, async):
      │  ├── SELECT agent_requests FOR UPDATE
      │  ├── INSERT AgentRunRecord
      │  ├── UPDATE AgentRequestRecord (status=completed)
      │  ├── UPDATE IdempotencyRecord (status=completed, response_snapshot)
      │  ├── INSERT AuditEvent (request_completed / policy_blocked)
      │  └── COMMIT
      │
      │  Transaction B-fail (on ApplicationExecutionError):
      │  ├── UPDATE AgentRequestRecord (status=failed, failure_code)
      │  ├── UPDATE IdempotencyRecord (status=failed)
      │  ├── INSERT AuditEvent (request_failed)
      │  └── COMMIT
      │
  → ResponseProjector.project()
  → AgentResponse (HTTP 200)
```

**Key invariants:**
- Coordinator runs outside any database transaction
- AsyncSession never enters the worker thread
- No database lock held during LLM / RAG / Policy execution
- app.py does not call database operations directly

## 3. 幂等性状态机（Idempotency State Machine）

### 3.1 作用范围（Scope）

```text
scope = SHA256(
    tenant_id || ":" ||
    organization_id || ":" ||
    principal_user_id || ":" ||
    operation
)
```

M1.4-E 的 `operation`（版本化操作名）固定为 `v1.chat`（版本 1 通用对话）或 `v1.qa`（版本 1 问答），由服务端 Route（路由）决定，不从请求体推断。

### 3.2 请求指纹（Request Fingerprint）

```text
fingerprint = SHA256(
    fingerprint_version || ":" ||
    operation || ":" ||
    session_id || ":" ||
    user_text || ":" ||
    server_task_override
)
```

`fingerprint_version` starts at 1. Incremented if the fingerprint algorithm changes in a future migration.

### 3.3 原子声明——事务 A（Atomic Claim / Transaction A）

```sql
INSERT INTO idempotency_records
  (scope, idempotency_key, request_fingerprint, fingerprint_version,
   status, claim_version, owner_request_id,
   claimed_at, lease_expires_at, created_at, updated_at, expires_at)
VALUES (?, ?, ?, 1,
        'in_progress', 1, ?,
        NOW(), NOW() + interval '300 seconds', NOW(), NOW(), NOW() + interval '1 hour')
ON CONFLICT (scope, idempotency_key) DO NOTHING
RETURNING idempotency_key
```

If RETURNING empty → key exists. Then SELECT to determine state:

```sql
SELECT status, request_fingerprint, response_snapshot, completed_run_id
FROM idempotency_records
WHERE scope = ? AND idempotency_key = ?
```

### 3.4 状态转换（State Transitions）

```text
                    INSERT (ON CONFLICT, atomic)
                    ┌─────────────────────────────┐
                    │      in_progress             │
                    └─────────────┬───────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
        Coordinator         Coordinator         Coordinator
        succeeds            blocked by          raises error
              │              policy                   │
              ▼                   │                   ▼
         completed ◄──────────────┘              failed
```

### 3.5 已有记录释义（Existing Record Interpretation）

| Status | Fingerprint | Expired | Result |
|---|---|---|---|
| `completed` | match | no | **Replay** — return canonical result |
| `completed` | mismatch | no | **HTTP 409** `idempotency_key_conflict` |
| `in_progress` | any | no | **HTTP 409** `idempotency_in_progress` |
| `failed` | match | no | Re-claim allowed |
| any | — | yes | Re-claim allowed |

### 3.6 重新声明——带 Fencing 的原子操作（Re-claim / Atomic with Fencing）

```sql
UPDATE idempotency_records
SET status = 'in_progress',
    claim_version = claim_version + 1,
    owner_request_id = ?,
    request_fingerprint = ?,
    claimed_at = NOW(),
    lease_expires_at = NOW() + interval '300 seconds',
    updated_at = NOW(),
    expires_at = NOW() + interval '1 hour'
WHERE scope = ? AND idempotency_key = ?
  AND (status = 'failed'
       OR (status = 'in_progress' AND lease_expires_at < NOW()))
RETURNING idempotency_key, claim_version
```

If RETURNING empty → another request claimed it or lease still active → 409.

### 3.7 Transaction B Fencing (condition-based UPDATE)

```sql
UPDATE idempotency_records
SET status = 'completed',
    completed_run_id = ?,
    response_snapshot = ?,
    response_snapshot_schema_version = 1,
    updated_at = NOW()
WHERE scope = ? AND idempotency_key = ?
  AND owner_request_id = ?
  AND claim_version = ?
  AND status = 'in_progress'
```

If 0 rows affected → lost ownership (another request re-claimed or completed it) → log fencing violation, abort.

### 3.8 孤儿与 Fencing 策略——M1.4（Orphan & Fencing Strategy）

M1.4 does **not** implement a background orphan sweeper. However, any new request for the same `(scope, idempotency_key)` can atomically re-claim a stale `in_progress` record whose `lease_expires_at < NOW()`. This provides self-healing: orphaned records are naturally recovered by the next legitimate retry.

A stale owner that eventually completes will be fenced out by the `owner_request_id + claim_version` condition in Transaction B — its UPDATE affects 0 rows and it must abort without overwriting the new owner's result.

## 4. AgentRequest（代理请求）/ AgentRun（代理运行）语义

### 4.1 AgentRequestRecord（代理请求记录）

One row per authenticated + authorized HTTP call entering the application layer.

| Column | Type | Constraint |
|---|---|---|
| `id` | `INTEGER` | PK, autoincrement |
| `request_id` | `VARCHAR(128)` | UNIQUE, NOT NULL |
| `trace_id` | `VARCHAR(128)` | NOT NULL |
| `session_id` | `VARCHAR(128)` | nullable |
| `operation` | `VARCHAR(64)` | NOT NULL |
| `principal_user_id` | `VARCHAR(128)` | NOT NULL |
| `tenant_id` | `VARCHAR(128)` | NOT NULL |
| `organization_id` | `VARCHAR(128)` | NOT NULL |
| `status` | `VARCHAR(32)` | NOT NULL, CHECK (in_progress, completed, failed) |
| `idempotency_key_hash` | `VARCHAR(64)` | nullable |
| `request_fingerprint` | `VARCHAR(64)` | NOT NULL |
| `fingerprint_version` | `INTEGER` | NOT NULL, DEFAULT 1 |
| `replayed_from_request_id` | `VARCHAR(128)` | nullable, self-FK → agent_requests.request_id |
| `authorization_snapshot` | `JSONB` | NOT NULL |
| `failure_code` | `VARCHAR(64)` | nullable |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() |
| `completed_at` | `TIMESTAMPTZ` | nullable |

**Indexes:**
- PK: `id`
- UNIQUE: `ix_agent_requests_request_id` (`request_id`)
- `ix_agent_requests_trace_id` (`trace_id`)
- `ix_agent_requests_tenant_org_time` (`tenant_id`, `organization_id`, `created_at`)
- `ix_agent_requests_principal_time` (`principal_user_id`, `created_at`)
- `ix_agent_requests_status_time` (`status`, `created_at`)

**Note:** `agent_requests` does NOT have a direct FK to `agent_runs`. The run reference lives on `agent_runs.original_request_id` (FK → agent_requests.id) and `idempotency_records.completed_run_id` (FK → agent_runs.id).

### 4.2 AgentRunRecord（代理运行记录）

One row per Coordinator execution. NOT created on replay.

| Column | Type | Constraint |
|---|---|---|
| `id` | `INTEGER` | PK, autoincrement |
| `run_id` | `VARCHAR(128)` | UNIQUE, NOT NULL |
| `original_request_id` | `INTEGER` | NOT NULL, UNIQUE, FK → agent_requests.id |
| `session_id` | `VARCHAR(128)` | nullable |
| `status` | `VARCHAR(32)` | NOT NULL, CHECK (completed, blocked, failed) |
| `routed_task` | `VARCHAR(64)` | nullable |
| `policy_outcome` | `VARCHAR(32)` | nullable |
| `result_snapshot` | `JSONB` | nullable |
| `result_snapshot_schema_version` | `INTEGER` | nullable |
| `confidence` | `FLOAT` | nullable |
| `trace_snapshot` | `JSONB` | nullable |
| `rag_provider` | `VARCHAR(32)` | nullable |
| `started_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() |
| `completed_at` | `TIMESTAMPTZ` | nullable |

**Constraints:**
- UNIQUE on `original_request_id` ensures 1:1 between an agent_request and its agent_run

**Indexes:**
- PK: `id`
- UNIQUE: `ix_agent_runs_run_id` (`run_id`)
- UNIQUE: `ix_agent_runs_request_id` (`original_request_id`)
- `ix_agent_runs_status_time` (`status`, `completed_at`)

### 4.3 AuditEventRecord（审计事件记录）

| Column | Type | Constraint |
|---|---|---|
| `id` | `INTEGER` | PK, autoincrement |
| `event_id` | `VARCHAR(128)` | UNIQUE, NOT NULL |
| `request_id` | `VARCHAR(128)` | nullable |
| `trace_id` | `VARCHAR(128)` | nullable |
| `tenant_id` | `VARCHAR(128)` | NOT NULL |
| `organization_id` | `VARCHAR(128)` | NOT NULL |
| `event_type` | `VARCHAR(64)` | NOT NULL |
| `principal_user_id` | `VARCHAR(128)` | nullable |
| `outcome` | `VARCHAR(32)` | NOT NULL |
| `details_json` | `JSONB` | nullable |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() |

**Indexes:**
- PK: `id`
- UNIQUE: `ix_audit_events_event_id` (`event_id`)
- `ix_audit_events_request_id` (`request_id`)
- `ix_audit_events_type_time` (`event_type`, `created_at`)
- `ix_audit_events_tenant_org_time` (`tenant_id`, `organization_id`, `created_at`)

### 4.4 IdempotencyRecord——带 Fencing（幂等记录）

| Column | Type | Constraint |
|---|---|---|
| `id` | `INTEGER` | PK, autoincrement |
| `scope` | `VARCHAR(64)` | NOT NULL |
| `idempotency_key` | `VARCHAR(255)` | NOT NULL |
| `request_fingerprint` | `VARCHAR(64)` | NOT NULL |
| `fingerprint_version` | `INTEGER` | NOT NULL, DEFAULT 1 |
| `status` | `VARCHAR(32)` | NOT NULL, CHECK (in_progress, completed, failed) |
| `claim_version` | `INTEGER` | NOT NULL, DEFAULT 1 |
| `owner_request_id` | `VARCHAR(128)` | NOT NULL |
| `claimed_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() |
| `lease_expires_at` | `TIMESTAMPTZ` | NOT NULL |
| `completed_run_id` | `VARCHAR(128)` | nullable, FK → agent_runs.id ON DELETE SET NULL |
| `response_snapshot` | `JSONB` | nullable |
| `response_snapshot_schema_version` | `INTEGER` | nullable |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, DEFAULT NOW() |
| `expires_at` | `TIMESTAMPTZ` | NOT NULL |

**Constraints:**
- UNIQUE: `ix_idempotency_scope_key` (`scope`, `idempotency_key`)
- CHECK: `claim_version >= 1`

**Indexes:**
- `ix_idempotency_status_expires` (`status`, `expires_at`)
- `ix_idempotency_lease` (`status`, `lease_expires_at`)

**Fencing semantics:**
- On atomic claim (INSERT): `claim_version = 1`, `claimed_at = NOW()`, `lease_expires_at = NOW() + stale_timeout`
- On re-claim (UPDATE): `claim_version = claim_version + 1`, `claimed_at = NOW()`, `lease_expires_at = NOW() + stale_timeout`, new `owner_request_id`
- Transaction B must include `WHERE owner_request_id = ? AND claim_version = ?` to fence out stale owners
- If Transaction B's UPDATE affects 0 rows → lost ownership → abort + log fencing violation
- Lost ownership never overwrites the new owner's result

### 4.5 Authorization Snapshot (JSONB in AgentRequestRecord)

```json
{
  "strategy": "conservative_route_union",
  "policy_version": "rbac_abac_v1",
  "decision": "allowed",
  "required_permissions": ["chat:invoke", "rag:read"],
  "effective_permissions": ["chat:invoke", "qa:invoke", "rag:read", "crm:read"],
  "tenant_id": "single_tenant",
  "organization_id": "default_organization",
  "resource_scope": {
    "resource_type": "organization",
    "scope_type": "organization"
  },
  "principal_roles": ["agent_user"],
  "denial_code": null,
  "evaluated_at": "2026-07-14T09:30:00Z"
}
```

Does NOT contain: raw JWT Claims, email, display_name, token material.

## 5. 事务边界（Transaction Boundaries）

### 事务 A（声明 + 请求）： Claim + Request (short, < 50ms expected)

```
BEGIN
  INSERT idempotency_records (ON CONFLICT DO NOTHING, RETURNING)
  -- If RETURNING empty → read existing → determine replay/conflict/in_progress
  --   → if replay/conflict: COMMIT + return to caller
  INSERT agent_requests (status='in_progress')
  INSERT audit_events (event_type='request_accepted', outcome='accepted')
COMMIT
```

### 无事务区域（No-Transaction Zone）

Coordinator execution. No `AsyncSession`, no DB connection, no DB lock.

### 事务 B——成功（Transaction B-success） (short, with fencing)

```
BEGIN
  -- Verify request ownership and in_progress status
  SELECT id, status FROM agent_requests WHERE request_id = ? FOR UPDATE
  -- (must be 'in_progress' and belong to this execution)

  INSERT agent_runs (run_id, original_request_id, ...)
    VALUES (?, (SELECT id FROM agent_requests WHERE request_id = ?), ...)

  UPDATE agent_requests
    SET status='completed', completed_at=NOW()
    WHERE request_id = ? AND status = 'in_progress'

  -- Fenced update: only succeeds if we still own the claim
  UPDATE idempotency_records
    SET status='completed',
        completed_run_id = (SELECT id FROM agent_runs WHERE run_id = ?),
        response_snapshot = ?,
        response_snapshot_schema_version = 1,
        updated_at = NOW()
    WHERE scope = ? AND idempotency_key = ?
      AND owner_request_id = ?
      AND claim_version = ?
      AND status = 'in_progress'

  -- If idempotency UPDATE affected 0 rows → fencing violation → ROLLBACK

  INSERT audit_events (event_type='request_completed' OR 'policy_blocked', ...)
COMMIT
```

### 事务 B——失败（Transaction B-fail） (short, with fencing)

```
BEGIN
  SELECT id, status FROM agent_requests WHERE request_id = ? FOR UPDATE

  UPDATE agent_requests
    SET status='failed', failure_code=?, completed_at=NOW()
    WHERE request_id = ? AND status = 'in_progress'

  UPDATE idempotency_records
    SET status='failed', updated_at = NOW()
    WHERE scope = ? AND idempotency_key = ?
      AND owner_request_id = ?
      AND claim_version = ?
      AND status = 'in_progress'

  INSERT audit_events (event_type='request_failed', outcome='failure')
COMMIT
```

## 6. 重放行为（Replay Behavior）

### 6.1 What Gets Saved (response_snapshot)

The sanitized canonical result — `OrchestrationResult.to_public_dict(include_raw_response=False)` — plus replay metadata and schema version:

```json
{
  "schema_version": 1,
  "sanitized_result": { "...public orchestration dict..." },
  "policy_outcome": "SAFE",
  "routed_task": "qa",
  "confidence": 0.72,
  "rag_provider": "external"
}
```

`response_snapshot_schema_version` is stored as a column on `idempotency_records`.
`result_snapshot_schema_version` is stored as a column on `agent_runs`.
Both start at 1. Incremented if the snapshot structure changes.

### 6.2 重放时发生什么

1. Transaction A finds existing `completed` record with matching fingerprint
2. New `AgentRequestRecord` is INSERTed with `status='completed'`, `replayed_from_request_id` referencing the original request_id
3. No new `AgentRunRecord` is created
4. `AuditEvent` of type `idempotency_replayed` is inserted
5. Transaction A COMMITs
6. The saved `sanitized_result` is re-projected through `ResponseProjector` with:
   - **Current** `RequestMetadata` (new `request_id`, new `trace_id`)
   - **Current** `AuthorizationDecision` (re-evaluated, not replayed)
   - **Current** `include_raw_response` config
   - **Current** security trace steps
7. A trace step `idempotency/replayed` is appended
8. `original_request_id` and `original_run_id` are exposed in response (resolved via the joined agent_requests + agent_runs)

### 6.3 不会被重放的内容

Old `request_id`, old `trace_id`, old auth trace, old `debug`, old `raw_response`.

## 7. 数据最小化（Data Minimization）

### 7.1 永不被持久化的数据

Bearer Token, raw JWT Claims, JWKS Document, key material, email, display_name, `raw_response`, `debug.rag_raw_response`, full internal exception stack traces, prompt templates, provider SDK response bodies.

### 7.2 用户文本策略

**Choice A (M1.4 default):** Only store `request_fingerprint` (SHA-256). Full `user_text` persistence requires opt-in via `CONVAGENT_DATABASE_STORE_USER_TEXT=true`. This is the safer default for enterprise deployment.

### 7.3 结果快照

`response_snapshot` (idempotency) and `result_snapshot` (agent_runs) both use `OrchestrationResult.to_public_dict(include_raw_response=False)`. No raw provider data, no debug payload.

## 8. 审计范围——方案 A（Audit Scope）

M1.4 implements **application-level audit only**. Authentication and authorization failures (400/401/403/503) continue to use existing logging and trace — they are NOT persisted in `audit_events`. Documentation must state this is "application audit" not "complete security audit."

| event_type | When | outcome |
|---|---|---|
| `request_accepted` | Successful claim in Transaction A | `accepted` |
| `idempotency_replayed` | Cache hit, execution skipped | `replayed` |
| `policy_blocked` | Coordinator returned BLOCKED | `blocked` |
| `request_completed` | Coordinator executed successfully | `success` |
| `request_failed` | ApplicationExecutionError raised | `failure` |

## 9. 配置（Configuration）

### 9.1 DatabaseConfig（数据库配置）

```python
class DatabaseConfig(BaseModel):
    enabled: bool = True
    required: bool = False
    url: str = ""
    pool_size: int = Field(default=5, ge=1, le=50)
    max_overflow: int = Field(default=10, ge=0, le=100)
    pool_timeout_seconds: float = Field(default=30.0, ge=1.0)
    echo: bool = False
    auto_migrate: bool = False          # demo/test only; forbidden in production
    store_user_text: bool = False       # opt-in for full text persistence
    idempotency_ttl_seconds: int = Field(default=3600, ge=60)
    stale_in_progress_timeout_seconds: int = Field(default=300, ge=60)
```

### 9.2 运行时模式规则

| Mode | DB URL Empty | DB URL Set, Connection Fails |
|---|---|---|
| `demo` | OK — persistence disabled, idempotency skipped | MUST fail |
| `test` | OK (unit tests use NullRepository) | Requires explicit URL for integration tests |
| `production` | MUST fail | MUST fail |

### 9.3 生产环境验证器

```python
@model_validator(mode="after")
def _production_requires_database(self) -> "AppConfig":
    if self.runtime_mode == "production":
        if not self.database.url:
            raise ValueError("production mode requires database URL")
        if not self.database.enabled:
            raise ValueError("database cannot be disabled in production")
        if self.database.auto_migrate:
            raise ValueError("auto_migrate must be false in production")
    return self
```

### 9.4 环境变量

```
CONVAGENT_DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/convagent
CONVAGENT_DATABASE_POOL_SIZE=5
CONVAGENT_DATABASE_MAX_OVERFLOW=10
CONVAGENT_DATABASE_POOL_TIMEOUT=30
CONVAGENT_DATABASE_ECHO=false
CONVAGENT_DATABASE_AUTO_MIGRATE=false
CONVAGENT_DATABASE_REQUIRED=false
CONVAGENT_DATABASE_STORE_USER_TEXT=false
CONVAGENT_IDEMPOTENCY_TTL=3600
CONVAGENT_IDEMPOTENCY_STALE_TIMEOUT=300
```

## 10. Alembic（数据库迁移工具）策略

- `alembic.ini` at project root (`/alembic.ini`), not inside `alembic/` directory
- Migrations executed via explicit command: `uv run alembic upgrade head`
- Startup does NOT auto-migrate by default
- `CONVAGENT_DATABASE_AUTO_MIGRATE=true` allowed only in `demo`/`test` modes
- `production` mode forbids auto-migration
- Startup may optionally verify schema revision matches expected revision

## 11. 线程边界（Thread Boundary）

- `DurableApplicationService.execute()` owns the `asyncio.to_thread()` call
- `AsyncSession` is created and COMMITed in the async context (main event loop)
- Only pure Python objects cross the thread boundary: `UserRequest`, `Principal`, `AuthorizationDecision`, `ApplicationResult`
- `Coordinator.run()` receives only `text`, `session_id`, `task_override`
- Default `asyncio` executor (shared thread pool), no per-request `ThreadPoolExecutor`
- `app.py` contains zero `asyncio.to_thread` calls

## 12. 模块结构（Module Structure）

```
src/conversation_agent/
  database/
    __init__.py
    engine.py               # DatabaseEngine (async lifecycle)
    models.py               # SQLAlchemy ORM models (4 tables)
    repository.py           # DatabaseRepository (data access)
    unit_of_work.py         # UnitOfWork protocol + SqlAlchemyUnitOfWork
    null_persistence.py     # NullUnitOfWork + NullRepository

  application/
    __init__.py
    models.py               # UserRequest (unchanged)
    service.py              # ChatService (unchanged, sync)
    durable_service.py      # DurableApplicationService (NEW, async)

alembic.ini                 # at project root
alembic/
  env.py
  script.py.mako
  versions/
    0001_initial_schema.py
```

## 13. 核心类签名（Core Class Signatures）

### 13.1 DurableApplicationService（持久化应用服务）

```python
class DurableApplicationService:
    def __init__(
        self,
        *,
        inner: ChatService,
        uow_factory: Callable[[], UnitOfWork],
        repo: DatabaseRepository,
        config: DatabaseConfig,
        id_factory: Callable[[], str],
    ) -> None: ...

    async def execute(
        self,
        request: UserRequest,
        *,
        metadata: RequestMetadata,
        principal: Principal,
        authorization: AuthorizationDecision,
        idempotency_key: str | None = None,
        forced_task: TaskName | None = None,
    ) -> ApplicationResult: ...
```

### 13.2 create_app() Changes

```python
def create_app(
    *,
    service: ChatService | None = None,
    durable_service: DurableApplicationService | None = None,  # NEW
    id_factory: Callable[[], str] | None = None,
    config: AppConfig | None = None,
    security_service: RequestSecurityService | None = None,
    db_engine: DatabaseEngine | None = None,                    # NEW
    http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> FastAPI:
```

### 13.3 Route Handlers

```python
@api.post("/v1/chat", ...)
async def chat(request: Request, body: UserRequest,
               security: SecurityContext = Depends(secure_chat),
               idempotency_key: str | None = Header(...)) -> AgentResponse:
    durable: DurableApplicationService = request.app.state.durable_service
    result = await durable.execute(body, metadata=..., principal=..., ...)
    return projector.project(result, ...)

@api.post("/v1/qa", ...)
async def qa(...) -> AgentResponse:
    # same pattern, forced_task="qa"
```

## 14. Test Strategy

### 14.1 Unit Tests (no PostgreSQL, no socket)

File: `tests/test_durable_service.py`
Marker: `pytest.mark.unit`

- `NullUnitOfWork` / `NullRepository` (no-op)
- Mock `ChatService` with `RecordingCoordinator`
- Idempotency: replay, conflict (diff fingerprint), in_progress conflict
- Request/run/audit persistence flows
- Failure path (status transitions)
- Replay re-projection with current metadata/auth
- Debug/raw_response exclusion from snapshot
- Concurrent claim (mock ON CONFLICT behavior)
- Coordinator call_count = 0 on replay, call_count = 1 on fresh execution
- DB session never passed to Coordinator

### 14.2 PostgreSQL Integration Tests

Files: `tests/integration/test_database.py`, `tests/integration/test_durable_service.py`
Marker: `@pytest.mark.postgres_integration`

- Only run when `CONVAGENT_DATABASE_URL` is set
- Real `DatabaseEngine` against test database
- Schema created via `Base.metadata.create_all` (per session)
- Idempotency unique constraint + concurrent claim
- Transaction rollback behavior
- FK constraint enforcement
- Migration upgrade → downgrade → upgrade cycle
- Expired record atomic re-claim
- Index usage verification (EXPLAIN)

### 14.3 pytest Configuration

```toml
[tool.pytest.ini_options]
markers = [
    "unit",
    "integration",
    "e2e",
    "postgres_integration: tests requiring a real PostgreSQL database",
]
```

Use `@pytest.mark.asyncio` explicitly. Do NOT set global `asyncio_mode = "auto"`.

## 15. Delivery Batches

### M1.4-A: Foundation
- Dependencies: sqlalchemy>=2.0.30, asyncpg>=0.29.0, alembic>=1.13.0, pytest-asyncio>=0.24.0
- `DatabaseConfig` in config.py + AppConfig field + production validator
- `DatabaseEngine` in database/engine.py
- `UnitOfWork` protocol + `NullUnitOfWork`
- `DatabaseRepository` base + `NullRepository`
- pytest marker registration
- **Gate:** uv lock --check, compileall, existing 423 tests pass. No API changes.

### M1.4-B: Schema & Migration
- 4 ORM models in database/models.py (all constraints, indexes, FKs)
- alembic.ini + alembic/env.py + alembic/versions/0001_initial_schema.py
- PostgreSQL integration test for migration cycle
- **Gate:** upgrade → downgrade → upgrade works. Constraints verified.

### M1.4-C: Persistence (no idempotency)
- `SqlAlchemyUnitOfWork` + `DatabaseRepository` implementation (no idempotency methods yet)
- `DurableApplicationService` (Transaction A: request insert only, Transaction B: run + audit persistence)
- Thread bridge (asyncio.to_thread for ChatService)
- Failure path (Transaction B-fail)
- **Gate:** Repository/UoW/short transaction verified with real PostgreSQL. Request/Run/Audit records written correctly. No idempotency claim or fencing in this batch.

### M1.4-D: Idempotency + Fencing
- Atomic claim with INSERT ON CONFLICT DO NOTHING (scope + key)
- Request fingerprint computation with fingerprint_version
- claim_version + owner_request_id + lease_expires_at fencing
- Conflict detection (409 idempotency_key_conflict)
- In-progress detection (409 idempotency_in_progress)
- Stale lease re-claim (lease_expires_at < NOW())
- Fenced Transaction B UPDATE (owner_request_id + claim_version conditional)
- Fencing violation detection (0 rows affected → abort)
- Replay with canonical result saving + snapshot schema version
- Replay re-projection (current metadata + current auth + saved result)
- Concurrent claim unit tests (NullRepository simulation)
- Concurrent claim PostgreSQL integration tests (real concurrent sessions)
- **Gate:** Two concurrent PostgreSQL requests for same scope+key → exactly one Coordinator execution. Stale owner fenced out. Replay returns correct re-projected response.

### M1.4-E: API Integration（API 接入）— COMPLETED（已完成）
- create_app() updated: inject DurableApplicationService
- Route handlers become async
- Runtime mode fail-closed (demo/test/production)
- Authorization snapshot persistence
- End-to-end replay projection through ResponseProjector
- API contract tests (new 409 status codes)
- **Gate:** All M1.3 API tests still pass. End-to-end flow verified: HTTP → DurableService → PostgreSQL → ResponseProjector → correct AgentResponse.

### M1.4-F: Final Validation（最终验证）— COMPLETED（已完成）
- Full PostgreSQL integration tests
- Alembic migration cycle test
- Orphan record detection tests
- M1.4 Closeout document
- Roadmap update
- Full regression: unit, full suite, PostgreSQL, policy-boundary, rag-adapter, legacy Node IDs, uv.lock
- Doctor（诊断器）、Integrity Checker（完整性检查器）与受保护 Prune（清理）
- 进程崩溃、多实例协调、数据库中断恢复、连接池 Soak（稳定性运行）和最小权限角色
- 逻辑备份与全新数据库恢复演练
- **Gate:** All regression gates pass.

## 16. Frozen Decisions

All previously open design choices are now frozen:

1. **User text default:** `hash_only` — only `request_fingerprint` is stored by default. Full `user_text` persistence requires explicit `CONVAGENT_DATABASE_STORE_USER_TEXT=true`.

2. **Orphan recovery:** No background orphan sweeper in M1.4. However, any new request for the same `(scope, idempotency_key)` can atomically re-claim a stale `in_progress` record whose `lease_expires_at < NOW()`. The stale owner is fenced out by `claim_version` in Transaction B.

3. **Idempotency TTL vs stale timeout:** Independently configured. `idempotency_ttl_seconds` (default 3600s) controls `expires_at`. `stale_in_progress_timeout_seconds` (default 300s) controls `lease_expires_at` and re-claim eligibility.

4. **agent_requests retention:** M1.4 retains all rows indefinitely. No DELETE or archive strategy. This will be addressed in a future phase.

5. **Alembic config location:** `alembic.ini` at project root directory.

6. **Scope includes principal_user_id:** Same idempotency key from different users maps to different scopes → no conflict. This is intentional.

7. **Idempotency-Key source:** HTTP header only, via `Idempotency-Key` header. Not a `UserRequest` body field. Mapped internally into `DurableApplicationService.execute()` as the `idempotency_key` parameter.

8. **Fencing:** `claim_version` + `owner_request_id` conditional UPDATE in Transaction B. Lost ownership is detected (0 rows affected) and aborts without overwriting.

9. **Fingerprint algorithm（请求指纹算法）：** M1.4-E 使用 `fingerprint_version = 2`（请求指纹版本 2），覆盖版本化 operation（操作名）、原始 user text（用户文本）、服务端 task override（任务覆盖）和解析默认值后的客户端 `session_id`（会话标识）。版本 1 记录在 Terminal TTL（终态有效期）内安全失败，不跨版本比较 Hash（哈希）。

10. **Snapshot schema:** `response_snapshot_schema_version = 1` and `result_snapshot_schema_version = 1`. Both are opaque integers — consumers check the version before deserializing.

## 17. M1.4-A Implementation Prompt

```
Implement M1.4-A: Foundation

1. Add to pyproject.toml dependencies:
   - sqlalchemy>=2.0.30
   - asyncpg>=0.29.0
   - alembic>=1.13.0
   - pytest-asyncio>=0.24.0 (dev extra)

2. Run uv lock, then uv sync --frozen --extra dev.

3. Add DatabaseConfig to src/conversation_agent/config.py:
   Fields: enabled, required, url, pool_size, max_overflow,
     pool_timeout_seconds, echo, auto_migrate, store_user_text,
     idempotency_ttl_seconds, stale_in_progress_timeout_seconds
   - Add database field to AppConfig
   - Add _production_requires_database validator:
     * production requires url, enabled=true, auto_migrate=false
   - Add env var loading in _build_default_config():
     * CONVAGENT_DATABASE_URL, CONVAGENT_DATABASE_POOL_SIZE,
       CONVAGENT_DATABASE_MAX_OVERFLOW, CONVAGENT_DATABASE_POOL_TIMEOUT,
       CONVAGENT_DATABASE_ECHO, CONVAGENT_DATABASE_AUTO_MIGRATE,
       CONVAGENT_DATABASE_REQUIRED, CONVAGENT_DATABASE_STORE_USER_TEXT,
       CONVAGENT_IDEMPOTENCY_TTL, CONVAGENT_IDEMPOTENCY_STALE_TIMEOUT

4. Create src/conversation_agent/database/__init__.py (package marker).

5. Create src/conversation_agent/database/engine.py:
   - DatabaseEngine class
   - async def start(): create_async_engine(url, pool_size, max_overflow, ...)
     + async_sessionmaker(class_=AsyncSession, expire_on_commit=False)
   - async def stop(): engine.dispose()
   - session() → async context manager yielding AsyncSession
   - pool_pre_ping=True

6. Create src/conversation_agent/database/repository.py:
   - DatabaseRepository class with abstract method signatures:
     * claim_idempotency(session, scope, key, fingerprint, fingerprint_version,
         owner_request_id, lease_duration) → (claimed: bool, existing_record | None)
     * re_claim_idempotency(session, scope, key, new_fingerprint,
         new_owner_request_id, lease_duration)
         → (re_claimed: bool, new_claim_version | None)
     * insert_agent_request(session, record) → AgentRequestRecord
     * insert_agent_run(session, record) → AgentRunRecord
     * insert_audit_event(session, record) → AuditEventRecord
     * complete_idempotency_fenced(session, scope, key, owner_request_id,
         claim_version, completed_run_id, response_snapshot)
         → (updated: bool)
     * fail_idempotency_fenced(session, scope, key, owner_request_id,
         claim_version) → (updated: bool)
     * find_idempotency_by_scope_key(session, scope, key) → IdempotencyRecord | None
     * update_agent_request_status(session, request_id, status, **fields) → bool

7. Create src/conversation_agent/database/null_persistence.py:
   - NullUnitOfWork (no-op async context manager)
   - NullRepository (all methods return safe defaults:
     claim succeeds, re_claim succeeds, updates return True, finds return None)

8. Create src/conversation_agent/database/unit_of_work.py:
   - UnitOfWork Protocol: async begin(), async commit(), async rollback(), session

9. Add markers to pyproject.toml [tool.pytest.ini_options].markers:
   - "postgres_integration: tests requiring a real PostgreSQL database"

10. Verify:
    - uv lock --check (lockfile unchanged)
    - uv run python -m compileall -q src tests
    - uv run pytest -m unit -q（当阶段既有单元测试全部通过）

Do NOT create ORM models, Alembic files, durable_service.py, or modify app.py.
```

## 18. M1.4-A Closeout — COMPLETED

### 18.1 Summary

M1.4-A 建立了 PostgreSQL / SQLAlchemy async persistence 的 Foundation 层：依赖声明、DatabaseConfig 配置 Contract、DatabaseEngine 生命周期、UnitOfWork Protocol、DatabaseRepository 抽象、Null/Fake 持久化实现和 73 项单元测试。未修改 Coordinator、ChatService、FastAPI 路由或 HTTP 运行时行为。

### 18.2 Files Changed

| File | Action |
|------|--------|
| `src/conversation_agent/config.py` | **Modified** — 新增 `DatabaseConfig` 模型（12 字段 + `enabled/required` 冲突校验 + `is_configured` 属性）、`AppConfig.database` 字段、production validator（URL 必填 + enabled=true + auto_migrate=false）、`_build_default_config()` 中 12 项 env var 加载 |
| `src/conversation_agent/database/__init__.py` | **Created** — Package marker |
| `src/conversation_agent/database/engine.py` | **Created** — `DatabaseEngine`（async lifecycle: `start`/`stop`/`session`、`pool_pre_ping=True`、`expire_on_commit=False`、空 URL 构造时拒绝、未 start 访问报错、dispose 幂等） |
| `src/conversation_agent/database/unit_of_work.py` | **Created** — `UnitOfWork` Protocol（`begin`/`commit`/`rollback`） |
| `src/conversation_agent/database/repository.py` | **Created** — `DatabaseRepository` 抽象基类 + `IdempotencyClaimResult` dataclass（9 个方法签名：claim/re_claim/complete_fenced/fail_fenced/find + insert_request/update_request/insert_run/insert_audit） |
| `src/conversation_agent/database/null_persistence.py` | **Created** — `NullRepository`（全部操作返回安全默认值）、`NullUnitOfWork`（no-op）、`FakeRepository` + `FakeRepositoryState`（内存可观测持久化）、`FakeUnitOfWork`（跟踪 committed/rolled_back 标志） |
| `tests/test_database.py` | **Created** — 73 项单元测试（14 类） |
| `tests/test_security.py` | **Modified** — `test_production_can_disable_openapi_and_docs` 新增 `database={"url": "..."}`（production validator 所需） |

### 18.3 Dependencies

`pyproject.toml` 中以下依赖已在 M1.4-A 实施前预先声明，`uv.lock` 已解析完成：

| Package | Version |
|---------|---------|
| `sqlalchemy` | `>=2.0.30` |
| `asyncpg` | `>=0.29.0` |
| `alembic` | `>=1.13.0` |
| `pytest-asyncio` (dev) | `>=0.24.0` |

### 18.4 DatabaseConfig Contract

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `enabled` | `bool` | `True` | `CONVAGENT_DATABASE_ENABLED` |
| `required` | `bool` | `False` | `CONVAGENT_DATABASE_REQUIRED` |
| `url` | `str` | `""` | `CONVAGENT_DATABASE_URL` |
| `pool_size` | `int` (1–50) | `5` | `CONVAGENT_DATABASE_POOL_SIZE` |
| `max_overflow` | `int` (0–100) | `10` | `CONVAGENT_DATABASE_MAX_OVERFLOW` |
| `pool_timeout_seconds` | `float` (≥1.0) | `30.0` | `CONVAGENT_DATABASE_POOL_TIMEOUT` |
| `pool_recycle_seconds` | `float` (≥1.0) | `3600.0` | `CONVAGENT_DATABASE_POOL_RECYCLE` |
| `echo` | `bool` | `False` | `CONVAGENT_DATABASE_ECHO` |
| `auto_migrate` | `bool` | `False` | `CONVAGENT_DATABASE_AUTO_MIGRATE` |
| `store_user_text` | `bool` | `False` | `CONVAGENT_DATABASE_STORE_USER_TEXT` |
| `idempotency_ttl_seconds` | `int` (≥60) | `3600` | `CONVAGENT_IDEMPOTENCY_TTL` |
| `stale_in_progress_timeout_seconds` | `int` (≥60) | `300` | `CONVAGENT_IDEMPOTENCY_STALE_TIMEOUT` |

**冲突防护：**
- `enabled=false, required=true` → `ValidationError`（`DatabaseConfig._enabled_required_consistency`）
- `production` + 空 URL → `ValidationError`（`AppConfig._validate_production_security`）
- `production` + `enabled=false` → `ValidationError`
- `production` + `auto_migrate=true` → `ValidationError`

**运行模式语义：**

| Mode | DB URL 为空 | DB URL 已设置，连接失败 |
|------|-------------|------------------------|
| `demo` | OK — persistence 禁用，不创建 `DatabaseEngine` | MUST fail |
| `test` | OK — 单元测试使用 `NullRepository` | 集成测试需显式 URL |
| `production` | MUST fail（`ValidationError`） | MUST fail（M1.4-E 接入点） |

### 18.5 Persistence Guarantee Levels

M1.4-A 定义了三个持久化保证级别（Contract-only，运行时不强制）：

| Level | 实现 | 幂等保证 | 条件 |
|-------|------|---------|------|
| `none` | `NullRepository` / `NullUnitOfWork` | **无** — `claim_idempotency` 总是返回 `claimed=True`；并发请求无法检测冲突；不提供防重语义 | `enabled=false` 或 URL 为空 + demo/test 模式 |
| `fake` | `FakeRepository` / `FakeUnitOfWork` | **内存模拟** — 仅在单进程单元测试中可观测；不跨请求持久化 | 单元测试专用 |
| `persistent_atomic` | `DatabaseRepository`（PostgreSQL） | **原子持久** — `INSERT ON CONFLICT` + fencing `claim_version` + `owner_request_id` 条件 UPDATE | PostgreSQL 可用 + M1.4-C/D 完成 |

**明确约束：**
- `NullRepository` **不提供**持久幂等保证。`idempotency_guarantee = none`。
- URL 为空时 **不创建** `DatabaseEngine`（demo/test 允许，production 拒绝）。
- 真实 `persistent_atomic` 保证仅在 `DatabaseRepository`（PostgreSQL）完成后生效（M1.4-D）。
- Production fail-closed（连接失败 → 启动失败）在 M1.4-E 接入 FastAPI lifespan 时执行，不在 M1.4-A。

### 18.6 NullRepository Idempotency Semantics

```
NullRepository.claim_idempotency()
  → 总是返回 IdempotencyClaimResult(claimed=True)
  → 不检查 scope/idempotency_key 是否已存在
  → 不存储任何记录
  → 并发请求各自独立获得 claimed=True
  → 无 fencing 保护
  → 适用场景：demo 模式、开发调试、离线单元测试
  → 不适用场景：任何需要防重的生产路径
```

### 18.7 Validation Results

```text
uv lock --check                                      = Resolved 44 packages (unchanged)
uv sync --frozen --extra dev                         = Checked 43 packages
compileall -q src tests                              = PASS (no errors)
pytest --collect-only -q                             = 497 tests collected
pytest -m unit -q                                    = 496 passed, 1 skipped, 1 deselected
pytest -q                                            = 496 passed, 1 skipped
convagent eval policy-boundary                       = Status PASS
convagent eval rag-adapter                           = Status PASS
uv.lock SHA-256 (before M1.4-A)                      = bdb8986c1832728d1fe3a19a3b30eb61927cc09df30f3ad0d81d20e5a2594fe5
uv.lock SHA-256 (after M1.4-A)                       = bdb8986c1832728d1fe3a19a3b30eb61927cc09df30f3ad0d81d20e5a2594fe5
uv.lock changed during M1.4-A                        = no
```

### 18.8 Node ID Comparison

```text
m1_3_nodeids (baseline)                              = 424
m1_4a_total_nodeids                                  = 497
m1_4a_new_nodeids (test_database.py)                 = 73
m1_3_nodeids_preserved                               = 424
missing_m1_3_nodeids                                 = 0
modified_m1_3_tests                                  = 1 (test_production_can_disable_openapi_and_docs)
```

**Modified test detail:**
- `tests/test_security.py::test_production_can_disable_openapi_and_docs` — 新增 `database={"url": "postgresql+asyncpg://localhost:5432/db"}` 参数。该测试之前构造 production `AppConfig` 且无数据库 URL，与新增的 production database validator 冲突。修改仅满足 Contract 要求，不改变测试意图（验证 production 模式可禁用 OpenAPI/docs）。

**Legacy Node IDs:**
- M1.1 保存的 347 个 legacy node IDs：全部保留，缺失 0。

### 18.9 Architecture Invariants Preserved

| 不变量 | 状态 |
|--------|------|
| Coordinator 不导入 database 模块 | ✅ AST 验证通过 |
| ChatService 不导入 database 模块 | ✅ AST 验证通过 |
| `api/app.py` 不导入 database 模块 | ✅ AST 验证通过 |
| HTTP 运行时行为未改变 | ✅ demo 模式无数据库正常启动 |
| 无 `asyncio_mode=auto` | ✅ 仅使用显式 `@pytest.mark.asyncio` |
| `DatabaseEngine` 不在 import/构造时建立连接 | ✅ 测试验证 |
| `dispose()` 幂等 | ✅ 测试验证 |
| 默认 unit suite 无 PostgreSQL socket | ✅ `--disable-socket` 生效 |

### 18.10 M1.4-B Readiness

**Ready.** Foundation Contracts 就绪：
- `DatabaseConfig` — 配置 Contract + production fail-closed 语义
- `DatabaseEngine` — SQLAlchemy 2 async 生命周期（M1.4-B 可直接复用）
- `UnitOfWork` Protocol / `DatabaseRepository` 抽象 — 持久化接口已定义
- `NullRepository` / `NullUnitOfWork` — demo/test 的 no-op 实现
- `FakeRepository` / `FakeUnitOfWork` — 单元测试的可观测内存实现
- `pytest.mark.postgres_integration` — 已注册，M1.4-B 集成测试可用

**M1.4-C 入口：** `SqlAlchemyUnitOfWork` + `DatabaseRepository` 实现、`DurableApplicationService`、短事务（Transaction A/B）、线程桥（`asyncio.to_thread`）。

## 20. M1.4-B Closeout — COMPLETED

### 20.1 Summary

M1.4-B 在 Foundation 之上建立了四张 ORM 表、Alembic 迁移基础设施和初始迁移脚本。新增 32 项 PostgreSQL 集成测试覆盖 Schema、约束、索引、CHECK、外键和 migration cycle。Coordinator、ChatService 和 FastAPI 路由未变更。

### 20.2 Files Changed

| File | Action |
|------|--------|
| `src/conversation_agent/database/models.py` | **Created** — `DeclarativeBase` + `naming_convention` + 4 ORM 表（`AgentRequest`, `AgentRun`, `AuditEvent`, `IdempotencyRecord`），含全部 CK、FK、UQ、IX |
| `alembic.ini` | **Created** — Project-root Alembic config（`sqlalchemy.url =` 为空） |
| `alembic/env.py` | **Created** — Async env、URL 解析链（`-x database_url` → ini → `CONVAGENT_DATABASE_URL`）、`NullPool`、offline+online 双模 |
| `alembic/script.py.mako` | **Created** — Migration 模板 |
| `alembic/versions/0001_initial_schema.py` | **Created** — `upgrade()` 建 4 表 + 全部约束/索引；`downgrade()` FK 反序 DROP（无 CASCADE） |
| `tests/integration/__init__.py` | **Created** — Package marker |
| `tests/integration/test_postgresql_migration.py` | **Created** — 32 项 PostgreSQL integration tests |

### 20.3 ORM Schema Summary

| Table | Columns | CKs | UQs | FKs | IXs |
|-------|---------|-----|-----|-----|-----|
| `agent_requests` | 20 | 8 | 1 (`request_id`) | 1 (self-FK) | 5 |
| `agent_runs` | 14 | 7 | 2 (`run_id`, `original_request_id`) | 1 → agent_requests.id | 1 |
| `audit_events` | 11 | 0 | 1 (`event_id`) | 0 | 3 |
| `idempotency_records` | 18 | 9 | 1 (5-field composite) | 1 → agent_runs.id | 2 |

**Total: 30 CHECK constraints, 5 UNIQUE constraints, 3 FKs, 11 composite indexes.**

### 20.4 Key Design Decisions

- **idempotency unique scope:** 显式 5 字段 `(tenant_id, organization_id, principal_user_id, operation, idempotency_key_hash)`，不透明 scope hash 被替换。原始 Idempotency-Key 永不持久化。
- **Internal vs external FKs:** `agent_runs.original_request_id` → `agent_requests.id`（INTEGER FK，UNIQUE，1:1）。外部标识（`request_id`, `run_id`）保持 VARCHAR UNIQUE。
- **No CASCADE on downgrade:** 按 FK 依赖反序 DROP TABLE。
- **No duplicate unique indexes:** UNIQUE CONSTRAINT 自身提供索引。
- **`alembic.ini` `sqlalchemy.url` = empty:** 确保 env var 解析链不被占位符短路。
- **Alembic URL contract:** 测试通过 `AlembicConfig.set_main_option()` 注入 URL，不覆盖 `os.environ`。
- **Destructive triple-gate:** (1) URL 必须来自 `CONVAGENT_POSTGRES_TEST_URL` (2) `CONVAGENT_ALLOW_DESTRUCTIVE_DB_TESTS=true` (3) DB 名含 `test`/`testing`/`ci` 或 `CONVAGENT_TEST_DB_CONFIRMED=<dbname>` 精确匹配。
- **PostgreSQL integration = serial only:** 不允许 xdist 并行。

### 20.5 CHAR(64) Hex CHECKs

| Table | Column | Constraint Name |
|-------|--------|-----------------|
| `agent_requests` | `request_fingerprint` | `ck_agent_requests_fingerprint_hex` |
| `agent_requests` | `user_text_hash` | `ck_agent_requests_user_text_hash_hex` |
| `agent_requests` | `idempotency_key_hash` | `ck_agent_requests_idempotency_key_hash_hex` (nullable) |
| `idempotency_records` | `idempotency_key_hash` | `ck_idempotency_records_key_hash_hex` |
| `idempotency_records` | `request_fingerprint` | `ck_idempotency_records_fingerprint_hex` |

### 20.6 Version/Time Ordering Constraints

- `fingerprint_version >= 1` (both agent_requests + idempotency_records)
- `authorization_snapshot_schema_version >= 1`
- `result_snapshot_schema_version >= 1` (nullable)
- `trace_snapshot_schema_version >= 1` (nullable)
- `response_snapshot_schema_version >= 1` (nullable)
- `confidence >= 0.0 AND <= 1.0` (nullable)
- `completed_at >= created_at` (agent_requests)
- `completed_at >= started_at` (agent_runs)
- `lease_expires_at >= claimed_at` (idempotency_records)
- `expires_at >= created_at` (idempotency_records)

### 20.7 Validation Results

```text
uv lock --check                                      = Resolved 44 packages (unchanged)
uv sync --frozen --extra dev                         = Checked 43 packages
compileall -q src tests                              = PASS (no errors)
pytest --collect-only -q                             = 529 tests collected
pytest -m unit -q                                    = 496 passed, 33 deselected
pytest -m "not postgres_integration" -q              = 496 passed, 1 skipped, 32 deselected
convagent eval policy-boundary                       = Status PASS
convagent eval rag-adapter                           = Status PASS
uv.lock SHA-256                                      = bdb8986c1832728d1fe3a19a3b30eb61927cc09df30f3ad0d81d20e5a2594fe5
uv.lock changed during M1.4-B                        = no
```

### 20.8 Node ID Comparison

```text
m1_4a_nodeids (baseline)                             = 497
m1_4b_total_nodeids                                  = 529
m1_4b_new_nodeids (test_postgresql_migration.py)     = 32
m1_4a_nodeids_preserved                              = 497
missing_m1_4a_nodeids                                = 0
```

### 20.9 Architecture Invariants Preserved

| 不变量 | 状态 |
|--------|------|
| Coordinator 不导入 database 模块 | ✅ |
| ChatService 不导入 database 模块 | ✅ |
| `api/app.py` 不导入 database 模块 | ✅ |
| `alembic/env.py` 不导入 AppConfig | ✅ |
| Alembic 不自动执行 Migration | ✅ |
| ORM metadata 与 Migration Schema 一致 | ✅ 测试验证 |
| 无循环 FK | ✅ |
| 无重复 unique index | ✅ |
| 无 raw_response/JWT/key material 列 | ✅ 测试验证 |

### 20.10 M1.4-C Readiness

**Completed（已完成）。** ORM 模型、Alembic 基础设施和 PostgreSQL 集成测试框架已由 M1.4-C 消费；实际实现采用与既有 Contract（数据协议）一致的 `SQLAlchemyExecutionUnitOfWork`（SQLAlchemy 执行工作单元）、`SQLAlchemyExecutionRepository`（SQLAlchemy 执行仓储）和 `DurableApplicationService`（持久化应用服务），没有创建平行应用模型。

## 21. M1.4-B-R1 Closeout（里程碑 1.4-B 修复轮次收尾）— COMPLETED（已完成）

### 21.1 修复范围

M1.4-B-R1（里程碑 1.4-B 修复轮次）只稳定 PostgreSQL Schema Contract（PostgreSQL 结构契约），没有开始 M1.4-C。主要修复包括：

- pytest-asyncio strict mode（pytest 严格异步模式）下的 fixture（测试夹具）和测试类标记；数据库异步夹具保持 function scope（函数级作用域），不存在 session loop scope mismatch（会话级事件循环作用域不匹配）。
- ORM Metadata（对象关系映射元数据）与 `0001` Migration（初始迁移）的 `JSONB`（二进制 JSON 类型）、默认值、具名约束和索引对齐。
- `op.f()`（已完成命名标记）保护冻结约束名，避免 naming convention（命名约定）重复添加前缀；`revision="0001"`（迁移版本标识）和 `down_revision=None`（无前置迁移）保持不变。
- Alembic `compare_metadata`（元数据比较）仅排除 `alembic_version`（迁移版本表），并启用类型和受限 server default comparator（服务端默认值比较器）。
- 手工 Schema signature（结构签名）精确验证类型、长度、可空性、默认值、主键、唯一约束、外键、CHECK（检查约束）和索引字段顺序。
- 每个预期 `IntegrityError`（完整性错误）都从 SQLAlchemy/asyncpg（数据库抽象层/异步 PostgreSQL 驱动）包装链提取 `constraint_name`（约束名称）并精确断言；无结构化名称时直接失败。
- PostgreSQL 17 CI Job（持续集成任务）包含非破坏性与破坏性两轮精确 JUnit（JUnit XML 测试报告）计数，防止全部跳过时误报成功。

### 21.2 真实 PostgreSQL 17 验收

```text
postgres_non_destructive = 31 passed, 2 skipped, 497 deselected
postgres_destructive = 33 passed, 0 skipped, 497 deselected
destructive_migration_tests_executed = 2
final_revision = 0001 (head)
compare_metadata_business_diff = empty
manual_schema_signature = PASS
structured_constraint_name_assertions = PASS
path_separator_warning_count = 0
```

非破坏性运行明确跳过两项 migration cycle（迁移循环）测试；开启 `CONVAGENT_ALLOW_DESTRUCTIVE_DB_TESTS=true`（允许破坏性数据库测试）后，33 项全部执行且无跳过。最终迁移版本为 `0001 (head)`（初始迁移头版本）。

### 21.3 默认回归与基线

```text
collected_nodeids = 530
m1_1_baseline_nodeids = 347
missing_m1_1_nodeids = 0
m1_4_b_pre_r1_nodeids = 529
missing_m1_4_b_nodeids = 0
unit = 496 passed, 34 deselected
not_postgres_integration = 496 passed, 1 skipped, 33 deselected
full_without_database_url = 496 passed, 34 skipped
policy_boundary = PASS
rag_adapter = PASS
uv_lock_sha256 = bdb8986c1832728d1fe3a19a3b30eb61927cc09df30f3ad0d81d20e5a2594fe5
uv_lock_changed = no
```

测试结果证明当前回归集与本地 PostgreSQL 17 Schema Contract（结构契约）通过，不等于系统达到生产级可靠性。

### 21.4 CI 与环境状态

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

`postgres_ci_workflow_static_validation`（PostgreSQL 持续集成工作流静态验证）只表示 YAML（配置文件格式）语法和任务结构检查通过。当前环境没有真实 GitHub Actions Run（GitHub 自动化运行），因此不得将其描述为 CI 已运行通过。

### 21.5 边界与下一阶段

已完成：ORM Schema（对象关系映射结构）、Alembic Migration（数据库迁移）、真实 PostgreSQL 17 结构/约束/迁移循环验证。

R1 收尾当时尚未实现：`SQLAlchemyExecutionRepository`（SQLAlchemy 执行仓储）、`SQLAlchemyUnitOfWork`（SQLAlchemy 工作单元）、`DurableApplicationService`（持久化应用服务）、运行时 idempotency（幂等）、Fencing（执行权隔离）逻辑、Replay（重放）、FastAPI 数据库接入、DatabaseEngine lifespan（数据库引擎生命周期）、Redis（内存数据服务）和 Celery（任务队列）。前三项已由后续 M1.4-C 完成，其余能力仍未实现。

M1.4-C（里程碑 1.4-C）已在后续批次完成；R1 本身仍只负责 Schema Contract（结构契约）稳定化。

## 22. M1.4-C Closeout（里程碑 1.4-C 收尾）— COMPLETED（已完成）

### 22.1 实际实现

- `ExecutionRepository`（执行仓储协议）与 `ExecutionUnitOfWork`（执行工作单元协议）是新增的窄接口；既有 `DatabaseRepository`（数据库仓储协议）、Null/Fake persistence（空/模拟持久化）保持兼容。
- `SQLAlchemyExecutionRepository`（SQLAlchemy 执行仓储）只在调用方拥有的 `AsyncSession`（异步数据库会话）中 stage/flush（暂存/刷新）记录，不自行 commit/rollback（提交/回滚），也不访问 `idempotency_records`（幂等记录表）。
- `SQLAlchemyExecutionUnitOfWork`（SQLAlchemy 执行工作单元）统一拥有 Session 生命周期。commit（提交）失败时尝试 rollback（回滚）、关闭 Session、禁止再次提交，并以安全异常链返回；`__aexit__`（异步退出边界）不吞业务或数据库异常。
- `DurableApplicationService`（持久化应用服务）执行 Transaction A（事务 A）接受请求，在线程中且事务外调用同步 `ChatService`（对话应用服务），再以独立 Transaction B（事务 B）写入运行终态。
- `RequestPersistenceMapper`（请求持久化映射器）、`RunPersistenceMapper`（运行持久化映射器）和 `AuditPersistenceMapper`（审计持久化映射器）只保存 allowlist snapshot（白名单快照），不保存用户正文、回答正文、JWT（JSON Web 令牌）、Claims（声明）、电子邮件、原始响应或数据库凭据。

真实短事务链：

```text
Transaction A（事务 A）:
AgentRequest（智能体请求） + request_accepted（请求已接受审计） → COMMIT（提交）

No database transaction（无数据库事务）:
run_id（运行标识） + run_started_at（运行开始时间）
→ ChatService（对话应用服务） → Coordinator（协调器）
→ run_completed_at（运行完成时间）

Transaction B（事务 B）:
AgentRun（智能体运行） + request terminal state（请求终态）
+ completed/blocked/failed audit（完成/阻断/失败审计） → COMMIT（提交）
```

### 22.2 Contract 差异与不变量

- 计划示例中的名称没有机械照抄；最终签名以实际 `ApplicationResult`（应用结果）、`RequestContext`（请求上下文）、`AuthorizationDecision`（授权决定）和 `ChatService`（对话应用服务）为准。
- BLOCKED（策略阻断）只读取 `ApplicationResult.orchestration.policy.is_blocked`（应用结果中的结构化策略阻断标志），没有修改 Coordinator 或 Policy 输出。
- `user_text_length`（用户文本长度）在 Mapper（映射器）内严格使用 `len(user_text)`，`user_text_hash`（用户文本哈希）使用传入字符串原样 UTF-8 SHA-256；Mapper 不 trim/lower/normalize（去空白/转小写/规范化）。既有 `UserRequest`（用户请求）Pydantic Contract 会先执行 `str_strip_whitespace=True`（去除首尾空白），因此当前持久化值基于应用层已验证文本，而不是原始 HTTP 字节；本阶段为保持公开 Contract 未改变该行为。
- 所有持久化时间必须是 timezone-aware UTC datetime（带时区的 UTC 日期时间），由注入 Clock（时钟）产生；同一状态转换复用明确的事件时间。
- `0001` Migration（初始迁移）未修改，未创建 `0002`；M1.4-C 仅使用冻结的四张业务表。

### 22.3 验收结果

```text
collected_nodeids = 573
m1_1_baseline_nodeids = 347
missing_m1_1_nodeids = 0
m1_4_b_pre_r1_nodeids = 529
missing_m1_4_b_nodeids = 0
m1_4_b_r1_pre_c_nodeids = 530
missing_m1_4_b_r1_nodeids = 0

unit = 522 passed, 51 deselected
not_postgres_integration = 522 passed, 1 skipped, 50 deselected
full_without_database_url = 522 passed, 51 skipped
postgres_non_destructive = 48 passed, 2 skipped
postgres_destructive = 50 passed, 0 skipped
destructive_migration_tests_executed = 2
final_revision = 0001 (head)
policy_boundary = PASS
rag_adapter = PASS
uv_lock_changed = no
uv_lock_sha256 = bdb8986c1832728d1fe3a19a3b30eb61927cc09df30f3ad0d81d20e5a2594fe5
```

### 22.4 CI 与阶段边界

```text
postgres_ci_job = implemented
postgres_ci_workflow_static_validation = PASS
postgres_ci_runtime_execution = not_run_in_current_environment
local_equivalent_postgres_ci_gate = PASS
git_diff_gate = unavailable
git_worktree_clean = not_asserted

m1_4c_repository_runtime_status = component_implemented
m1_4c_uow_runtime_status = component_implemented
m1_4c_durable_service_runtime_status = component_implemented
m1_4c_fastapi_database_wiring = not_implemented
m1_4d_runtime_idempotency = not_implemented
m1_4d_fencing = not_implemented
m1_4d_replay = not_implemented
m1_4e_status = not_implemented
```

本地 PostgreSQL 17 equivalent CI gate（等价持续集成验收门）已通过，但没有真实 GitHub Actions Run（GitHub 自动化运行），因此 CI runtime（持续集成运行时）不能标记为通过。此段记录的是 M1.4-C 收尾当时状态：M1.4-C 在此停止，M1.4-D 当时仅为 next/ready（下一阶段/可开始）；其后续完成状态见第 23 节。

完整独立记录见 [M1.4-C Closeout](m1_4c_execution_persistence_closeout.md)。

## 23. M1.4-D Closeout（里程碑 1.4-D 收尾）— COMPLETED（已完成）

M1.4-D 已在冻结的 `0001` Migration（初始迁移）上实现 persistent idempotency component（持久幂等组件），没有修改 ORM Schema（对象关系映射结构）、没有创建 `0002` Migration（第二迁移），也没有把组件接入 FastAPI（Web 应用接口框架）。

实际组件包括：

- `IdempotencyRepository`（幂等仓储协议）与 `SQLAlchemyIdempotencyRepository`（SQLAlchemy 幂等仓储）。
- `IdempotentExecutionUnitOfWork`（幂等执行工作单元协议）与 `SQLAlchemyIdempotentExecutionUnitOfWork`（SQLAlchemy 幂等执行工作单元）。
- `IdempotencyStateValidator`（幂等状态校验器）和版本化 `ReplaySnapshotMapper`（重放快照映射器）。
- `IdempotentDurableApplicationService`（幂等持久化应用服务）。

冻结实现语义：PostgreSQL `clock_timestamp()`（数据库当前时间）是 Lease（租约）与 Terminal TTL（终态有效期）的权威时间源；五个 Scope（作用域）字段共同参与原子唯一 Claim（声明）；Fingerprint Version（请求指纹版本）不一致时在有效期内安全失败；连续 Replay（结果重放）始终指向原始实际执行请求；Reclaim（回收）为旧请求创建固定失败码的管理型 Run（运行记录）；取消不写普通失败终态；Fencing（执行权隔离）同时校验 Owner（所有者）、`claim_version`（执行权版本）和 ACTIVE（执行中）状态。

```text
m1_4d_status = completed                              # M1.4-D 状态：已完成
collected_nodeids = 613                               # 当前收集节点：613
unit = 546 passed, 67 deselected                      # 单元测试结果
postgres_non_destructive = 64 passed, 2 skipped      # 非破坏性 PostgreSQL 结果
postgres_destructive = 66 passed, 0 skipped          # 完整 PostgreSQL 结果
full_with_postgres = 612 passed, 1 skipped           # 启用 PostgreSQL 的全量回归结果
legacy_m1_1_missing = 0                               # M1.1 历史节点缺失数
legacy_m1_4_b_missing = 0                             # M1.4-B 历史节点缺失数
legacy_m1_4_b_r1_missing = 0                          # M1.4-B-R1 历史节点缺失数
legacy_m1_4_c_missing = 0                             # M1.4-C 历史节点缺失数
final_revision = 0001 (head)                          # 最终迁移版本
postgres_ci_job = implemented                         # PostgreSQL CI 任务：已实现
postgres_ci_workflow_static_validation = PASS         # CI 工作流静态验证：通过
postgres_ci_runtime_execution = not_run_in_current_environment # 当前环境未运行远程 CI
fastapi_idempotency_wiring = not_implemented           # FastAPI 幂等接线：尚未实现
external_side_effect_exactly_once = not_guaranteed     # 外部副作用严格一次：不保证
```

完整独立记录见 [M1.4-D Closeout](m1_4d_persistent_idempotency_closeout.md)。该段保留 M1.4-D 收尾时的历史状态；M1.4-E 后续已完成，当前状态见下一节。

## 24. M1.4-E Closeout（里程碑 1.4-E 收尾）— COMPLETED（已完成）

M1.4-E 已将 `DurableApplicationService`（持久应用服务）和 `IdempotentDurableApplicationService`（幂等持久应用服务）接入唯一 FastAPI composition root（FastAPI 组合根）。`DatabaseEngine`（数据库引擎）只在 worker lifespan（工作进程生命周期）创建和释放；PostgreSQL 模式启动时检查 connectivity（连通性）和 `0001` Alembic revision（迁移版本），不自动迁移。

HTTP Contract（HTTP 契约）包含原始 ASGI Header 重复检测、`optional`（可选）/`required`（必需）策略、`v1.chat`/`v1.qa`（版本化操作名）、完整应用 DTO Fingerprint（请求指纹）、executed/replayed（已执行/已重放）成功响应头，以及 409/500/503 安全错误映射。`/healthz`（存活探针）与 `/readyz`（就绪探针）职责分离。

完整独立证据见 [M1.4-E Closeout](m1_4e_fastapi_durable_wiring_closeout.md)。M1.4-F 已在后续批次完成，当前状态见下一节。

## 25. M1.4-F 与最终 Closeout（收尾）— COMPLETED（已完成）

M1.4-F 已实现并本地验证 persistence Doctor（持久化诊断器）、Integrity Checker（完整性检查器）、manual terminal prune（手动终态清理）、PostgreSQL least-privilege roles（最小权限角色）、进程崩溃 Reclaim（回收）、共享数据库多实例 Claim（声明）协调、数据库网络中断恢复、连接池 Soak（稳定性运行）以及逻辑备份/全新数据库恢复。

```text
collected_nodeids = 663
unit = 582 passed, 81 deselected
postgres_non_destructive = 72 passed, 2 skipped
postgres_destructive = 74 passed, 0 skipped
operational = 6 passed, 0 skipped
default_full = 582 passed, 81 skipped
main_with_real_postgres = 656 passed, 1 skipped, 6 operational deselected
all_historical_nodeids_missing = 0
final_revision = 0001 (head)
metadata_diff = 0
backup_restore = PASS
postgres_ci_runtime_execution = not_run_in_current_environment
operational_ci_runtime_execution = not_run_in_current_environment
```

本地演练不表示生产备份基础设施、外部副作用 exactly-once（严格一次）、Claim heartbeat（声明心跳）或自动 orphan sweeper（孤儿清理器）已经实现。完整证据见 [M1.4-F Closeout](m1_4f_operational_readiness_closeout.md) 与 [M1.4 Final Closeout](m1_4_final_closeout.md)。

## 26. References（参考资料）

- [M1.3 Closeout](m1_3_oidc_jwt_authorization_closeout.md)
- [M1.4-D Closeout](m1_4d_persistent_idempotency_closeout.md)
- [M1.4-E Closeout](m1_4e_fastapi_durable_wiring_closeout.md)
- [M1.4-F Closeout](m1_4f_operational_readiness_closeout.md)
- [M1.4 Final Closeout](m1_4_final_closeout.md)
- [Productization Roadmap](../sme-productization-roadmap.md)
- [Project Design](../project-design.md)
