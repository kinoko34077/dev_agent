from concurrent.futures import ThreadPoolExecutor
import inspect

import pytest

from src.dev_agent.resources.budget import BudgetExceeded, BudgetGovernor, BudgetPolicy, UnknownPrice
from src.dev_agent.resources.ledger import BudgetPeriod, MoneyAmount, ResourceLedger


def _ledger(tmp_path):
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    ledger.register_resource(
        "local-qwen",
        provider_id="ollama",
        native_unit="request",
        capacity=100,
        capabilities=["text", "tool_call"],
        sensitivity="sensitive",
        cost_minor=0,
    )
    ledger.register_resource(
        "remote-gemini",
        provider_id="gemini",
        native_unit="request",
        capacity=100,
        capabilities=["text", "tool_call"],
        sensitivity="internal",
        cost_minor=50,
    )
    ledger.observe("local-qwen", available=90, health="healthy")
    ledger.observe("remote-gemini", available=90, health="healthy")
    return ledger


def test_resource_ledger_persists_native_unit_observations(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.observe("local-qwen", available=87, health="degraded", confidence=0.8)
    reopened = ResourceLedger(tmp_path / "resources.sqlite3")
    resource = reopened.get_resource("local-qwen")
    assert resource["native_unit"] == "request"
    assert resource["available"] == 87
    assert resource["health"] == "degraded"
    assert resource["confidence"] == pytest.approx(0.8)


def test_budget_reservation_is_atomic_under_concurrency(tmp_path):
    ledger = _ledger(tmp_path)
    governor = BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))

    def reserve(index):
        try:
            return governor.reserve(f"task-{index}", "remote-gemini", estimated_cost_minor=50).reservation_id
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(reserve, range(2)))
    assert [item for item in reservations if item] and sum(item is not None for item in reservations) == 1


def test_budget_reservation_is_atomic_across_independent_ledger_connections(tmp_path):
    first = _ledger(tmp_path)
    second = ResourceLedger(tmp_path / "resources.sqlite3")
    first_governor = BudgetGovernor(first, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    second_governor = BudgetGovernor(second, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))

    def reserve(governor, index):
        try:
            return governor.reserve(f"task-{index}", "remote-gemini", estimated_cost=MoneyAmount("JPY", 50)).reservation_id
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(lambda pair: reserve(*pair), ((first_governor, 1), (second_governor, 2))))
    assert sum(item is not None for item in reservations) == 1


def test_budget_rejects_unknown_price_and_preserves_recovery_reserve(tmp_path):
    ledger = _ledger(tmp_path)
    governor = BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=30))
    with pytest.raises(UnknownPrice):
        governor.reserve("task-unknown", "local-qwen", estimated_cost_minor=None)
    reservation = governor.reserve("task-normal", "remote-gemini", estimated_cost_minor=70)
    assert reservation.reservation_id
    with pytest.raises(BudgetExceeded):
        governor.reserve("task-normal-2", "remote-gemini", estimated_cost_minor=1)
    recovery = governor.reserve("task-recovery", "remote-gemini", estimated_cost_minor=30, recovery=True)
    assert recovery.recovery


def test_budget_reconciliation_persists_actual_usage(tmp_path):
    ledger = _ledger(tmp_path)
    governor = BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    reservation = governor.reserve("task-1", "remote-gemini", estimated_cost_minor=40)
    governor.reconcile(reservation.reservation_id, actual_cost_minor=25)
    snapshot = governor.snapshot()
    assert snapshot["normal_committed_minor"] == 25
    assert snapshot["active_reservations"] == 0


def test_unknown_provider_charge_remains_reserved_until_reconciliation(tmp_path):
    ledger = _ledger(tmp_path)
    governor = BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    reservation = governor.reserve("task-1", "remote-gemini", estimated_cost_minor=40)
    governor.mark_unknown(reservation.reservation_id)
    assert governor.snapshot()["active_reservations"] == 1
    governor.reconcile(reservation.reservation_id, actual_cost_minor=40)
    assert governor.snapshot()["active_reservations"] == 0


def test_budget_reservations_are_currency_and_period_bound(tmp_path):
    ledger = _ledger(tmp_path)
    period = BudgetPeriod("2026-09", "2026-09-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00")
    governor = BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0, currency="JPY", period=period))
    reservation = governor.reserve("task-1", "remote-gemini", estimated_cost=MoneyAmount("JPY", 40))
    assert reservation.estimated_cost == MoneyAmount("JPY", 40)
    assert ledger.reservation_row(reservation.reservation_id)["period_id"] == "2026-09"
    with pytest.raises(BudgetExceeded, match="currency"):
        governor.reserve("task-2", "remote-gemini", estimated_cost=MoneyAmount("USD", 1))


def test_missing_actual_cost_is_held_unknown_not_estimated(tmp_path):
    ledger = _ledger(tmp_path)
    governor = BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0))
    reservation = governor.reserve("task-1", "remote-gemini", estimated_cost=MoneyAmount("JPY", 40))
    governor.mark_unknown(reservation.reservation_id)
    row = ledger.reservation_row(reservation.reservation_id)
    assert row["status"] == "unknown"
    assert row["actual_minor"] is None


def test_budget_governor_uses_ledger_transaction_api_not_private_sqlite_state():
    from src.dev_agent.resources.budget import BudgetGovernor

    source = inspect.getsource(BudgetGovernor)
    assert "ledger.connection" not in source
    assert "ledger._lock" not in source
