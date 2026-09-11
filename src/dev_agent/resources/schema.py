"""SQLite schema and ordered migrations for the ResourceLedger facade."""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3


SCHEMA_VERSION = 10

SCHEMA = """
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
CREATE TABLE IF NOT EXISTS resource_repairs (
    audit_id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    operator_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_schema(connection: sqlite3.Connection, *, schema_version: int = SCHEMA_VERSION) -> None:
    """Create or migrate the ResourceLedger tables in one connection."""

    existing_tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    try:
        connection.executescript(SCHEMA)
        if not existing_tables:
            connection.execute(
                "INSERT OR REPLACE INTO resource_schema_meta(key, value) VALUES ('schema_version', ?)",
                (str(schema_version),),
            )
            connection.commit()
            return
        connection.execute(
            "INSERT OR IGNORE INTO resource_schema_meta(key, value) VALUES ('schema_version', '1')"
        )
        current = int(
            connection.execute(
                "SELECT value FROM resource_schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        )
        if current > schema_version:
            raise ValueError(f"unsupported resource schema version: {current}")
        connection.commit()
        connection.execute("BEGIN")
        if current < 2:
            _ensure_column(connection, "resources", "price_currency", "TEXT")
            _ensure_column(connection, "budget_config", "period_id", "TEXT NOT NULL DEFAULT 'legacy'")
            _ensure_column(connection, "budget_config", "period_starts_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "budget_config", "period_ends_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "budget_reservations", "period_id", "TEXT NOT NULL DEFAULT 'legacy'")
            _ensure_column(connection, "budget_reservations", "currency", "TEXT NOT NULL DEFAULT 'JPY'")
            connection.execute("UPDATE resource_schema_meta SET value='2' WHERE key='schema_version'")
            current = 2
        if current < 3:
            config = connection.execute(
                "SELECT currency, period_id, period_starts_at, period_ends_at FROM budget_config WHERE id=1"
            ).fetchone()
            if config is not None and (config["period_id"] == "legacy" or not config["period_starts_at"] or not config["period_ends_at"]):
                now = datetime.now(timezone.utc)
                start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
                period_id = start.strftime("%Y-%m")
                connection.execute(
                    "UPDATE budget_config SET period_id=?, period_starts_at=?, period_ends_at=? WHERE id=1",
                    (period_id, start.isoformat(), end.isoformat()),
                )
                connection.execute(
                    "UPDATE budget_reservations SET period_id=?, currency=? WHERE period_id='legacy'",
                    (period_id, config["currency"]),
                )
            connection.execute("UPDATE budget_reservations SET status='prepared' WHERE status='reserved'")
            connection.execute("UPDATE resource_schema_meta SET value='3' WHERE key='schema_version'")
        if current < 4:
            _ensure_column(connection, "budget_reservations", "intent_key", "TEXT")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_reservations_intent_key ON budget_reservations(intent_key) WHERE intent_key IS NOT NULL"
            )
            connection.execute("UPDATE resource_schema_meta SET value='4' WHERE key='schema_version'")
            current = 4
        if current < 5:
            _ensure_column(connection, "resources", "quota_domain", "TEXT")
            connection.execute(
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
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quota_observations_resource_observed_at ON quota_observations(resource_id, observed_at)"
            )
            connection.execute("UPDATE resource_schema_meta SET value='5' WHERE key='schema_version'")
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
                _ensure_column(connection, "resources", column, definition)
                _ensure_column(connection, "resource_observations", column, definition)
            connection.execute("UPDATE resource_schema_meta SET value='6' WHERE key='schema_version'")
            current = 6
        if current < 7:
            for column, definition in (
                ("unit", "TEXT NOT NULL DEFAULT 'requests'"),
                ("limit_value", "REAL"),
                ("remaining_value", "REAL"),
                ("consumed_value", "REAL"),
                ("authority", "TEXT NOT NULL DEFAULT 'provider'"),
            ):
                _ensure_column(connection, "quota_observations", column, definition)
            connection.execute(
                "UPDATE quota_observations SET unit='tokens', limit_value=token_limit, remaining_value=token_remaining WHERE request_limit IS NULL AND request_remaining IS NULL AND token_limit IS NOT NULL"
            )
            connection.execute(
                "UPDATE quota_observations SET unit='requests', limit_value=request_limit, remaining_value=request_remaining WHERE request_limit IS NOT NULL OR request_remaining IS NOT NULL"
            )
            connection.execute("UPDATE resource_schema_meta SET value='7' WHERE key='schema_version'")
        if current < 8:
            for column, definition in (
                ("metric", "TEXT NOT NULL DEFAULT 'quota'"),
                ("window", "TEXT NOT NULL DEFAULT 'unknown'"),
                ("reset_source", "TEXT"),
                ("blocked_until", "TEXT"),
                ("block_reason", "TEXT"),
            ):
                _ensure_column(connection, "quota_observations", column, definition)
            connection.execute("UPDATE resource_schema_meta SET value='8' WHERE key='schema_version'")
            current = 8
        if current < 9:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS quota_unknown_admissions (
                    quota_domain TEXT PRIMARY KEY,
                    window_started_at REAL NOT NULL,
                    admitted_count INTEGER NOT NULL
                )"""
            )
            connection.execute("UPDATE resource_schema_meta SET value='9' WHERE key='schema_version'")
            current = 9
        if current < 10:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS resource_repairs (
                    audit_id TEXT PRIMARY KEY,
                    resource_id TEXT NOT NULL,
                    operator_ref TEXT NOT NULL,
                    status TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute("UPDATE resource_schema_meta SET value='10' WHERE key='schema_version'")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
