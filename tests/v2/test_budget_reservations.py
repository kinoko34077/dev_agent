from concurrent.futures import ThreadPoolExecutor
import inspect
import multiprocessing
import os

import pytest

from src.dev_agent.domain.protocol import RecoveryTaskAuthority, Task
from src.dev_agent.resources.budget import BudgetAuthority, BudgetExceeded, BudgetGovernor, BudgetPolicy, ResourceUnavailable, UnknownPrice
from src.dev_agent.resources.ledger import BudgetPeriod, MoneyAmount, ResourceLedger, ResourcePrice, _BUDGET_ADMIN_TOKEN
from tests.v2.resource_test_support import governor, ledger, reserve_in_process


def test_resource_price_currency_is_normalized_before_budget_matching(tmp_path):
    resource_ledger = ledger(tmp_path)
    resource_ledger.register_resource("paid", provider_id="remote", native_unit="request", capacity=1, capabilities=["text"], cost_minor=10, price_currency="jpy")
    resource_ledger.observe("paid", available=1, health="healthy")
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0, currency="JPY"))

    reservation = resource_governor.reserve("task", "paid", estimated_cost_minor=10)

    assert reservation.reservation_id
    assert resource_ledger.get_resource("paid")["price_currency"] == "JPY"


def test_budget_configuration_rejects_invalid_currency_at_ledger_boundary(tmp_path):
    resource_ledger = ResourceLedger(tmp_path / "invalid-currency.sqlite3")

    with pytest.raises(ValueError, match="currency"):
        resource_ledger.configure_budget(hard_cap_minor=100, recovery_reserve_minor=0, currency="JPYX", period=BudgetPeriod("2026-09", "2026-09-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00"), _authority=_BUDGET_ADMIN_TOKEN)


def test_budget_configuration_cannot_drop_below_committed_or_abandon_unknown_reservations(tmp_path):
    resource_ledger = ledger(tmp_path)
    policy = BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20)
    resource_governor = governor(resource_ledger, policy)
    reservation = resource_governor.reserve("task", "remote-gemini", estimated_cost_minor=40)

    with pytest.raises(ValueError, match="below existing committed"):
        BudgetAuthority.configure(resource_ledger, BudgetPolicy(hard_cap_minor=30, recovery_reserve_minor=20, period=resource_governor.period))

    resource_governor.mark_unknown(reservation.reservation_id)
    next_period = BudgetPeriod("2026-10", "2026-10-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="reservations are active"):
        BudgetAuthority.configure(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20, period=next_period))


def test_budget_configuration_cannot_reinterpret_committed_amounts_in_another_currency(tmp_path):
    resource_ledger = ledger(tmp_path)
    policy = BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20, currency="JPY")
    resource_governor = governor(resource_ledger, policy)
    reservation = resource_governor.reserve("task", "remote-gemini", estimated_cost_minor=40)
    resource_governor.reconcile(reservation.reservation_id, actual_cost_minor=30)

    with pytest.raises(ValueError, match="currency"):
        BudgetAuthority.configure(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20, currency="USD", period=resource_governor.period))


def test_budget_reservation_is_atomic_under_concurrency(tmp_path):
    resource_ledger = ledger(tmp_path)
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))

    def reserve(index):
        try:
            return resource_governor.reserve(f"task-{index}", "remote-gemini", estimated_cost_minor=50).reservation_id
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(reserve, range(2)))
    assert [item for item in reservations if item] and sum(item is not None for item in reservations) == 1


def test_recovery_reserve_requires_authorized_task_class(tmp_path):
    resource_ledger = ledger(tmp_path)
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=40))
    normal_task = Task(objective="normal task")
    recovery_task = RecoveryTaskAuthority.create(objective="recovery task")

    with pytest.raises(PermissionError, match="BudgetAuthority"):
        resource_governor.reserve("task-recovery", "remote-gemini", estimated_cost_minor=30, recovery=True)
    with pytest.raises(PermissionError, match="task class"):
        BudgetAuthority.reserve_recovery(resource_governor, normal_task, "remote-gemini", estimated_cost_minor=30)

    reservation = BudgetAuthority.reserve_recovery(
        resource_governor,
        recovery_task,
        "remote-gemini",
        estimated_cost_minor=30,
    )
    assert reservation.recovery is True
    assert resource_governor.snapshot()["recovery_committed_minor"] == 30


