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
class MoneyAmount:
    """An exact, currency-bound monetary amount in native minor units."""

    currency: str
    minor_units: int

    def __post_init__(self) -> None:
        if not isinstance(self.currency, str) or len(self.currency.strip()) != 3 or not self.currency.strip().isalpha():
            raise ValueError("currency must be a three-letter code")
        if isinstance(self.minor_units, bool) or not isinstance(self.minor_units, int) or self.minor_units < 0:
            raise ValueError("minor_units must be a non-negative integer")
        object.__setattr__(self, "currency", self.currency.upper())


@dataclass(frozen=True)
class BudgetPeriod:
    """An explicit accounting period; reservations never cross its boundary."""

    period_id: str
    starts_at: str
    ends_at: str

    def __post_init__(self) -> None:
        if not self.period_id.strip() or self.starts_at >= self.ends_at:
            raise ValueError("budget period requires an id and increasing boundaries")


@dataclass(frozen=True)
class ResourcePrice:
    currency: str
    worst_case: MoneyAmount | None

    def __post_init__(self) -> None:
        normalized = self.currency.upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("currency must be a three-letter code")
        if self.worst_case is not None and self.worst_case.currency != normalized:
            raise ValueError("resource price currency must match worst_case currency")
        object.__setattr__(self, "currency", normalized)


