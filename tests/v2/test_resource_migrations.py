import sqlite3

from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger


def test_resource_ledger_runs_ordered_migrations_for_legacy_database(tmp_path):
    path = tmp_path / "legacy-resources.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE resources (resource_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, native_unit TEXT NOT NULL, capacity REAL NOT NULL, capabilities_json TEXT NOT NULL, sensitivity TEXT NOT NULL, cost_minor INTEGER, available REAL NOT NULL, health TEXT NOT NULL, confidence REAL NOT NULL, observed_at TEXT NOT NULL, consecutive_failures INTEGER NOT NULL DEFAULT 0, circuit_open_until REAL NOT NULL DEFAULT 0, metadata_json TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE budget_config (id INTEGER PRIMARY KEY CHECK (id=1), hard_cap_minor INTEGER NOT NULL, recovery_reserve_minor INTEGER NOT NULL, currency TEXT NOT NULL);
        CREATE TABLE budget_reservations (reservation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, resource_id TEXT NOT NULL, estimated_minor INTEGER NOT NULL, actual_minor INTEGER, recovery INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, reconciled_at TEXT);
        CREATE TABLE resource_reservations (reservation_id TEXT PRIMARY KEY, resource_id TEXT NOT NULL, native_units REAL NOT NULL, status TEXT NOT NULL);
        CREATE TABLE resource_control (id INTEGER PRIMARY KEY CHECK (id=1), maintenance INTEGER NOT NULL DEFAULT 0);
        INSERT INTO budget_config VALUES (1, 100, 10, 'JPY');
        """
    )
    connection.commit()
    connection.close()

    ledger = ResourceLedger(path)
    columns = {row[1] for row in ledger.connection.execute("PRAGMA table_info(resources)")}
    config = ledger.budget_config()
    version = ledger.connection.execute("SELECT value FROM resource_schema_meta WHERE key='schema_version'").fetchone()[0]

    assert "price_currency" in columns
    assert "quota_domain" in columns
    assert config["period_id"] != "legacy"
    assert config["period_starts_at"] < config["period_ends_at"]
    assert version == "9"
    assert ledger.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quota_unknown_admissions'"
    ).fetchone() is not None


def test_resource_ledger_migrates_v7_quota_table_with_reset_fields(tmp_path):
    path = tmp_path / "v7-resources.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE resources (resource_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, native_unit TEXT NOT NULL, capacity REAL NOT NULL, capabilities_json TEXT NOT NULL, sensitivity TEXT NOT NULL, cost_minor INTEGER, quota_domain TEXT, available REAL NOT NULL, health TEXT NOT NULL, confidence REAL NOT NULL, observed_at TEXT NOT NULL, consecutive_failures INTEGER NOT NULL DEFAULT 0, circuit_open_until REAL NOT NULL DEFAULT 0, metadata_json TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE quota_observations (observation_id TEXT PRIMARY KEY, resource_id TEXT NOT NULL, quota_domain TEXT NOT NULL, unit TEXT NOT NULL DEFAULT 'requests', limit_value REAL, remaining_value REAL, consumed_value REAL, authority TEXT NOT NULL DEFAULT 'provider', request_limit INTEGER, request_remaining INTEGER, token_limit INTEGER, token_remaining INTEGER, reset_at TEXT, daily_remaining INTEGER, concurrency_limit REAL, confidence REAL NOT NULL, observed_at TEXT NOT NULL, source TEXT NOT NULL);
        CREATE TABLE budget_config (id INTEGER PRIMARY KEY CHECK (id=1), hard_cap_minor INTEGER NOT NULL, recovery_reserve_minor INTEGER NOT NULL, currency TEXT NOT NULL);
        CREATE TABLE budget_reservations (reservation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, resource_id TEXT NOT NULL, estimated_minor INTEGER NOT NULL, actual_minor INTEGER, recovery INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, reconciled_at TEXT);
        CREATE TABLE resource_reservations (reservation_id TEXT PRIMARY KEY, resource_id TEXT NOT NULL, native_units REAL NOT NULL, status TEXT NOT NULL);
        CREATE TABLE resource_control (id INTEGER PRIMARY KEY CHECK (id=1), maintenance INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE resource_schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO resource_schema_meta VALUES ('schema_version', '7');
        """
    )
    connection.commit()
    connection.close()

    ledger = ResourceLedger(path)
    columns = {row[1] for row in ledger.connection.execute("PRAGMA table_info(quota_observations)")}
    assert {"metric", "window", "reset_source", "blocked_until", "block_reason"}.issubset(columns)
    assert ledger.connection.execute("SELECT value FROM resource_schema_meta WHERE key='schema_version'").fetchone()[0] == "9"
    assert ledger.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quota_unknown_admissions'"
    ).fetchone() is not None


def test_control_plane_maintenance_uses_governor_store_not_router_ledger(tmp_path):
    ledger = ResourceLedger(tmp_path / "control-plane-maintenance.sqlite3")
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0))
    governor = BudgetGovernor(ledger)

    class ReadOnlyRouter:
        pass

    control = ResourceControlPlane(ReadOnlyRouter(), governor)
    control.set_maintenance(True)

    assert ledger.maintenance_enabled() is True
