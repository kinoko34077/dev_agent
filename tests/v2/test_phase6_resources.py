from concurrent.futures import ThreadPoolExecutor
import inspect
import multiprocessing
import sqlite3

import pytest

from src.dev_agent.resources.budget import BudgetAuthority, BudgetExceeded, BudgetGovernor, BudgetPolicy, ResourceUnavailable, UnknownPrice
from src.dev_agent.resources.ledger import BudgetPeriod, MoneyAmount, ResourceLedger, ResourcePrice, _BUDGET_ADMIN_TOKEN
from src.dev_agent.domain.protocol import RecoveryTaskAuthority, Task, TaskClass


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
    assert config["period_id"] != "legacy"
    assert config["period_starts_at"] < config["period_ends_at"]
    assert version == "4"


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


def _governor(ledger, policy):
    BudgetAuthority.configure(ledger, policy)
    return BudgetGovernor(ledger, policy)


def _reserve_in_process(path, task_id, result_queue):
    ledger = ResourceLedger(path)
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    try:
        result_queue.put(governor.reserve(task_id, "remote-gemini", estimated_cost=MoneyAmount("JPY", 50)).reservation_id)
    except BudgetExceeded:
        result_queue.put(None)


def test_resource_ledger_persists_native_unit_observations(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.observe("local-qwen", available=87, health="degraded", confidence=0.8)
    reopened = ResourceLedger(tmp_path / "resources.sqlite3")
    resource = reopened.get_resource("local-qwen")
    assert resource["native_unit"] == "request"
    assert resource["available"] == 87
    assert resource["health"] == "degraded"
    assert resource["confidence"] == pytest.approx(0.8)


def test_resource_observation_cannot_exceed_registered_capacity(tmp_path):
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    ledger.register_resource("small", provider_id="local", native_unit="request", capacity=1, capabilities=["text"])
    with pytest.raises(ValueError, match="exceeds resource capacity"):
        ledger.observe("small", available=2, health="healthy")


def test_resource_price_currency_is_normalized_before_budget_matching(tmp_path):
    ledger = ResourceLedger(tmp_path / "currency-normalization.sqlite3")
    ledger.register_resource("paid", provider_id="remote", native_unit="request", capacity=1, capabilities=["text"], cost_minor=10, price_currency="jpy")
    ledger.observe("paid", available=1, health="healthy")
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=20, recovery_reserve_minor=0, currency="JPY"))

    reservation = governor.reserve("task", "paid", estimated_cost_minor=10)

    assert reservation.reservation_id
    assert ledger.get_resource("paid")["price_currency"] == "JPY"


def test_budget_configuration_rejects_invalid_currency_at_ledger_boundary(tmp_path):
    ledger = ResourceLedger(tmp_path / "invalid-currency.sqlite3")

    with pytest.raises(ValueError, match="currency"):
        ledger.configure_budget(hard_cap_minor=100, recovery_reserve_minor=0, currency="JPYX", period=BudgetPeriod("2026-09", "2026-09-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00"), _authority=_BUDGET_ADMIN_TOKEN)


def test_budget_configuration_cannot_drop_below_committed_or_abandon_unknown_reservations(tmp_path):
    ledger = _ledger(tmp_path)
    policy = BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20)
    governor = _governor(ledger, policy)
    reservation = governor.reserve("task", "remote-gemini", estimated_cost_minor=40)

    with pytest.raises(ValueError, match="below existing committed"):
        BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=30, recovery_reserve_minor=20, period=governor.period))

    governor.mark_unknown(reservation.reservation_id)
    next_period = BudgetPeriod("2026-10", "2026-10-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="reservations are active"):
        BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20, period=next_period))


def test_budget_configuration_cannot_reinterpret_committed_amounts_in_another_currency(tmp_path):
    ledger = _ledger(tmp_path)
    policy = BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20, currency="JPY")
    governor = _governor(ledger, policy)
    reservation = governor.reserve("task", "remote-gemini", estimated_cost_minor=40)
    governor.reconcile(reservation.reservation_id, actual_cost_minor=30)

    with pytest.raises(ValueError, match="currency"):
        BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20, currency="USD", period=governor.period))