def test_budget_admin_reads_only_protected_config_outside_agent_workspace(tmp_path):
    workspace = tmp_path / "agent-workspace"
    protected = tmp_path / "operator-config" / "budget.json"
    protected.parent.mkdir()
    protected.write_text('{"hard_cap_minor": 100, "recovery_reserve_minor": 25, "currency": "JPY"}', encoding="utf-8")
    resource_ledger = ResourceLedger(tmp_path / "protected-budget.sqlite3")

    BudgetAuthority.configure_from_protected_file(resource_ledger, protected, agent_root=workspace)
    assert resource_ledger.budget_config()["hard_cap_minor"] == 100
    with pytest.raises(PermissionError, match="outside"):
        BudgetAuthority.configure_from_protected_file(resource_ledger, workspace / "budget.json", agent_root=workspace)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('{"hard_cap_minor": true, "recovery_reserve_minor": 0}', "hard_cap_minor"),
        ('{"hard_cap_minor": 100, "recovery_reserve_minor": "25"}', "recovery_reserve_minor"),
        ('{"hard_cap_minor": 100, "recovery_reserve_minor": 25, "currency": 1}', "currency"),
        ('{"hard_cap_minor": 100, "recovery_reserve_minor": 25, "period": {"period_id": 1, "starts_at": "a", "ends_at": "b"}}', "period fields"),
    ],
)
def test_protected_budget_config_rejects_type_coercion(tmp_path, payload, message):
    protected = tmp_path / "operator-config" / "budget.json"
    protected.parent.mkdir()
    protected.write_text(payload, encoding="utf-8")
    resource_ledger = ResourceLedger(tmp_path / "protected-budget-types.sqlite3")

    with pytest.raises(ValueError, match=message):
        BudgetAuthority.configure_from_protected_file(resource_ledger, protected, agent_root=tmp_path / "agent")


def test_protected_budget_config_rejects_symlink(tmp_path):
    protected = tmp_path / "operator-config" / "budget.json"
    target = tmp_path / "real-budget.json"
    protected.parent.mkdir()
    target.write_text('{"hard_cap_minor": 100, "recovery_reserve_minor": 25}', encoding="utf-8")
    try:
        protected.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    resource_ledger = ResourceLedger(tmp_path / "protected-budget-link.sqlite3")

    with pytest.raises(PermissionError, match="symlink"):
        BudgetAuthority.configure_from_protected_file(resource_ledger, protected, agent_root=tmp_path / "agent")


@pytest.mark.skipif(os.name == "nt", reason="Windows ACLs are deployment-owned")
def test_protected_budget_config_rejects_group_or_world_writable_file(tmp_path):
    protected = tmp_path / "operator-config" / "budget.json"
    protected.parent.mkdir()
    protected.write_text('{"hard_cap_minor": 100, "recovery_reserve_minor": 25}', encoding="utf-8")
    protected.chmod(0o666)
    resource_ledger = ResourceLedger(tmp_path / "protected-budget-mode.sqlite3")

    with pytest.raises(PermissionError, match="group/world"):
        BudgetAuthority.configure_from_protected_file(resource_ledger, protected, agent_root=tmp_path / "agent")


def test_budget_reservation_is_atomic_across_independent_ledger_connections(tmp_path):
    first = ledger(tmp_path)
    second = ResourceLedger(tmp_path / "resources.sqlite3")
    first_governor = governor(first, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    second_governor = BudgetGovernor(second, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))

    def reserve(resource_governor, index):
        try:
            return resource_governor.reserve(f"task-{index}", "remote-gemini", estimated_cost=MoneyAmount("JPY", 50)).reservation_id
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(lambda pair: reserve(*pair), ((first_governor, 1), (second_governor, 2))))
    assert sum(item is not None for item in reservations) == 1


def test_budget_reservation_is_atomic_across_independent_processes(tmp_path):
    resource_ledger = ledger(tmp_path)
    BudgetAuthority.configure(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    path = str(tmp_path / "resources.sqlite3")
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    processes = [context.Process(target=reserve_in_process, args=(path, f"process-{index}", result_queue)) for index in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert sum(result_queue.get(timeout=2) is not None for _ in processes) == 1


def test_budget_rejects_unknown_price_and_preserves_recovery_reserve(tmp_path):
    resource_ledger = ledger(tmp_path)
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=30))
    with pytest.raises(UnknownPrice):
        resource_governor.reserve("task-unknown", "local-qwen", estimated_cost_minor=None)
    reservation = resource_governor.reserve("task-normal", "remote-gemini", estimated_cost_minor=70)
    assert reservation.reservation_id
    with pytest.raises(BudgetExceeded):
        resource_governor.reserve("task-normal-2", "remote-gemini", estimated_cost_minor=1)
    recovery = BudgetAuthority.reserve_recovery(resource_governor, RecoveryTaskAuthority.create(objective="task recovery"), "remote-gemini", estimated_cost_minor=30)
    assert recovery.recovery


