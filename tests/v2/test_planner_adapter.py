import json
from uuid import uuid4

import pytest

from src.dev_agent.domain.protocol import ModelRequest, ModelResponse, TaskType
from src.dev_agent.intelligence.planner import PlanningValidationError, RootPlanningProposal
from src.dev_agent.intelligence.planner_adapter import ModelPlanningAdapter, PlanningAdapterError
from src.dev_agent.operation import OperationConfig, OperationService


class _Provider:
    provider_id = "planner-test"

    def __init__(self, response: ModelResponse):
        self.response = response
        self.requests: list[ModelRequest] = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.response


def _payload(parent_task_id: str) -> dict:
    return {
        "parent_task_id": parent_task_id,
        "rationale": "split the bounded work into one implementation child",
        "planning_cycle": 1,
        "proposal_id": "planner-proposal-1",
        "children": [
            {
                "child_key": "implementation",
                "objective": "implement the narrow change",
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


def test_model_planner_returns_typed_proposal_and_keeps_host_authority(tmp_path):
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            structured_output=_payload(parent_task_id),
        )
    )
    adapter = ModelPlanningAdapter(provider)

    proposal = adapter.propose(
        parent_task_id=parent_task_id,
        objective="split this narrow development objective",
        sensitivity="normal",
        context_references={"repository": "kinoko34077/dev_agent", "branch": "v2/bootstrap"},
    )

    assert proposal.parent_task_id == parent_task_id
    assert proposal.children[0].child_key == "implementation"
    assert proposal.children[0].task_type.value == "worker"
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.requested_capabilities == ["structured_output", "json"]
    assert request.response_schema is not None
    assert request.metadata["planning_mode"] == "proposal_only"
    assert request.metadata["authority"] == "host_validation_required"
    assert "split this narrow development objective" in request.messages[0]["content"]
    assert "v2/bootstrap" in request.messages[0]["content"]


def test_model_planner_accepts_bounded_json_text_when_provider_has_no_structured_field():
    parent_task_id = str(uuid4())
    payload = _payload(parent_task_id)
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            text_segments=[json.dumps(payload)],
        )
    )

    proposal = ModelPlanningAdapter(provider).propose(parent_task_id=parent_task_id, objective="bounded objective")

    assert proposal.proposal_id == "planner-proposal-1"


def test_model_planner_rejects_narrative_or_wrong_parent_as_non_authoritative_output():
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            text_segments=["I recommend three tasks, but here is no JSON."],
        )
    )
    with pytest.raises(PlanningAdapterError, match="JSON"):
        ModelPlanningAdapter(provider).propose(parent_task_id=parent_task_id, objective="bounded objective")

    wrong_parent = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            structured_output=_payload(wrong_parent),
        )
    )
    with pytest.raises(PlanningAdapterError, match="parent_task_id"):
        ModelPlanningAdapter(provider).propose(parent_task_id=parent_task_id, objective="bounded objective")


def test_typed_proposal_round_trip_rejects_untracked_fields():
    parent_task_id = str(uuid4())
    payload = _payload(parent_task_id)
    proposal = RootPlanningProposal.from_dict(payload)

    assert RootPlanningProposal.from_dict(proposal.to_dict()).to_dict() == proposal.to_dict()
    payload["untracked_authority"] = "must not be accepted"
    with pytest.raises(PlanningValidationError, match="unknown planning proposal field"):
        RootPlanningProposal.from_dict(payload)


def test_planner_output_is_only_a_proposal_until_operation_host_validation(tmp_path):
    config = OperationConfig(
        data_dir=tmp_path,
        provider_id="fake",
        model="deterministic",
        worker_id="planner-shadow-test",
        idle_sleep_seconds=0.01,
    )
    parent = OperationService.submit(config, "coordinate a bounded planner shadow", task_type=TaskType.REASONING)
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            structured_output=_payload(parent.task_id),
        )
    )
    proposal = ModelPlanningAdapter(provider).propose(
        parent_task_id=parent.task_id,
        objective=parent.objective,
    )

    with OperationService.open(config) as service:
        accepted = service.validate_planning_proposal(proposal)

    assert len(accepted) == 1
    assert accepted[0].child_key == "implementation"
    assert OperationService.read_status(config, parent.task_id)["state"] == "queued"
