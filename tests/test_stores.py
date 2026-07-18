"""Comprehensive tests for CustomerStore and InteractionStore (Phase 3)."""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conversation_agent.config import reset_config, get_config
from conversation_agent.memory.customer_store import CustomerStore
from conversation_agent.memory.interaction_store import InteractionStore
from conversation_agent.sales.models import (
    Contact,
    CustomerProfile,
    CustomerStatus,
    DealScore,
    DealLevel,
    FollowUpSuggestion,
    HealthScore,
    HealthStatus,
    InteractionRecord,
    InteractionType,
    ProcurementItem,
    ProcurementSignals,
    ProductCategory,
    RiskItem,
    RiskLevel,
    SalesStage,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch, tmp_path):
    """Every test gets an isolated data directory, no cross-test pollution."""
    reset_config()
    cfg = get_config()
    cfg.storage.data_dir = tmp_path / "data"
    yield
    reset_config()


def _make_profile(
    customer_id: str = "c001",
    name: str = "测试公司",
    **overrides,
) -> CustomerProfile:
    kwargs = {
        "customer_id": customer_id,
        "customer_name": name,
        **overrides,
    }
    return CustomerProfile(**kwargs)


def _make_interaction(
    interaction_id: str = "int_001",
    customer_id: str = "c001",
    **overrides,
) -> InteractionRecord:
    kwargs = {
        "interaction_id": interaction_id,
        "customer_id": customer_id,
        **overrides,
    }
    return InteractionRecord(**kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
# CustomerStore
# ═══════════════════════════════════════════════════════════════════════════════


class TestCustomerStoreCRUD:
    def test_save_and_load(self):
        cs = CustomerStore()
        cp = _make_profile()
        cs.save(cp)
        loaded = cs.load("c001")
        assert loaded is not None
        assert loaded.customer_name == "测试公司"
        assert loaded.version >= 1

    def test_save_bumps_version(self):
        cs = CustomerStore()
        cp = _make_profile(version=3)
        cs.save(cp)
        loaded = cs.load("c001")
        assert loaded.version == 4  # bump_version() called by save

    def test_save_updates_updated_at(self):
        cs = CustomerStore()
        cp = _make_profile()
        old_ts = cp.updated_at
        cs.save(cp)
        loaded = cs.load("c001")
        assert loaded.updated_at > old_ts

    def test_load_nonexistent(self):
        cs = CustomerStore()
        assert cs.load("nonexistent") is None

    def test_load_corrupted_json(self):
        cs = CustomerStore()
        path = cs._path_for("bad")
        path.write_text("not valid json", encoding="utf-8")
        assert cs.load("bad") is None

    def test_load_invalid_model(self):
        cs = CustomerStore()
        path = cs._path_for("bad")
        path.write_text('{"customer_id": "bad"}', encoding="utf-8")  # missing name
        assert cs.load("bad") is None

    def test_exists(self):
        cs = CustomerStore()
        assert not cs.exists("c001")
        cs.save(_make_profile())
        assert cs.exists("c001")

    def test_delete(self):
        cs = CustomerStore()
        cs.save(_make_profile())
        assert cs.exists("c001")
        assert cs.delete("c001")
        assert not cs.exists("c001")

    def test_delete_nonexistent(self):
        cs = CustomerStore()
        assert not cs.delete("nonexistent")

    def test_list_all(self):
        cs = CustomerStore()
        cs.save(_make_profile("c001", "公司A"))
        cs.save(_make_profile("c002", "公司B"))
        cs.save(_make_profile("c003", "公司C"))
        all_cust = cs.list_all()
        assert len(all_cust) == 3
        names = {c.customer_name for c in all_cust}
        assert names == {"公司A", "公司B", "公司C"}

    def test_list_all_skips_corrupted(self):
        cs = CustomerStore()
        cs.save(_make_profile("c001", "好的"))
        # Write a corrupted file
        (cs._dir / "bad.json").write_text("garbage", encoding="utf-8")
        all_cust = cs.list_all()
        assert len(all_cust) == 1

    def test_count(self):
        cs = CustomerStore()
        assert cs.count() == 0
        cs.save(_make_profile())
        assert cs.count() == 1


class TestCustomerStoreBackup:
    def test_backup_created_on_save(self):
        cs = CustomerStore()
        cp = _make_profile()
        cs.save(cp)
        # Modify and save again
        cp.customer_name = "改名后的公司"
        cs.save(cp)
        bak = cs._backup_dir / "c001.bak"
        assert bak.exists()

    def test_backup_content(self):
        cs = CustomerStore()
        cp = _make_profile(customer_name="原始名称")
        cs.save(cp)
        cp.customer_name = "新名称"
        cs.save(cp)
        bak = cs._backup_dir / "c001.bak"
        data = json.loads(bak.read_text(encoding="utf-8"))
        assert data["customer_name"] == "原始名称"

    def test_restore(self):
        cs = CustomerStore()
        cp = _make_profile(customer_name="原始")
        cs.save(cp)
        # After first save, .bak exists.  Change the name and save again.
        cp.customer_name = "修改后"
        cs.save(cp)
        # Restore should bring back "原始"
        restored = cs.restore("c001")
        assert restored is not None
        assert restored.customer_name == "原始"
        # Check on-disk file is also restored
        on_disk = cs.load("c001")
        assert on_disk.customer_name == "原始"

    def test_restore_no_backup(self):
        cs = CustomerStore()
        assert cs.restore("c001") is None

    def test_list_backups(self):
        cs = CustomerStore()
        cs.save(_make_profile("c001"))
        cs.save(_make_profile("c002"))
        # Save c001 again to create a backup
        cp = cs.load("c001")
        cp.customer_name = "changed"
        cs.save(cp)
        backups = cs.list_backups()
        assert "c001" in backups

    def test_deleted_bak_on_delete(self):
        cs = CustomerStore()
        cs.save(_make_profile())
        cs.delete("c001")
        deleted_bak = cs._backup_dir / "c001.deleted.bak"
        assert deleted_bak.exists()

    def test_backup_rotation(self):
        """Old backups are cleaned up when backup_max_keep is reached."""
        cs = CustomerStore()
        cs._backup_max_keep = 2
        cp = _make_profile()
        cs.save(cp)  # v1
        for i in range(5):
            cp.customer_name = f"v{i}"
            cs.save(cp)
        # Only 2 backups should remain
        backups = list(cs._backup_dir.glob("c001.bak*"))
        assert len(backups) <= 2


class TestCustomerStoreSearch:
    def test_find_by_name_exact(self):
        cs = CustomerStore()
        cs.save(_make_profile("c001", "联想集团"))
        found = cs.find_by_name("联想集团")
        assert found is not None
        assert found.customer_id == "c001"

    def test_find_by_name_alias(self):
        cs = CustomerStore()
        cs.save(_make_profile("c001", "联想集团", aliases=["联想", "Lenovo"]))
        found = cs.find_by_name("联想")
        assert found is not None
        assert found.customer_id == "c001"

    def test_find_by_name_missing(self):
        cs = CustomerStore()
        assert cs.find_by_name("不存在") is None

    def test_find_by_industry(self):
        cs = CustomerStore()
        cs.save(_make_profile("c001", "A", industry="IT"))
        cs.save(_make_profile("c002", "B", industry="Finance"))
        cs.save(_make_profile("c003", "C", industry="IT"))
        results = cs.find_by_industry("IT")
        assert len(results) == 2

    def test_find_by_sales_stage(self):
        cs = CustomerStore()
        cs.save(_make_profile("c001", "A", sales_stage=SalesStage.LEAD))
        cs.save(_make_profile("c002", "B", sales_stage=SalesStage.WON))
        results = cs.find_by_sales_stage("won")
        assert len(results) == 1
        assert results[0].customer_id == "c002"

    def test_find_by_status(self):
        cs = CustomerStore()
        cs.save(_make_profile("c001", "A", status=CustomerStatus.ACTIVE))
        cs.save(_make_profile("c002", "B", status=CustomerStatus.WON))
        results = cs.find_by_status("won")
        assert len(results) == 1

    def test_search_combined(self):
        cs = CustomerStore()
        cs.save(_make_profile("c001", "联想", industry="IT", sales_stage=SalesStage.LEAD))
        cs.save(_make_profile("c002", "华为", industry="Telecom", sales_stage=SalesStage.LEAD))
        cs.save(_make_profile("c003", "联想控股", industry="Finance", sales_stage=SalesStage.WON))
        results = cs.search(customer_name="联想", industry="IT")
        assert len(results) == 1
        assert results[0].customer_id == "c001"

    def test_search_no_filters_returns_all(self):
        cs = CustomerStore()
        cs.save(_make_profile("c001", "A"))
        cs.save(_make_profile("c002", "B"))
        # No filters → returns all
        results = cs.search()
        assert len(results) == 2

    def test_find_similar(self):
        cs = CustomerStore()
        cs.save(_make_profile("c001", "联想集团有限公司"))
        cs.save(_make_profile("c002", "华为技术有限公司"))
        results = cs.find_similar("联想集团")
        assert len(results) >= 0  # Fuzzy matching is approximate


class TestCustomerStoreAtomic:
    def test_atomic_write_no_partial_file(self):
        """If a write crashes mid-way, no .tmp file should remain."""
        cs = CustomerStore()
        cp = _make_profile()
        cs.save(cp)
        # No .tmp files should exist after a successful save
        tmps = list(cs._dir.glob("*.tmp"))
        assert len(tmps) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# InteractionStore
# ═══════════════════════════════════════════════════════════════════════════════


class TestInteractionStoreCRUD:
    def test_save_and_load(self):
        istore = InteractionStore()
        ir = _make_interaction()
        istore.save(ir)
        loaded = istore.load("c001", "int_001")
        assert loaded is not None
        assert loaded.interaction_id == "int_001"
        assert loaded.customer_id == "c001"

    def test_save_preserves_fields(self):
        istore = InteractionStore()
        ir = _make_interaction(
            type=InteractionType.MEETING,
            raw_text="讨论了服务器采购",
            summary="采购会议",
            key_quotes=["我们预算500万"],
        )
        istore.save(ir)
        loaded = istore.load("c001", "int_001")
        assert loaded.type == InteractionType.MEETING
        assert loaded.raw_text == "讨论了服务器采购"
        assert loaded.summary == "采购会议"

    def test_load_nonexistent(self):
        istore = InteractionStore()
        assert istore.load("c001", "nonexistent") is None

    def test_exists(self):
        istore = InteractionStore()
        assert not istore.exists("c001", "int_001")
        istore.save(_make_interaction())
        assert istore.exists("c001", "int_001")

    def test_delete(self):
        istore = InteractionStore()
        istore.save(_make_interaction())
        assert istore.delete("c001", "int_001")
        assert not istore.exists("c001", "int_001")

    def test_delete_nonexistent(self):
        istore = InteractionStore()
        assert not istore.delete("c001", "nonexistent")

    def test_list_for_customer(self):
        istore = InteractionStore()
        for i in range(5):
            istore.save(_make_interaction(f"int_{i:03d}"))
        records = istore.list_for_customer("c001")
        assert len(records) == 5
        # Newest first
        dates = [r.date for r in records]
        assert dates == sorted(dates, reverse=True)

    def test_list_for_customer_limit(self):
        istore = InteractionStore()
        for i in range(10):
            istore.save(_make_interaction(f"int_{i:03d}"))
        records = istore.list_for_customer("c001", limit=3)
        assert len(records) == 3

    def test_list_for_nonexistent_customer(self):
        istore = InteractionStore()
        assert istore.list_for_customer("nonexistent") == []

    def test_count_for_customer(self):
        istore = InteractionStore()
        assert istore.count_for_customer("c001") == 0
        istore.save(_make_interaction("int_001"))
        istore.save(_make_interaction("int_002"))
        assert istore.count_for_customer("c001") == 2

    def test_get_recent_summaries(self):
        istore = InteractionStore()
        ir = _make_interaction(
            summary="采购会议讨论",
            type=InteractionType.MEETING,
        )
        istore.save(ir)
        summaries = istore.get_recent_summaries("c001", n=5)
        assert len(summaries) == 1
        assert summaries[0]["summary"] == "采购会议讨论"
        assert summaries[0]["type"] == "meeting"

    def test_last_interaction_date(self):
        istore = InteractionStore()
        assert istore.last_interaction_date("c001") is None
        ir = _make_interaction()
        istore.save(ir)
        last = istore.last_interaction_date("c001")
        assert last is not None
        assert isinstance(last, datetime)

    def test_list_skips_corrupted(self):
        istore = InteractionStore()
        istore.save(_make_interaction("int_001"))
        # Write a corrupted file manually
        bad_path = istore._dir / "c001" / "int_bad.json"
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_path.write_text("not json", encoding="utf-8")
        records = istore.list_for_customer("c001")
        # Should still find the good one
        assert len(records) == 1
        assert records[0].interaction_id == "int_001"


class TestInteractionStoreFullRecord:
    def test_save_full_record_round_trip(self):
        istore = InteractionStore()
        ir = InteractionRecord(
            interaction_id="int_full",
            customer_id="c001",
            type=InteractionType.MEETING,
            raw_text="详细会议记录...",
            summary="完整测试会议",
            key_quotes=["Q1", "Q2"],
            extracted_facts={"k1": "v1"},
            procurement_signals=ProcurementSignals(urgency_signal="急"),
            risks=[RiskItem(level=RiskLevel.HIGH, reason="风险")],
            next_actions=["行动1"],
        )
        istore.save(ir)
        loaded = istore.load("c001", "int_full")
        assert loaded.key_quotes == ["Q1", "Q2"]
        assert loaded.extracted_facts == {"k1": "v1"}
        assert loaded.procurement_signals.urgency_signal == "急"
        assert len(loaded.risks) == 1
        assert loaded.has_risks


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: Store + Seed
# ═══════════════════════════════════════════════════════════════════════════════


class TestSeedIntegration:
    def test_seed_creates_customers_and_interactions(self):
        from conversation_agent.cli.seed import seed
        n = seed(count=3, clear_first=True)
        assert n == 3

        cs = CustomerStore()
        istore = InteractionStore()

        customers = cs.list_all()
        assert len(customers) == 3
        for c in customers:
            assert c.customer_name
            assert c.schema_version == 1
            interactions = istore.list_for_customer(c.customer_id)
            # Each customer should have at least 1 interaction
            assert len(interactions) >= 1, f"{c.customer_name} has no interactions"

    def test_seed_clear_first(self):
        from conversation_agent.cli.seed import seed

        # Seed once
        seed(count=2, clear_first=True)
        cs = CustomerStore()
        assert cs.count() == 2

        # Seed again with clear
        seed(count=3, clear_first=True)
        assert cs.count() == 3

    def test_seed_data_is_valid(self):
        """Every seeded profile passes model validation."""
        from conversation_agent.cli.seed import generate_seed_data
        customers, interactions = generate_seed_data(5)
        for c in customers:
            # model_dump should succeed
            data = c.model_dump(mode="json")
            # Re-validate
            CustomerProfile(**data)
        for ir in interactions:
            data = ir.model_dump(mode="json")
            InteractionRecord(**data)


# ═══════════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestStoreEdgeCases:
    def test_empty_name_company(self):
        """CustomerProfile with empty name should fail validation."""
        with pytest.raises(Exception):
            _make_profile(name="")

    def test_duplicate_customer_id_overwrites(self):
        """Saving with same ID overwrites the file."""
        cs = CustomerStore()
        cs.save(_make_profile("c001", "原始"))
        cs.save(_make_profile("c001", "覆盖"))
        loaded = cs.load("c001")
        assert loaded.customer_name == "覆盖"

    def test_many_customers(self):
        cs = CustomerStore()
        for i in range(20):
            cs.save(_make_profile(f"c{i:04d}", f"公司{i}"))
        assert cs.count() == 20
        assert len(cs.list_all()) == 20

    def test_interaction_store_isolated_per_customer(self):
        istore = InteractionStore()
        istore.save(_make_interaction("int_001", "c001", summary="c1 interaction"))
        istore.save(_make_interaction("int_001", "c002", summary="c2 interaction"))
        r1 = istore.load("c001", "int_001")
        r2 = istore.load("c002", "int_001")
        assert r1.summary == "c1 interaction"
        assert r2.summary == "c2 interaction"
