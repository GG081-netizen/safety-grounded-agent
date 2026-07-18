"""M1.4-A unit tests — DatabaseConfig, DatabaseEngine, null/fake persistence.

These tests verify the configuration Contract, engine lifecycle,
and null/fake persistence behaviour WITHOUT accessing a real PostgreSQL
database or the network.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from conversation_agent.config import (
    AppConfig,
    DatabaseConfig,
    get_config,
    load_config,
    reset_config,
)
from conversation_agent.database.engine import DatabaseEngine
from conversation_agent.database.null_persistence import (
    FakeRepository,
    FakeRepositoryState,
    FakeUnitOfWork,
    NullRepository,
    NullUnitOfWork,
)
from conversation_agent.database.repository import (
    DatabaseRepository,
    IdempotencyClaimResult,
)
from conversation_agent.database.unit_of_work import UnitOfWork

pytestmark = pytest.mark.unit


# ═══════════════════════════════════════════════════════════════════════════════
# DatabaseConfig defaults
# ═══════════════════════════════════════════════════════════════════════════════


class TestDatabaseConfigDefaults:
    def test_default_enabled(self):
        cfg = DatabaseConfig()
        assert cfg.enabled is True

    def test_default_required(self):
        cfg = DatabaseConfig()
        assert cfg.required is False

    def test_default_url_empty(self):
        cfg = DatabaseConfig()
        assert cfg.url_value == ""

    def test_default_pool_size(self):
        cfg = DatabaseConfig()
        assert cfg.pool_size == 5

    def test_default_max_overflow(self):
        cfg = DatabaseConfig()
        assert cfg.max_overflow == 10

    def test_default_pool_timeout(self):
        cfg = DatabaseConfig()
        assert cfg.pool_timeout_seconds == 30.0

    def test_default_pool_recycle(self):
        cfg = DatabaseConfig()
        assert cfg.pool_recycle_seconds == 3600.0

    def test_default_echo(self):
        cfg = DatabaseConfig()
        assert cfg.echo is False

    def test_default_auto_migrate(self):
        cfg = DatabaseConfig()
        assert cfg.auto_migrate is False

    def test_default_store_user_text(self):
        cfg = DatabaseConfig()
        assert cfg.store_user_text is False

    def test_default_idempotency_ttl(self):
        cfg = DatabaseConfig()
        assert cfg.idempotency_ttl_seconds == 3600

    def test_default_stale_in_progress_timeout(self):
        cfg = DatabaseConfig()
        assert cfg.stale_in_progress_timeout_seconds == 300

    def test_is_configured_false_when_url_empty(self):
        cfg = DatabaseConfig()
        assert cfg.is_configured is False

    def test_is_configured_true_when_url_set(self):
        cfg = DatabaseConfig(url="postgresql+asyncpg://localhost/db")
        assert cfg.is_configured is True


# ═══════════════════════════════════════════════════════════════════════════════
# DatabaseConfig validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestDatabaseConfigValidation:
    def test_enabled_false_and_required_true_is_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            DatabaseConfig(enabled=False, required=True)
        errors = exc_info.value.errors()
        assert any("cannot be required when disabled" in e["msg"] for e in errors)

    def test_enabled_false_and_required_false_is_allowed(self):
        cfg = DatabaseConfig(enabled=False, required=False)
        assert cfg.enabled is False
        assert cfg.required is False

    def test_enabled_true_and_required_true_is_allowed(self):
        cfg = DatabaseConfig(enabled=True, required=True)
        assert cfg.enabled is True
        assert cfg.required is True

    def test_pool_size_below_minimum_rejected(self):
        with pytest.raises(ValidationError):
            DatabaseConfig(pool_size=0)

    def test_pool_size_above_maximum_rejected(self):
        with pytest.raises(ValidationError):
            DatabaseConfig(pool_size=51)

    def test_max_overflow_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            DatabaseConfig(max_overflow=-1)

    def test_pool_timeout_below_minimum_rejected(self):
        with pytest.raises(ValidationError):
            DatabaseConfig(pool_timeout_seconds=0.5)


# ═══════════════════════════════════════════════════════════════════════════════
# Runtime mode configuration semantics
# ═══════════════════════════════════════════════════════════════════════════════


class TestRuntimeModeDatabaseSemantics:
    """demo / test / production configuration Contract."""

    def test_demo_default_has_no_database_url(self):
        cfg = AppConfig(runtime_mode="demo")
        assert cfg.database.url_value == ""
        assert cfg.database.is_configured is False
        # demo + no URL must NOT raise
        AppConfig.model_validate(cfg.model_dump())

    def test_test_default_has_no_database_url(self):
        cfg = AppConfig(runtime_mode="test")
        assert cfg.database.url_value == ""

    def test_production_without_url_is_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            AppConfig(
                runtime_mode="production",
                oidc={
                    "issuer": "https://idp.example.com",
                    "audience": "aud",
                    "jwks_url": "https://idp.example.com/.well-known/jwks.json",
                },
                # database.url is empty by default
            )
        errors = exc_info.value.errors()
        assert any("explicit persistence mode" in e["msg"] for e in errors)

    def test_production_with_disabled_database_is_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            AppConfig(
                runtime_mode="production",
                oidc={
                    "issuer": "https://idp.example.com",
                    "audience": "aud",
                    "jwks_url": "https://idp.example.com/.well-known/jwks.json",
                },
                database={"url": "postgresql+asyncpg://localhost/db", "enabled": False},
            )
        errors = exc_info.value.errors()
        assert any("explicit persistence mode" in e["msg"] for e in errors)

    def test_production_with_auto_migrate_true_is_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            AppConfig(
                runtime_mode="production",
                oidc={
                    "issuer": "https://idp.example.com",
                    "audience": "aud",
                    "jwks_url": "https://idp.example.com/.well-known/jwks.json",
                },
                database={
                    "url": "postgresql+asyncpg://localhost/db",
                    "persistence_mode": "postgres",
                    "idempotency_header_mode": "required",
                    "enabled": True,
                    "auto_migrate": True,
                },
            )
        errors = exc_info.value.errors()
        assert any("auto_migrate must be false in production" in e["msg"] for e in errors)

    def test_production_with_valid_db_config_is_accepted(self):
        cfg = AppConfig(
            runtime_mode="production",
            oidc={
                "issuer": "https://idp.example.com",
                "audience": "aud",
                "jwks_url": "https://idp.example.com/.well-known/jwks.json",
            },
            database={
                "url": "postgresql+asyncpg://localhost/db",
                "persistence_mode": "postgres",
                "idempotency_header_mode": "required",
                "enabled": True,
                "auto_migrate": False,
            },
        )
        assert cfg.database.url_value == "postgresql+asyncpg://localhost/db"


# ═══════════════════════════════════════════════════════════════════════════════
# DatabaseEngine — no network on import/construct
# ═══════════════════════════════════════════════════════════════════════════════


class TestDatabaseEngineNoNetwork:
    """DatabaseEngine must not access the network at import or construction time."""

    def test_construction_does_not_connect(self):
        """Creating a DatabaseEngine must not create connections."""
        cfg = DatabaseConfig(url="postgresql+asyncpg://localhost:5432/db")
        engine = DatabaseEngine(cfg)
        assert engine._engine is None
        assert engine._session_factory is None

    def test_empty_url_raises_at_construction(self):
        cfg = DatabaseConfig(url="")
        with pytest.raises(ValueError, match="non-empty string"):
            DatabaseEngine(cfg)

    def test_accessing_engine_before_start_raises(self):
        cfg = DatabaseConfig(url="postgresql+asyncpg://localhost:5432/db")
        engine = DatabaseEngine(cfg)
        with pytest.raises(RuntimeError, match="has not been started"):
            _ = engine.engine

    def test_accessing_session_factory_before_start_raises(self):
        cfg = DatabaseConfig(url="postgresql+asyncpg://localhost:5432/db")
        engine = DatabaseEngine(cfg)
        with pytest.raises(RuntimeError, match="has not been started"):
            _ = engine.session_factory

    @pytest.mark.asyncio
    async def test_session_context_manager_before_start_raises(self):
        cfg = DatabaseConfig(url="postgresql+asyncpg://localhost:5432/db")
        engine = DatabaseEngine(cfg)
        with pytest.raises(RuntimeError, match="has not been started"):
            async with engine.session():
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# DatabaseEngine dispose idempotency
# ═══════════════════════════════════════════════════════════════════════════════


class TestDatabaseEngineDisposeIdempotent:
    """stop() / dispose() must be safe to call multiple times."""

    @pytest.mark.asyncio
    async def test_stop_before_start_is_safe(self):
        cfg = DatabaseConfig(url="postgresql+asyncpg://localhost:5432/db")
        engine = DatabaseEngine(cfg)
        # Should not raise
        await engine.stop()

    @pytest.mark.asyncio
    async def test_stop_twice_is_safe(self):
        cfg = DatabaseConfig(url="postgresql+asyncpg://localhost:5432/db")
        engine = DatabaseEngine(cfg)
        await engine.stop()
        await engine.stop()  # idempotent


# ═══════════════════════════════════════════════════════════════════════════════
# NullRepository / NullUnitOfWork
# ═══════════════════════════════════════════════════════════════════════════════


class TestNullRepository:
    """NullRepository returns safe defaults with no side-effects."""

    def test_is_a_database_repository(self):
        repo = NullRepository()
        assert isinstance(repo, DatabaseRepository)

    @pytest.mark.asyncio
    async def test_claim_idempotency_always_succeeds(self):
        repo = NullRepository()
        result = await repo.claim_idempotency(
            session=None, scope="s", idempotency_key="k",
            request_fingerprint="fp", fingerprint_version=1,
            owner_request_id="rid", lease_duration_seconds=300,
        )
        assert result.claimed is True
        assert result.existing_record is None

    @pytest.mark.asyncio
    async def test_re_claim_always_succeeds(self):
        repo = NullRepository()
        ok, version = await repo.re_claim_idempotency(
            session=None, scope="s", idempotency_key="k",
            new_fingerprint="fp2", new_owner_request_id="rid2",
            lease_duration_seconds=300,
        )
        assert ok is True
        assert version == 1

    @pytest.mark.asyncio
    async def test_complete_fenced_always_succeeds(self):
        repo = NullRepository()
        ok = await repo.complete_idempotency_fenced(
            session=None, scope="s", idempotency_key="k",
            owner_request_id="rid", claim_version=1,
            completed_run_id="run1", response_snapshot={},
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_fail_fenced_always_succeeds(self):
        repo = NullRepository()
        ok = await repo.fail_idempotency_fenced(
            session=None, scope="s", idempotency_key="k",
            owner_request_id="rid", claim_version=1,
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_find_always_returns_none(self):
        repo = NullRepository()
        result = await repo.find_idempotency_by_scope_key(
            session=None, scope="s", idempotency_key="k",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_insert_agent_request_echoes_record(self):
        repo = NullRepository()
        record = {"request_id": "r1", "status": "in_progress"}
        result = await repo.insert_agent_request(None, record)
        assert result["request_id"] == "r1"

    @pytest.mark.asyncio
    async def test_update_agent_request_status_always_succeeds(self):
        repo = NullRepository()
        ok = await repo.update_agent_request_status(None, "r1", "completed")
        assert ok is True

    @pytest.mark.asyncio
    async def test_insert_agent_run_echoes_record(self):
        repo = NullRepository()
        record = {"run_id": "run1"}
        result = await repo.insert_agent_run(None, record)
        assert result["run_id"] == "run1"

    @pytest.mark.asyncio
    async def test_insert_audit_event_echoes_record(self):
        repo = NullRepository()
        record = {"event_type": "request_accepted"}
        result = await repo.insert_audit_event(None, record)
        assert result["event_type"] == "request_accepted"


class TestNullUnitOfWork:
    """NullUnitOfWork is a no-op UnitOfWork suitable for demo mode."""

    @pytest.mark.asyncio
    async def test_begin_returns_sentinel(self):
        uow = NullUnitOfWork()
        session = await uow.begin()
        assert session is not None

    @pytest.mark.asyncio
    async def test_commit_does_not_raise(self):
        uow = NullUnitOfWork()
        await uow.begin()
        await uow.commit()

    @pytest.mark.asyncio
    async def test_rollback_does_not_raise(self):
        uow = NullUnitOfWork()
        await uow.begin()
        await uow.rollback()

    def test_satisfies_unit_of_work_protocol(self):
        uow = NullUnitOfWork()
        assert isinstance(uow, UnitOfWork)


# ═══════════════════════════════════════════════════════════════════════════════
# FakeRepository / FakeUnitOfWork
# ═══════════════════════════════════════════════════════════════════════════════


class TestFakeRepository:
    """FakeRepository provides observable in-memory persistence for tests."""

    def test_is_a_database_repository(self):
        repo = FakeRepository()
        assert isinstance(repo, DatabaseRepository)

    @pytest.mark.asyncio
    async def test_claim_inserts_and_returns_claimed(self):
        state = FakeRepositoryState()
        repo = FakeRepository(state)
        result = await repo.claim_idempotency(
            session=None, scope="s1", idempotency_key="k1",
            request_fingerprint="fp", fingerprint_version=1,
            owner_request_id="rid1", lease_duration_seconds=300,
        )
        assert result.claimed is True
        assert ("s1", "k1") in state.idempotency_records
        assert state.idempotency_records[("s1", "k1")]["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_claim_duplicate_returns_existing(self):
        state = FakeRepositoryState()
        state.idempotency_records[("s1", "k1")] = {"status": "completed"}
        repo = FakeRepository(state)
        result = await repo.claim_idempotency(
            session=None, scope="s1", idempotency_key="k1",
            request_fingerprint="fp", fingerprint_version=1,
            owner_request_id="rid2", lease_duration_seconds=300,
        )
        assert result.claimed is False
        assert result.existing_record == {"status": "completed"}

    @pytest.mark.asyncio
    async def test_re_claim_increments_version(self):
        state = FakeRepositoryState()
        state.idempotency_records[("s1", "k1")] = {
            "status": "failed", "claim_version": 3,
            "owner_request_id": "old", "request_fingerprint": "fp-old",
        }
        repo = FakeRepository(state)
        ok, version = await repo.re_claim_idempotency(
            session=None, scope="s1", idempotency_key="k1",
            new_fingerprint="fp-new", new_owner_request_id="rid-new",
            lease_duration_seconds=300,
        )
        assert ok is True
        assert version == 4
        rec = state.idempotency_records[("s1", "k1")]
        assert rec["owner_request_id"] == "rid-new"
        assert rec["request_fingerprint"] == "fp-new"
        assert rec["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_re_claim_missing_key_returns_false(self):
        repo = FakeRepository()
        ok, version = await repo.re_claim_idempotency(
            session=None, scope="s1", idempotency_key="k1",
            new_fingerprint="fp", new_owner_request_id="rid",
            lease_duration_seconds=300,
        )
        assert ok is False
        assert version is None

    @pytest.mark.asyncio
    async def test_complete_fenced_succeeds_with_correct_fence(self):
        state = FakeRepositoryState()
        state.idempotency_records[("s1", "k1")] = {
            "status": "in_progress",
            "owner_request_id": "rid1",
            "claim_version": 2,
        }
        repo = FakeRepository(state)
        ok = await repo.complete_idempotency_fenced(
            session=None, scope="s1", idempotency_key="k1",
            owner_request_id="rid1", claim_version=2,
            completed_run_id="run1", response_snapshot={"result": "ok"},
        )
        assert ok is True
        rec = state.idempotency_records[("s1", "k1")]
        assert rec["status"] == "completed"
        assert rec["completed_run_id"] == "run1"

    @pytest.mark.asyncio
    async def test_complete_fenced_fails_with_wrong_owner(self):
        state = FakeRepositoryState()
        state.idempotency_records[("s1", "k1")] = {
            "status": "in_progress",
            "owner_request_id": "rid1",
            "claim_version": 1,
        }
        repo = FakeRepository(state)
        ok = await repo.complete_idempotency_fenced(
            session=None, scope="s1", idempotency_key="k1",
            owner_request_id="rid2", claim_version=1,  # wrong owner
            completed_run_id="run1", response_snapshot={},
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_complete_fenced_fails_with_wrong_version(self):
        state = FakeRepositoryState()
        state.idempotency_records[("s1", "k1")] = {
            "status": "in_progress",
            "owner_request_id": "rid1",
            "claim_version": 2,
        }
        repo = FakeRepository(state)
        ok = await repo.complete_idempotency_fenced(
            session=None, scope="s1", idempotency_key="k1",
            owner_request_id="rid1", claim_version=1,  # stale version
            completed_run_id="run1", response_snapshot={},
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_fail_fenced_succeeds_with_correct_fence(self):
        state = FakeRepositoryState()
        state.idempotency_records[("s1", "k1")] = {
            "status": "in_progress",
            "owner_request_id": "rid1",
            "claim_version": 1,
        }
        repo = FakeRepository(state)
        ok = await repo.fail_idempotency_fenced(
            session=None, scope="s1", idempotency_key="k1",
            owner_request_id="rid1", claim_version=1,
        )
        assert ok is True
        assert state.idempotency_records[("s1", "k1")]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_find_returns_record(self):
        state = FakeRepositoryState()
        state.idempotency_records[("s1", "k1")] = {"status": "completed"}
        repo = FakeRepository(state)
        result = await repo.find_idempotency_by_scope_key(
            session=None, scope="s1", idempotency_key="k1",
        )
        assert result == {"status": "completed"}

    @pytest.mark.asyncio
    async def test_find_missing_returns_none(self):
        repo = FakeRepository()
        result = await repo.find_idempotency_by_scope_key(
            session=None, scope="s1", idempotency_key="k1",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_insert_and_update_agent_request(self):
        repo = FakeRepository()
        await repo.insert_agent_request(None, {"request_id": "r1", "status": "in_progress"})
        assert repo.state.agent_requests["r1"]["status"] == "in_progress"
        ok = await repo.update_agent_request_status(None, "r1", "completed", completed_at="now")
        assert ok is True
        assert repo.state.agent_requests["r1"]["status"] == "completed"
        assert repo.state.agent_requests["r1"]["completed_at"] == "now"

    @pytest.mark.asyncio
    async def test_update_agent_request_missing_returns_false(self):
        repo = FakeRepository()
        ok = await repo.update_agent_request_status(None, "nonexistent", "completed")
        assert ok is False

    @pytest.mark.asyncio
    async def test_insert_agent_run(self):
        repo = FakeRepository()
        record = {"run_id": "run1", "status": "completed"}
        result = await repo.insert_agent_run(None, record)
        assert result["run_id"] == "run1"
        assert "run1" in repo.state.agent_runs

    @pytest.mark.asyncio
    async def test_insert_audit_event(self):
        repo = FakeRepository()
        record = {"event_type": "request_accepted"}
        result = await repo.insert_audit_event(None, record)
        assert result["event_type"] == "request_accepted"
        assert len(repo.state.audit_events) == 1


class TestFakeUnitOfWork:
    """FakeUnitOfWork provides observable transactional semantics for tests."""

    @pytest.mark.asyncio
    async def test_begin_resets_flags(self):
        uow = FakeUnitOfWork()
        uow.committed = True
        await uow.begin()
        assert uow.committed is False
        assert uow.rolled_back is False

    @pytest.mark.asyncio
    async def test_commit_sets_flag(self):
        uow = FakeUnitOfWork()
        await uow.begin()
        await uow.commit()
        assert uow.committed is True
        assert uow.rolled_back is False

    @pytest.mark.asyncio
    async def test_rollback_sets_flag(self):
        uow = FakeUnitOfWork()
        await uow.begin()
        await uow.rollback()
        assert uow.rolled_back is True
        assert uow.committed is False

    def test_exposes_repository_and_state(self):
        state = FakeRepositoryState()
        repo = FakeRepository(state)
        uow = FakeUnitOfWork(repository=repo, state=state)
        assert uow.repository is repo
        assert uow.state is state

    def test_default_constructor_creates_state(self):
        uow = FakeUnitOfWork()
        assert uow.repository is not None
        assert uow.state is not None


# ═══════════════════════════════════════════════════════════════════════════════
# Coordinator / ChatService / API isolation from database
# ═══════════════════════════════════════════════════════════════════════════════


class TestCoordinatorDatabaseIsolation:
    """Coordinator must NOT import or depend on database modules."""

    def test_coordinator_does_not_import_database(self):
        import ast
        import inspect
        from conversation_agent.orchestration.coordinator import Coordinator

        source = inspect.getsource(Coordinator)
        tree = ast.parse(source)
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        db_imports = [i for i in imports if "database" in i.lower()]
        assert db_imports == [], f"Coordinator imports database: {db_imports}"

    def test_chat_service_does_not_import_database(self):
        import ast
        import inspect
        from conversation_agent.application.service import ChatService

        source = inspect.getsource(ChatService)
        tree = ast.parse(source)
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        db_imports = [i for i in imports if "database" in i.lower()]
        assert db_imports == [], f"ChatService imports database: {db_imports}"

    def test_api_module_does_not_import_database(self):
        import ast
        from pathlib import Path

        source_file = (
            Path(__file__).resolve().parent.parent
            / "src" / "conversation_agent" / "api" / "app.py"
        )
        source = source_file.read_text()
        tree = ast.parse(source)
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        db_imports = [i for i in imports if "database" in i.lower()]
        assert db_imports
        source_lower = source.lower()
        assert "sqlalchemy" not in source_lower
        assert "commit(" not in source_lower
        assert "rollback(" not in source_lower


# ═══════════════════════════════════════════════════════════════════════════════
# Config singleton integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfigIntegration:
    """get_config() includes DatabaseConfig in the singleton."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_get_config_includes_database(self):
        cfg = get_config()
        assert hasattr(cfg, "database")
        assert isinstance(cfg.database, DatabaseConfig)

    def test_database_field_in_app_config_schema(self):
        cfg = AppConfig()
        assert cfg.database.enabled is True
        assert cfg.database.pool_size == 5
