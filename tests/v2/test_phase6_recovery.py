import json

import pytest

from recovery.phase6_recovery import RecoveryOperator
from recovery.validate_resources import validate_resource_ledger
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.ledger import ResourceLedger


def test_rescue_cli_diagnose_is_read_only_json(capsys):
    from recovery.rescue import main

    assert main(["diagnose", "--root", ".", "--json"]) == 0
    output = capsys.readouterr().out
    assert '"name": "repository_root"' in output


def test_recovery_operator_is_read_only_by_default_and_can_backup_restore(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    operator = RecoveryOperator(root)
    report = operator.snapshot()
    assert report["repository_root"] is True
    with pytest.raises(PermissionError):
        operator.restore_state(tmp_path / "state.sqlite3", tmp_path / "restored.sqlite3")


def test_recovery_validates_resource_ledger_without_runtime_import(tmp_path):
    ledger_path = tmp_path / "resources.sqlite3"
    ledger = ResourceLedger(ledger_path)
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    ledger.register_resource("local", provider_id="ollama", native_unit="request", capacity=1, capabilities=["text"])
    assert validate_resource_ledger(ledger_path) == (True, "Phase 6 resource ledger is readable")


def test_recovery_rejects_stale_resource_schema_version(tmp_path):
    ledger_path = tmp_path / "resources.sqlite3"
    ledger = ResourceLedger(ledger_path)
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    ledger.connection.execute("UPDATE resource_schema_meta SET value='3' WHERE key='schema_version'")
    ledger.connection.commit()

    ok, detail = validate_resource_ledger(ledger_path)

    assert not ok
    assert "schema version" in detail


def test_recovery_rejects_invalid_budget_intent_binding(tmp_path):
    ledger_path = tmp_path / "resources.sqlite3"
    ledger = ResourceLedger(ledger_path)
    policy = BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20)
    BudgetAuthority.configure(ledger, policy)
    ledger.register_resource("paid", provider_id="remote", native_unit="request", capacity=1, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=1, health="healthy")
    reservation = BudgetGovernor(ledger, policy).reserve("task", "paid", estimated_cost_minor=10, intent_key="provider:request:paid")
    ledger.connection.execute("UPDATE budget_reservations SET intent_key='' WHERE reservation_id=?", (reservation.reservation_id,))
    ledger.connection.commit()

    ok, detail = validate_resource_ledger(ledger_path)

    assert not ok
    assert "intent binding" in detail


def test_recovery_rejects_orphan_native_resource_reservation(tmp_path):
    ledger_path = tmp_path / "resources.sqlite3"
    ledger = ResourceLedger(ledger_path)
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    ledger.register_resource("local", provider_id="ollama", native_unit="request", capacity=1, capabilities=["text"])
    ledger.connection.execute(
        "INSERT INTO resource_reservations(reservation_id, resource_id, native_units, status) VALUES ('orphan', 'local', 1, 'reserved')"
    )
    ledger.connection.commit()

    ok, detail = validate_resource_ledger(ledger_path)

    assert not ok
    assert "resource reservation" in detail


def test_recovery_rejects_budget_native_reservation_status_mismatch(tmp_path):
    ledger_path = tmp_path / "resources.sqlite3"
    ledger = ResourceLedger(ledger_path)
    policy = BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20)
    BudgetAuthority.configure(ledger, policy)
    ledger.register_resource("paid", provider_id="remote", native_unit="request", capacity=1, capabilities=["text"], cost_minor=10)
    ledger.observe("paid", available=1, health="healthy")
    reservation = BudgetGovernor(ledger, policy).reserve("task", "paid", estimated_cost_minor=10)
    ledger.connection.execute("UPDATE resource_reservations SET status='released' WHERE reservation_id=?", (reservation.reservation_id,))
    ledger.connection.commit()

    ok, detail = validate_resource_ledger(ledger_path)

    assert not ok
    assert "resource reservation" in detail
