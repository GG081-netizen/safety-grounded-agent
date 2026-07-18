"""Interaction record persistence — one directory per customer.

Phase 3 upgrades:
  - Atomic writes (write .tmp → rename)
  - Delete method
  - Proper I/O error handling
  - _index.json kept lightweight (moved to V1.5 in design, but functional)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from conversation_agent.config import get_config
from conversation_agent.sales.models import InteractionRecord

logger = logging.getLogger(__name__)


class InteractionStore:
    """Persist InteractionRecord as individual JSON files under
    ``data/interactions/<customer_id>/``.

    Each interaction is stored as ``<interaction_id>.json``.  The per-customer
    ``_index.json`` provides a lightweight summary for fast listing without
    parsing every interaction file.
    """

    def __init__(self, interactions_dir: Path | None = None) -> None:
        storage_cfg = get_config().storage
        self._dir = Path(interactions_dir or storage_cfg.interactions_dir)
        self._backup_dir = storage_cfg.backups_dir / "interactions"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._backup_dir.mkdir(parents=True, exist_ok=True)

    # ── CRUD ───────────────────────────────────────────────────────────────

    def save(self, record: InteractionRecord) -> Path:
        """Persist an interaction record with atomic write.

        Returns the Path to the written file. Raises OSError on I/O failure.
        """
        customer_dir = self._dir / record.customer_id
        customer_dir.mkdir(parents=True, exist_ok=True)

        path = customer_dir / f"{record.interaction_id}.json"
        data = record.model_dump(mode="json")
        json_text = json.dumps(data, ensure_ascii=False, indent=2)

        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json_text, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise

        self._refresh_index(record.customer_id)
        logger.info(
            "Saved interaction %s for customer %s",
            record.interaction_id,
            record.customer_id,
        )
        return path

    def load(
        self, customer_id: str, interaction_id: str
    ) -> InteractionRecord | None:
        """Load a single interaction record.

        Returns None if the file doesn't exist or is unreadable.
        """
        path = self._dir / customer_id / f"{interaction_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return InteractionRecord(**data)
        except (json.JSONDecodeError, OSError, Exception) as exc:
            logger.error(
                "Failed to load interaction %s/%s: %s",
                customer_id, interaction_id, exc,
            )
            return None

    def exists(self, customer_id: str, interaction_id: str) -> bool:
        """Check whether an interaction record exists on disk."""
        path = self._dir / customer_id / f"{interaction_id}.json"
        return path.exists()

    def delete(self, customer_id: str, interaction_id: str) -> bool:
        """Delete an interaction record.

        Returns True if deleted, False if the file didn't exist or failed.
        """
        path = self._dir / customer_id / f"{interaction_id}.json"
        if not path.exists():
            return False
        try:
            path.unlink()
            self._refresh_index(customer_id)
            logger.info(
                "Deleted interaction %s for customer %s",
                interaction_id, customer_id,
            )
            return True
        except OSError as exc:
            logger.error(
                "Failed to delete interaction %s/%s: %s",
                customer_id, interaction_id, exc,
            )
            return False

    def list_for_customer(
        self, customer_id: str, limit: int = 20
    ) -> list[InteractionRecord]:
        """Return recent interactions for a customer, newest first."""
        customer_dir = self._dir / customer_id
        if not customer_dir.exists():
            return []

        records: list[InteractionRecord] = []
        for path in sorted(customer_dir.glob("*.json"), reverse=True):
            if path.name == "_index.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                records.append(InteractionRecord(**data))
            except (json.JSONDecodeError, OSError, Exception) as exc:
                logger.warning(
                    "Skipping unreadable interaction %s: %s",
                    path.name, exc,
                )
            if len(records) >= limit:
                break

        records.sort(key=lambda r: r.date, reverse=True)
        return records[:limit]

    def count_for_customer(self, customer_id: str) -> int:
        """Number of interaction files for a customer (fast, doesn't parse)."""
        customer_dir = self._dir / customer_id
        if not customer_dir.exists():
            return 0
        return len([p for p in customer_dir.glob("*.json") if p.name != "_index.json"])

    def get_recent_summaries(
        self, customer_id: str, n: int = 5
    ) -> list[dict]:
        """Return lightweight summaries of the most recent interactions."""
        records = self.list_for_customer(customer_id, limit=n)
        return [
            {
                "interaction_id": r.interaction_id,
                "date": r.date.isoformat(),
                "type": r.type.value,
                "summary": r.summary,
            }
            for r in records
        ]

    def last_interaction_date(self, customer_id: str) -> datetime | None:
        """Return the date of the most recent interaction, or None."""
        records = self.list_for_customer(customer_id, limit=1)
        if not records:
            return None
        return records[0].date

    # ── Index ──────────────────────────────────────────────────────────────

    def _refresh_index(self, customer_id: str) -> None:
        """Rebuild the lightweight index file for a customer.

        The index is an optimization — it avoids parsing every interaction
        JSON for listing.  It is regenerated on every write/delete.
        """
        customer_dir = self._dir / customer_id
        if not customer_dir.exists():
            return

        records = self.list_for_customer(customer_id, limit=200)
        index = [
            {
                "interaction_id": r.interaction_id,
                "date": r.date.isoformat(),
                "type": r.type.value,
                "summary": r.summary,
            }
            for r in records
        ]
        index_path = customer_dir / "_index.json"
        json_text = json.dumps(index, ensure_ascii=False, indent=2)
        try:
            tmp = index_path.with_suffix(".tmp")
            tmp.write_text(json_text, encoding="utf-8")
            os.replace(tmp, index_path)
        except OSError as exc:
            logger.warning(
                "Failed to refresh index for customer %s: %s",
                customer_id, exc,
            )
