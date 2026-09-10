import pytest

from src.dev_agent.domain.protocol import IntelligenceTier, ModelRequest, ModelResponse, Task, TaskStatus
from src.dev_agent.intelligence.coordination import EvaluationCoordinator
from src.dev_agent.intelligence.escalation import EscalationContext
from src.dev_agent.intelligence.evaluator import EvaluationEvidence, EvaluatorDecision
from src.dev_agent.intelligence.execution import EscalationExecutor
from src.dev_agent.intelligence.lifecycle import TaskLifecycleCoordinator
from src.dev_agent.intelligence.loop import EvaluationDispatchCoordinator, EvaluationDispatchStatus
from src.dev_agent.providers.base import ModelProvider, ProviderError
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.state import JsonStateStore


_TASK_ID = "00000000-0000-0000-0000-000000000031"


class RecordingProvider(ModelProvider):
    provider_id = "primary"

    def __init__(self) -> None:
        self.provider_binding_id = "primary"
        self.model = "primary-model"
        self.requests = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            provider=self.provider_id,
            model=self.model,
            text_segments=["retry succeeded"],
            usage={"cost_minor": 0},
        )


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


def _request() -> ModelRequest:
    return ModelRequest(task_id=_TASK_ID, messages=[{"role": "user", "content": "retry"}])


def _setup(tmp_path):
    provider = RecordingProvider()
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    ledger.register_resource(
        "primary",
        provider_id="primary",
        provider_binding_id="primary",
        native_unit="request",
        capacity=10,
        capabilities=["text"],
        cost_minor=0,
        intelligence_tier="L1",
    )
    ledger.observe("primary", available=10, health="healthy")
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0))
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger))
    dispatcher = ProviderDispatcher(ProviderRegistry([provider]), control)
    store = JsonStateStore(tmp_path / "state.json")
    dispatcher.bind_runtime(state_store=store)
    store.save_task(Task(task_id=_TASK_ID, objective="bounded evaluation loop", status=TaskStatus.FAILED))
    return store, ledger, provider, dispatcher


def test_evaluation_dispatch_cycle_waits_for_review_then_dispatches_once(tmp_path):
    store, ledger, provider, dispatcher = _setup(tmp_path)
    evaluation = EvaluationCoordinator(store)
    loop = EvaluationDispatchCoordinator(
        store,
        evaluation_coordinator=evaluation,
        executor=EscalationExecutor(store, dispatcher),
    )

    waiting = loop.evaluate(_evidence(), escalation_context=_context())

    assert waiting.status is EvaluationDispatchStatus.AWAITING_REVIEW
    assert waiting.evaluation.result.decision is EvaluatorDecision.RETRY_SAME
    assert waiting.dispatch_request is None
    assert not provider.requests
    assert not store.has_event(_TASK_ID, "escalation.dispatch_ready")

    review = evaluation.review_plan(
        waiting.evaluation,
        actor="operator",
        approved=True,
        approval_reference="loop-review-001",
    )
    completed = loop.dispatch(
        waiting,
        review,
        model_request=_request(),
        provider_binding_id="primary",
    )

    assert completed.status is EvaluationDispatchStatus.DISPATCHED
    assert completed.dispatch_request is not None
    assert completed.execution is not None
    assert completed.execution.status == "succeeded"
    assert len(provider.requests) == 1
    assert store.has_event(_TASK_ID, "escalation.dispatching")
    assert store.has_event(_TASK_ID, "escalation.succeeded")
    assert store.load_task(_TASK_ID).status is TaskStatus.FAILED
    ledger.close()


def test_evaluation_dispatch_cycle_never_dispatches_without_explicit_review(tmp_path):
    store, ledger, _provider, dispatcher = _setup(tmp_path)
    loop = EvaluationDispatchCoordinator(store, executor=EscalationExecutor(store, dispatcher))

    cycle = loop.evaluate(_evidence(), escalation_context=_context())

    with pytest.raises(TypeError, match="review_event"):
        loop.dispatch(cycle, None, model_request=_request(), provider_binding_id="primary")
    assert not store.has_event(_TASK_ID, "escalation.dispatch_ready")
    ledger.close()


def test_evaluation_dispatch_cycle_preserves_terminal_evaluator_result(tmp_path):
    store, ledger, _provider, dispatcher = _setup(tmp_path)
    loop = EvaluationDispatchCoordinator(store, executor=EscalationExecutor(store, dispatcher))

    cycle = loop.evaluate(
        _evidence(objective_met=True, deterministic_checks_passed=True),
    )

    assert cycle.status is EvaluationDispatchStatus.TERMINAL
    assert cycle.evaluation.result.decision is EvaluatorDecision.PASS
    assert cycle.dispatch_request is None
    assert cycle.execution is None
    assert not store.has_event(_TASK_ID, "escalation.dispatch_ready")
    ledger.close()


