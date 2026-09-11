"""Crash-resistance tests for apply_proposal (Step 6).

Verifies that apply_proposal is idempotent:
- Re-calling with same proposal returns same children without error.
- Partial state (some children persisted, others not) is detected and
  the missing children are created while the existing ones are reused.
- Child task IDs are deterministic across calls.
"""

from __future__ import annotations

from src.dev_agent.domain.protocol import TaskStatus, TaskType
from src.dev_agent.intelligence.planner import (
    ChildTaskProposal,
    RootPlanningProposal,
)
from src.dev_agent.operation import OperationConfig, OperationService
from src.dev_agent.operation_planning import _child_task_id


def _config(tmp_path):
    return OperationConfig(
        data_dir=tmp_path,
        provider_id="fake",
        model="deterministic",
        worker_id="idempotency-test-worker",
        idle_sleep_seconds=0.01,
    )


def _root(config):
    return OperationService.submit(
        config,
        "coordinate idempotency test",
        task_type=TaskType.REASONING,
    )


def _proposal(parent_task_id: str, *, proposal_id: str = "fixed-proposal-id") -> RootPlanningProposal:
    return RootPlanningProposal(
        parent_task_id=parent_task_id,
        rationale="split implementation and verification",
        proposal_id=proposal_id,
        children=(
            ChildTaskProposal(
                child_key="impl",
                objective="implement the feature",
                task_type=TaskType.WORKER,
            ),
            ChildTaskProposal(
                child_key="verify",
                objective="verify the implementation",
                task_type=TaskType.DETERMINISTIC,
                dependencies=("impl",),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Deterministic child ID
# ---------------------------------------------------------------------------


def test_child_task_id_is_deterministic():
    a = _child_task_id("proposal-1", "impl")
    b = _child_task_id("proposal-1", "impl")
    assert a == b


def test_child_task_id_differs_by_child_key():
    a = _child_task_id("proposal-1", "impl")
    b = _child_task_id("proposal-1", "verify")
    assert a != b


def test_child_task_id_differs_by_proposal_id():
    a = _child_task_id("proposal-1", "impl")
    b = _child_task_id("proposal-2", "impl")
    assert a != b


# ---------------------------------------------------------------------------
# Full idempotency: apply twice → same result, no error
# ---------------------------------------------------------------------------


def test_apply_proposal_twice_returns_same_children(tmp_path):
    config = _config(tmp_path)
    root = _root(config)
    proposal = _proposal(root.task_id)

    with OperationService.open(config) as service:
        first = service.apply_planning_proposal(proposal)
        second = service.apply_planning_proposal(proposal)

    assert len(first) == len(second) == 2
    assert [t.task_id for t in first] == [t.task_id for t in second]


def test_apply_proposal_twice_task_ids_match_deterministic_formula(tmp_path):
    config = _config(tmp_path)
    root = _root(config)
    proposal = _proposal(root.task_id)

    with OperationService.open(config) as service:
        children = service.apply_planning_proposal(proposal)

    assert children[0].task_id == _child_task_id(proposal.proposal_id, "impl")
    assert children[1].task_id == _child_task_id(proposal.proposal_id, "verify")


# ---------------------------------------------------------------------------
# Partial-state recovery: simulate crash after first child saved
# ---------------------------------------------------------------------------


def test_apply_proposal_recovers_from_partial_state(tmp_path):
    """Crash after saving only the first child → second call creates the rest."""

    config = _config(tmp_path)
    root = _root(config)
    proposal = _proposal(root.task_id)

    # First call: intercept save_task and only persist the first child.
    with OperationService.open(config) as service:
        original_save = service.store.save_task
        save_calls: list[str] = []

        def partial_save(task):
            save_calls.append(task.task_id)
            if len(save_calls) == 1:
                # Save only the first child; abort the rest (simulating a crash).
                original_save(task)

        service.store.save_task = partial_save  # type: ignore[method-assign]
        try:
            service.apply_planning_proposal(proposal)
        except Exception:
            pass  # crash after first save — expected in this simulation

    # Restore the real save_task for the second call.
    with OperationService.open(config) as service:
        children = service.apply_planning_proposal(proposal)

    # Both children must be present now.
    assert len(children) == 2
    keys = [t.metadata.get("planner_child_key") for t in children]
    assert "impl" in keys
    assert "verify" in keys


def test_apply_proposal_partial_recovery_does_not_duplicate_existing_child(tmp_path):
    """Re-running after partial state must not double-enqueue the existing child."""

    config = _config(tmp_path)
    root = _root(config)
    proposal = _proposal(root.task_id)

    # Save only the first child manually (simulate crash mid-apply).
    with OperationService.open(config) as service:
        from src.dev_agent.domain.protocol import Task
        from src.dev_agent.operation_planning import _child_task_id

        impl_id = _child_task_id(proposal.proposal_id, "impl")
        partial_task = Task(
            task_id=impl_id,
            objective="implement the feature",
            parent_task_id=root.task_id,
            root_task_id=root.task_id,
            depth=root.depth + 1,
            status=TaskStatus.QUEUED,
            task_type=TaskType.WORKER,
            metadata={
                "planning_proposal_id": proposal.proposal_id,
                "planner_child_key": "impl",
            },
            constraints={
                "planner_proposal_id": proposal.proposal_id,
                "planner_child_key": "impl",
                "planner_dependencies": [],
                "planner_dependency_types": {},
            },
        )
        service.store.save_task(partial_task)

    # Second call must complete the proposal without duplicating impl.
    with OperationService.open(config) as service:
        children = service.apply_planning_proposal(proposal)

    impl_children = [t for t in children if t.metadata.get("planner_child_key") == "impl"]
    assert len(impl_children) == 1, "impl child must appear exactly once"
    assert impl_children[0].task_id == _child_task_id(proposal.proposal_id, "impl")
