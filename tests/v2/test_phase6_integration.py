from src.dev_agent.resources.budget import BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.domain.protocol import Task
from src.dev_agent.providers.fake.provider import FakeProvider
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from src.dev_agent.tools.registry import ToolRegistry, ToolSpec
from src.dev_agent.tools.runtime import ToolRuntime
from src.dev_agent.runtime.controller import Controller


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
