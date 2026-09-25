from __future__ import annotations

import inspect

import pytest

from src.dev_agent.domain.protocol import Task, TaskType
from src.dev_agent.intelligence.planner import ChildTaskProposal, RootPlanningProposal


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
