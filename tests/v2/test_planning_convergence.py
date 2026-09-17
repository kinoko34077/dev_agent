from uuid import uuid4

import pytest

from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.intelligence.convergence import (
    ConvergenceState,
    ConvergenceStopReason,
)
from src.dev_agent.intelligence.planner_adapter import (
    ModelPlanningAdapter,
    ModelPlanningCriticAdapter,
    PlanningConvergenceError,
    PlanningResponseError,
    propose_with_planning_convergence,
)
from src.dev_agent.providers.base import ProviderError


class _SequenceProvider:
    def __init__(self, provider_id, model_id, responses=(), errors=()):
        self.provider_id = provider_id
        self.model_id = model_id
        self.provider_binding_id = f"{provider_id}-binding"
        self.responses = list(responses)
        self.errors = list(errors)
        self.requests: list[ModelRequest] = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.errors:
            raise self.errors.pop(0)
        if not self.responses:
            raise AssertionError("test provider response queue is empty")
        return self.responses.pop(0)


def _proposal(parent_task_id: str) -> dict[str, object]:
    return {
        "parent_task_id": parent_task_id,
        "rationale": "split the bounded objective into one safe child",
        "planning_cycle": 1,
        "proposal_id": "converged-proposal",
        "children": [
            {
                "child_key": "implementation",
                "objective": "implement the narrow production change",
                "task_type": "worker",
                "risk": "low",
                "sensitivity": "normal",
                "required_capabilities": ["coding"],
                "dependencies": [],
                "dependency_types": {},
                "suggested_owner": "worker",
            }
        ],
    }


def _invalid_json(provider: str, model: str) -> ModelResponse:
    return ModelResponse(provider=provider, model=model, text_segments=["not-json"])


def _critic_response(parent_task_id: str, provider: str, model: str) -> ModelResponse:
    return ModelResponse(
        provider=provider,
        model=model,
        structured_output={"corrected_proposal": _proposal(parent_task_id)},
    )


def test_planning_convergence_fast_path_skips_all_critics():
    parent_task_id = str(uuid4())
    planner_provider = _SequenceProvider(
        "planner-provider",
        "planner-model",
        responses=(
            ModelResponse(
                provider="planner-provider",
                model="planner-model",
                structured_output=_proposal(parent_task_id),
            ),
        ),
    )
    critic_provider = _SequenceProvider("critic-provider", "critic-model")

    result = propose_with_planning_convergence(
        ModelPlanningAdapter(planner_provider),
        (ModelPlanningCriticAdapter(critic_provider),),
        parent_task_id=parent_task_id,
        objective="bounded planning objective",
    )

    assert result.completed is True
    assert result.proposal is not None
    assert result.attempts[0].convergence_state is ConvergenceState.FAST_PATH
    assert result.attempts[0].refinement_round == 0
    assert planner_provider.requests and critic_provider.requests == []


def test_planning_convergence_uses_fresh_critics_until_a_valid_proposal():
    parent_task_id = str(uuid4())
    planner_provider = _SequenceProvider(
        "planner-provider",
        "planner-model",
        responses=(_invalid_json("planner-provider", "planner-model"),),
    )
    first_critic_provider = _SequenceProvider(
        "critic-provider-a",
        "critic-model-a",
        responses=(
            ModelResponse(
                provider="critic-provider-a",
                model="critic-model-a",
                structured_output={"unexpected": "shape"},
            ),
        ),
    )
    second_critic_provider = _SequenceProvider(
        "critic-provider-b",
        "critic-model-b",
        responses=(_critic_response(parent_task_id, "critic-provider-b", "critic-model-b"),),
    )

    result = propose_with_planning_convergence(
        ModelPlanningAdapter(planner_provider),
        (
            ModelPlanningCriticAdapter(first_critic_provider),
            ModelPlanningCriticAdapter(second_critic_provider),
        ),
        parent_task_id=parent_task_id,
        objective="bounded planning objective",
    )

    assert result.completed is True
    assert result.proposal.parent_task_id == parent_task_id
    assert len(planner_provider.requests) == 1
    assert len(first_critic_provider.requests) == 1
    assert len(second_critic_provider.requests) == 1
    assert result.refinement_rounds == 2
    assert result.attempts[-1].convergence_state is ConvergenceState.COMPLETED
    assert result.attempts[-1].fresh_attempt_id == second_critic_provider.requests[0].request_id
    assert all(item.failure_signature is None or len(item.failure_signature) == 64 for item in result.attempts)


