from __future__ import annotations

import pytest

from src.dev_agent.domain.protocol import RiskLevel, TaskStatus, TaskType
from src.dev_agent.intelligence.planner import (
    ChildTaskProposal,
    PlannerDependencyType,
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


def test_apply_planning_proposal_reuses_one_task_snapshot(tmp_path, monkeypatch):
    config = _config(tmp_path)
    root = _root(config)
    proposal = RootPlanningProposal(
        parent_task_id=root.task_id,
        rationale="one bounded child",
        children=(ChildTaskProposal(child_key="worker", objective="implement", task_type=TaskType.WORKER),),
    )

    with OperationService.open(config) as service:
        original_snapshot = service.store.snapshot
        calls = 0

        def counted_snapshot():
            nonlocal calls
            calls += 1
            return original_snapshot()

        monkeypatch.setattr(service.store, "snapshot", counted_snapshot)
        service.apply_planning_proposal(proposal)

    assert calls == 1


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


def test_code_integrated_dependency_requires_integration_evidence(tmp_path):
    config = _config(tmp_path)
    root = _root(config)
    proposal = RootPlanningProposal(
        parent_task_id=root.task_id,
        rationale="release code-dependent work only after integration",
        children=(
            ChildTaskProposal(child_key="implementation", objective="implement", task_type=TaskType.WORKER),
            ChildTaskProposal(
                child_key="followup",
                objective="follow up on integrated implementation",
                task_type=TaskType.DETERMINISTIC,
                dependencies=("implementation",),
                dependency_types={"implementation": PlannerDependencyType.CODE_INTEGRATED},
            ),
        ),
    )

    with OperationService.open(config) as service:
        implementation, followup = service.apply_planning_proposal(proposal)
        implementation.status = TaskStatus.COMPLETED
        service.store.save_task(implementation)
        assert service.release_planner_dependencies(proposal_id=proposal.proposal_id) == ()

        implementation.metadata["integration_status"] = "INTEGRATED"
        implementation.metadata["integration_revision"] = "abc123"
        service.store.save_task(implementation)
        released = service.release_planner_dependencies(proposal_id=proposal.proposal_id)

    assert [task.task_id for task in released] == [followup.task_id]


def test_artifact_dependency_releases_from_explicit_artifact_evidence(tmp_path):
    config = _config(tmp_path)
    root = _root(config)
    proposal = RootPlanningProposal(
        parent_task_id=root.task_id,
        rationale="release benchmark consumer after artifact publication",
        children=(
            ChildTaskProposal(child_key="benchmark", objective="produce benchmark", task_type=TaskType.WORKER),
            ChildTaskProposal(
                child_key="consumer",
                objective="consume benchmark",
                task_type=TaskType.DETERMINISTIC,
                dependencies=("benchmark",),
                dependency_types={"benchmark": PlannerDependencyType.ARTIFACT_READY},
            ),
        ),
    )

    with OperationService.open(config) as service:
        benchmark, consumer = service.apply_planning_proposal(proposal)
        benchmark.metadata["artifact_ready"] = True
        service.store.save_task(benchmark)
        released = service.release_planner_dependencies(proposal_id=proposal.proposal_id)

    assert [task.task_id for task in released] == [consumer.task_id]


def test_failed_planner_dependency_is_durable_and_never_released_by_artifact_metadata(tmp_path):
    config = _config(tmp_path)
    root = _root(config)
    proposal = RootPlanningProposal(
        parent_task_id=root.task_id,
        rationale="failed dependency must terminalize the consumer",
        children=(
            ChildTaskProposal(child_key="producer", objective="fail", task_type=TaskType.WORKER),
            ChildTaskProposal(
                child_key="consumer",
                objective="must not run",
                task_type=TaskType.DETERMINISTIC,
                dependencies=("producer",),
            ),
        ),
    )

    with OperationService.open(config) as service:
        producer, consumer = service.apply_planning_proposal(proposal)
        producer.status = TaskStatus.FAILED
        producer.metadata["artifact_ready"] = True
        producer.metadata["integration_status"] = "INTEGRATED"
        producer.metadata["integration_revision"] = "deadbeef"
        service.store.save_task(producer)

        changed = service.release_planner_dependencies(proposal_id=proposal.proposal_id)

        assert [task.task_id for task in changed] == [consumer.task_id]
        durable = service.store.load_task(consumer.task_id)
        assert durable.status is TaskStatus.FAILED
        assert durable.metadata["planner_dependency_state"] == "failed"
        with pytest.raises(KeyError):
            service.queue.snapshot(consumer.task_id)
        assert service.store.has_event(consumer.task_id, "task.planner_dependency_failed")
