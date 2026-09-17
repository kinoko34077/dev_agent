from __future__ import annotations

from uuid import uuid4

import pytest

from src.dev_agent.domain.protocol import ModelRequest, ModelResponse, Task, TaskStatus, TaskType
from src.dev_agent.intelligence.planner import RootPlanningValidator
from src.dev_agent.intelligence.planner_adapter import (
    ModelPlanningAdapter,
    ModelPlanningCriticAdapter,
    PLANNING_CRITIC_RESPONSE_SCHEMA,
    PlanningCriticAdapterError,
    PlanningResponseError,
    propose_with_planning_critic,
)
from src.dev_agent.providers.base import ProviderError


class _Provider:
    provider_id = "planner-role-provider"

    def __init__(self, response: ModelResponse | None = None, error: BaseException | None = None):
        self.response = response
        self.error = error
        self.requests: list[ModelRequest] = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _proposal(parent_task_id: str) -> dict[str, object]:
    return {
        "parent_task_id": parent_task_id,
        "rationale": "split the bounded objective into one safe child",
        "planning_cycle": 1,
        "proposal_id": "critic-corrected-proposal",
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


def _failure(*, response_contract: str = "invalid_json") -> PlanningResponseError:
    return PlanningResponseError(
        "redacted planner contract failure",
        request_id=str(uuid4()),
        provider_response_observed=True,
        response_contract=response_contract,
    )


def test_planning_critic_returns_corrected_proposal_with_fresh_proposal_only_request():
    parent_task_id = str(uuid4())
    planner_failure = _failure()
    provider = _Provider(
        ModelResponse(
            provider="independent-planning-critic",
            model="free-l1-critic",
            structured_output={"corrected_proposal": _proposal(parent_task_id)},
        )
    )

    proposal = ModelPlanningCriticAdapter(provider).correct(
        parent_task_id=parent_task_id,
        objective="split this bounded objective",
        planner_failure=planner_failure,
        context_references={"repository": "kinoko34077/dev_agent", "branch": "v2/bootstrap"},
    )

    assert proposal.parent_task_id == parent_task_id
    assert proposal.children[0].suggested_owner == "worker"
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.request_id != planner_failure.request_id
    assert request.response_schema == PLANNING_CRITIC_RESPONSE_SCHEMA
    assert request.metadata["planning_mode"] == "proposal_only"
    assert request.metadata["planning_refinement"] == "independent_critic"
    assert request.metadata["proposal_only"] is True
    assert request.metadata["integration_authority"] == "host_and_codex"
    assert request.metadata["allowed_intelligence_tiers"] == ["L1"]
    assert request.metadata["critic_of_request_id"] == planner_failure.request_id
    assert "raw response" not in request.messages[0]["content"].lower()
    assert "split this bounded objective" in request.messages[0]["content"]


def test_planning_critic_rejects_authority_fields_and_parent_mismatch():
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="independent-planning-critic",
            model="free-l1-critic",
            structured_output={
                "corrected_proposal": {**_proposal(str(uuid4())), "approval": True},
            },
        )
    )

    with pytest.raises(PlanningCriticAdapterError, match="unknown"):
        ModelPlanningCriticAdapter(provider).correct(
            parent_task_id=parent_task_id,
            objective="bounded objective",
            planner_failure=_failure(response_contract="invalid_proposal"),
        )


def test_planning_critic_requires_model_output_failure_and_does_not_accept_transport():
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="independent-planning-critic",
            model="free-l1-critic",
            structured_output={"corrected_proposal": _proposal(parent_task_id)},
        )
    )

    with pytest.raises(PlanningCriticAdapterError, match="response-contract"):
        ModelPlanningCriticAdapter(provider).correct(
            parent_task_id=parent_task_id,
            objective="bounded objective",
            planner_failure=ProviderError("transport", category="transport"),  # type: ignore[arg-type]
        )
    assert provider.requests == []


def test_planner_composition_uses_one_critic_action_then_host_validation():
    parent_task_id = str(uuid4())
    planner_provider = _Provider(
        ModelResponse(
            provider="planner",
            model="free-l2-planner",
            text_segments=["not-json"],
        )
    )
    critic_provider = _Provider(
        ModelResponse(
            provider="independent-planning-critic",
            model="free-l1-critic",
            structured_output={"corrected_proposal": _proposal(parent_task_id)},
        )
    )
    parent = Task(
        task_id=parent_task_id,
        objective="split this bounded objective",
        status=TaskStatus.READY,
        task_type=TaskType.REASONING,
    )
    validated: list[object] = []

    def host_validate(proposal):
        accepted = RootPlanningValidator.validate(parent, proposal)
        validated.append(accepted)
        return accepted

    proposal = propose_with_planning_critic(
        ModelPlanningAdapter(planner_provider),
        ModelPlanningCriticAdapter(critic_provider),
        parent_task_id=parent_task_id,
        objective=parent.objective,
        host_validate=host_validate,
    )

    assert proposal.parent_task_id == parent_task_id
    assert len(planner_provider.requests) == 1
    assert len(critic_provider.requests) == 1
    assert len(validated) == 1


def test_planner_composition_does_not_invoke_critic_for_provider_failure():
    parent_task_id = str(uuid4())
    planner_provider = _Provider(error=ProviderError("transport", category="transport"))
    critic_provider = _Provider(
        ModelResponse(
            provider="independent-planning-critic",
            model="free-l1-critic",
            structured_output={"corrected_proposal": _proposal(parent_task_id)},
        )
    )

    with pytest.raises(ProviderError):
        propose_with_planning_critic(
            ModelPlanningAdapter(planner_provider),
            ModelPlanningCriticAdapter(critic_provider),
            parent_task_id=parent_task_id,
            objective="bounded objective",
        )

    assert critic_provider.requests == []
