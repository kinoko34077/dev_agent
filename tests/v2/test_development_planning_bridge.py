from uuid import uuid4

import pytest

from scripts.devfarm import write_manifest
from scripts.devfarm_commander import create_plan
from scripts.devfarm_planning_bridge import (
    DevelopmentPlanningBridge,
    PlanningBridgeError,
)
from src.dev_agent.domain.protocol import RiskLevel, Task, TaskType
from src.dev_agent.intelligence.planner import (
    ChildTaskProposal,
    PlannerDependencyType,
    RootPlanningProposal,
)


def _parent(*, task_type=TaskType.REASONING):
    return Task(
        task_id=str(uuid4()),
        objective="coordinate a bounded development slice",
        task_type=task_type,
    )


def _manifest_spec(path="src/example.py"):
    return {
        "manifest": {
            "task_type": "worker",
            "allowed_files": [path],
            "read_files": [path],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["gemini"],
            "outbound_files": [path],
            "requirements": ["keep the change narrow"],
            "acceptance": ["the focused test passes"],
            "test_commands": ["python -m pytest tests/v2/test_example.py -q"],
            "max_attempts": 2,
            "output_contract": {},
        },
        "assignment": {
            "provider_id": "gemini",
            "provider_binding_id": "gemini:worker",
            "model_id": "gemini-3.5-flash-lite",
        },
    }


def test_bridge_returns_host_validated_candidate_without_writing_devfarm(tmp_path):
    parent = _parent()
    proposal = RootPlanningProposal(
        parent_task_id=parent.task_id,
        rationale="one narrow worker task",
        children=(
            ChildTaskProposal(
                child_key="implementation",
                objective="implement the narrow change",
                task_type=TaskType.WORKER,
                required_capabilities=("coding",),
            ),
        ),
    )

    candidate = DevelopmentPlanningBridge(tmp_path).build_candidate(
        parent,
        proposal,
        run_id="planner-shadow-001",
        base_revision="abc123",
        task_specs={"implementation": _manifest_spec()},
    )

    assert not (tmp_path / ".devfarm").exists()
    assert len(candidate.manifests) == 1
    manifest_path, manifest = candidate.manifests[0]
    task = candidate.plan["tasks"][0]
    assert task["owner"] == "worker"
    assert task["task_type"] == "worker"
    assert task["risk"] == "normal"
    assert task["sensitivity"] == "normal"
    assert task["manifest_path"] == manifest_path
    assert manifest["task_id"] == task["task_id"]
    assert manifest["objective"] == "implement the narrow change"
    assert manifest["base_revision"] == "abc123"
    assert task["delegation_reason"] == "planner_worker_candidate"


def test_bridge_candidate_can_use_existing_manifest_and_plan_boundaries(tmp_path):
    parent = _parent()
    proposal = RootPlanningProposal(
        parent_task_id=parent.task_id,
        rationale="persist only through the existing Host boundaries",
        children=(
            ChildTaskProposal(
                child_key="implementation",
                objective="implement the narrow change",
                task_type=TaskType.WORKER,
            ),
        ),
    )

    candidate = DevelopmentPlanningBridge(tmp_path).build_candidate(
        parent,
        proposal,
        run_id="planner-shadow-boundary-001",
        base_revision="abc123",
        task_specs={"implementation": _manifest_spec()},
    )
    for manifest_path, manifest in candidate.manifests:
        assert manifest_path.startswith(".devfarm/tasks/")
        write_manifest(tmp_path, manifest)

    created = create_plan(tmp_path, candidate.plan)

    assert created["run_id"] == "planner-shadow-boundary-001"
    assert created["tasks"][0]["status"] == "READY"
    assert created["tasks"][0]["manifest_path"] == candidate.manifests[0][0]


def test_bridge_does_not_silently_convert_unsupported_dependency_type(tmp_path):
    parent = _parent()
    proposal = RootPlanningProposal(
        parent_task_id=parent.task_id,
        rationale="preserve dependency semantics",
        children=(
            ChildTaskProposal(
                child_key="a",
                objective="produce an artifact",
                task_type=TaskType.WORKER,
            ),
            ChildTaskProposal(
                child_key="b",
                objective="consume the artifact",
                task_type=TaskType.WORKER,
                dependencies=("a",),
                dependency_types={"a": PlannerDependencyType.ARTIFACT_READY},
            ),
        ),
    )

    with pytest.raises(PlanningBridgeError, match="CODE_INTEGRATED"):
        DevelopmentPlanningBridge(tmp_path).build_candidate(
            parent,
            proposal,
            run_id="planner-shadow-unsupported-dependency",
            base_revision="abc123",
            task_specs={"a": _manifest_spec(), "b": _manifest_spec()},
        )


def test_bridge_preserves_supported_dependency_type_in_commander_candidate(tmp_path):
    parent = _parent()
    proposal = RootPlanningProposal(
        parent_task_id=parent.task_id,
        rationale="preserve the code integration dependency contract",
        children=(
            ChildTaskProposal(
                child_key="producer",
                objective="produce the integrated change",
                task_type=TaskType.WORKER,
            ),
            ChildTaskProposal(
                child_key="consumer",
                objective="consume the integrated change",
                task_type=TaskType.WORKER,
                dependencies=("producer",),
                dependency_types={"producer": PlannerDependencyType.CODE_INTEGRATED},
            ),
        ),
    )

    candidate = DevelopmentPlanningBridge(tmp_path).build_candidate(
        parent,
        proposal,
        run_id="planner-dependency-type-preservation",
        base_revision="abc123",
        task_specs={
            "producer": _manifest_spec("src/producer.py"),
            "consumer": _manifest_spec("src/consumer.py"),
        },
    )

    producer_id = candidate.plan["tasks"][0]["task_id"]
    consumer = candidate.plan["tasks"][1]
    assert consumer["dependencies"] == [producer_id]
    assert consumer["dependency_types"] == {producer_id: PlannerDependencyType.CODE_INTEGRATED.value}
    assert candidate.plan["dependencies"][1]["dependency_types"] == {
        producer_id: PlannerDependencyType.CODE_INTEGRATED.value
    }


def test_bridge_host_overrides_protected_worker_suggestion_and_records_reason(tmp_path):
    parent = _parent()
    proposal = RootPlanningProposal(
        parent_task_id=parent.task_id,
        rationale="protected work must remain with Codex",
        children=(
            ChildTaskProposal(
                child_key="protected",
                objective="review protected authority",
                task_type=TaskType.PROTECTED,
                risk=RiskLevel.CRITICAL,
                suggested_owner="codex",
            ),
        ),
    )

    candidate = DevelopmentPlanningBridge(tmp_path).build_candidate(
        parent,
        proposal,
        run_id="planner-shadow-protected",
        base_revision="abc123",
        task_specs={"protected": {"owner": "worker", "ownership": ["docs/review.md"]}},
    )

    task = candidate.plan["tasks"][0]
    assert task["owner"] == "codex"
    assert task["worker_candidate"] is False
    assert task["delegation_reason"] == "protected"
    assert candidate.manifests == ()
