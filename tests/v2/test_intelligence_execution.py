import pytest
from uuid import uuid4

from src.dev_agent.domain.protocol import (
    IntelligenceTier,
    ModelRequest,
    ModelResponse,
    Task,
    TaskStatus,
)
from src.dev_agent.intelligence.coordination import EvaluationCoordinator
from src.dev_agent.intelligence.escalation import EscalationContext
from src.dev_agent.intelligence.execution import (
    EscalationExecutionDenied,
    EscalationExecutionError,
    EscalationExecutor,
)
from src.dev_agent.intelligence.evaluator import EvaluationEvidence
from src.dev_agent.providers.base import ModelProvider, ProviderError
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.state import JsonStateStore


_TASK_ID = "00000000-0000-0000-0000-000000000021"


class RecordingProvider(ModelProvider):
    def __init__(self, provider_id: str, binding_id: str, tier: str, *, failure: ProviderError | None = None) -> None:
        self.provider_id = provider_id
        self.provider_binding_id = binding_id
        self.model = f"{provider_id}-model"
        self.intelligence_tier = tier
        self.failure = failure
        self.requests = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return ModelResponse(provider=self.provider_id, model=self.model, text_segments=[self.provider_id])


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


def _setup(tmp_path, providers):
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    for provider in providers:
        ledger.register_resource(
            provider.provider_binding_id,
            provider_id=provider.provider_id,
            provider_binding_id=provider.provider_binding_id,
            native_unit="request",
            capacity=10,
            capabilities=["text"],
            cost_minor=0,
            intelligence_tier=provider.intelligence_tier,
        )
        ledger.observe(provider.provider_binding_id, available=10, health="healthy")
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0))
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger))
    dispatcher = ProviderDispatcher(ProviderRegistry(list(providers)), control)
    store = JsonStateStore(tmp_path / "state.json")
    dispatcher.bind_runtime(state_store=store)
    return store, dispatcher, ledger


def _approved_request(store, *, context=None, evidence=None, provider_binding_id=None):
    coordinator = EvaluationCoordinator(store)
    cycle = coordinator.evaluate_and_plan(
        evidence or _evidence(),
        escalation_context=context or _context(),
    )
    review = coordinator.review_plan(
        cycle,
        actor="operator",
        approved=True,
        approval_reference="execution-review-001",
    )
    return coordinator.prepare_dispatch(
        cycle,
        review,
        provider_binding_id=provider_binding_id,
    )


def _model_request():
    return ModelRequest(task_id=_TASK_ID, messages=[{"role": "user", "content": "retry"}])


def test_escalation_executor_dispatches_same_binding_and_replays_durably(tmp_path):
    provider = RecordingProvider("primary", "primary", "L1")
    store, dispatcher, ledger = _setup(tmp_path, [provider])
    task = Task(task_id=_TASK_ID, objective="bounded retry", status=TaskStatus.FAILED)
    store.save_task(task)
    request = _approved_request(store, provider_binding_id="primary")

    first = EscalationExecutor(store, dispatcher).execute(request, _model_request())
    second = EscalationExecutor(store, dispatcher).execute(request, _model_request())

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.replayed is True
    assert first.dispatch_id == request.dispatch_id
    assert first.attempt == 2
    assert len(provider.requests) == 1
    assert provider.requests[0].metadata["allowed_provider_binding_ids"] == ["primary"]
    intent = store.get_effect_intent(f"escalation:{request.dispatch_id}")
    assert intent["status"] == "succeeded"
    assert store.has_event(_TASK_ID, "escalation.dispatching")
    assert store.has_event(_TASK_ID, "escalation.succeeded")
    assert task.status == TaskStatus.FAILED
    ledger.close()


