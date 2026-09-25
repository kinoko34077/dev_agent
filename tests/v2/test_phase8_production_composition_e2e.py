from __future__ import annotations

import inspect

import pytest

from src.dev_agent.domain.protocol import Task, TaskStatus, TaskType
from src.dev_agent.intelligence.planner import ChildTaskProposal, RootPlanningProposal
from src.dev_agent.operation import OperationConfig, OperationError, OperationService


def test_phase8_composition_delegates_submission_and_observation_without_resequencing():
    from scripts.devfarm_production_composition import Phase8ProductionComposition

    calls = []
    composition = Phase8ProductionComposition(
        submit_boundary=lambda objective, **kwargs: calls.append(("submit", objective, kwargs))
        or {"run_id": "run-1", "status": "SUBMITTED"},
        observe_boundary=lambda run_id, **kwargs: calls.append(("observe", run_id, kwargs))
        or {"run_id": run_id, "status": "COMPLETED"},
    )

    assert composition.submit("one fresh root", source="test") == {
        "run_id": "run-1",
        "status": "SUBMITTED",
    }
    assert composition.observe("run-1", view="bounded") == {
        "run_id": "run-1",
        "status": "COMPLETED",
    }
    assert calls == [
        ("submit", "one fresh root", {"source": "test"}),
        ("observe", "run-1", {"view": "bounded"}),
    ]


def test_phase8_composition_rejects_non_callable_boundary():
    from scripts.devfarm_production_composition import Phase8ProductionComposition

    with pytest.raises(TypeError, match="boundary"):
        Phase8ProductionComposition(
            submit_boundary=None,
            observe_boundary=lambda run_id: {},
        )


def test_phase8_composition_rejects_non_mapping_boundary_result():
    from scripts.devfarm_production_composition import (
        Phase8ProductionComposition,
        ProductionCompositionError,
    )

    composition = Phase8ProductionComposition(
        submit_boundary=lambda objective, **kwargs: ["raw"],
        observe_boundary=lambda run_id, **kwargs: {"run_id": run_id},
    )

    with pytest.raises(ProductionCompositionError, match="mapping"):
        composition.submit("bounded objective")


def test_phase8_composition_rejects_secret_shaped_projection_fields():
    from scripts.devfarm_production_composition import (
        Phase8ProductionComposition,
        ProductionCompositionError,
    )

    composition = Phase8ProductionComposition(
        submit_boundary=lambda objective, **kwargs: {
            "run_id": "run-1",
            "api_key": "must-not-cross",
        },
        observe_boundary=lambda run_id, **kwargs: {"run_id": run_id},
    )

    with pytest.raises(ProductionCompositionError, match="bounded"):
        composition.submit("bounded objective")


def test_phase8_production_composition_exposes_only_submission_and_observation_boundary():
    """The E2E driver must not become a shadow orchestrator.

    Phase 8 composition owns Planner -> Worker -> Host Verification -> Reviewer
    -> Integration -> dependency release internally.  A caller/test submits one
    root and observes durable state; it must not sequence those authorities.
    """

    from scripts.devfarm_production_composition import Phase8ProductionComposition

    public = {
        name
        for name, member in inspect.getmembers(Phase8ProductionComposition)
        if callable(member) and not name.startswith("_")
    }

    assert public == {"submit", "observe"}


def test_phase8_composition_joins_operation_and_commander_projections_by_child_key():
    from scripts.devfarm_production_composition import _development_task_links

    proposal = RootPlanningProposal(
        parent_task_id="11111111-1111-4111-8111-111111111111",
        proposal_id="phase8-proposal",
        rationale="two bounded implementation children",
        children=(
            ChildTaskProposal(child_key="worker-a", objective="change a", task_type=TaskType.WORKER),
            ChildTaskProposal(child_key="worker-b", objective="change b", task_type=TaskType.WORKER),
        ),
    )
    operation_children = tuple(
        Task(
            task_id=task_id,
            objective=child.objective,
            task_type=TaskType.WORKER,
            metadata={
                "planning_proposal_id": proposal.proposal_id,
                "planner_child_key": child.child_key,
            },
        )
        for child, task_id in zip(
            proposal.children,
            (
                "22222222-2222-4222-8222-222222222222",
                "33333333-3333-4333-8333-333333333333",
            ),
            strict=True,
        )
    )
    commander_tasks = (
        {
            "task_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "planning_proposal_id": proposal.proposal_id,
            "planner_child_key": "worker-a",
        },
        {
            "task_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "planning_proposal_id": proposal.proposal_id,
            "planner_child_key": "worker-b",
        },
    )

    links = _development_task_links(proposal, operation_children, commander_tasks)

    assert [(link.child_key, link.operation_task_id, link.commander_task_id) for link in links] == [
        (
            "worker-a",
            "22222222-2222-4222-8222-222222222222",
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        ),
        (
            "worker-b",
            "33333333-3333-4333-8333-333333333333",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        ),
    ]


