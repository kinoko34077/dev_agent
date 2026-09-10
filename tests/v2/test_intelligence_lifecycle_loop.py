import pytest

from src.dev_agent.domain.protocol import IntelligenceTier, ModelRequest, Task, TaskStatus
from src.dev_agent.intelligence.coordination import EvaluationCoordinator
from src.dev_agent.intelligence.escalation import EscalationContext
from src.dev_agent.intelligence.evaluator import EvaluationEvidence
from src.dev_agent.intelligence.execution import EscalationExecutor
from src.dev_agent.intelligence.lifecycle import TaskLifecycleCoordinator
from src.dev_agent.intelligence.lifecycle_loop import FiniteLifecycleLoop, LifecycleLimitExceeded
from src.dev_agent.intelligence.loop import EvaluationDispatchCoordinator, EvaluationDispatchStatus
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
from src.dev_agent.providers.fake.provider import FakeProvider
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.state import JsonStateStore


_TASK_ID = "00000000-0000-0000-0000-000000000041"


def _evidence(**overrides):
    values = {
        "task_id": _TASK_ID,
        "attempt": 1,
        "max_attempts": 3,
        "objective_met": False,
        "deterministic_checks_passed": False,
        "policy_compliant": True,
        "external_outcome_known": True,
        "retryable_failure": True,
        "alternate_provider_available": False,
        "human_approval_required": False,
    }
    values.update(overrides)
    return EvaluationEvidence(**values)


def _context(**overrides):
    values = {
        "task_id": _TASK_ID,
        "current_tier": IntelligenceTier.L1,
        "allowed_tiers": (IntelligenceTier.L1,),
        "attempt": 1,
        "max_attempts": 3,
        "escalation_count": 0,
        "max_escalations": 1,
        "now_epoch": 100.0,
        "deadline_epoch": 200.0,
        "budget_remaining": 1.0,
        "estimated_cost": 0.0,
        "retryable_failure": True,
        "same_provider_available": True,
        "alternate_provider_available": False,
    }
    values.update(overrides)
    return EscalationContext(**values)


def _setup(tmp_path, *, max_cycles=2):
    provider = FakeProvider()
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    ledger.register_resource(
        "fake",
        provider_id="fake",
        provider_binding_id="fake",
        native_unit="request",
        capacity=10,
        capabilities=["text"],
        cost_minor=0,
        intelligence_tier="L1",
    )
    ledger.observe("fake", available=10, health="healthy")
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0))
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger))
    dispatcher = ProviderDispatcher(ProviderRegistry([provider]), control)
    store = JsonStateStore(tmp_path / "state.json")
    dispatcher.bind_runtime(state_store=store)
    store.save_task(Task(task_id=_TASK_ID, objective="finite lifecycle", status=TaskStatus.FAILED))
    evaluation = EvaluationCoordinator(store)
    dispatch = EvaluationDispatchCoordinator(
        store,
        evaluation_coordinator=evaluation,
        executor=EscalationExecutor(store, dispatcher),
    )
    return store, ledger, evaluation, FiniteLifecycleLoop(dispatch, TaskLifecycleCoordinator(store), max_cycles=max_cycles)


def test_finite_lifecycle_applies_terminal_evaluation_and_stops_at_ceiling(tmp_path):
    store, ledger, _evaluation, loop = _setup(tmp_path, max_cycles=1)
    step = loop.evaluate_and_apply(_evidence(objective_met=True, deterministic_checks_passed=True))

    assert step.phase == "evaluation"
    assert step.dispatch_cycle.status is EvaluationDispatchStatus.TERMINAL
    assert step.transition.task.status is TaskStatus.COMPLETED
    assert loop.evaluations_used == 1
    with pytest.raises(LifecycleLimitExceeded):
        loop.evaluate_and_apply(_evidence(objective_met=True, deterministic_checks_passed=True))
    ledger.close()


def test_finite_lifecycle_requires_review_before_dispatch_and_applies_both_boundaries(tmp_path):
    store, ledger, evaluation, loop = _setup(tmp_path)
    waiting = loop.evaluate_and_apply(_evidence(), escalation_context=_context())

    assert waiting.transition.task.status is TaskStatus.READY
    assert waiting.dispatch_cycle.status is EvaluationDispatchStatus.AWAITING_REVIEW
    review = evaluation.review_plan(
        waiting.dispatch_cycle.evaluation,
        actor="operator",
        approved=True,
        approval_reference="finite-loop-review",
    )
    dispatched = loop.dispatch_and_apply(
        waiting,
        review,
        model_request=ModelRequest(task_id=_TASK_ID, messages=[{"role": "user", "content": "retry"}]),
        provider_binding_id="fake",
    )

    assert dispatched.phase == "dispatch"
    assert dispatched.dispatch_cycle.status is EvaluationDispatchStatus.DISPATCHED
    assert dispatched.transition.task.status is TaskStatus.RUNNING
    assert loop.evaluations_used == 1
    ledger.close()