def test_escalation_executor_routes_retry_to_another_binding(tmp_path):
    primary = RecordingProvider("cloud", "cloud:key-a", "L1")
    secondary = RecordingProvider("cloud", "cloud:key-b", "L1")
    store, dispatcher, ledger = _setup(tmp_path, [primary, secondary])
    store.save_task(Task(task_id=_TASK_ID, objective="alternate retry", status=TaskStatus.FAILED))
    request = _approved_request(
        store,
        context=_context(
            same_provider_available=False,
            alternate_provider_available=True,
        ),
        evidence=_evidence(
            alternate_provider_available=True,
        ),
        provider_binding_id="cloud:key-a",
    )

    result = EscalationExecutor(store, dispatcher).execute(request, _model_request())

    assert result.status == "succeeded"
    assert not primary.requests
    assert len(secondary.requests) == 1
    assert secondary.requests[0].metadata["excluded_provider_binding_ids"] == ["cloud:key-a"]
    ledger.close()


def test_escalation_executor_routes_higher_tier_only_with_durable_allowed_tier(tmp_path):
    worker = RecordingProvider("worker", "worker", "L1")
    core = RecordingProvider("core", "core", "L2")
    store, dispatcher, ledger = _setup(tmp_path, [worker, core])
    store.save_task(Task(task_id=_TASK_ID, objective="escalate", status=TaskStatus.FAILED))
    request = _approved_request(
        store,
        context=_context(
            allowed_tiers=(IntelligenceTier.L1, IntelligenceTier.L2),
            retryable_failure=False,
            same_provider_available=False,
            alternate_provider_available=False,
        ),
        evidence=_evidence(
            retryable_failure=False,
            alternate_provider_available=False,
        ),
    )

    result = EscalationExecutor(store, dispatcher).execute(request, _model_request())

    assert request.next_tier is IntelligenceTier.L2
    assert result.status == "succeeded"
    assert not worker.requests
    assert len(core.requests) == 1
    assert core.requests[0].metadata["allowed_intelligence_tiers"] == ["L2"]
    ledger.close()


def test_escalation_executor_holds_unknown_and_refuses_replay(tmp_path):
    provider = RecordingProvider(
        "primary",
        "primary",
        "L1",
        failure=ProviderError("connection lost", category="transport", retryable=True),
    )
    store, dispatcher, ledger = _setup(tmp_path, [provider])
    store.save_task(Task(task_id=_TASK_ID, objective="unknown retry", status=TaskStatus.FAILED))
    request = _approved_request(store, provider_binding_id="primary")
    executor = EscalationExecutor(store, dispatcher)

    with pytest.raises(EscalationExecutionError, match="reconciliation") as first:
        executor.execute(request, _model_request())
    with pytest.raises(EscalationExecutionError, match="reconciliation"):
        executor.execute(request, _model_request())

    assert first.value.category == "reconciliation_required"
    assert len(provider.requests) == 1
    assert store.get_effect_intent(f"escalation:{request.dispatch_id}")["status"] == "unknown"
    assert store.has_event(_TASK_ID, "escalation.unknown")
    ledger.close()


def test_escalation_executor_rechecks_ready_identity_and_task_state(tmp_path):
    provider = RecordingProvider("primary", "primary", "L1")
    store, dispatcher, ledger = _setup(tmp_path, [provider])
    task = Task(task_id=_TASK_ID, objective="stale task", status=TaskStatus.FAILED)
    store.save_task(task)
    request = _approved_request(store, provider_binding_id="primary")

    store.save_task(Task(task_id=_TASK_ID, objective="already done", status=TaskStatus.COMPLETED))
    with pytest.raises(EscalationExecutionDenied, match="task state"):
        EscalationExecutor(store, dispatcher).execute(request, _model_request())

    store.save_task(task)
    forged = type(request)(
        task_id=request.task_id,
        plan_id=request.plan_id,
        decision=request.decision,
        target=request.target,
        next_tier=request.next_tier,
        approved_by=request.approved_by,
        approval_reference=request.approval_reference,
        provider_binding_id=request.provider_binding_id,
        current_tier=request.current_tier,
        allowed_tiers=request.allowed_tiers,
        attempt=request.attempt,
        dispatch_id=str(uuid4()),
    )
    with pytest.raises(EscalationExecutionDenied, match="dispatch_ready"):
        EscalationExecutor(store, dispatcher).execute(forged, _model_request())

    assert not provider.requests
    ledger.close()