def test_budget_reconciliation_persists_actual_usage(tmp_path):
    resource_ledger = ledger(tmp_path)
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    reservation = resource_governor.reserve("task-1", "remote-gemini", estimated_cost_minor=40)
    resource_governor.reconcile(reservation.reservation_id, actual_cost_minor=25)
    snapshot = resource_governor.snapshot()
    assert snapshot["normal_committed_minor"] == 25
    assert snapshot["active_reservations"] == 0


def test_unknown_provider_charge_remains_reserved_until_reconciliation(tmp_path):
    resource_ledger = ledger(tmp_path)
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    reservation = resource_governor.reserve("task-1", "remote-gemini", estimated_cost_minor=40)
    resource_governor.mark_unknown(reservation.reservation_id)
    assert resource_governor.snapshot()["active_reservations"] == 1
    resource_governor.reconcile(reservation.reservation_id, actual_cost_minor=40)
    assert resource_governor.snapshot()["active_reservations"] == 0


def test_budget_reservation_persists_dispatch_lifecycle(tmp_path):
    resource_ledger = ledger(tmp_path)
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    reservation = resource_governor.reserve("task-1", "remote-gemini", estimated_cost_minor=40)
    assert resource_ledger.reservation_row(reservation.reservation_id)["status"] == "prepared"

    resource_governor.mark_dispatching(reservation.reservation_id)
    assert resource_ledger.reservation_row(reservation.reservation_id)["status"] == "dispatching"

    resource_governor.confirm_no_charge(reservation.reservation_id)
    assert resource_ledger.reservation_row(reservation.reservation_id)["status"] == "confirmed_no_charge"
    assert resource_governor.snapshot()["active_reservations"] == 0


def test_budget_reservation_reuses_the_same_dispatch_intent_after_restart(tmp_path):
    first = ledger(tmp_path)
    policy = BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20)
    first_governor = governor(first, policy)
    intent_key = "provider:request-1:remote-gemini"
    first_reservation = first_governor.reserve("task-1", "remote-gemini", estimated_cost_minor=40, intent_key=intent_key)
    first_governor.mark_dispatching(first_reservation.reservation_id)

    second = ResourceLedger(tmp_path / "resources.sqlite3")
    second_governor = BudgetGovernor(second, policy)
    replay = second_governor.reserve("task-1", "remote-gemini", estimated_cost_minor=40, intent_key=intent_key)

    assert replay.reservation_id == first_reservation.reservation_id
    second_governor.mark_dispatching(replay.reservation_id)
    assert second.reservation_row(replay.reservation_id)["status"] == "dispatching"
    assert second.connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1


def test_budget_reservation_intent_cannot_be_rebound_to_different_charge(tmp_path):
    resource_ledger = ledger(tmp_path)
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    resource_governor.reserve("task-1", "remote-gemini", estimated_cost_minor=40, intent_key="provider:request-1:remote-gemini")

    with pytest.raises(BudgetExceeded, match="different budget reservation"):
        resource_governor.reserve("task-2", "remote-gemini", estimated_cost_minor=20, intent_key="provider:request-1:remote-gemini")


def test_budget_reservations_are_currency_and_period_bound(tmp_path):
    resource_ledger = ledger(tmp_path)
    period = BudgetPeriod("2026-09", "2026-09-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00")
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0, currency="JPY", period=period))
    reservation = resource_governor.reserve("task-1", "remote-gemini", estimated_cost=MoneyAmount("JPY", 40))
    assert reservation.estimated_cost == MoneyAmount("JPY", 40)
    assert resource_ledger.reservation_row(reservation.reservation_id)["period_id"] == "2026-09"
    with pytest.raises(BudgetExceeded, match="currency"):
        resource_governor.reserve("task-2", "remote-gemini", estimated_cost=MoneyAmount("USD", 1))


