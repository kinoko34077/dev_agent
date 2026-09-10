from __future__ import annotations

import pytest

from src.dev_agent.domain.protocol import RiskLevel, TaskStatus, TaskType
from src.dev_agent.intelligence.planner import (
    ChildTaskProposal,
    PlanningValidationError,
    RootPlanningProposal,
)
from src.dev_agent.operation import OperationConfig, OperationService


def _config(tmp_path):
    return OperationConfig(
        data_dir=tmp_path,
        provider_id="fake",
        model="deterministic",
        worker_id="planner-test-worker",
        idle_sleep_seconds=0.01,
    )


def _root(service_config, *, sensitivity="normal"):
    return OperationService.submit(
        service_config,
        "coordinate a bounded project",
        task_type=TaskType.REASONING,
        sensitivity=sensitivity,
    )


def test_planning_proposal_creates_finite_children_and_parks_dependencies(tmp_path):
    config = _config(tmp_path)
    root = _root(config, sensitivity="internal")
    proposal = RootPlanningProposal(
        parent_task_id=root.task_id,
        rationale="split independent implementation and dependent documentation",
        children=(
            ChildTaskProposal(
                child_key="implementation",
                objective="add the focused implementation",
                task_type=TaskType.WORKER,
                sensitivity="internal",
                required_capabilities=("coding",),
            ),
            ChildTaskProposal(
                child_key="documentation",
                objective="document the verified implementation",
                task_type=TaskType.DETERMINISTIC,
                sensitivity="internal",
                dependencies=("implementation",),
                required_capabilities=("documentation",),
            ),
        ),
    )

    with OperationService.open(config) as service:
        children = service.apply_planning_proposal(proposal)

    assert [child.task_type for child in children] == [TaskType.WORKER, TaskType.DETERMINISTIC]
    assert children[0].status is TaskStatus.QUEUED
    assert children[1].status is TaskStatus.WAITING_DEPENDENCY
    assert children[1].constraints["planner_dependencies"] == ["implementation"]
    assert children[1].metadata["wait_reason"] == "planner_dependency"


def test_planning_rejects_cycle_and_sensitivity_downgrade(tmp_path):
    config = _config(tmp_path)
    root = _root(config, sensitivity="sensitive")
    cyclic = RootPlanningProposal(
        parent_task_id=root.task_id,
        rationale="invalid cycle",
        children=(
            ChildTaskProposal(
                child_key="a",
                objective="a",
                task_type=TaskType.WORKER,
                sensitivity="sensitive",
                dependencies=("b",),
            ),
            ChildTaskProposal(
                child_key="b",
                objective="b",
                task_type=TaskType.WORKER,
                sensitivity="public",
                dependencies=("a",),
            ),
        ),
    )

    with OperationService.open(config) as service:
        with pytest.raises(PlanningValidationError, match="sensitivity"):
            service.validate_planning_proposal(cyclic)


def test_planning_rejects_dependency_cycle_before_task_creation(tmp_path):
    config = _config(tmp_path)
    root = _root(config)
    proposal = RootPlanningProposal(
        parent_task_id=root.task_id,
        rationale="cycle",
        children=(
            ChildTaskProposal(
                child_key="a",
                objective="a",
                task_type=TaskType.WORKER,
                dependencies=("b",),
            ),
            ChildTaskProposal(
                child_key="b",
                objective="b",
                task_type=TaskType.WORKER,
                dependencies=("a",),
            ),
        ),
    )

    with OperationService.open(config) as service:
        with pytest.raises(PlanningValidationError, match="cycle"):
            service.validate_planning_proposal(proposal)


def test_planning_rejects_unknown_capability_and_worker_protected_child(tmp_path):
    config = _config(tmp_path)
    root = _root(config)

    with OperationService.open(config) as service:
        unknown = RootPlanningProposal(
            parent_task_id=root.task_id,
            rationale="unknown capability",
            children=(
                ChildTaskProposal(
                    child_key="bad-capability",
                    objective="bad capability",
                    task_type=TaskType.WORKER,
                    required_capabilities=("not-a-capability",),
                ),
            ),
        )
        with pytest.raises(PlanningValidationError, match="capability"):
            service.validate_planning_proposal(unknown)

        protected = RootPlanningProposal(
            parent_task_id=root.task_id,
            rationale="protected work cannot be assigned to a free worker",
            children=(
                ChildTaskProposal(
                    child_key="protected",
                    objective="change protected authority",
                    task_type=TaskType.PROTECTED,
                    risk=RiskLevel.CRITICAL,
                    suggested_owner="worker",
                ),
            ),
        )
        with pytest.raises(PlanningValidationError, match="protected"):
            service.validate_planning_proposal(protected)


def test_planner_dependency_release_enqueues_only_after_all_dependencies_complete(tmp_path):
    config = _config(tmp_path)
    root = _root(config)
    proposal = RootPlanningProposal(
        parent_task_id=root.task_id,
        rationale="release dependent work after implementation",
        children=(
            ChildTaskProposal(child_key="implementation", objective="implement", task_type=TaskType.WORKER),
            ChildTaskProposal(
                child_key="docs",
                objective="document",
                task_type=TaskType.DETERMINISTIC,
                dependencies=("implementation",),
            ),
        ),
    )

    with OperationService.open(config) as service:
        children = service.apply_planning_proposal(proposal)
        assert service.release_planner_dependencies() == ()
        implementation, docs = children
        implementation.status = TaskStatus.COMPLETED
        service.store.save_task(implementation)
        released = service.release_planner_dependencies(proposal_id=proposal.proposal_id)
        assert [task.task_id for task in released] == [docs.task_id]
        assert service.store.load_task(docs.task_id).status is TaskStatus.QUEUED
        assert service.queue.snapshot(docs.task_id).state == "queued"
        assert service.store.has_event(docs.task_id, "task.planner_dependency_released")
