import pytest

from src.dev_agent.resources.budget import BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.control import DispatchDenied
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.domain.protocol import Task
from src.dev_agent.providers.fake.provider import FakeProvider
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from src.dev_agent.tools.registry import ToolRegistry, ToolSpec
from src.dev_agent.tools.runtime import ToolRuntime
from src.dev_agent.runtime.controller import Controller
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse


def test_phase6_control_plane_reserves_before_paid_dispatch(tmp_path):
    ledger = ResourceLedger(tmp_path / "phase6.sqlite3")
    ledger.register_resource("paid", provider_id="remote", native_unit="request", capacity=10, capabilities=["text"], sensitivity="internal", cost_minor=50)
    ledger.observe("paid", available=10, health="healthy")
    governor = BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=50, recovery_reserve_minor=10))
    reservation = governor.reserve("task-1", "paid", estimated_cost_minor=40)
    assert governor.snapshot()["active_reservations"] == 1
    governor.reconcile(reservation.reservation_id, actual_cost_minor=40)
    assert governor.snapshot()["normal_committed_minor"] == 40


def test_controller_reserves_and_reconciles_before_each_provider_dispatch(tmp_path):
    ledger = ResourceLedger(tmp_path / "controller.sqlite3")
    ledger.register_resource("fake-resource", provider_id="fake", native_unit="request", capacity=10, capabilities=["text"], sensitivity="normal", cost_minor=10)
    ledger.observe("fake-resource", available=10, health="healthy")
    governor = BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=30, recovery_reserve_minor=5))
    control = ResourceControlPlane(ResourceRouter(ledger), governor)
    registry = ToolRegistry()
    registry.register(ToolSpec(name="echo", description="echo", handler=lambda args: args))
    task = Task(objective="phase 6", limits={"max_cost": 25})
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(FakeProvider(), ToolRuntime(registry), store, resource_policy=control).run(task)
        assert result.status.value == "completed"
    assert governor.snapshot()["normal_committed_minor"] == 20


def test_control_never_converts_generic_float_ceiling_and_holds_missing_charge_unknown(tmp_path):
    ledger = ResourceLedger(tmp_path / "control.sqlite3")
    ledger.register_resource("paid", provider_id="remote", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10, price_currency="JPY")
    ledger.observe("paid", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0)))
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}], cost_ceiling=0.5)
    reservation = control.reserve_for_provider("task-1", "remote", request)
    control.reconcile_response(reservation, ModelResponse(provider="remote", model="test", usage={}))
    assert ledger.reservation_row(reservation.budget.reservation_id)["status"] == "unknown"
    ledger.register_resource("usd", provider_id="usd", native_unit="request", capacity=10, capabilities=["text"], cost_minor=10, price_currency="USD")
    ledger.observe("usd", available=10, health="healthy")
    with pytest.raises(DispatchDenied) as exc:
        control.reserve_for_provider("task-2", "usd", request)
    assert exc.value.category == "budget"
