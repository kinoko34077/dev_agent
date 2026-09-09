"""SQLite-backed resource catalog storage used by :mod:`ledger`."""

from __future__ import annotations

from collections.abc import Mapping
import json
import sqlite3
from threading import RLock
from typing import Any


class ResourceCatalogStore:
    """Own resource catalog SQL while sharing the ledger connection/lock.

    This is an internal repository.  ``ResourceLedger`` remains the public
    facade and continues to own schema creation, migrations, and transaction
    boundaries between resource and budget records.
    """

    def __init__(self, connection: sqlite3.Connection, lock: RLock) -> None:
        self.connection = connection
        self._lock = lock

    def upsert(
        self,
        *,
        resource_id: str,
        provider_id: str,
        native_unit: str,
        capacity: int | float,
        capabilities: tuple[str, ...],
        sensitivity: str,
        cost_minor: int | None,
        price_currency: str | None,
        quota_domain: str | None,
        metadata: Mapping[str, Any],
        observed_at: str,
    ) -> None:
        with self._lock:
            self.connection.execute(
                """INSERT INTO resources(resource_id, provider_id, native_unit, capacity, capabilities_json,
                   sensitivity, cost_minor, price_currency, quota_domain, available, health, confidence, observed_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', 0, ?, ?)
                   ON CONFLICT(resource_id) DO UPDATE SET provider_id=excluded.provider_id,
                   native_unit=excluded.native_unit, capacity=excluded.capacity,
                   capabilities_json=excluded.capabilities_json, sensitivity=excluded.sensitivity,
                   cost_minor=excluded.cost_minor, price_currency=excluded.price_currency,
                   quota_domain=excluded.quota_domain, metadata_json=excluded.metadata_json""",
                (
                    resource_id,
                    provider_id,
                    native_unit,
                    capacity,
                    json.dumps(capabilities),
                    sensitivity,
                    cost_minor,
                    price_currency,
                    quota_domain,
                    capacity,
                    observed_at,
                    json.dumps(dict(metadata), ensure_ascii=False),
                ),
            )
            self.connection.commit()

    @staticmethod
    def from_row(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["capabilities"] = tuple(json.loads(result.pop("capabilities_json")))
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def get(self, resource_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.connection.execute("SELECT * FROM resources WHERE resource_id = ?", (resource_id,)).fetchone()
            if row is None:
                raise KeyError(resource_id)
            return self.from_row(row)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute("SELECT * FROM resources ORDER BY resource_id").fetchall()
            return [self.from_row(row) for row in rows]

    def rows(self) -> list[sqlite3.Row]:
        """Return raw rows for a caller building a larger read snapshot."""
        with self._lock:
            return self.connection.execute("SELECT * FROM resources ORDER BY resource_id").fetchall()


__all__ = ["ResourceCatalogStore"]