@dataclass(frozen=True)
class ResourceSpec:
    resource_id: str
    provider_id: str
    native_unit: str
    capacity: int | float
    capabilities: tuple[str, ...]
    sensitivity: str
    cost_minor: int | None
    price_currency: str | None = None


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
        , period_id TEXT NOT NULL DEFAULT 'legacy'
        , period_starts_at TEXT NOT NULL DEFAULT ''
        , period_ends_at TEXT NOT NULL DEFAULT ''
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
        , period_id TEXT NOT NULL DEFAULT 'legacy'
        , currency TEXT NOT NULL DEFAULT 'JPY'
    );
    CREATE TABLE IF NOT EXISTS resource_reservations (
        reservation_id TEXT PRIMARY KEY,
        resource_id TEXT NOT NULL,
        native_units REAL NOT NULL,
        status TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS resource_control (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        maintenance INTEGER NOT NULL DEFAULT 0
    );
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self.connection.executescript(self._SCHEMA)
        self._ensure_column("resources", "price_currency", "TEXT")
        self._ensure_column("budget_config", "period_id", "TEXT NOT NULL DEFAULT 'legacy'")
        self._ensure_column("budget_config", "period_starts_at", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("budget_config", "period_ends_at", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("budget_reservations", "period_id", "TEXT NOT NULL DEFAULT 'legacy'")
        self._ensure_column("budget_reservations", "currency", "TEXT NOT NULL DEFAULT 'JPY'")
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        existing = {row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ResourceLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def set_maintenance(self, enabled: bool) -> None:
        """Persist the runtime dispatch maintenance fence for all connections."""
        with self._lock:
            self.connection.execute(
                "INSERT INTO resource_control(id, maintenance) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET maintenance=excluded.maintenance",
                (int(bool(enabled)),),
            )
            self.connection.commit()

    def maintenance_enabled(self) -> bool:
        row = self.connection.execute("SELECT maintenance FROM resource_control WHERE id=1").fetchone()
        return bool(row and row[0])

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
        price_currency: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ResourceSpec:
        if not resource_id.strip() or not provider_id.strip() or not native_unit.strip():
            raise ValueError("resource_id, provider_id, and native_unit are required")
        self._number(capacity, "capacity")
        if cost_minor is not None:
            if isinstance(cost_minor, bool) or not isinstance(cost_minor, int) or cost_minor < 0:
                raise ValueError("cost_minor must be a non-negative integer or None")
            price_currency = price_currency or "JPY"
        if price_currency is not None and (len(price_currency.strip()) != 3 or not price_currency.isalpha()):
            raise ValueError("price_currency must be a three-letter code")
        capability_list = tuple(sorted({str(item) for item in capabilities if str(item).strip()}))
        if not capability_list:
            raise ValueError("at least one capability is required")
        now = _now()
        with self._lock:
            self.connection.execute(
                """INSERT INTO resources(resource_id, provider_id, native_unit, capacity, capabilities_json,
                   sensitivity, cost_minor, price_currency, available, health, confidence, observed_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', 0, ?, ?)
                   ON CONFLICT(resource_id) DO UPDATE SET provider_id=excluded.provider_id,
                   native_unit=excluded.native_unit, capacity=excluded.capacity,
                   capabilities_json=excluded.capabilities_json, sensitivity=excluded.sensitivity,
                   cost_minor=excluded.cost_minor, price_currency=excluded.price_currency, metadata_json=excluded.metadata_json""",
                (resource_id, provider_id, native_unit, capacity, json.dumps(capability_list), sensitivity, cost_minor, price_currency.upper() if price_currency else None, capacity, now, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            self.connection.commit()
        return ResourceSpec(resource_id, provider_id, native_unit, capacity, capability_list, sensitivity, cost_minor, price_currency.upper() if price_currency else None)

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

    def configure_budget(self, *, hard_cap_minor: int, recovery_reserve_minor: int, currency: str, period: BudgetPeriod | None = None) -> None:
        if not isinstance(hard_cap_minor, int) or hard_cap_minor < 0 or not isinstance(recovery_reserve_minor, int) or recovery_reserve_minor < 0 or recovery_reserve_minor > hard_cap_minor:
            raise ValueError("invalid budget cap or recovery reserve")
        if period is None:
            now = datetime.now(timezone.utc)
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
            period = BudgetPeriod(start.strftime("%Y-%m"), start.isoformat(), end.isoformat())
        with self._lock:
            self.connection.execute("INSERT INTO budget_config(id, hard_cap_minor, recovery_reserve_minor, currency, period_id, period_starts_at, period_ends_at) VALUES (1, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET hard_cap_minor=excluded.hard_cap_minor, recovery_reserve_minor=excluded.recovery_reserve_minor, currency=excluded.currency, period_id=excluded.period_id, period_starts_at=excluded.period_starts_at, period_ends_at=excluded.period_ends_at", (hard_cap_minor, recovery_reserve_minor, currency.upper(), period.period_id, period.starts_at, period.ends_at))
            self.connection.commit()

    def budget_config(self) -> dict[str, Any]:
        row = self.connection.execute("SELECT hard_cap_minor, recovery_reserve_minor, currency, period_id, period_starts_at, period_ends_at FROM budget_config WHERE id=1").fetchone()
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

    def reservation_totals(self, *, period_id: str | None = None) -> dict[str, int]:
        query = "SELECT recovery, status, COALESCE(actual_minor, estimated_minor) AS amount FROM budget_reservations WHERE status IN ('reserved', 'unknown', 'reconciled')"
        params: tuple[str, ...] = ()
        if period_id is not None:
            query += " AND period_id=?"
            params = (period_id,)
        rows = self.connection.execute(query, params).fetchall()
        normal_committed = sum(int(row["amount"]) for row in rows if not row["recovery"])
        recovery_committed = sum(int(row["amount"]) for row in rows if row["recovery"])
        active = sum(1 for row in rows if row["status"] in {"reserved", "unknown"})
        return {"normal_committed_minor": normal_committed, "recovery_committed_minor": recovery_committed, "active_reservations": active}

    def reserve_budget(self, *, task_id: str, resource_id: str, amount: MoneyAmount, recovery: bool, period: BudgetPeriod, normal_limit_minor: int, recovery_limit_minor: int, native_units: int | float = 1) -> str:
        """Atomically check and create a reservation behind the ledger boundary."""
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                if self.maintenance_enabled():
                    raise ValueError("maintenance mode")
                totals = self.reservation_totals(period_id=period.period_id)
                resource = self.connection.execute("SELECT available FROM resources WHERE resource_id=?", (resource_id,)).fetchone()
                if resource is None:
                    raise KeyError(resource_id)
                used = self.connection.execute("SELECT COALESCE(SUM(native_units), 0) FROM resource_reservations WHERE resource_id=? AND status='reserved'", (resource_id,)).fetchone()[0]
                if float(used) + float(native_units) > float(resource["available"]):
                    raise ValueError(f"resource capacity exceeded: {resource_id}")
                remaining = (recovery_limit_minor if recovery else normal_limit_minor) - (totals["recovery_committed_minor"] if recovery else totals["normal_committed_minor"])
                if amount.minor_units > remaining:
                    raise ValueError(f"budget exceeded: requested {amount.minor_units}, remaining {remaining}")
                reservation_id = str(uuid4())
                self.connection.execute("INSERT INTO budget_reservations(reservation_id, task_id, resource_id, estimated_minor, actual_minor, recovery, status, created_at, reconciled_at, period_id, currency) VALUES (?, ?, ?, ?, NULL, ?, 'reserved', ?, NULL, ?, ?)", (reservation_id, task_id, resource_id, amount.minor_units, int(recovery), datetime.now(timezone.utc).isoformat(), period.period_id, amount.currency))
                self.connection.execute("INSERT INTO resource_reservations(reservation_id, resource_id, native_units, status) VALUES (?, ?, ?, 'reserved')", (reservation_id, resource_id, native_units))
                self.connection.commit()
                return reservation_id
            except Exception:
                if self.connection.in_transaction:
                    self.connection.rollback()
                raise

    def reconcile_budget(self, reservation_id: str, *, actual: MoneyAmount, period: BudgetPeriod, normal_limit_minor: int, recovery_limit_minor: int) -> None:
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.connection.execute("SELECT * FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
                if row is None:
                    raise KeyError(reservation_id)
                if row["status"] not in {"reserved", "unknown"}:
                    raise ValueError(f"reservation is not active: {reservation_id}")
                if row["period_id"] != period.period_id or row["currency"] != actual.currency:
                    raise ValueError("reservation does not belong to current budget period or currency")
                totals = self.reservation_totals(period_id=period.period_id)
                current_total = totals["recovery_committed_minor"] if row["recovery"] else totals["normal_committed_minor"]
                current_total -= int(row["estimated_minor"])
                limit = recovery_limit_minor if row["recovery"] else normal_limit_minor
                if current_total + actual.minor_units > limit:
                    raise ValueError("actual usage exceeds the protected budget")
                self.connection.execute("UPDATE budget_reservations SET actual_minor=?, status='reconciled', reconciled_at=? WHERE reservation_id=?", (actual.minor_units, datetime.now(timezone.utc).isoformat(), reservation_id))
                self.connection.execute("UPDATE resource_reservations SET status='released' WHERE reservation_id=?", (reservation_id,))
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise

    def release_budget(self, reservation_id: str) -> None:
        with self._lock:
            cursor = self.connection.execute("UPDATE budget_reservations SET status='released', reconciled_at=? WHERE reservation_id=? AND status='reserved'", (datetime.now(timezone.utc).isoformat(), reservation_id))
            self.connection.execute("UPDATE resource_reservations SET status='released' WHERE reservation_id=?", (reservation_id,))
            self.connection.commit()
            if cursor.rowcount != 1:
                raise ValueError(f"reservation is not active: {reservation_id}")

    def mark_budget_unknown(self, reservation_id: str) -> None:
        with self._lock:
            cursor = self.connection.execute("UPDATE budget_reservations SET status='unknown' WHERE reservation_id=? AND status='reserved'", (reservation_id,))
            self.connection.commit()
            if cursor.rowcount != 1:
                raise ValueError(f"reservation is not active: {reservation_id}")