def _composition_inputs():
    proposal = RootPlanningProposal(
        parent_task_id="11111111-1111-4111-8111-111111111111",
        proposal_id="phase8-proposal",
        rationale="two bounded implementation children",
        children=(
            ChildTaskProposal(child_key="worker-a", objective="change a", task_type=TaskType.WORKER),
            ChildTaskProposal(child_key="worker-b", objective="change b", task_type=TaskType.WORKER),
        ),
    )
    operation_children = tuple(
        Task(
            task_id=task_id,
            objective=child.objective,
            task_type=TaskType.WORKER,
            metadata={
                "planning_proposal_id": proposal.proposal_id,
                "planner_child_key": child.child_key,
            },
        )
        for child, task_id in zip(
            proposal.children,
            (
                "22222222-2222-4222-8222-222222222222",
                "33333333-3333-4333-8333-333333333333",
            ),
            strict=True,
        )
    )
    commander_tasks = (
        {
            "task_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "planning_proposal_id": proposal.proposal_id,
            "planner_child_key": "worker-a",
        },
        {
            "task_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "planning_proposal_id": proposal.proposal_id,
            "planner_child_key": "worker-b",
        },
    )
    return proposal, operation_children, commander_tasks


def test_phase8_composition_reordered_commander_tasks_still_join_by_identity():
    from scripts.devfarm_production_composition import _development_task_links

    proposal, operation_children, commander_tasks = _composition_inputs()

    links = _development_task_links(proposal, operation_children, tuple(reversed(commander_tasks)))

    assert [(link.child_key, link.commander_task_id) for link in links] == [
        ("worker-a", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ("worker-b", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda task: task.pop("planning_proposal_id"), "planner identity"),
        (lambda task: task.pop("planner_child_key"), "child key"),
        (lambda task: task.update(planning_proposal_id="other-proposal"), "proposal"),
        (lambda task: task.update(planner_child_key="unknown"), "child key"),
        (lambda task: task.update(planner_child_key="worker-b"), "duplicate"),
    ],
)
def test_phase8_composition_rejects_invalid_commander_identity(mutation, message):
    from scripts.devfarm_production_composition import (
        ProductionCompositionError,
        _development_task_links,
    )

    proposal, operation_children, commander_tasks = _composition_inputs()
    invalid = [dict(task) for task in commander_tasks]
    mutation(invalid[0])

    with pytest.raises(ProductionCompositionError, match=message):
        _development_task_links(proposal, operation_children, tuple(invalid))


def test_phase8_composition_rejects_missing_commander_child_identity():
    from scripts.devfarm_production_composition import (
        ProductionCompositionError,
        _development_task_links,
    )

    proposal, operation_children, commander_tasks = _composition_inputs()

    with pytest.raises(ProductionCompositionError, match="missing planner children"):
        _development_task_links(proposal, operation_children, commander_tasks[:1])


def _handoff_config(tmp_path):
    return OperationConfig(
        data_dir=tmp_path,
        provider_id="fake",
        model="deterministic",
        worker_id="phase8-handoff-test-worker",
        idle_sleep_seconds=0.01,
    )


def _handoff_proposal(parent_task_id: str) -> RootPlanningProposal:
    return RootPlanningProposal(
        parent_task_id=parent_task_id,
        proposal_id="phase8-handoff-proposal",
        rationale="two bounded production children",
        children=(
            ChildTaskProposal(
                child_key="worker-a",
                objective="implement bounded change a",
                task_type=TaskType.WORKER,
            ),
            ChildTaskProposal(
                child_key="worker-b",
                objective="implement bounded change b",
                task_type=TaskType.WORKER,
            ),
        ),
    )


