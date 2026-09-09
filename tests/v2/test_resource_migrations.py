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
    assert version == "7"


def test_control_plane_maintenance_uses_governor_store_not_router_ledger(tmp_path):
    ledger = ResourceLedger(tmp_path / "control-plane-maintenance.sqlite3")
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0))
    governor = BudgetGovernor(ledger)

    class ReadOnlyRouter:
        pass

    control = ResourceControlPlane(ReadOnlyRouter(), governor)
    control.set_maintenance(True)

    assert ledger.maintenance_enabled() is True