def test_evaluation_dispatch_cycle_records_rejected_review_without_dispatch(tmp_path):
    store, ledger, provider, dispatcher = _setup(tmp_path)
    evaluation = EvaluationCoordinator(store)
    loop = EvaluationDispatchCoordinator(
        store,
        evaluation_coordinator=evaluation,
        executor=EscalationExecutor(store, dispatcher),
    )
    cycle = loop.evaluate(_evidence(), escalation_context=_context())
    review = evaluation.review_plan(
        cycle.evaluation,
        actor="operator",
        approved=False,
        approval_reference="loop-review-002",
        reason="hold for more evidence",
    )

    result = loop.dispatch(cycle, review, model_request=_request(), provider_binding_id="primary")

    assert result.status is EvaluationDispatchStatus.REVIEW_REJECTED
    assert result.review_event is review
    assert result.dispatch_request is None
    assert result.execution is None
    assert not provider.requests
    assert not store.has_event(_TASK_ID, "escalation.dispatch_ready")
    ledger.close()


def test_task_lifecycle_applies_terminal_evaluation_idempotently(tmp_path):
    store, ledger, _provider, dispatcher = _setup(tmp_path)
    loop = EvaluationDispatchCoordinator(store, executor=EscalationExecutor(store, dispatcher))
    cycle = loop.evaluate(_evidence(objective_met=True, deterministic_checks_passed=True))
    lifecycle = TaskLifecycleCoordinator(store)

    first = lifecycle.apply_evaluation(cycle)
    second = lifecycle.apply_evaluation(cycle)

    assert first.task.status is TaskStatus.COMPLETED
    assert first.event.event_type == "task.completed"
    assert second.replayed is True
    matching = [
        item
        for item in store.snapshot()["events"]
        if item["event_type"] == "task.completed"
    ]
    assert len(matching) == 1
    ledger.close()


def test_task_lifecycle_moves_retry_to_ready_then_dispatch_to_running(tmp_path):
    store, ledger, provider, dispatcher = _setup(tmp_path)
    evaluation = EvaluationCoordinator(store)
    loop = EvaluationDispatchCoordinator(
        store,
        evaluation_coordinator=evaluation,
        executor=EscalationExecutor(store, dispatcher),
    )
    cycle = loop.evaluate(_evidence(), escalation_context=_context())
    lifecycle = TaskLifecycleCoordinator(store)

    scheduled = lifecycle.apply_evaluation(cycle)
    review = evaluation.review_plan(
        cycle.evaluation,
        actor="operator",
        approved=True,
        approval_reference="lifecycle-review-001",
    )
    dispatched = loop.dispatch(
        cycle,
        review,
        model_request=_request(),
        provider_binding_id="primary",
    )
    running = lifecycle.apply_dispatch(dispatched)

    assert scheduled.task.status is TaskStatus.READY
    assert scheduled.event.event_type == "task.retry_scheduled"
    assert running.task.status is TaskStatus.RUNNING
    assert running.event.event_type == "task.dispatch_started"
    assert len(provider.requests) == 1
    ledger.close()


def test_task_lifecycle_parks_unknown_dispatch_for_reconciliation(tmp_path):
    store, ledger, provider, dispatcher = _setup(tmp_path)

    def fail(_request):
        raise ProviderError("connection lost", category="transport", retryable=True)

    provider.request = fail
    evaluation = EvaluationCoordinator(store)
    loop = EvaluationDispatchCoordinator(
        store,
        evaluation_coordinator=evaluation,
        executor=EscalationExecutor(store, dispatcher),
    )
    cycle = loop.evaluate(_evidence(), escalation_context=_context())
    lifecycle = TaskLifecycleCoordinator(store)
    lifecycle.apply_evaluation(cycle)
    review = evaluation.review_plan(
        cycle.evaluation,
        actor="operator",
        approved=True,
        approval_reference="lifecycle-review-002",
    )
    dispatched = loop.dispatch(
        cycle,
        review,
        model_request=_request(),
        provider_binding_id="primary",
    )

    parked = lifecycle.apply_dispatch(dispatched)

    assert dispatched.status is EvaluationDispatchStatus.RECONCILIATION_REQUIRED
    assert parked.task.status is TaskStatus.WAITING_RECONCILIATION
    assert parked.event.payload["dispatch_id"] == dispatched.execution.dispatch_id
    assert store.has_event(_TASK_ID, "task.waiting_reconciliation")
    ledger.close()
