from __future__ import annotations

from dev_agent.domain.protocol import Step, StepStatus, Task, TaskStatus
from dev_agent.providers.fake.provider import FakeProvider
from dev_agent.runtime.controller import Controller
from dev_agent.state.sqlite_store import SQLiteStateStore
from dev_agent.tools.runtime import ToolRegistry, ToolRuntime


def _controller(tmp_path):
    store = SQLiteStateStore(tmp_path / "state.sqlite3")
    return Controller(FakeProvider(), ToolRuntime(ToolRegistry()), store), store


def _step(task: Task) -> Step:
    return Step(task_id=task.task_id, order=0, kind="wait", status=StepStatus.WAITING)


def test_resource_wait_producer_persists_typed_condition(tmp_path) -> None:
    controller, store = _controller(tmp_path)
    try:
        task = Task(objective="wait for a route")
        controller._wait_for_resource(
            task,
            {},
            step=_step(task),
            category="no_route",
            message="no eligible route",
        )

        condition = task.metadata["wait_condition"]
        assert condition["kind"] == "resource"
        assert condition["wake_authority"] == "resource_observation"
        assert condition["wake_predicate"] == "eligible_route_observed"
        assert task.status is TaskStatus.WAITING_DEPENDENCY
    finally:
        store.close()


def test_user_delay_producer_persists_checkpoint_condition(tmp_path) -> None:
    controller, store = _controller(tmp_path)
    try:
        task = Task(objective="resume after a delay")
        controller._park_for_user_delay(
            task,
            {},
            {
                "accepted_at_epoch": 100.0,
                "wake_at_epoch": 120.0,
                "delay_seconds": 20,
            },
        )

        condition = task.metadata["wait_condition"]
        assert condition["kind"] == "user_delay"
        assert condition["deadline_epoch"] == 120.0
        assert condition["replay_policy"] == "resume_checkpoint"
    finally:
        store.close()


def test_reconciliation_producer_persists_no_replay_condition(tmp_path) -> None:
    controller, store = _controller(tmp_path)
    try:
        task = Task(objective="reconcile provider effect")
        controller._provider_waiting_reconciliation(
            task,
            {},
            step=_step(task),
            request_id="00000000-0000-4000-8000-000000000001",
            cause="provider_decode",
            message="response outcome is unknown",
        )

        condition = task.metadata["wait_condition"]
        assert condition["kind"] == "reconciliation"
        assert condition["replay_policy"] == "no_external_replay"
        assert condition["wake_predicate"] == "effect_outcome_confirmed"
    finally:
        store.close()


def test_quota_producer_preserves_observed_domain_for_targeted_wake(tmp_path) -> None:
    controller, store = _controller(tmp_path)
    try:
        task = Task(objective="wait for quota", metadata={"quota_domain": "domain-a"})
        controller._block_quota(task, {}, step=_step(task), message="quota exhausted")

        condition = task.metadata["wait_condition"]
        assert condition["kind"] == "quota"
        assert condition["subject"] == "domain-a"
        assert condition["queue_reason"] == "quota:domain-a"
    finally:
        store.close()