def test_budget_reservation_is_atomic_under_concurrency(tmp_path):
    ledger = _ledger(tmp_path)
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))

    def reserve(index):
        try:
            return governor.reserve(f"task-{index}", "remote-gemini", estimated_cost_minor=50).reservation_id
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(reserve, range(2)))
    assert [item for item in reservations if item] and sum(item is not None for item in reservations) == 1


def test_recovery_reserve_requires_authorized_task_class(tmp_path):
    ledger = _ledger(tmp_path)
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=40))
    normal_task = Task(objective="normal task")
    recovery_task = RecoveryTaskAuthority.create(objective="recovery task")

    with pytest.raises(PermissionError, match="BudgetAuthority"):
        governor.reserve("task-recovery", "remote-gemini", estimated_cost_minor=30, recovery=True)
    with pytest.raises(PermissionError, match="task class"):
        BudgetAuthority.reserve_recovery(governor, normal_task, "remote-gemini", estimated_cost_minor=30)

    reservation = BudgetAuthority.reserve_recovery(
        governor,
        recovery_task,
        "remote-gemini",
        estimated_cost_minor=30,
    )
    assert reservation.recovery is True
    assert governor.snapshot()["recovery_committed_minor"] == 30


def test_budget_admin_reads_only_protected_config_outside_agent_workspace(tmp_path):
    workspace = tmp_path / "agent-workspace"
    protected = tmp_path / "operator-config" / "budget.json"
    protected.parent.mkdir()
    protected.write_text('{"hard_cap_minor": 100, "recovery_reserve_minor": 25, "currency": "JPY"}', encoding="utf-8")
    ledger = ResourceLedger(tmp_path / "protected-budget.sqlite3")

    BudgetAuthority.configure_from_protected_file(ledger, protected, agent_root=workspace)
    assert ledger.budget_config()["hard_cap_minor"] == 100
    with pytest.raises(PermissionError, match="outside"):
        BudgetAuthority.configure_from_protected_file(ledger, workspace / "budget.json", agent_root=workspace)


def test_budget_reservation_is_atomic_across_independent_ledger_connections(tmp_path):
    first = _ledger(tmp_path)
    second = ResourceLedger(tmp_path / "resources.sqlite3")
    first_governor = _governor(first, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    second_governor = BudgetGovernor(second, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))

    def reserve(governor, index):
        try:
            return governor.reserve(f"task-{index}", "remote-gemini", estimated_cost=MoneyAmount("JPY", 50)).reservation_id
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(lambda pair: reserve(*pair), ((first_governor, 1), (second_governor, 2))))
    assert sum(item is not None for item in reservations) == 1


def test_budget_reservation_is_atomic_across_independent_processes(tmp_path):
    ledger = _ledger(tmp_path)
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    path = str(tmp_path / "resources.sqlite3")
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    processes = [context.Process(target=_reserve_in_process, args=(path, f"process-{index}", result_queue)) for index in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert sum(result_queue.get(timeout=2) is not None for _ in processes) == 1


def test_budget_rejects_unknown_price_and_preserves_recovery_reserve(tmp_path):
    ledger = _ledger(tmp_path)
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=30))
    with pytest.raises(UnknownPrice):
        governor.reserve("task-unknown", "local-qwen", estimated_cost_minor=None)
    reservation = governor.reserve("task-normal", "remote-gemini", estimated_cost_minor=70)
    assert reservation.reservation_id
    with pytest.raises(BudgetExceeded):
        governor.reserve("task-normal-2", "remote-gemini", estimated_cost_minor=1)
    recovery = BudgetAuthority.reserve_recovery(governor, RecoveryTaskAuthority.create(objective="task recovery"), "remote-gemini", estimated_cost_minor=30)
    assert recovery.recovery


def test_budget_reconciliation_persists_actual_usage(tmp_path):
    ledger = _ledger(tmp_path)
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    reservation = governor.reserve("task-1", "remote-gemini", estimated_cost_minor=40)
    governor.reconcile(reservation.reservation_id, actual_cost_minor=25)
    snapshot = governor.snapshot()
    assert snapshot["normal_committed_minor"] == 25
    assert snapshot["active_reservations"] == 0


def test_unknown_provider_charge_remains_reserved_until_reconciliation(tmp_path):
    ledger = _ledger(tmp_path)
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    reservation = governor.reserve("task-1", "remote-gemini", estimated_cost_minor=40)
    governor.mark_unknown(reservation.reservation_id)
    assert governor.snapshot()["active_reservations"] == 1
    governor.reconcile(reservation.reservation_id, actual_cost_minor=40)
    assert governor.snapshot()["active_reservations"] == 0


