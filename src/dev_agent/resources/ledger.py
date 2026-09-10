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
from uuid import uuid4  # compatibility export for legacy observation tests

from .._sqlite import connect
from .snapshot import RoutingSnapshot
from .budget_store import BudgetReservationStore, _BUDGET_TRANSITIONS
from .catalog import ResourceCatalogStore
from .health import ProviderHealthStore
from .observations import QuotaObservationStore, ResourceObservationStore
from .quota_policy import QuotaBlockDecision


# Capability held only by the explicit budget-administration facade. Runtime
# reservation code may read the persisted policy but must not rewrite it.
_BUDGET_ADMIN_TOKEN = object()


def unknown_quota_wake_reason(quota_domain: str) -> str:
    if not isinstance(quota_domain, str) or not quota_domain.strip():
        raise ValueError("quota_domain must be a non-empty string")
    return f"quota_unknown:{quota_domain.strip()}"

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
    provider_binding_id: str | None = None
    intelligence_tier: str | None = None


@dataclass(frozen=True)
class UnknownQuotaAdmission:
    """Durable local admission result for a quota-telemetry-unknown domain."""

    admitted: bool
    retry_at_epoch: float | None = None

    def __post_init__(self) -> None:
        if type(self.admitted) is not bool:
            raise ValueError("admitted must be a boolean")
        if self.retry_at_epoch is not None:
            if isinstance(self.retry_at_epoch, bool) or not isinstance(self.retry_at_epoch, (int, float)) or not math.isfinite(float(self.retry_at_epoch)):
                raise ValueError("retry_at_epoch must be finite or None")


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
    unit: str = "requests"
    limit_value: int | float | None = None
    remaining_value: int | float | None = None
    consumed_value: int | float | None = None
    authority: str = "provider"
    metric: str = "quota"
    window: str = "unknown"
    reset_source: str | None = None
    blocked_until: str | None = None
    block_reason: str | None = None


