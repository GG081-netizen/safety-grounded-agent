"""Customer profile persistence — one JSON file per customer.

Phase 3 upgrades:
  - Delegates version bump to CustomerProfile.bump_version()
  - Atomic writes (write .tmp → rename) to prevent corruption
  - Restore from backup
  - Backup rotation (enforces backup_max_keep)
  - Proper I/O error handling with structured logging
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from conversation_agent.config import get_config
from conversation_agent.sales.models import CustomerProfile

logger = logging.getLogger(__name__)


class CustomerStore:
    """Persist CustomerProfile as individual JSON files under data/customers/.

    Each customer is stored as ``<customer_id>.json``.  Before every write a
    backup is saved to ``data/backups/customers/<customer_id>.bak`` (if
    ``backup_enabled`` is True).  Writes are atomic — data goes to a temp file
    first, then os.rename replaces the target path.
    """

    def __init__(self, customers_dir: Path | None = None) -> None:
        cfg = get_config()
        storage_cfg = cfg.storage
        self._dir = Path(customers_dir or storage_cfg.customers_dir)
        self._backup_dir = storage_cfg.backups_dir / "customers"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._aliases_path = storage_cfg.aliases_file
        self._backup_enabled = storage_cfg.backup_enabled
        self._backup_max_keep = storage_cfg.backup_max_keep
        self._schema_version = cfg.schema_version

    # ── CRUD ───────────────────────────────────────────────────────────────

    def save(self, profile: CustomerProfile) -> Path:
        """Persist a customer profile (atomic write + backup).

        Returns the Path to the written file.  Raises OSError on I/O failure
        — callers should wrap with tool-level exception handling.
        """
        # Let the model own its version lifecycle
        profile.bump_version()

        path = self._path_for(profile.customer_id)

        # Backup before write
        if self._backup_enabled and path.exists():
            self._create_backup(profile.customer_id, path)

        # Atomic write: temp file → rename
        data = profile.model_dump(mode="json")
        json_text = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json_text, encoding="utf-8")
            os.replace(tmp, path)  # atomic on POSIX
        except OSError:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise

        self._sync_alias(profile.customer_id, profile.customer_name, profile.aliases)
        logger.info(
            "Saved customer %s (v%d, stage=%s)",
            profile.customer_id,
            profile.version,
            profile.sales_stage.value,
        )
        return path

    def load(self, customer_id: str) -> CustomerProfile | None:
        """Load a customer profile by ID.

        Returns None if the file doesn't exist or the JSON is corrupted /
        fails Pydantic validation.
        """
        path = self._path_for(customer_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read customer %s: %s", customer_id, exc)
            return None

        sv = data.get("schema_version", 0)
        if sv < self._schema_version:
            logger.warning(
                "Customer %s schema_version=%d < current=%d",
                customer_id,
                sv,
                self._schema_version,
            )
        try:
            return CustomerProfile(**data)
        except Exception as exc:
            logger.error(
                "Failed to validate customer %s: %s", customer_id, exc
            )
            return None

    def exists(self, customer_id: str) -> bool:
        """Check whether a customer profile exists on disk."""
        return self._path_for(customer_id).exists()

    def delete(self, customer_id: str) -> bool:
        """Delete a customer profile (moves to .deleted.bak first).

        Returns True if deleted, False if the file didn't exist.
        """
        path = self._path_for(customer_id)
        if not path.exists():
            return False
        try:
            deleted_bak = self._backup_dir / f"{customer_id}.deleted.bak"
            shutil.copy2(path, deleted_bak)
            path.unlink()
            self._remove_alias(customer_id)
            logger.info("Deleted customer %s", customer_id)
            return True
        except OSError as exc:
            logger.error("Failed to delete customer %s: %s", customer_id, exc)
            return False

    def list_all(self) -> list[CustomerProfile]:
        """Return all customer profiles, skipping unreadable files."""
        profiles: list[CustomerProfile] = []
        for path in sorted(self._dir.glob("*.json")):
            cid = path.stem
            profile = self.load(cid)
            if profile is not None:
                profiles.append(profile)
        return profiles

    def count(self) -> int:
        """Number of customer JSON files (fast, doesn't parse)."""
        return len(list(self._dir.glob("*.json")))

    # ── Backup & Restore ───────────────────────────────────────────────────

    def restore(self, customer_id: str) -> CustomerProfile | None:
        """Restore from the most recent backup (.bak file).

        Returns the restored profile, or None if no backup exists.
        """
        bak_path = self._backup_dir / f"{customer_id}.bak"
        if not bak_path.exists():
            logger.warning("No backup found for customer %s", customer_id)
            return None
        try:
            data = json.loads(bak_path.read_text(encoding="utf-8"))
            profile = CustomerProfile(**data)
            # Write the restored profile back
            path = self._path_for(customer_id)
            json_text = json.dumps(data, ensure_ascii=False, indent=2)
            path.write_text(json_text, encoding="utf-8")
            logger.info("Restored customer %s from backup", customer_id)
            return profile
        except (json.JSONDecodeError, OSError, Exception) as exc:
            logger.error("Failed to restore customer %s: %s", customer_id, exc)
            return None

    def list_backups(self) -> list[str]:
        """Return customer IDs that have a .bak backup file."""
        return sorted(
            p.stem for p in self._backup_dir.glob("*.bak")
            if not p.stem.endswith(".deleted")
        )

    # ── Search ─────────────────────────────────────────────────────────────

    def find_by_name(self, name: str) -> CustomerProfile | None:
        """Resolve a customer by name or alias."""
        aliases = self._load_aliases()
        customer_id = aliases.get(name)
        if customer_id:
            return self.load(customer_id)
        for profile in self.list_all():
            if profile.customer_name == name:
                return profile
        return None

    def find_by_industry(self, industry: str) -> list[CustomerProfile]:
        """Return customers matching an industry."""
        return [p for p in self.list_all() if p.industry == industry]

    def find_by_sales_stage(self, stage: str) -> list[CustomerProfile]:
        """Return customers matching a sales stage."""
        return [
            p for p in self.list_all()
            if p.sales_stage.value == stage or p.sales_stage.value == stage
        ]

    def find_by_status(self, status: str) -> list[CustomerProfile]:
        """Return customers matching a CustomerStatus value."""
        return [
            p for p in self.list_all()
            if p.status.value == status
        ]

    def search(
        self,
        customer_name: str | None = None,
        industry: str | None = None,
        sales_stage: str | None = None,
        status: str | None = None,
    ) -> list[CustomerProfile]:
        """Combined search — at least one filter should be provided.

        All filters are AND-ed together.
        """
        profiles = self.list_all()
        if customer_name:
            name_lower = customer_name.lower()
            profiles = [
                p for p in profiles
                if name_lower in p.customer_name.lower()
                or any(name_lower in a.lower() for a in p.aliases)
            ]
        if industry:
            profiles = [p for p in profiles if p.industry == industry]
        if sales_stage:
            profiles = [
                p for p in profiles
                if p.sales_stage.value == sales_stage
            ]
        if status:
            profiles = [
                p for p in profiles
                if p.status.value == status
            ]
        return profiles

    def find_similar(self, name: str) -> list[CustomerProfile]:
        """Simple fuzzy match — returns profiles with overlapping name tokens."""
        tokens = set(name)
        results: list[CustomerProfile] = []
        for p in self.list_all():
            if p.customer_name == name:
                continue
            if len(tokens & set(p.customer_name)) >= 3:
                results.append(p)
            for alias in p.aliases:
                if len(tokens & set(alias)) >= 3:
                    results.append(p)
                    break
        return results

    # ── Internals ──────────────────────────────────────────────────────────

    def _path_for(self, customer_id: str) -> Path:
        return self._dir / f"{customer_id}.json"

    def _create_backup(self, customer_id: str, source: Path) -> None:
        """Copy source to backup dir and rotate old backups."""
        try:
            bak = self._backup_dir / f"{customer_id}.bak"
            shutil.copy2(source, bak)
            self._rotate_backups(customer_id)
        except OSError as exc:
            logger.warning(
                "Failed to backup customer %s: %s", customer_id, exc
            )

    def _rotate_backups(self, customer_id: str) -> None:
        """Keep at most backup_max_keep backups per customer."""
        if self._backup_max_keep <= 0:
            return
        pattern = f"{customer_id}.bak*"
        backups = sorted(
            self._backup_dir.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[self._backup_max_keep:]:
            try:
                old.unlink()
                logger.debug("Rotated old backup: %s", old.name)
            except OSError:
                pass

    # ── Aliases ────────────────────────────────────────────────────────────

    def _load_aliases(self) -> dict[str, str]:
        if not self._aliases_path.exists():
            return {}
        try:
            return json.loads(self._aliases_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to load aliases, returning empty")
            return {}

    def _save_aliases(self, mapping: dict[str, str]) -> None:
        try:
            json_text = json.dumps(mapping, ensure_ascii=False, indent=2)
            tmp = self._aliases_path.with_suffix(".tmp")
            tmp.write_text(json_text, encoding="utf-8")
            os.replace(tmp, self._aliases_path)
        except OSError as exc:
            logger.error("Failed to save aliases: %s", exc)

    def _sync_alias(
        self, customer_id: str, name: str, aliases: list[str]
    ) -> None:
        mapping = self._load_aliases()
        mapping[name] = customer_id
        for alias in aliases:
            mapping[alias] = customer_id
        self._save_aliases(mapping)

    def _remove_alias(self, customer_id: str) -> None:
        mapping = self._load_aliases()
        mapping = {k: v for k, v in mapping.items() if v != customer_id}
        self._save_aliases(mapping)
