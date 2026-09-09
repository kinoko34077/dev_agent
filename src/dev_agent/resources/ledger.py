"""Durable native-unit resource observations for Phase 6."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from collections.abc import Mapping
import json
import math
from pathlib import Path
import sqlite3
from threading import RLock
import time
from typing import Any, Iterable
from uuid import uuid4


# Capability held only by the explicit budget-administration facade. Runtime
# reservation code may read the persisted policy but must not rewrite it.
_BUDGET_ADMIN_TOKEN = object()

_BUDGET_TRANSITIONS = {
    "prepared": {"dispatching", "unknown", "confirmed_no_charge"},
    "dispatching": {"unknown", "reconciled", "confirmed_no_charge"},
    "unknown": {"reconciled", "confirmed_no_charge"},
    "reconciled": set(),
    "confirmed_no_charge": set(),
    "released": set(),
}


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
    quota_domain: str | None = None


@dataclass(frozen=True)
class QuotaObservation:
    resource_id: str
    quota_domain: str
    request_limit: int | None
    request_remaining: int | None
    token_limit: int | None
    token_remaining: int | None
    reset_at: str | None
    daily_remaining: int | None
    concurrency_limit: int | float | None
    confidence: float
    observed_at: str
    source: str


class ResourceLedger:
    """SQLite-backed resource observations and budget reservation records."""

    SCHEMA_VERSION = 6
    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS resources (
        resource_id TEXT PRIMARY KEY,
        provider_id TEXT NOT NULL,
        native_unit TEXT NOT NULL,
        capacity REAL NOT NULL,
        capabilities_json TEXT NOT NULL,
        sensitivity TEXT NOT NULL,
        cost_minor INTEGER,
        price_currency TEXT,
        quota_domain TEXT,
        quota_remaining_ratio REAL,
        quota_reset_at TEXT,
        latency_ewma_ms REAL,
        failure_ewma REAL,
        inflight REAL NOT NULL DEFAULT 0,
        concurrency_limit REAL,
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
        observed_at TEXT NOT NULL,
        quota_remaining_ratio REAL,
        quota_reset_at TEXT,
        latency_ewma_ms REAL,
        failure_ewma REAL,
        inflight REAL NOT NULL DEFAULT 0,
        concurrency_limit REAL
    );
    CREATE TABLE IF NOT EXISTS quota_observations (
        observation_id TEXT PRIMARY KEY,
        resource_id TEXT NOT NULL,
        quota_domain TEXT NOT NULL,
        request_limit INTEGER,
        request_remaining INTEGER,
        token_limit INTEGER,
        token_remaining INTEGER,
        reset_at TEXT,
        daily_remaining INTEGER,
        concurrency_limit REAL,
        confidence REAL NOT NULL,
        observed_at TEXT NOT NULL,
        source TEXT NOT NULL
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
        intent_key TEXT UNIQUE,
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
    CREATE TABLE IF NOT EXISTS resource_schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        existing_tables = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        try:
            self.connection.executescript(self._SCHEMA)
            if not existing_tables:
                self.connection.execute("INSERT OR REPLACE INTO resource_schema_meta(key, value) VALUES ('schema_version', ?)", (str(self.SCHEMA_VERSION),))
                self.connection.commit()
                return
            self.connection.execute("INSERT OR IGNORE INTO resource_schema_meta(key, value) VALUES ('schema_version', '1')")
            current = int(self.connection.execute("SELECT value FROM resource_schema_meta WHERE key='schema_version'").fetchone()[0])
            if current > self.SCHEMA_VERSION:
                raise ValueError(f"unsupported resource schema version: {current}")
            self.connection.commit()
            self.connection.execute("BEGIN")
            if current < 2:
                self._ensure_column("resources", "price_currency", "TEXT")
                self._ensure_column("budget_config", "period_id", "TEXT NOT NULL DEFAULT 'legacy'")
                self._ensure_column("budget_config", "period_starts_at", "TEXT NOT NULL DEFAULT ''")
                self._ensure_column("budget_config", "period_ends_at", "TEXT NOT NULL DEFAULT ''")
                self._ensure_column("budget_reservations", "period_id", "TEXT NOT NULL DEFAULT 'legacy'")
                self._ensure_column("budget_reservations", "currency", "TEXT NOT NULL DEFAULT 'JPY'")
                self.connection.execute("UPDATE resource_schema_meta SET value='2' WHERE key='schema_version'")
                current = 2
            if current < 3:
                config = self.connection.execute("SELECT currency, period_id, period_starts_at, period_ends_at FROM budget_config WHERE id=1").fetchone()
                if config is not None and (config["period_id"] == "legacy" or not config["period_starts_at"] or not config["period_ends_at"]):
                    now = datetime.now(timezone.utc)
                    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
                    period_id = start.strftime("%Y-%m")
                    self.connection.execute("UPDATE budget_config SET period_id=?, period_starts_at=?, period_ends_at=? WHERE id=1", (period_id, start.isoformat(), end.isoformat()))
                    self.connection.execute("UPDATE budget_reservations SET period_id=?, currency=? WHERE period_id='legacy'", (period_id, config["currency"]))
                self.connection.execute("UPDATE budget_reservations SET status='prepared' WHERE status='reserved'")
                self.connection.execute("UPDATE resource_schema_meta SET value='3' WHERE key='schema_version'")
            if current < 4:
                self._ensure_column("budget_reservations", "intent_key", "TEXT")
                self.connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_reservations_intent_key ON budget_reservations(intent_key) WHERE intent_key IS NOT NULL")
                self.connection.execute("UPDATE resource_schema_meta SET value='4' WHERE key='schema_version'")
                current = 4
            if current < 5:
                self._ensure_column("resources", "quota_domain", "TEXT")
                self.connection.execute(
                    """CREATE TABLE IF NOT EXISTS quota_observations (
                        observation_id TEXT PRIMARY KEY,
                        resource_id TEXT NOT NULL,
                        quota_domain TEXT NOT NULL,
                        request_limit INTEGER,
                        request_remaining INTEGER,
                        token_limit INTEGER,
                        token_remaining INTEGER,
                        reset_at TEXT,
                        daily_remaining INTEGER,
                        concurrency_limit REAL,
                        confidence REAL NOT NULL,
                        observed_at TEXT NOT NULL,
                        source TEXT NOT NULL
                    )"""
                )
                self.connection.execute("CREATE INDEX IF NOT EXISTS idx_quota_observations_resource_observed_at ON quota_observations(resource_id, observed_at)")
                self.connection.execute("UPDATE resource_schema_meta SET value='5' WHERE key='schema_version'")
                current = 5
            if current < 6:
                for column, definition in (
                    ("quota_remaining_ratio", "REAL"),
                    ("quota_reset_at", "TEXT"),
                    ("latency_ewma_ms", "REAL"),
                    ("failure_ewma", "REAL"),
                    ("inflight", "REAL NOT NULL DEFAULT 0"),
                    ("concurrency_limit", "REAL"),
                ):
                    self._ensure_column("resources", column, definition)
                    self._ensure_column("resource_observations", column, definition)
                self.connection.execute("UPDATE resource_schema_meta SET value='6' WHERE key='schema_version'")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

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
        with self._lock:
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
        quota_domain: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ResourceSpec:
        if not resource_id.strip() or not provider_id.strip() or not native_unit.strip():
            raise ValueError("resource_id, provider_id, and native_unit are required")
        if quota_domain is not None:
            if not isinstance(quota_domain, str) or not quota_domain.strip():
                raise ValueError("quota_domain must be a non-empty string or None")
            quota_domain = quota_domain.strip()
        self._number(capacity, "capacity")
        if cost_minor is not None:
            if isinstance(cost_minor, bool) or not isinstance(cost_minor, int) or cost_minor < 0:
                raise ValueError("cost_minor must be a non-negative integer or None")
            price_currency = price_currency or "JPY"
        if price_currency is not None:
            if not isinstance(price_currency, str) or len(price_currency.strip()) != 3 or not price_currency.strip().isalpha():
                raise ValueError("price_currency must be a three-letter code")
            price_currency = price_currency.strip().upper()
        capability_list = tuple(sorted({str(item) for item in capabilities if str(item).strip()}))
        if not capability_list:
            raise ValueError("at least one capability is required")
        now = _now()
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
                (resource_id, provider_id, native_unit, capacity, json.dumps(capability_list), sensitivity, cost_minor, price_currency.upper() if price_currency else None, quota_domain, capacity, now, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            self.connection.commit()
        return ResourceSpec(resource_id, provider_id, native_unit, capacity, capability_list, sensitivity, cost_minor, price_currency.upper() if price_currency else None, quota_domain)

    def observe(
        self,
        resource_id: str,
        *,
        available: int | float,
        health: str,
        confidence: float = 1.0,
        observed_at: str | None = None,
        quota_remaining_ratio: float | None = None,
        quota_reset_at: str | None = None,
        latency_ewma_ms: int | float | None = None,
        failure_ewma: float | None = None,
        inflight: int | float = 0,
        concurrency_limit: int | float | None = None,
    ) -> None:
        self._number(available, "available")
        self._number(confidence, "confidence")
        if confidence > 1:
            raise ValueError("confidence must be at most 1")
        if health not in {"healthy", "degraded", "unhealthy", "unknown"}:
            raise ValueError("invalid resource health")
        if quota_remaining_ratio is not None:
            self._number(quota_remaining_ratio, "quota_remaining_ratio")
            if quota_remaining_ratio > 1:
                raise ValueError("quota_remaining_ratio must be at most 1")
        if quota_reset_at is not None and (not isinstance(quota_reset_at, str) or not quota_reset_at.strip()):
            raise ValueError("quota_reset_at must be a non-empty string or None")
        if latency_ewma_ms is not None:
            self._number(latency_ewma_ms, "latency_ewma_ms")
        if failure_ewma is not None:
            self._number(failure_ewma, "failure_ewma")
            if failure_ewma > 1:
                raise ValueError("failure_ewma must be at most 1")
        self._number(inflight, "inflight")
        if concurrency_limit is not None:
            self._number(concurrency_limit, "concurrency_limit")
            if inflight > concurrency_limit:
                raise ValueError("inflight cannot exceed concurrency_limit")
        timestamp = observed_at or _now()
        with self._lock:
            resource = self.connection.execute("SELECT capacity FROM resources WHERE resource_id = ?", (resource_id,)).fetchone()
            if resource is None:
                raise KeyError(resource_id)
            if available > resource[0]:
                raise ValueError(f"available capacity exceeds resource capacity: {resource_id}")
            self.connection.execute(
                """UPDATE resources
                   SET available=?, health=?, confidence=?, observed_at=?,
                       quota_remaining_ratio=?, quota_reset_at=?,
                       latency_ewma_ms=?, failure_ewma=?, inflight=?,
                       concurrency_limit=?
                   WHERE resource_id=?""",
                (
                    available,
                    health,
                    confidence,
                    timestamp,
                    quota_remaining_ratio,
                    quota_reset_at.strip() if isinstance(quota_reset_at, str) else None,
                    latency_ewma_ms,
                    failure_ewma,
                    inflight,
                    concurrency_limit,
                    resource_id,
                ),
            )
            self.connection.execute(
                """INSERT INTO resource_observations(
                    observation_id, resource_id, available, health, confidence,
                    observed_at, quota_remaining_ratio, quota_reset_at,
                    latency_ewma_ms, failure_ewma, inflight, concurrency_limit
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    resource_id,
                    available,
                    health,
                    confidence,
                    timestamp,
                    quota_remaining_ratio,
                    quota_reset_at.strip() if isinstance(quota_reset_at, str) else None,
                    latency_ewma_ms,
                    failure_ewma,
                    inflight,
                    concurrency_limit,
                ),
            )
            self.connection.commit()

    @staticmethod
    def _quota_integer(value: int | None, name: str) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer or None")
        return value

    def observe_quota(
        self,
        resource_id: str,
        *,
        request_limit: int | None = None,
        request_remaining: int | None = None,
        token_limit: int | None = None,
        token_remaining: int | None = None,
        reset_at: str | None = None,
        daily_remaining: int | None = None,
        concurrency_limit: int | float | None = None,
        confidence: float = 1.0,
        observed_at: str | None = None,
        source: str = "provider",
    ) -> None:
        request_limit = self._quota_integer(request_limit, "request_limit")
        request_remaining = self._quota_integer(request_remaining, "request_remaining")
        token_limit = self._quota_integer(token_limit, "token_limit")
        token_remaining = self._quota_integer(token_remaining, "token_remaining")
        daily_remaining = self._quota_integer(daily_remaining, "daily_remaining")
        if request_limit is not None and request_remaining is not None and request_remaining > request_limit:
            raise ValueError("request_remaining cannot exceed request_limit")
        if token_limit is not None and token_remaining is not None and token_remaining > token_limit:
            raise ValueError("token_remaining cannot exceed token_limit")
        if isinstance(concurrency_limit, bool) or (concurrency_limit is not None and (not isinstance(concurrency_limit, (int, float)) or not math.isfinite(float(concurrency_limit)) or concurrency_limit < 0)):
            raise ValueError("concurrency_limit must be non-negative or None")
        self._number(confidence, "confidence")
        if confidence > 1:
            raise ValueError("confidence must be at most 1")
        if reset_at is not None and (not isinstance(reset_at, str) or not reset_at.strip()):
            raise ValueError("reset_at must be a non-empty string or None")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must be a non-empty string")
        timestamp = observed_at or _now()
        with self._lock:
            resource = self.connection.execute("SELECT quota_domain FROM resources WHERE resource_id = ?", (resource_id,)).fetchone()
            if resource is None:
                raise KeyError(resource_id)
            quota_domain = resource["quota_domain"]
            if not isinstance(quota_domain, str) or not quota_domain.strip():
                raise ValueError("quota_domain is required for quota observations")
            self.connection.execute(
                """INSERT INTO quota_observations(
                    observation_id, resource_id, quota_domain, request_limit,
                    request_remaining, token_limit, token_remaining, reset_at,
                    daily_remaining, concurrency_limit, confidence, observed_at,
                    source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    resource_id,
                    quota_domain,
                    request_limit,
                    request_remaining,
                    token_limit,
                    token_remaining,
                    reset_at.strip() if isinstance(reset_at, str) else None,
                    daily_remaining,
                    concurrency_limit,
                    confidence,
                    timestamp,
                    source.strip(),
                ),
            )
            self.connection.commit()

    def get_quota_observation(self, resource_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.connection.execute(
                """SELECT resource_id, quota_domain, request_limit,
                          request_remaining, token_limit, token_remaining,
                          reset_at, daily_remaining, concurrency_limit,
                          confidence, observed_at, source
                   FROM quota_observations
                   WHERE resource_id=?
                   ORDER BY observed_at DESC, observation_id DESC
                   LIMIT 1""",
                (resource_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def list_quota_observations(self, *, quota_domain: str | None = None) -> list[dict[str, Any]]:
        """Return the newest observation for each resource in a quota domain.

        Credentials are intentionally kept as separate resources.  Callers
        that need a domain-level headroom must combine these observations
        conservatively; this API never sums them.
        """
        if quota_domain is not None and (not isinstance(quota_domain, str) or not quota_domain.strip()):
            raise ValueError("quota_domain must be a non-empty string or None")
        with self._lock:
            clauses = " WHERE quota_domain=?" if quota_domain is not None else ""
            params: tuple[object, ...] = (quota_domain.strip(),) if quota_domain is not None else ()
            rows = self.connection.execute(
                """SELECT resource_id, quota_domain, request_limit,
                          request_remaining, token_limit, token_remaining,
                          reset_at, daily_remaining, concurrency_limit,
                          confidence, observed_at, source
                   FROM quota_observations""" + clauses +
                " ORDER BY observed_at DESC, observation_id DESC",
                params,
            ).fetchall()
            latest: dict[str, dict[str, Any]] = {}
            for row in rows:
                latest.setdefault(row["resource_id"], dict(row))
            return [latest[resource_id] for resource_id in sorted(latest)]

    def ingest_quota_observation(
        self,
        resource_id: str,
        usage: Mapping[str, Any],
        *,
        observed_at: str | None = None,
        source: str = "provider-response",
    ) -> bool:
        """Ingest the provider-neutral quota observation in response usage.

        Provider adapters may expose provider headers as the normalized
        ``usage.quota_observation`` object.  Unknown or malformed auxiliary
        telemetry is ignored so it cannot turn an otherwise valid model
        response into a duplicate retry.  The resource's persisted domain is
        always authoritative.
        """
        if not isinstance(usage, Mapping):
            return False
        payload = usage.get("quota_observation")
        if payload is None:
            # Accept the short alias for transition compatibility, while the
            # documented contract remains quota_observation.
            payload = usage.get("quota")
        if not isinstance(payload, Mapping):
            return False
        fields = (
            "request_limit",
            "request_remaining",
            "token_limit",
            "token_remaining",
            "reset_at",
            "daily_remaining",
            "concurrency_limit",
        )
        if not any(field in payload for field in fields):
            return False
        try:
            reported_domain = payload.get("quota_domain")
            if reported_domain is not None:
                resource = self.get_resource(resource_id)
                if reported_domain != resource.get("quota_domain"):
                    return False
            self.observe_quota(
                resource_id,
                request_limit=payload.get("request_limit"),
                request_remaining=payload.get("request_remaining"),
                token_limit=payload.get("token_limit"),
                token_remaining=payload.get("token_remaining"),
                reset_at=payload.get("reset_at"),
                daily_remaining=payload.get("daily_remaining"),
                concurrency_limit=payload.get("concurrency_limit"),
                confidence=payload.get("confidence", 1.0),
                observed_at=payload.get("observed_at") or observed_at,
                source=payload.get("source", source),
            )
        except (TypeError, ValueError, KeyError):
            return False
        return True

    def get_resource(self, resource_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.connection.execute("SELECT * FROM resources WHERE resource_id = ?", (resource_id,)).fetchone()
            if row is None:
                raise KeyError(resource_id)
            result = dict(row)
            result["capabilities"] = tuple(json.loads(result.pop("capabilities_json")))
            result["metadata"] = json.loads(result.pop("metadata_json"))
            return result

    def list_resources(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self.get_resource(row[0]) for row in self.connection.execute("SELECT resource_id FROM resources ORDER BY resource_id")]

    def configure_budget(self, *, hard_cap_minor: int, recovery_reserve_minor: int, currency: str, period: BudgetPeriod | None = None, _authority: object | None = None) -> None:
        if _authority is not _BUDGET_ADMIN_TOKEN:
            raise PermissionError("budget configuration requires admin authority")
        if isinstance(hard_cap_minor, bool) or not isinstance(hard_cap_minor, int) or hard_cap_minor < 0 or isinstance(recovery_reserve_minor, bool) or not isinstance(recovery_reserve_minor, int) or recovery_reserve_minor < 0 or recovery_reserve_minor > hard_cap_minor:
            raise ValueError("invalid budget cap or recovery reserve")
        normalized_currency = MoneyAmount(currency, 0).currency
        if period is None:
            now = datetime.now(timezone.utc)
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
            period = BudgetPeriod(start.strftime("%Y-%m"), start.isoformat(), end.isoformat())
        with self._lock:
            existing = self.connection.execute("SELECT period_id, currency FROM budget_config WHERE id=1").fetchone()
            if existing is not None:
                active = self.connection.execute("SELECT period_id, recovery, status, COALESCE(actual_minor, estimated_minor) AS amount FROM budget_reservations WHERE status IN ('prepared', 'dispatching', 'unknown')").fetchall()
                if any(row["period_id"] != period.period_id for row in active):
                    raise ValueError("cannot change budget period while reservations are active")
                committed = self.connection.execute("SELECT recovery, COALESCE(actual_minor, estimated_minor) AS amount FROM budget_reservations WHERE period_id=? AND status IN ('prepared', 'dispatching', 'unknown', 'reconciled')", (period.period_id,)).fetchall()
                if existing["period_id"] == period.period_id and existing["currency"] != normalized_currency and committed:
                    raise ValueError("cannot change budget currency while reservations are committed")
                normal_committed = sum(int(row["amount"]) for row in committed if not row["recovery"])
                recovery_committed = sum(int(row["amount"]) for row in committed if row["recovery"])
                if normal_committed > hard_cap_minor - recovery_reserve_minor or recovery_committed > recovery_reserve_minor:
                    raise ValueError("budget configuration is below existing committed reservations")
            self.connection.execute("INSERT INTO budget_config(id, hard_cap_minor, recovery_reserve_minor, currency, period_id, period_starts_at, period_ends_at) VALUES (1, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET hard_cap_minor=excluded.hard_cap_minor, recovery_reserve_minor=excluded.recovery_reserve_minor, currency=excluded.currency, period_id=excluded.period_id, period_starts_at=excluded.period_starts_at, period_ends_at=excluded.period_ends_at", (hard_cap_minor, recovery_reserve_minor, normalized_currency, period.period_id, period.starts_at, period.ends_at))
            self.connection.commit()

    def budget_config(self) -> dict[str, Any]:
        with self._lock:
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
        with self._lock:
            row = self.connection.execute("SELECT * FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if row is None:
                raise KeyError(reservation_id)
            return dict(row)

    def reservation_totals(self, *, period_id: str | None = None) -> dict[str, int]:
        with self._lock:
            query = "SELECT recovery, status, COALESCE(actual_minor, estimated_minor) AS amount FROM budget_reservations WHERE status IN ('prepared', 'dispatching', 'unknown', 'reconciled')"
            params: tuple[str, ...] = ()
            if period_id is not None:
                query += " AND period_id=?"
                params = (period_id,)
            rows = self.connection.execute(query, params).fetchall()
            normal_committed = sum(int(row["amount"]) for row in rows if not row["recovery"])
            recovery_committed = sum(int(row["amount"]) for row in rows if row["recovery"])
            active = sum(1 for row in rows if row["status"] in {"prepared", "dispatching", "unknown"})
            return {"normal_committed_minor": normal_committed, "recovery_committed_minor": recovery_committed, "active_reservations": active}

    def reserve_budget(self, *, task_id: str, resource_id: str, amount: MoneyAmount, recovery: bool, period: BudgetPeriod, normal_limit_minor: int, recovery_limit_minor: int, native_units: int | float = 1, intent_key: str | None = None) -> str:
        """Atomically check and create a reservation behind the ledger boundary."""
        if intent_key is not None and (not isinstance(intent_key, str) or not intent_key.strip()):
            raise ValueError("intent_key must be a non-empty string or None")
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                if self.maintenance_enabled():
                    raise ValueError("maintenance mode")
                if intent_key is not None:
                    existing = self.connection.execute("SELECT * FROM budget_reservations WHERE intent_key=?", (intent_key,)).fetchone()
                    if existing is not None:
                        if (
                            existing["task_id"] != task_id
                            or existing["resource_id"] != resource_id
                            or int(existing["estimated_minor"]) != amount.minor_units
                            or bool(existing["recovery"]) != bool(recovery)
                            or existing["period_id"] != period.period_id
                            or existing["currency"] != amount.currency
                        ):
                            raise ValueError("intent key is bound to a different budget reservation")
                        if existing["status"] in {"reconciled", "confirmed_no_charge", "released"}:
                            raise ValueError("intent key is bound to a terminal budget reservation")
                        resource_reservation = self.connection.execute("SELECT native_units, status FROM resource_reservations WHERE reservation_id=?", (existing["reservation_id"],)).fetchone()
                        if resource_reservation is None or resource_reservation["status"] != "reserved" or float(resource_reservation["native_units"]) != float(native_units):
                            raise ValueError("intent key is bound to an invalid resource reservation")
                        self.connection.commit()
                        return existing["reservation_id"]
                totals = self.reservation_totals(period_id=period.period_id)
                resource = self.connection.execute("SELECT available, health, price_currency FROM resources WHERE resource_id=?", (resource_id,)).fetchone()
                if resource is None:
                    raise KeyError(resource_id)
                if resource["health"] == "unhealthy":
                    raise ValueError(f"resource is unhealthy: {resource_id}")
                if resource["price_currency"] is not None and resource["price_currency"] != amount.currency:
                    raise ValueError(f"currency mismatch for resource: {resource_id}")
                used = self.connection.execute("SELECT COALESCE(SUM(native_units), 0) FROM resource_reservations WHERE resource_id=? AND status='reserved'", (resource_id,)).fetchone()[0]
                if float(used) + float(native_units) > float(resource["available"]):
                    raise ValueError(f"resource capacity exceeded: {resource_id}")
                remaining = (recovery_limit_minor if recovery else normal_limit_minor) - (totals["recovery_committed_minor"] if recovery else totals["normal_committed_minor"])
                if amount.minor_units > remaining:
                    raise ValueError(f"budget exceeded: requested {amount.minor_units}, remaining {remaining}")
                reservation_id = str(uuid4())
                self.connection.execute("INSERT INTO budget_reservations(reservation_id, task_id, resource_id, intent_key, estimated_minor, actual_minor, recovery, status, created_at, reconciled_at, period_id, currency) VALUES (?, ?, ?, ?, ?, NULL, ?, 'prepared', ?, NULL, ?, ?)", (reservation_id, task_id, resource_id, intent_key, amount.minor_units, int(recovery), datetime.now(timezone.utc).isoformat(), period.period_id, amount.currency))
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
                if row["status"] not in {"prepared", "dispatching", "unknown"}:
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

    def transition_budget(self, reservation_id: str, *, to_status: str, expected_from: set[str] | None = None) -> None:
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.connection.execute("SELECT status FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
                if row is None:
                    raise KeyError(reservation_id)
                current = row["status"]
                if expected_from is not None and current not in expected_from:
                    raise ValueError(f"budget reservation is not in an expected state: {current}")
                if to_status not in _BUDGET_TRANSITIONS or to_status not in _BUDGET_TRANSITIONS.get(current, set()):
                    raise ValueError(f"invalid budget reservation transition: {current} -> {to_status}")
                terminal = to_status in {"reconciled", "confirmed_no_charge", "released"}
                self.connection.execute("UPDATE budget_reservations SET status=?, reconciled_at=CASE WHEN ? THEN ? ELSE reconciled_at END WHERE reservation_id=?", (to_status, int(terminal), datetime.now(timezone.utc).isoformat(), reservation_id))
                if terminal:
                    self.connection.execute("UPDATE resource_reservations SET status='released' WHERE reservation_id=?", (reservation_id,))
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def release_budget(self, reservation_id: str) -> None:
        self.transition_budget(reservation_id, to_status="confirmed_no_charge", expected_from={"prepared", "dispatching"})

    def mark_budget_unknown(self, reservation_id: str) -> None:
        with self._lock:
            row = self.connection.execute("SELECT status FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if row is None:
                raise KeyError(reservation_id)
            if row["status"] == "unknown":
                return
        self.transition_budget(reservation_id, to_status="unknown", expected_from={"prepared", "dispatching"})
