import pytest

from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import DispatchDenied, ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter


def _governor(ledger, policy):
    BudgetAuthority.configure(ledger, policy)
    return BudgetGovernor(ledger, policy)


def test_maintenance_mode_denies_new_provider_reservations(tmp_path):
    ledger = ResourceLedger(tmp_path / "maintenance.sqlite3")
    ledger.register_resource("free", provider_id="free", native_unit="request", capacity=10, capabilities=["text"], cost_minor=0)
    ledger.observe("free", available=10, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    control.set_maintenance(True)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])
    with pytest.raises(DispatchDenied) as exc:
        control.reserve_for_provider("task-1", "free", request)
    assert exc.value.category == "maintenance"


def test_runtime_maintenance_is_shared_across_ledger_connections(tmp_path):
    path = tmp_path / "shared-maintenance.sqlite3"
    first = ResourceLedger(path)
    first.register_resource("free", provider_id="free", native_unit="request", capacity=10, capabilities=["text"], cost_minor=0)
    first.observe("free", available=10, health="healthy")
    first_control = ResourceControlPlane(ResourceRouter(first), _governor(first, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    second = ResourceLedger(path)
    second_control = ResourceControlPlane(ResourceRouter(second), BudgetGovernor(second, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    first_control.set_maintenance(True)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000005", messages=[{"role": "user", "content": "x"}])

    with pytest.raises(DispatchDenied) as exc:
        second_control.reserve_for_provider(request.task_id, "free", request)

    assert exc.value.category == "maintenance"
    first_control.set_maintenance(False)
    assert second_control.reserve_for_provider(request.task_id, "free", request).budget.reservation_id


def test_native_capacity_denial_has_unavailable_category(tmp_path):
    ledger = ResourceLedger(tmp_path / "capacity-category.sqlite3")
    ledger.register_resource("tiny", provider_id="tiny", native_unit="request", capacity=1, capabilities=["text"], cost_minor=0)
    ledger.observe("tiny", available=1, health="healthy")
    control = ResourceControlPlane(ResourceRouter(ledger), _governor(ledger, BudgetPolicy(hard_cap_minor=10, recovery_reserve_minor=0)))
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "x"}])
    control.reserve_for_provider("task-1", "tiny", request)
    with pytest.raises(DispatchDenied) as exc:
        control.reserve_for_provider("task-2", "tiny", request)
    assert exc.value.category == "unavailable"
