from __future__ import annotations

import pytest

from src.dev_agent.domain.protocol import Task, TaskStatus
from src.dev_agent.human import HumanRequest, HumanResponse, SQLiteHumanInteractionPort
from src.dev_agent.intelligence.lifecycle import TaskLifecycleCoordinator
from src.dev_agent.scheduler.queue import DurableQueue
from src.dev_agent.scheduler.worker import WorkerRunner
from src.dev_agent.state.sqlite_store import SQLiteStateStore


def _request() -> HumanRequest:
    return HumanRequest(
        request_id="human-request-1",
        root_id="root-1",
        task_id="task-1",
        attempt_id="attempt-1",
        reason="protected path requires a decision",
        question="May the protected path change be proposed?",
        context={"path": "spec/v2/GATE_STATUS.json", "api_key": "do-not-store"},
        allowed_answers=("allow", "deny"),
        response_shape={"decision": "allow|deny"},
    )


def test_waiting_human_is_a_durable_task_status_roundtrip():
    task = Task(objective="wait for a bounded human decision", status=TaskStatus.WAITING_HUMAN)

    restored = Task.from_dict(task.to_dict())

    assert restored.status is TaskStatus.WAITING_HUMAN


def test_human_request_is_bounded_and_redacts_sensitive_context():
    request = _request()

    assert request.to_dict()["context"]["api_key"] == "[REDACTED]"
    assert request.required_authority == "HUMAN_REQUIRED"
    assert request.to_dict()["request_id"] == "human-request-1"


def test_human_response_is_correlated_and_consumed_once(tmp_path):
    path = tmp_path / "human.sqlite3"
    request = _request()
    response = HumanResponse(
        request_id=request.request_id,
        responder="human:operator",
        response={"decision": "deny"},
        decision="deny",
        received_at="2026-09-23T00:00:00+00:00",
    )

    with SQLiteStateStore(path) as store:
        store.save_human_request(request)
        store.save_human_response(response)
        consumed = store.consume_human_response(request.request_id)

        assert consumed == response
        with pytest.raises(ValueError, match="already consumed"):
            store.consume_human_response(request.request_id)


def test_human_response_cannot_be_saved_for_another_request(tmp_path):
    path = tmp_path / "human-correlation.sqlite3"
    request = _request()
    response = HumanResponse(
        request_id="different-request",
        responder="human:operator",
        response={"decision": "allow"},
        decision="allow",
        received_at="2026-09-23T00:00:00+00:00",
    )

    with SQLiteStateStore(path) as store:
        store.save_human_request(request)
        with pytest.raises(KeyError, match="different-request"):
            store.save_human_response(response)


def test_unanswered_human_request_survives_store_restart(tmp_path):
    path = tmp_path / "human-restart.sqlite3"
    request = _request()

    with SQLiteStateStore(path) as store:
        store.save_human_request(request)

    with SQLiteStateStore(path) as reopened:
        pending = reopened.list_pending_human_requests()

    assert pending == [request]


def test_sqlite_human_port_preserves_poll_then_single_consume(tmp_path):
    path = tmp_path / "human-port.sqlite3"
    request = _request()
    response = HumanResponse(
        request_id=request.request_id,
        responder="human:operator",
        response={"decision": "allow"},
        decision="allow",
        received_at="2026-09-23T00:00:00+00:00",
    )

    with SQLiteStateStore(path) as store:
        port = SQLiteHumanInteractionPort(store)
        port.request_human(request)
        assert port.poll_response(request.request_id) is None
        store.save_human_response(response)
        assert port.poll_response(request.request_id) == response
        assert port.consume_response(request.request_id) == response


def test_waiting_human_is_deferred_without_blocking_other_ready_work(tmp_path):
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    waiting = Task(objective="needs a human", status=TaskStatus.QUEUED)
    ready = Task(objective="independent work", status=TaskStatus.QUEUED)

    class Controller:
        def __init__(self, store):
            self.store = store

        def resume(self, task_id, *, execution_context=None):
            current = self.store.load_task(task_id)
            if current.task_id == waiting.task_id:
                current.status = TaskStatus.WAITING_HUMAN
            else:
                current.status = TaskStatus.COMPLETED
            self.store.save_task(current)
            return current

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        store.save_task(waiting)
        store.save_task(ready)
        queue.enqueue(waiting.task_id, priority=10)
        queue.enqueue(ready.task_id, priority=1)
        runner = WorkerRunner(queue, Controller(store), worker_id="worker-a")

        first = runner.run_once()
        assert first.status is TaskStatus.WAITING_HUMAN
        assert queue.snapshot(waiting.task_id).state == "waiting"
        second = runner.run_once()
        assert second.status is TaskStatus.COMPLETED
        assert queue.snapshot(ready.task_id).state == "completed"


def test_lifecycle_parks_and_resumes_exact_human_request(tmp_path):
    path = tmp_path / "human-lifecycle.sqlite3"
    request = HumanRequest(
        request_id="human-request-lifecycle",
        root_id="root-lifecycle",
        task_id="11111111-1111-4111-8111-111111111111",
        attempt_id="attempt-lifecycle",
        reason="protected path requires a decision",
        question="May the protected path change be proposed?",
        context={"path": "spec/v2/GATE_STATUS.json"},
        allowed_answers=("allow", "deny"),
        response_shape={"decision": "allow|deny"},
    )
    task = Task(objective="protected decision", status=TaskStatus.RUNNING, task_id=request.task_id)

    with SQLiteStateStore(path) as store:
        store.save_task(task)
        lifecycle = TaskLifecycleCoordinator(store)
        parked = lifecycle.park_for_human(task.task_id, request)
        assert parked.task.status is TaskStatus.WAITING_HUMAN
        assert store.get_human_request(request.request_id) == request

        response = HumanResponse(
            request_id=request.request_id,
            responder="human:operator",
            response={"decision": "deny"},
            decision="deny",
            received_at="2026-09-23T00:00:00+00:00",
        )
        store.save_human_response(response)
        resumed, consumed = lifecycle.consume_human_response(task.task_id, request.request_id)

        assert resumed.task.status is TaskStatus.READY
        assert consumed == response
        assert store.load_task(task.task_id).status is TaskStatus.READY
        with pytest.raises(ValueError, match="already consumed"):
            lifecycle.consume_human_response(task.task_id, request.request_id)