class ResourceLedger:
    """SQLite-backed resource observations and budget reservation records."""

    SCHEMA_VERSION = 9
    UNKNOWN_QUOTA_ADMISSION_LIMIT = 1
    UNKNOWN_QUOTA_ADMISSION_WINDOW_SECONDS = 60.0
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
        unit TEXT NOT NULL DEFAULT 'requests',
        limit_value REAL,
        remaining_value REAL,
        consumed_value REAL,
        authority TEXT NOT NULL DEFAULT 'provider',
        metric TEXT NOT NULL DEFAULT 'quota',
        window TEXT NOT NULL DEFAULT 'unknown',
        reset_source TEXT,
        blocked_until TEXT,
        block_reason TEXT,
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
    CREATE TABLE IF NOT EXISTS quota_unknown_admissions (
        quota_domain TEXT PRIMARY KEY,
        window_started_at REAL NOT NULL,
        admitted_count INTEGER NOT NULL
    );
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._catalog_store = ResourceCatalogStore(self.connection, self._lock)
        self._observation_store = ResourceObservationStore(self.connection, self._lock)
        self._quota_store = QuotaObservationStore(self.connection, self._lock)
        self._health_store = ProviderHealthStore(self.connection, self._lock)
        self._budget_store = BudgetReservationStore(self.connection, self._lock)
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
                current = 6
            if current < 7:
                for column, definition in (
                    ("unit", "TEXT NOT NULL DEFAULT 'requests'"),
                    ("limit_value", "REAL"),
                    ("remaining_value", "REAL"),
                    ("consumed_value", "REAL"),
                    ("authority", "TEXT NOT NULL DEFAULT 'provider'"),
                ):
                    self._ensure_column("quota_observations", column, definition)
                self.connection.execute(
                    "UPDATE quota_observations SET unit='tokens', limit_value=token_limit, remaining_value=token_remaining WHERE request_limit IS NULL AND request_remaining IS NULL AND token_limit IS NOT NULL"
                )
                self.connection.execute(
                    "UPDATE quota_observations SET unit='requests', limit_value=request_limit, remaining_value=request_remaining WHERE request_limit IS NOT NULL OR request_remaining IS NOT NULL"
                )
                self.connection.execute("UPDATE resource_schema_meta SET value='7' WHERE key='schema_version'")
            if current < 8:
                for column, definition in (
                    ("metric", "TEXT NOT NULL DEFAULT 'quota'"),
                    ("window", "TEXT NOT NULL DEFAULT 'unknown'"),
                    ("reset_source", "TEXT"),
                    ("blocked_until", "TEXT"),
                    ("block_reason", "TEXT"),
                ):
                    self._ensure_column("quota_observations", column, definition)
                self.connection.execute("UPDATE resource_schema_meta SET value='8' WHERE key='schema_version'")
                current = 8
            if current < 9:
                self.connection.execute(
                    """CREATE TABLE IF NOT EXISTS quota_unknown_admissions (
                        quota_domain TEXT PRIMARY KEY,
                        window_started_at REAL NOT NULL,
                        admitted_count INTEGER NOT NULL
                    )"""
                )
                self.connection.execute("UPDATE resource_schema_meta SET value='9' WHERE key='schema_version'")
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
        provider_binding_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        intelligence_tier: str | None = None,
    ) -> ResourceSpec:
        if not resource_id.strip() or not provider_id.strip() or not native_unit.strip():
            raise ValueError("resource_id, provider_id, and native_unit are required")
        if quota_domain is not None:
            if not isinstance(quota_domain, str) or not quota_domain.strip():
                raise ValueError("quota_domain must be a non-empty string or None")
            quota_domain = quota_domain.strip()
        if provider_binding_id is not None:
            if not isinstance(provider_binding_id, str) or not provider_binding_id.strip():
                raise ValueError("provider_binding_id must be a non-empty string or None")
            provider_binding_id = provider_binding_id.strip()
        resource_metadata = dict(metadata or {})
        metadata_tier = resource_metadata.get("intelligence_tier")
        if intelligence_tier is None:
            intelligence_tier = metadata_tier
        if intelligence_tier is not None:
            if hasattr(intelligence_tier, "value"):
                intelligence_tier = intelligence_tier.value
            if not isinstance(intelligence_tier, str) or intelligence_tier.strip() not in {"L0", "L1", "L2", "L3"}:
                raise ValueError("intelligence_tier must be one of L0, L1, L2, or L3")
            intelligence_tier = intelligence_tier.strip()
            resource_metadata["intelligence_tier"] = intelligence_tier
        metadata_binding = resource_metadata.get("provider_binding_id")
        if provider_binding_id is None and isinstance(metadata_binding, str) and metadata_binding.strip():
            provider_binding_id = metadata_binding.strip()
        provider_binding_id = provider_binding_id or provider_id.strip()
        resource_metadata["provider_binding_id"] = provider_binding_id
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
        self._catalog_store.upsert(
            resource_id=resource_id,
            provider_id=provider_id,
            native_unit=native_unit,
            capacity=capacity,
            capabilities=capability_list,
            sensitivity=sensitivity,
            cost_minor=cost_minor,
            price_currency=price_currency.upper() if price_currency else None,
            quota_domain=quota_domain,
            metadata=resource_metadata,
            observed_at=now,
        )
        return ResourceSpec(resource_id, provider_id, native_unit, capacity, capability_list, sensitivity, cost_minor, price_currency.upper() if price_currency else None, quota_domain, provider_binding_id, intelligence_tier)

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
        self._observation_store.record(
            resource_id=resource_id,
            available=available,
            health=health,
            confidence=confidence,
            observed_at=timestamp,
            quota_remaining_ratio=quota_remaining_ratio,
            quota_reset_at=quota_reset_at.strip() if isinstance(quota_reset_at, str) else None,
            latency_ewma_ms=latency_ewma_ms,
            failure_ewma=failure_ewma,
            inflight=inflight,
            concurrency_limit=concurrency_limit,
        )

    def refresh_resource_observation(
        self,
        resource_id: str,
        *,
        health: str = "healthy",
        observed_at: str | None = None,
        confidence: float = 1.0,
    ) -> None:
        """Refresh resource liveness while preserving operator configuration.

        A successful provider response is a real liveness observation, but it
        must not rewrite catalog fields such as price, quota domain, or
        metadata.  Reuse the observation repository so the current row and
        historical observation remain updated together.
        """

        current = self.get_resource(resource_id)
        self.observe(
            resource_id,
            available=current["available"],
            health=health,
            confidence=confidence,
            observed_at=observed_at,
            quota_remaining_ratio=current.get("quota_remaining_ratio"),
            quota_reset_at=current.get("quota_reset_at"),
            latency_ewma_ms=current.get("latency_ewma_ms"),
            failure_ewma=current.get("failure_ewma"),
            inflight=current.get("inflight", 0),
            concurrency_limit=current.get("concurrency_limit"),
        )

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
        unit: str | None = None,
        limit: int | float | None = None,
        remaining: int | float | None = None,
        consumed: int | float | None = None,
        authority: str = "provider",
        metric: str = "quota",
        window: str = "unknown",
        reset_source: str | None = None,
        blocked_until: str | None = None,
        block_reason: str | None = None,
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
        if unit is None:
            unit = "requests" if request_limit is not None or request_remaining is not None or daily_remaining is not None else "tokens" if token_limit is not None or token_remaining is not None else "requests"
        if not isinstance(unit, str) or unit not in {"requests", "tokens", "neurons"}:
            raise ValueError("unit must be one of requests, tokens, or neurons")
        if limit is not None:
            limit = self._number(limit, "limit")
        if remaining is not None:
            remaining = self._number(remaining, "remaining")
        if consumed is not None:
            consumed = self._number(consumed, "consumed")
        if limit is not None and remaining is not None and remaining > limit:
            raise ValueError("remaining cannot exceed limit")
        if not isinstance(authority, str) or not authority.strip():
            raise ValueError("authority must be a non-empty string")
        if not isinstance(metric, str) or not metric.strip() or len(metric.strip()) > 64:
            raise ValueError("metric must be a non-empty string of at most 64 characters")
        metric = metric.strip().lower()
        if not isinstance(window, str) or window.strip() not in {"unknown", "minute", "day", "month"}:
            raise ValueError("window must be one of unknown, minute, day, or month")
        window = window.strip()
        for name, value in (
            ("reset_source", reset_source),
            ("blocked_until", blocked_until),
            ("block_reason", block_reason),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value.strip()) > 256):
                raise ValueError(f"{name} must be a non-empty string of at most 256 characters or None")
        reset_source = reset_source.strip() if isinstance(reset_source, str) else None
        blocked_until = blocked_until.strip() if isinstance(blocked_until, str) else None
        block_reason = block_reason.strip().lower() if isinstance(block_reason, str) else None
        request_limit = self._quota_integer(request_limit, "request_limit")
        request_remaining = self._quota_integer(request_remaining, "request_remaining")
        token_limit = self._quota_integer(token_limit, "token_limit")
        token_remaining = self._quota_integer(token_remaining, "token_remaining")
        daily_remaining = self._quota_integer(daily_remaining, "daily_remaining")
        if request_limit is not None and request_remaining is not None and request_remaining > request_limit:
            raise ValueError("request_remaining cannot exceed request_limit")
        if token_limit is not None and token_remaining is not None and token_remaining > token_limit:
            raise ValueError("token_remaining cannot exceed token_limit")
        if limit is None:
            if unit == "requests":
                limit = request_limit
            elif unit == "tokens":
                limit = token_limit
        if remaining is None:
            if unit == "requests":
                remaining = request_remaining if request_remaining is not None else daily_remaining
            elif unit == "tokens":
                remaining = token_remaining
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
        resource = self._catalog_store.get(resource_id)
        quota_domain = resource.get("quota_domain")
        if not isinstance(quota_domain, str) or not quota_domain.strip():
            raise ValueError("quota_domain is required for quota observations")
        self._quota_store.record(
            resource_id=resource_id,
            quota_domain=quota_domain.strip(),
            unit=unit,
            limit=limit,
            remaining=remaining,
            consumed=consumed,
            authority=authority.strip(),
            metric=metric,
            window=window,
            reset_source=reset_source,
            blocked_until=blocked_until,
            block_reason=block_reason,
            request_limit=request_limit,
            request_remaining=request_remaining,
            token_limit=token_limit,
            token_remaining=token_remaining,
            reset_at=reset_at.strip() if isinstance(reset_at, str) else None,
            daily_remaining=daily_remaining,
            concurrency_limit=concurrency_limit,
            confidence=confidence,
            observed_at=timestamp,
            source=source.strip(),
        )

    def get_quota_observation(self, resource_id: str) -> dict[str, Any] | None:
        return self._quota_store.get_latest(resource_id)

    def claim_unknown_quota_admission(
        self,
        quota_domain: str,
        *,
        now_epoch: float | None = None,
        limit: int = UNKNOWN_QUOTA_ADMISSION_LIMIT,
        window_seconds: float = UNKNOWN_QUOTA_ADMISSION_WINDOW_SECONDS,
    ) -> UnknownQuotaAdmission:
        """Atomically admit a bounded request without inventing quota headroom.

        The counter is local policy, not provider telemetry.  It is keyed by
        quota domain so multiple credentials cannot multiply an unknown
        provider allowance.  A caller that has not crossed the external
        provider boundary may release its slot; a response/error path keeps
        the admission consumed for the remainder of the local window.
        """

        if not isinstance(quota_domain, str) or not quota_domain.strip():
            raise ValueError("quota_domain must be a non-empty string")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        if isinstance(window_seconds, bool) or not isinstance(window_seconds, (int, float)) or not math.isfinite(float(window_seconds)) or window_seconds <= 0:
            raise ValueError("window_seconds must be a finite positive number")
        current = time.time() if now_epoch is None else now_epoch
        if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(float(current)):
            raise ValueError("now_epoch must be finite")
        quota_domain = quota_domain.strip()
        window_seconds = float(window_seconds)
        current = float(current)
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                row = self.connection.execute(
                    "SELECT window_started_at, admitted_count FROM quota_unknown_admissions WHERE quota_domain=?",
                    (quota_domain,),
                ).fetchone()
                if row is None or current >= float(row["window_started_at"]) + window_seconds:
                    self.connection.execute(
                        "INSERT INTO quota_unknown_admissions(quota_domain, window_started_at, admitted_count) VALUES (?, ?, 1) ON CONFLICT(quota_domain) DO UPDATE SET window_started_at=excluded.window_started_at, admitted_count=excluded.admitted_count",
                        (quota_domain, current),
                    )
                    self.connection.commit()
                    return UnknownQuotaAdmission(True)
                admitted_count = int(row["admitted_count"])
                retry_at = float(row["window_started_at"]) + window_seconds
                if admitted_count >= limit:
                    self.connection.commit()
                    return UnknownQuotaAdmission(False, retry_at)
                self.connection.execute(
                    "UPDATE quota_unknown_admissions SET admitted_count=admitted_count+1 WHERE quota_domain=?",
                    (quota_domain,),
                )
                self.connection.commit()
                return UnknownQuotaAdmission(True)
            except BaseException:
                self.connection.rollback()
                raise

    def release_unknown_quota_admission(self, quota_domain: str) -> None:
        """Return a local slot when no external request crossed the boundary."""

        if not isinstance(quota_domain, str) or not quota_domain.strip():
            raise ValueError("quota_domain must be a non-empty string")
        quota_domain = quota_domain.strip()
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                row = self.connection.execute(
                    "SELECT admitted_count FROM quota_unknown_admissions WHERE quota_domain=?",
                    (quota_domain,),
                ).fetchone()
                if row is not None:
                    if int(row["admitted_count"]) <= 1:
                        self.connection.execute("DELETE FROM quota_unknown_admissions WHERE quota_domain=?", (quota_domain,))
                    else:
                        self.connection.execute(
                            "UPDATE quota_unknown_admissions SET admitted_count=admitted_count-1 WHERE quota_domain=?",
                            (quota_domain,),
                        )
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def due_unknown_quota_domains(self, *, now_epoch: float | None = None, window_seconds: float = UNKNOWN_QUOTA_ADMISSION_WINDOW_SECONDS) -> tuple[str, ...]:
        """List local admission windows that may accept another probe/request."""

        if isinstance(window_seconds, bool) or not isinstance(window_seconds, (int, float)) or not math.isfinite(float(window_seconds)) or window_seconds <= 0:
            raise ValueError("window_seconds must be a finite positive number")
        current = time.time() if now_epoch is None else now_epoch
        if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(float(current)):
            raise ValueError("now_epoch must be finite")
        with self._lock:
            rows = self.connection.execute(
                "SELECT quota_domain, window_started_at FROM quota_unknown_admissions WHERE admitted_count > 0 ORDER BY quota_domain",
            ).fetchall()
        return tuple(
            str(row["quota_domain"])
            for row in rows
            if float(current) >= float(row["window_started_at"]) + float(window_seconds)
        )

    def record_quota_block(
        self,
        resource_id: str,
        decision: QuotaBlockDecision,
        *,
        observed_at: str | None = None,
        source: str = "provider-error",
    ) -> bool:
        """Persist a routing block while preserving known quota facts."""

        if not isinstance(decision, QuotaBlockDecision):
            raise TypeError("decision must be a QuotaBlockDecision")
        resource = self.get_resource(resource_id)
        if not resource.get("quota_domain"):
            return False
        latest = self.get_quota_observation(resource_id) or {}
        self.observe_quota(
            resource_id,
            unit=latest.get("unit", "requests"),
            limit=latest.get("limit"),
            remaining=latest.get("remaining"),
            consumed=latest.get("consumed"),
            authority=decision.authority,
            metric=decision.metric,
            window=decision.window,
            reset_source=decision.reset_source,
            blocked_until=decision.blocked_until,
            block_reason=decision.block_reason,
            request_limit=latest.get("request_limit"),
            request_remaining=latest.get("request_remaining"),
            token_limit=latest.get("token_limit"),
            token_remaining=latest.get("token_remaining"),
            reset_at=latest.get("reset_at") or decision.blocked_until,
            daily_remaining=latest.get("daily_remaining"),
            concurrency_limit=latest.get("concurrency_limit"),
            confidence=decision.confidence,
            observed_at=observed_at,
            source=source,
        )
        return True

    def list_quota_observations(self, *, quota_domain: str | None = None) -> list[dict[str, Any]]:
        """Return the newest observation for each resource in a quota domain.

        Credentials are intentionally kept as separate resources.  Callers
        that need a domain-level headroom must combine these observations
        conservatively; this API never sums them.
        """
        if quota_domain is not None and (not isinstance(quota_domain, str) or not quota_domain.strip()):
            raise ValueError("quota_domain must be a non-empty string or None")
        return self._quota_store.list_latest(quota_domain=quota_domain.strip() if quota_domain is not None else None)

    @staticmethod
    def _quota_from_row(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        return QuotaObservationStore.from_row(row)

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
            "unit",
            "limit",
            "remaining",
            "consumed",
            "metric",
            "window",
            "reset_source",
            "blocked_until",
            "block_reason",
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
                unit=payload.get("unit"),
                limit=payload.get("limit"),
                remaining=payload.get("remaining"),
                consumed=payload.get("consumed"),
                authority=payload.get("authority", "provider"),
                metric=payload.get("metric", "quota"),
                window=payload.get("window", "unknown"),
                reset_source=payload.get("reset_source"),
                blocked_until=payload.get("blocked_until"),
                block_reason=payload.get("block_reason"),
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
        return self._catalog_store.get(resource_id)

    @staticmethod
    def _resource_from_row(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        return ResourceCatalogStore.from_row(row)

    def list_resources(self) -> list[dict[str, Any]]:
        return self._catalog_store.list()

    def routing_snapshot(self) -> RoutingSnapshot:
        """Read resources and current-domain quota observations in one batch.

        The router previously loaded each quota domain separately while
        iterating resources.  This keeps the same conservative latest-per-
        resource semantics while reducing the database work to one resource
        query and one quota query per routing decision.
        """
        with self._lock:
            resource_rows = self._catalog_store.rows()
            resources = tuple(self._resource_from_row(row) for row in resource_rows)
            domains = sorted({str(resource["quota_domain"]) for resource in resources if resource.get("quota_domain")})
            latest_by_resource: dict[str, dict[str, Any]] = {}
            if domains:
                quota_rows = self._quota_store.rows_for_domains(domains)
                for row in quota_rows:
                    latest_by_resource.setdefault(row["resource_id"], self._quota_from_row(row))
            observations_by_domain: dict[str, tuple[dict[str, Any], ...]] = {}
            grouped: dict[str, list[dict[str, Any]]] = {}
            for observation in latest_by_resource.values():
                grouped.setdefault(str(observation["quota_domain"]), []).append(observation)
            for domain, observations in grouped.items():
                observations_by_domain[domain] = tuple(sorted(observations, key=lambda item: str(item["resource_id"])))
            return RoutingSnapshot(resources, observations_by_domain)

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

    def record_provider_failure(
        self,
        provider_id: str,
        *,
        resource_id: str | None = None,
        provider_binding_id: str | None = None,
        threshold: int = 3,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self._health_store.record_failure(
            provider_id,
            resource_id=resource_id,
            provider_binding_id=provider_binding_id,
            threshold=threshold,
            cooldown_seconds=cooldown_seconds,
        )

    def record_provider_success(
        self,
        provider_id: str,
        *,
        resource_id: str | None = None,
        provider_binding_id: str | None = None,
    ) -> None:
        self._health_store.record_success(
            provider_id,
            resource_id=resource_id,
            provider_binding_id=provider_binding_id,
        )

    def reservation_row(self, reservation_id: str) -> dict[str, Any]:
        return self._budget_store.reservation_row(reservation_id)

    def reservation_totals(self, *, period_id: str | None = None) -> dict[str, int]:
        return self._budget_store.reservation_totals(period_id=period_id)

    def reserve_budget(self, *, task_id: str, resource_id: str, amount: MoneyAmount, recovery: bool, period: BudgetPeriod, normal_limit_minor: int, recovery_limit_minor: int, native_units: int | float = 1, intent_key: str | None = None) -> str:
        return self._budget_store.reserve_budget(
            task_id=task_id,
            resource_id=resource_id,
            amount=amount,
            recovery=recovery,
            period=period,
            normal_limit_minor=normal_limit_minor,
            recovery_limit_minor=recovery_limit_minor,
            native_units=native_units,
            intent_key=intent_key,
        )

    def reconcile_budget(self, reservation_id: str, *, actual: MoneyAmount, period: BudgetPeriod, normal_limit_minor: int, recovery_limit_minor: int) -> None:
        self._budget_store.reconcile_budget(
            reservation_id,
            actual=actual,
            period=period,
            normal_limit_minor=normal_limit_minor,
            recovery_limit_minor=recovery_limit_minor,
        )

    def transition_budget(self, reservation_id: str, *, to_status: str, expected_from: set[str] | None = None) -> None:
        self._budget_store.transition_budget(reservation_id, to_status=to_status, expected_from=expected_from)

    def release_budget(self, reservation_id: str) -> None:
        self._budget_store.release_budget(reservation_id)

    def mark_budget_unknown(self, reservation_id: str) -> None:
        self._budget_store.mark_budget_unknown(reservation_id)