def test_budget_reservation_persists_dispatch_lifecycle(tmp_path):
    ledger = _ledger(tmp_path)
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    reservation = governor.reserve("task-1", "remote-gemini", estimated_cost_minor=40)
    assert ledger.reservation_row(reservation.reservation_id)["status"] == "prepared"

    governor.mark_dispatching(reservation.reservation_id)
    assert ledger.reservation_row(reservation.reservation_id)["status"] == "dispatching"

    governor.confirm_no_charge(reservation.reservation_id)
    assert ledger.reservation_row(reservation.reservation_id)["status"] == "confirmed_no_charge"
    assert governor.snapshot()["active_reservations"] == 0


def test_budget_reservation_reuses_the_same_dispatch_intent_after_restart(tmp_path):
    first = _ledger(tmp_path)
    policy = BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20)
    first_governor = _governor(first, policy)
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
    ledger = _ledger(tmp_path)
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    governor.reserve("task-1", "remote-gemini", estimated_cost_minor=40, intent_key="provider:request-1:remote-gemini")

    with pytest.raises(BudgetExceeded, match="different budget reservation"):
        governor.reserve("task-2", "remote-gemini", estimated_cost_minor=20, intent_key="provider:request-1:remote-gemini")


def test_budget_reservations_are_currency_and_period_bound(tmp_path):
    ledger = _ledger(tmp_path)
    period = BudgetPeriod("2026-09", "2026-09-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00")
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0, currency="JPY", period=period))
    reservation = governor.reserve("task-1", "remote-gemini", estimated_cost=MoneyAmount("JPY", 40))
    assert reservation.estimated_cost == MoneyAmount("JPY", 40)
    assert ledger.reservation_row(reservation.reservation_id)["period_id"] == "2026-09"
    with pytest.raises(BudgetExceeded, match="currency"):
        governor.reserve("task-2", "remote-gemini", estimated_cost=MoneyAmount("USD", 1))


def test_budget_policy_rejects_invalid_currency_at_initialization(tmp_path):
    ledger = _ledger(tmp_path)

    with pytest.raises(ValueError, match="currency"):
        _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0, currency="JP"))


def test_budget_governor_cannot_overwrite_persisted_hard_cap(tmp_path):
    ledger = _ledger(tmp_path)
    policy = BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20)
    BudgetAuthority.configure(ledger, policy)
    BudgetGovernor(ledger, policy)

    with pytest.raises(ValueError, match="persisted budget"):
        BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=999999, recovery_reserve_minor=0))

    assert ledger.budget_config()["hard_cap_minor"] == 100


def test_missing_actual_cost_is_held_unknown_not_estimated(tmp_path):
    ledger = _ledger(tmp_path)
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0))
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


def test_resource_price_is_currency_bound_and_explicitly_unknown_when_unbounded():
    assert ResourcePrice("JPY", MoneyAmount("JPY", 50)).worst_case == MoneyAmount("JPY", 50)
    assert ResourcePrice("JPY", None).worst_case is None
    with pytest.raises(ValueError):
        ResourcePrice("USD", MoneyAmount("JPY", 50))


def test_native_units_are_reserved_and_released_with_budget_lifecycle(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.observe("remote-gemini", available=1, health="healthy")
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0))
    first = governor.reserve("task-1", "remote-gemini", estimated_cost=MoneyAmount("JPY", 10), native_units=1)
    with pytest.raises(ResourceUnavailable):
        governor.reserve("task-2", "remote-gemini", estimated_cost=MoneyAmount("JPY", 10), native_units=1)
    governor.release(first.reservation_id)
    assert governor.reserve("task-3", "remote-gemini", estimated_cost=MoneyAmount("JPY", 10), native_units=1).reservation_id


@pytest.mark.parametrize("native_units", [float("nan"), float("inf")])
def test_budget_rejects_non_finite_native_units(tmp_path, native_units):
    ledger = _ledger(tmp_path)
    governor = _governor(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0))
    with pytest.raises(ValueError, match="native_units"):
        governor.reserve("task-invalid", "remote-gemini", estimated_cost=MoneyAmount("JPY", 10), native_units=native_units)