def test_planning_convergence_stops_after_two_consecutive_same_signatures():
    parent_task_id = str(uuid4())
    planner_provider = _SequenceProvider(
        "planner-provider",
        "planner-model",
        responses=(_invalid_json("planner-provider", "planner-model"),),
    )
    critic_a = _SequenceProvider(
        "critic-provider-a",
        "critic-model-a",
        responses=(_invalid_json("critic-provider-a", "critic-model-a"),),
    )
    critic_b = _SequenceProvider(
        "critic-provider-b",
        "critic-model-b",
        responses=(_invalid_json("critic-provider-b", "critic-model-b"),),
    )

    result = propose_with_planning_convergence(
        ModelPlanningAdapter(planner_provider),
        (ModelPlanningCriticAdapter(critic_a), ModelPlanningCriticAdapter(critic_b)),
        parent_task_id=parent_task_id,
        objective="bounded planning objective",
    )

    assert result.completed is False
    assert result.proposal is None
    assert result.stop_reason is ConvergenceStopReason.SAME_SIGNATURE_LIMIT
    assert len(critic_a.requests) == 1
    assert critic_b.requests == []


def test_planning_convergence_allows_a_changed_failure_signature():
    parent_task_id = str(uuid4())
    planner_provider = _SequenceProvider(
        "planner-provider",
        "planner-model",
        responses=(_invalid_json("planner-provider", "planner-model"),),
    )
    critic_a = _SequenceProvider(
        "critic-provider-a",
        "critic-model-a",
        responses=(
            _critic_response(parent_task_id, "critic-provider-a", "critic-model-a"),
        ),
    )
    critic_b = _SequenceProvider(
        "critic-provider-b",
        "critic-model-b",
        responses=(_critic_response(parent_task_id, "critic-provider-b", "critic-model-b"),),
    )

    validation_calls = 0

    def host_validate(_proposal):
        nonlocal validation_calls
        validation_calls += 1
        return validation_calls > 1

    result = propose_with_planning_convergence(
        ModelPlanningAdapter(planner_provider),
        (ModelPlanningCriticAdapter(critic_a), ModelPlanningCriticAdapter(critic_b)),
        parent_task_id=parent_task_id,
        objective="bounded planning objective",
        host_validate=host_validate,
    )

    assert result.completed is True
    assert result.refinement_rounds == 2
    assert result.attempts[0].failure_signature != result.attempts[1].failure_signature
    assert len(critic_b.requests) == 1


def test_planning_convergence_rejects_planner_critic_identity_overlap():
    parent_task_id = str(uuid4())
    provider = _SequenceProvider(
        "same-provider",
        "same-model",
        responses=(_invalid_json("same-provider", "same-model"),),
    )

    with pytest.raises(PlanningConvergenceError, match="identity"):
        propose_with_planning_convergence(
            ModelPlanningAdapter(provider),
            (ModelPlanningCriticAdapter(provider),),
            parent_task_id=parent_task_id,
            objective="bounded planning objective",
        )
    assert provider.requests == []


def test_planning_convergence_does_not_turn_provider_failure_into_refinement():
    parent_task_id = str(uuid4())
    failure = ProviderError("transport detail", category="transport")
    planner_provider = _SequenceProvider(
        "planner-provider",
        "planner-model",
        errors=(failure,),
    )
    critic_provider = _SequenceProvider("critic-provider", "critic-model")

    with pytest.raises(ProviderError):
        propose_with_planning_convergence(
            ModelPlanningAdapter(planner_provider),
            (ModelPlanningCriticAdapter(critic_provider),),
            parent_task_id=parent_task_id,
            objective="bounded planning objective",
        )
    assert critic_provider.requests == []


def test_planning_convergence_rejects_invalid_round_budget():
    provider = _SequenceProvider("planner-provider", "planner-model")
    with pytest.raises(ValueError, match="max_rounds"):
        propose_with_planning_convergence(
            ModelPlanningAdapter(provider),
            (),
            parent_task_id=str(uuid4()),
            objective="bounded planning objective",
            max_rounds=5,
        )
