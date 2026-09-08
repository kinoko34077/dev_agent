import json

import pytest

from recovery.phase6_recovery import RecoveryOperator
from recovery.validate_resources import validate_resource_ledger
from src.dev_agent.resources.ledger import ResourceLedger


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
    ledger.configure_budget(hard_cap_minor=100, recovery_reserve_minor=20, currency="JPY")
    ledger.register_resource("local", provider_id="ollama", native_unit="request", capacity=1, capabilities=["text"])
    assert validate_resource_ledger(ledger_path) == (True, "Phase 6 resource ledger is readable")
