import multiprocessing
from concurrent.futures import ThreadPoolExecutor

from src.dev_agent.domain.protocol import RecoveryTaskAuthority
from src.dev_agent.resources.budget import BudgetAuthority, BudgetExceeded, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.ledger import MoneyAmount, ResourceLedger


def ledger(tmp_path):
    resource_ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    resource_ledger.register_resource(
        "local-qwen",
        provider_id="ollama",
        native_unit="request",
        capacity=100,
        capabilities=["text", "tool_call"],
        sensitivity="sensitive",
        cost_minor=0,
    )
    resource_ledger.register_resource(
        "remote-gemini",
        provider_id="gemini",
        native_unit="request",
        capacity=100,
        capabilities=["text", "tool_call"],
        sensitivity="internal",
        cost_minor=50,
    )
    resource_ledger.observe("local-qwen", available=90, health="healthy")
    resource_ledger.observe("remote-gemini", available=90, health="healthy")
    return resource_ledger


def governor(resource_ledger, policy):
    BudgetAuthority.configure(resource_ledger, policy)
    return BudgetGovernor(resource_ledger, policy)


def reserve_in_process(path, task_id, result_queue):
    resource_ledger = ResourceLedger(path)
    resource_governor = governor(resource_ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=20))
    try:
        result_queue.put(resource_governor.reserve(task_id, "remote-gemini", estimated_cost=MoneyAmount("JPY", 50)).reservation_id)
    except BudgetExceeded:
        result_queue.put(None)