def test_phase8_devfarm_handoff_claims_all_children_without_operation_queue(tmp_path):
    config = _handoff_config(tmp_path)
    root = OperationService.submit(config, "phase8 production handoff")
    proposal = _handoff_proposal(root.task_id)

    with OperationService.open(config) as service:
        children = service.apply_planning_proposal(proposal, execution_owner="devfarm")
        handed_off = service.handoff_planning_children_to_devfarm(
            proposal_id=proposal.proposal_id,
            run_id="devfarm-run-1",
            bindings={
                "worker-a": "devfarm-task-a",
                "worker-b": "devfarm-task-b",
            },
        )

        assert [task.task_id for task in handed_off] == [task.task_id for task in children]
        for task in handed_off:
            assert task.status is TaskStatus.WAITING_DEPENDENCY
            assert task.metadata["execution_owner"] == "devfarm"
            assert task.metadata["handoff_state"] == "DEVFARM_OWNED"
            assert task.metadata["devfarm_run_id"] == "devfarm-run-1"
            assert task.metadata["devfarm_task_id"] == f"devfarm-task-{task.metadata['planner_child_key'][-1]}"
            with pytest.raises(KeyError):
                service.queue.snapshot(task.task_id)

        # A repeated observation of the same durable handoff is idempotent.
        repeated = service.handoff_planning_children_to_devfarm(
            proposal_id=proposal.proposal_id,
            run_id="devfarm-run-1",
            bindings={
                "worker-a": "devfarm-task-a",
                "worker-b": "devfarm-task-b",
            },
        )
        assert [task.task_id for task in repeated] == [task.task_id for task in children]

    # Reopening the existing Operation state must not make the handoff
    # claimable by the Operation worker.
    with OperationService.open(config) as service:
        persisted = [service.store.load_task(task.task_id) for task in children]
        assert all(task is not None and task.metadata["handoff_state"] == "DEVFARM_OWNED" for task in persisted)
        for task in children:
            with pytest.raises(KeyError):
                service.queue.snapshot(task.task_id)


def test_phase8_devfarm_handoff_rejects_partial_or_conflicting_binding_without_mutation(tmp_path):
    config = _handoff_config(tmp_path)
    root = OperationService.submit(config, "phase8 invalid handoff")
    proposal = _handoff_proposal(root.task_id)

    with OperationService.open(config) as service:
        pending = service.apply_planning_proposal(proposal, execution_owner="devfarm")

        with pytest.raises(OperationError, match="exact child|missing|binding"):
            service.handoff_planning_children_to_devfarm(
                proposal_id=proposal.proposal_id,
                run_id="devfarm-run-invalid",
                bindings={"worker-a": "devfarm-task-a"},
            )
        assert all(
            service.store.load_task(task.task_id).metadata["handoff_state"] == "HANDOFF_PENDING"
            for task in pending
        )

        with pytest.raises(OperationError, match="duplicate"):
            service.handoff_planning_children_to_devfarm(
                proposal_id=proposal.proposal_id,
                run_id="devfarm-run-invalid",
                bindings={
                    "worker-a": "same-devfarm-task",
                    "worker-b": "same-devfarm-task",
                },
            )

        children = service.handoff_planning_children_to_devfarm(
            proposal_id=proposal.proposal_id,
            run_id="devfarm-run-2",
            bindings={
                "worker-a": "devfarm-task-a",
                "worker-b": "devfarm-task-b",
            },
        )
        with pytest.raises(OperationError, match="conflict|different"):
            service.handoff_planning_children_to_devfarm(
                proposal_id=proposal.proposal_id,
                run_id="devfarm-run-3",
                bindings={
                    "worker-a": "other-task-a",
                    "worker-b": "devfarm-task-b",
                },
            )
        assert all(task.metadata["devfarm_run_id"] == "devfarm-run-2" for task in children)


def test_phase8_devfarm_handoff_rejects_a_child_already_owned_by_operation_queue(tmp_path):
    config = _handoff_config(tmp_path)
    root = OperationService.submit(config, "phase8 queued ownership conflict")
    proposal = _handoff_proposal(root.task_id)

    with OperationService.open(config) as service:
        children = service.apply_planning_proposal(proposal, execution_owner="devfarm")
        service.queue.enqueue(children[0].task_id, priority=0, max_attempts=1)

        with pytest.raises(OperationError, match="Operation queue"):
            service.handoff_planning_children_to_devfarm(
                proposal_id=proposal.proposal_id,
                run_id="devfarm-run-queued-conflict",
                bindings={
                    "worker-a": "devfarm-task-a",
                    "worker-b": "devfarm-task-b",
                },
            )

        assert all(
            service.store.load_task(task.task_id).metadata["handoff_state"] == "HANDOFF_PENDING"
            for task in children
        )