def test_budget_policy_rejects_invalid_currency_at_initialization(tmp_path):
    resource_ledger = ledger(tmp_path)

    with pytest.raises(ValueError, match="currency"):
        governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0, currency="JP"))


def test_budget_governor_cannot_overwrite_persisted_hard_cap(tmp_path):
    resource_ledger = ledger(tmp_path)
    policy = BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20)
    BudgetAuthority.configure(resource_ledger, policy)
    BudgetGovernor(resource_ledger, policy)

    with pytest.raises(ValueError, match="persisted budget"):
        BudgetGovernor(resource_ledger, BudgetPolicy(hard_cap_minor=999999, recovery_reserve_minor=0))

    assert resource_ledger.budget_config()["hard_cap_minor"] == 100


def test_budget_rollover_is_admin_only_and_preserves_caps(tmp_path):
    resource_ledger = ledger(tmp_path)
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    next_period = BudgetPeriod("2026-10", "2026-10-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00")

    BudgetAuthority.rollover(resource_ledger, next_period)

    config = resource_ledger.budget_config()
    assert config["period_id"] == "2026-10"
    assert config["hard_cap_minor"] == 100
    assert config["recovery_reserve_minor"] == 20
    assert BudgetGovernor(resource_ledger).period.period_id == "2026-10"
    assert resource_governor.period.period_id == "2026-09"

    with pytest.raises(BudgetExceeded, match="period is stale"):
        resource_governor.reserve("stale-task", "remote-gemini", estimated_cost_minor=1)
    with pytest.raises(BudgetExceeded, match="period is stale"):
        resource_governor.snapshot()


def test_budget_rollover_rejects_active_reservations_and_backwards_period(tmp_path):
    resource_ledger = ledger(tmp_path)
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    resource_governor.reserve("task", "remote-gemini", estimated_cost_minor=10)
    next_period = BudgetPeriod("2026-10", "2026-10-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="active"):
        BudgetAuthority.rollover(resource_ledger, next_period)

    previous = BudgetPeriod("2026-08", "2026-08-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="after"):
        BudgetAuthority.rollover(resource_ledger, previous)


def test_missing_actual_cost_is_held_unknown_not_estimated(tmp_path):
    resource_ledger = ledger(tmp_path)
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0))
    reservation = resource_governor.reserve("task-1", "remote-gemini", estimated_cost=MoneyAmount("JPY", 40))
    resource_governor.mark_unknown(reservation.reservation_id)
    row = resource_ledger.reservation_row(reservation.reservation_id)
    assert row["status"] == "unknown"
    assert row["actual_minor"] is None


def test_budget_governor_uses_ledger_transaction_api_not_private_sqlite_state():
    from src.dev_agent.resources.budget import BudgetGovernor

    source = inspect.getsource(BudgetGovernor)
    assert "ledger.connection" not in source
    assert "ledger._lock" not in source


def test_resource_price_is_currency_bound_and_explicitly_unknown_when_unbounded():
    assert ResourcePrice("JPY", MoneyAmount("JPY", 50)).worst_case == MoneyAmount("JPY", 50)
    assert ResourcePrice("JPY", None).worst_case is None
    with pytest.raises(ValueError):
        ResourcePrice("USD", MoneyAmount("JPY", 50))


def test_native_units_are_reserved_and_released_with_budget_lifecycle(tmp_path):
    resource_ledger = ledger(tmp_path)
    resource_ledger.observe("remote-gemini", available=1, health="healthy")
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0))
    first = resource_governor.reserve("task-1", "remote-gemini", estimated_cost=MoneyAmount("JPY", 10), native_units=1)
    with pytest.raises(ResourceUnavailable):
        resource_governor.reserve("task-2", "remote-gemini", estimated_cost=MoneyAmount("JPY", 10), native_units=1)
    resource_governor.release(first.reservation_id)
    assert resource_governor.reserve("task-3", "remote-gemini", estimated_cost=MoneyAmount("JPY", 10), native_units=1).reservation_id


@pytest.mark.parametrize("native_units", [float("nan"), float("inf")])
def test_budget_rejects_non_finite_native_units(tmp_path, native_units):
    resource_ledger = ledger(tmp_path)
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0))
    with pytest.raises(ValueError, match="native_units"):
        resource_governor.reserve("task-invalid", "remote-gemini", estimated_cost=MoneyAmount("JPY", 10), native_units=native_units)
