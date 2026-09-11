"""Cross-boundary evidence for bounded L1 alternate and L2 escalation."""

from __future__ import annotations

import pytest

from src.dev_agent.domain.protocol import IntelligenceTier, ModelRequest, ModelResponse, Task, TaskStatus
from src.dev_agent.intelligence.coordination import EvaluationCoordinator
from src.dev_agent.intelligence.escalation import EscalationContext
from src.dev_agent.intelligence.evaluator import EvaluationEvidence
from src.dev_agent.intelligence.execution import EscalationExecutionError, EscalationExecutor
from src.dev_agent.providers.base import ModelProvider, ProviderError
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.state import JsonStateStore


_TASK_ID = "00000000-0000-0000-0000-000000000071"


class _TierProvider(ModelProvider):
    def __init__(self, provider_id: str, binding_id: str, tier: str, *, failure: ProviderError | None = None) -> None:
        self.provider_id = provider_id
        self.provider_binding_id = binding_id
        self.model = f"{binding_id}-model"
        self.model_id = self.model
        self.intelligence_tier = tier
        self.failure = failure
        self.requests: list[ModelRequest] = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return ModelResponse(
            provider=self.provider_id,
            model=self.model,
            text_segments=[self.provider_binding_id],
            usage={"cost_minor": 0},
        )


def _setup(tmp_path, providers: list[_TierProvider]):
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    for provider in providers:
        ledger.register_resource(
            provider.provider_binding_id,
            provider_id=provider.provider_id,
            provider_binding_id=provider.provider_binding_id,
            native_unit="request",
            capacity=8,
            capabilities=["text"],
            cost_minor=0,
            price_currency="JPY",
            metadata={
                "provider_binding_id": provider.provider_binding_id,
                "model_id": provider.model_id,
            },
            intelligence_tier=provider.intelligence_tier,
        )
        ledger.observe(provider.provider_binding_id, available=8, health="healthy")
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0, currency="JPY"))
    store = JsonStateStore(tmp_path / "state.json")
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger))
    dispatcher = ProviderDispatcher(ProviderRegistry(providers), control)
    dispatcher.bind_runtime(state_store=store)
    return store, ledger, dispatcher


def _request(*, binding_ids=None, tiers=("L1",)) -> ModelRequest:
    metadata = {
        "intelligence_routing": "bounded",
        "allowed_intelligence_tiers": list(tiers),
    }
    if binding_ids is not None:
        metadata["allowed_provider_binding_ids"] = list(binding_ids)
    return ModelRequest(task_id=_TASK_ID, messages=[{"role": "user", "content": "perform bounded work"}], metadata=metadata)


def _evidence(*, attempt: int, alternate: bool, retryable: bool) -> EvaluationEvidence:
    return EvaluationEvidence(
        task_id=_TASK_ID,
        attempt=attempt,
        max_attempts=3,
        objective_met=False,
        deterministic_checks_passed=False,
        policy_compliant=True,
        external_outcome_known=True,
        retryable_failure=retryable,
        alternate_provider_available=alternate,
        human_approval_required=False,
    )


def _context(*, attempt: int, alternate: bool, retryable: bool) -> EscalationContext:
    return EscalationContext(
        task_id=_TASK_ID,
        current_tier=IntelligenceTier.L1,
        allowed_tiers=(IntelligenceTier.L1, IntelligenceTier.L2),
        attempt=attempt,
        max_attempts=3,
        escalation_count=0,
        max_escalations=1,
        now_epoch=float(attempt),
        deadline_epoch=100.0,
        budget_remaining=1.0,
        estimated_cost=0.0,
        retryable_failure=retryable,
        same_provider_available=False,
        alternate_provider_available=alternate,
    )


def _approve_and_prepare(store, evidence, context, *, source_binding: str | None = None):
    coordinator = EvaluationCoordinator(store)
    cycle = coordinator.evaluate_and_plan(evidence, escalation_context=context)
    assert cycle.plan is not None
    review = coordinator.review_plan(
        cycle,
        actor="operator",
        approved=True,
        approval_reference=f"hierarchy-review-{evidence.attempt}",
    )
    return coordinator.prepare_dispatch(cycle, review, provider_binding_id=source_binding)


def test_l1_failure_uses_alternate_l1_before_bounded_l2_escalation(tmp_path):
    primary = _TierProvider(
        "gemini",
        "gemini:worker-a",
        "L1",
        failure=ProviderError("primary unavailable", category="provider_error", retryable=True),
    )
    alternate = _TierProvider(
        "cloudflare",
        "cloudflare:worker",
        "L1",
        failure=ProviderError("alternate unavailable", category="provider_error", retryable=True),
    )
    core = _TierProvider("gemini", "gemini:core", "L2")
    store, ledger, dispatcher = _setup(tmp_path, [primary, alternate, core])
    executor = EscalationExecutor(store, dispatcher)
    store.save_task(
        Task(
            task_id=_TASK_ID,
            objective="complete a bounded implementation",
            task_type="worker",
            status=TaskStatus.FAILED,
        )
    )

    with pytest.raises(ProviderError):
        dispatcher.request(_request(binding_ids=[primary.provider_binding_id]))

    first = _approve_and_prepare(
        store,
        _evidence(attempt=1, alternate=True, retryable=True),
        _context(attempt=1, alternate=True, retryable=True),
        source_binding=primary.provider_binding_id,
    )
    with pytest.raises(EscalationExecutionError):
        executor.execute(first, _request())

    assert first.target.value == "other_provider"
    assert len(primary.requests) == 1
    assert len(alternate.requests) == 1
    assert alternate.requests[0].metadata["allowed_intelligence_tiers"] == ["L1"]
    assert alternate.requests[0].metadata["excluded_provider_binding_ids"] == [primary.provider_binding_id]

    second = _approve_and_prepare(
        store,
        _evidence(attempt=2, alternate=False, retryable=False),
        _context(attempt=2, alternate=False, retryable=False),
        source_binding=alternate.provider_binding_id,
    )
    second_result = executor.execute(second, _request())

    assert second.target.value == "higher_tier"
    assert second.next_tier is IntelligenceTier.L2
    assert second_result.status.value == "succeeded"
    assert len(alternate.requests) == 1
    assert len(core.requests) == 1
    assert core.requests[0].metadata["allowed_intelligence_tiers"] == ["L2"]
    assert core.requests[0].metadata.get("excluded_provider_binding_ids") is None
    assert store.has_event(_TASK_ID, "evaluation.recorded")
    assert store.has_event(_TASK_ID, "escalation.succeeded")

    ledger.close()
