"""Durable native-unit resource observations for Phase 6."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from threading import RLock
import time
from typing import Any, Iterable
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ResourceSpec:
    resource_id: str
    provider_id: str
    native_unit: str
    capacity: int | float
    capabilities: tuple[str, ...]
    sensitivity: str
    cost_minor: int | None


class ResourceLedger:
    """SQLite-backed resource observations and budget reservation records."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS resources (
        resource_id TEXT PRIMARY KEY,
        provider_id TEXT NOT NULL,
        native_unit TEXT NOT NULL,
        capacity REAL NOT NULL,
        capabilities_json TEXT NOT NULL,
        sensitivity TEXT NOT NULL,
        cost_minor INTEGER,
        available REAL NOT NULL,
        health TEXT NOT NULL,
        confidence REAL NOT NULL,
        observed_at TEXT NOT NULL,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        circuit_open_until REAL NOT NULL DEFAULT 0,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS resource_observations (
        observation_id TEXT PRIMARY KEY,
        resource_id TEXT NOT NULL,
        available REAL NOT NULL,
        health TEXT NOT NULL,
        confidence REAL NOT NULL,
        observed_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS budget_config (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        hard_cap_minor INTEGER NOT NULL,
        recovery_reserve_minor INTEGER NOT NULL,
        currency TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS budget_reservations (
        reservation_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        resource_id TEXT NOT NULL,
        estimated_minor INTEGER NOT NULL,
        actual_minor INTEGER,
        recovery INTEGER NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        reconciled_at TEXT
    );
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self.connection.executescript(self._SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ResourceLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _number(value: int | float, name: str, *, nonnegative: bool = True) -> int | float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be numeric")
        if not math.isfinite(value) or (nonnegative and value < 0):
            raise ValueError(f"{name} must be non-negative")
        return value

    def register_resource(
        self,
        resource_id: str,
        *,
        provider_id: str,
        native_unit: str,
        capacity: int | float,
        capabilities: Iterable[str],
        sensitivity: str = "normal",
        cost_minor: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ResourceSpec:
        if not resource_id.strip() or not provider_id.strip() or not native_unit.strip():
            raise ValueError("resource_id, provider_id, and native_unit are required")
        self._number(capacity, "capacity")
        if cost_minor is not None:
            if isinstance(cost_minor, bool) or not isinstance(cost_minor, int) or cost_minor < 0:
                raise ValueError("cost_minor must be a non-negative integer or None")
        capability_list = tuple(sorted({str(item) for item in capabilities if str(item).strip()}))
        if not capability_list:
            raise ValueError("at least one capability is required")
        now = _now()
        with self._lock:
            self.connection.execute(
                """INSERT INTO resources(resource_id, provider_id, native_unit, capacity, capabilities_json,
                   sensitivity, cost_minor, available, health, confidence, observed_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unknown', 0, ?, ?)
                   ON CONFLICT(resource_id) DO UPDATE SET provider_id=excluded.provider_id,
                   native_unit=excluded.native_unit, capacity=excluded.capacity,
                   capabilities_json=excluded.capabilities_json, sensitivity=excluded.sensitivity,
                   cost_minor=excluded.cost_minor, metadata_json=excluded.metadata_json""",
                (resource_id, provider_id, native_unit, capacity, json.dumps(capability_list), sensitivity, cost_minor, capacity, now, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            self.connection.commit()
        return ResourceSpec(resource_id, provider_id, native_unit, capacity, capability_list, sensitivity, cost_minor)

    def observe(self, resource_id: str, *, available: int | float, health: str, confidence: float = 1.0, observed_at: str | None = None) -> None:
        self._number(available, "available")
        self._number(confidence, "confidence")
        if confidence > 1:
            raise ValueError("confidence must be at most 1")
        if health not in {"healthy", "degraded", "unhealthy", "unknown"}:
            raise ValueError("invalid resource health")
        timestamp = observed_at or _now()
        with self._lock:
            if self.connection.execute("SELECT 1 FROM resources WHERE resource_id = ?", (resource_id,)).fetchone() is None:
                raise KeyError(resource_id)
            self.connection.execute("UPDATE resources SET available=?, health=?, confidence=?, observed_at=? WHERE resource_id=?", (available, health, confidence, timestamp, resource_id))
            self.connection.execute("INSERT INTO resource_observations VALUES (?, ?, ?, ?, ?, ?)", (str(uuid4()), resource_id, available, health, confidence, timestamp))
            self.connection.commit()

    def get_resource(self, resource_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM resources WHERE resource_id = ?", (resource_id,)).fetchone()
        if row is None:
            raise KeyError(resource_id)
        result = dict(row)
        result["capabilities"] = tuple(json.loads(result.pop("capabilities_json")))
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def list_resources(self) -> list[dict[str, Any]]:
        return [self.get_resource(row[0]) for row in self.connection.execute("SELECT resource_id FROM resources ORDER BY resource_id")]

    def configure_budget(self, *, hard_cap_minor: int, recovery_reserve_minor: int, currency: str) -> None:
        if not isinstance(hard_cap_minor, int) or hard_cap_minor < 0 or not isinstance(recovery_reserve_minor, int) or recovery_reserve_minor < 0 or recovery_reserve_minor > hard_cap_minor:
            raise ValueError("invalid budget cap or recovery reserve")
        with self._lock:
            self.connection.execute("INSERT INTO budget_config(id, hard_cap_minor, recovery_reserve_minor, currency) VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET hard_cap_minor=excluded.hard_cap_minor, recovery_reserve_minor=excluded.recovery_reserve_minor, currency=excluded.currency", (hard_cap_minor, recovery_reserve_minor, currency))
            self.connection.commit()

    def budget_config(self) -> dict[str, Any]:
        row = self.connection.execute("SELECT hard_cap_minor, recovery_reserve_minor, currency FROM budget_config WHERE id=1").fetchone()
        if row is None:
            raise ValueError("budget is not configured")
        return dict(row)

    def record_provider_failure(self, provider_id: str, *, threshold: int = 3, cooldown_seconds: float = 60.0) -> None:
        if threshold <= 0 or cooldown_seconds < 0:
            raise ValueError("threshold and cooldown must be positive")
        with self._lock:
            rows = self.connection.execute("SELECT resource_id, consecutive_failures FROM resources WHERE provider_id=?", (provider_id,)).fetchall()
            for row in rows:
                failures = int(row["consecutive_failures"]) + 1
                opened = time.time() + cooldown_seconds if failures >= threshold else 0
                self.connection.execute("UPDATE resources SET consecutive_failures=?, circuit_open_until=? WHERE resource_id=?", (failures, opened, row["resource_id"]))
            self.connection.commit()

    def record_provider_success(self, provider_id: str) -> None:
        with self._lock:
            self.connection.execute("UPDATE resources SET consecutive_failures=0, circuit_open_until=0 WHERE provider_id=?", (provider_id,))
            self.connection.commit()

    def reservation_row(self, reservation_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if row is None:
            raise KeyError(reservation_id)
        return dict(row)

    def reservation_totals(self) -> dict[str, int]:
        rows = self.connection.execute("SELECT recovery, status, COALESCE(actual_minor, estimated_minor) AS amount FROM budget_reservations WHERE status IN ('reserved', 'unknown', 'reconciled')").fetchall()
        normal_committed = sum(int(row["amount"]) for row in rows if not row["recovery"])
        recovery_committed = sum(int(row["amount"]) for row in rows if row["recovery"])
        active = sum(1 for row in rows if row["status"] in {"reserved", "unknown"})
        return {"normal_committed_minor": normal_committed, "recovery_committed_minor": recovery_committed, "active_reservations": active}
