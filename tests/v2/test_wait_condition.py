from __future__ import annotations

import pytest

from dev_agent.domain.protocol import TaskStatus
from dev_agent.scheduler.worker import WorkerRunner
from dev_agent.domain.wait import (
    WAIT_CONDITION_REGISTRY,
    WaitCondition,
    WaitKind,
    WaitReplayPolicy,
    condition_from_task_metadata,
)


def test_wait_registry_covers_every_deferred_task_status() -> None:
    deferred = {
        TaskStatus.WAITING_DEPENDENCY,
        TaskStatus.WAITING_APPROVAL,
        TaskStatus.WAITING_HUMAN,
        TaskStatus.WAITING_RECONCILIATION,
        TaskStatus.BLOCKED_QUOTA,
        TaskStatus.BLOCKED_BUDGET,
    }

    assert deferred <= set(WAIT_CONDITION_REGISTRY)
    for status in deferred:
        entry = WAIT_CONDITION_REGISTRY[status]
        assert entry.kind in set(WaitKind)
        assert entry.wake_authority
        assert entry.wake_predicate
        assert entry.replay_policy in set(WaitReplayPolicy)
    assert set(WorkerRunner._DEFERRED_STATUSES) == set(WAIT_CONDITION_REGISTRY)


def test_user_delay_condition_round_trips_with_deadline() -> None:
    condition = condition_from_task_metadata(
        TaskStatus.WAITING_DEPENDENCY,
        {
            "wait_reason": "user_delay",
            "wait_until_epoch": 1234.5,
        },
    )

    assert condition.kind is WaitKind.USER_DELAY
    assert condition.deadline_epoch == 1234.5
    assert condition.wake_authority == "runtime_maintenance"
    assert condition.wake_predicate == "wake_at_reached"
    assert condition.replay_policy is WaitReplayPolicy.RESUME_CHECKPOINT
    assert WaitCondition.from_dict(condition.to_dict()) == condition


def test_provider_capacity_condition_targets_binding_or_pool() -> None:
    condition = condition_from_task_metadata(
        TaskStatus.WAITING_DEPENDENCY,
        {
            "wait_reason": "resource:provider_execution_saturated:gemini:worker:free-3",
        },
    )

    assert condition.kind is WaitKind.PROVIDER_CAPACITY
    assert condition.subject == "gemini:worker:free-3"
    assert condition.wake_authority == "provider_capacity"
    assert condition.wake_predicate == "binding_capacity_available"
    assert condition.replay_policy is WaitReplayPolicy.RESUME_CHECKPOINT


def test_reconciliation_condition_forbids_external_replay() -> None:
    condition = condition_from_task_metadata(
        TaskStatus.WAITING_RECONCILIATION,
        {"request_id": "request-1"},
    )

    assert condition.kind is WaitKind.RECONCILIATION
    assert condition.wake_authority == "reconciliation"
    assert condition.wake_predicate == "effect_outcome_confirmed"
    assert condition.replay_policy is WaitReplayPolicy.NO_EXTERNAL_REPLAY


def test_wait_condition_rejects_missing_future_predicate() -> None:
    with pytest.raises(ValueError, match="wake_predicate"):
        WaitCondition(
            kind=WaitKind.RESOURCE,
            subject="route",
            created_from="controller",
            wake_authority="resource_observation",
            wake_predicate="",
            replay_policy=WaitReplayPolicy.EVENT_ONLY,
        )


@pytest.mark.parametrize(
    ("reason", "kind", "authority"),
    [
        ("maintenance", WaitKind.MAINTENANCE, "maintenance"),
        ("quota_unknown:domain-a", WaitKind.QUOTA, "quota_requalification"),
        ("planner_dependency", WaitKind.DEPENDENCY, "dependency_graph"),
        ("interrupt", WaitKind.INTERRUPT, "interrupt_resume"),
        ("resource:no_route", WaitKind.RESOURCE, "resource_observation"),
    ],
)
def test_legacy_wait_reasons_have_one_wake_authority(reason, kind, authority) -> None:
    condition = condition_from_task_metadata(
        TaskStatus.WAITING_DEPENDENCY,
        {"wait_reason": reason},
    )

    assert condition.kind is kind
    assert condition.wake_authority == authority
    assert condition.wake_predicate
    assert condition.queue_reason == reason


def test_stale_condition_is_rebound_when_wait_reason_changes() -> None:
    stale = condition_from_task_metadata(
        TaskStatus.WAITING_DEPENDENCY,
        {"wait_reason": "user_delay", "wait_until_epoch": 120.0},
    ).to_dict()

    condition = condition_from_task_metadata(
        TaskStatus.WAITING_APPROVAL,
        {"wait_reason": "approval", "wait_condition": stale},
    )

    assert condition.kind is WaitKind.APPROVAL
    assert condition.queue_reason == "approval"


def test_malformed_typed_projection_rebinds_from_legacy_reason() -> None:
    condition = condition_from_task_metadata(
        TaskStatus.WAITING_DEPENDENCY,
        {
            "wait_reason": "user_delay",
            "wait_until_epoch": 321.0,
            "wait_condition": {"kind": "not-a-wait"},
        },
    )

    assert condition.kind is WaitKind.USER_DELAY
    assert condition.deadline_epoch == 321.0
    assert condition.queue_reason == "user_delay"
