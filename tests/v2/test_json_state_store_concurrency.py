"""JsonStateStore concurrent-write race regression.

_flush() writes to a single fixed temp path (<path>.tmp) before an atomic
rename. Without a lock guarding the read-modify-write + flush sequence,
concurrent threads calling into the same store (e.g. Controller.cancel()
racing a durable commit_transition() from the task's own execution thread)
could both write that same temp file at once, corrupting it, losing a
write, or raising FileNotFoundError when one thread's Path.replace() runs
between another thread's write and its own replace.
"""

from __future__ import annotations

import json
import threading

from src.dev_agent.domain.protocol import Event, Task, TaskStatus
from src.dev_agent.state.json_store import JsonStateStore


def test_concurrent_save_task_calls_do_not_corrupt_or_lose_data(tmp_path):
    store = JsonStateStore(tmp_path / "state.json")
    task_count = 40
    tasks = [Task(objective=f"task-{i}", status=TaskStatus.QUEUED) for i in range(task_count)]

    errors: list[BaseException] = []

    def _save(task: Task) -> None:
        try:
            store.save_task(task)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_save, args=(task,)) for task in tasks]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    assert not errors, f"concurrent save_task raised: {errors}"
    # No FileNotFoundError, no corrupted JSON: the file must be valid JSON
    # and every task must be present -- none lost to a lost write race.
    on_disk = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert len(on_disk["tasks"]) == task_count
    for task in tasks:
        assert task.task_id in on_disk["tasks"]

    # A freshly loaded store must see every task too (not just whatever
    # happened to be the final in-memory `_data` before the last flush).
    reloaded = JsonStateStore(tmp_path / "state.json")
    assert len(reloaded.snapshot()["tasks"]) == task_count


def test_concurrent_request_cancellation_and_commit_transition_do_not_race(tmp_path):
    """The audit's specifically named hot path: request_cancellation()
    (called from a canceling caller's thread) racing commit_transition()
    (called from the task's own executing thread) on the same store."""
    store = JsonStateStore(tmp_path / "state.json")
    task = Task(objective="racy task", status=TaskStatus.RUNNING)
    store.save_task(task)

    errors: list[BaseException] = []
    iterations = 50

    def _cancel_loop() -> None:
        for _ in range(iterations):
            try:
                store.request_cancellation(task.task_id, reason="stress test cancel")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    def _commit_loop() -> None:
        for i in range(iterations):
            try:
                store.commit_transition(
                    event=Event(event_type="step.progress", task_id=task.task_id, payload={"iteration": i}),
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=_cancel_loop), threading.Thread(target=_commit_loop)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15.0)

    assert not errors, f"concurrent request_cancellation/commit_transition raised: {errors}"

    on_disk = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert on_disk["tasks"][task.task_id] is not None
    # Every committed event must be present -- none silently dropped by a
    # lost write.
    assert len(on_disk["events"]) == iterations
    # The cancellation request itself must be durably recorded.
    assert any(item.get("control_type") == "cancellation_requested" for item in on_disk["task_controls"])


def test_concurrent_effect_intent_transitions_are_serialized(tmp_path):
    """Multiple threads racing create_effect_intent/claim_effect_intent for
    DIFFERENT keys on the same store must all succeed without corrupting
    each other's entries."""
    store = JsonStateStore(tmp_path / "state.json")
    task = Task(objective="effect task", status=TaskStatus.RUNNING)
    store.save_task(task)
    key_count = 30
    keys = [f"effect:{i}" for i in range(key_count)]

    errors: list[BaseException] = []

    def _worker(key: str) -> None:
        try:
            store.create_effect_intent(key, task_id=task.task_id, tool_name="tool", arguments={})
            store.claim_effect_intent(key, expected_statuses={"pending"})
            store.transition_effect_intent(key, to_status="succeeded", result={"ok": True})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(key,)) for key in keys]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15.0)

    assert not errors, f"concurrent effect intent transitions raised: {errors}"
    on_disk = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert len(on_disk["effect_intents"]) == key_count
    for key in keys:
        assert on_disk["effect_intents"][key]["status"] == "succeeded"


def test_flush_uses_a_fixed_temp_path_but_lock_prevents_the_race(tmp_path):
    """Directly exercises many overlapping _flush() calls (via save_step,
    a distinct method from the other tests here) to confirm the shared
    <path>.tmp file is never read back in a half-written state."""
    from src.dev_agent.domain.protocol import Step
    from uuid import uuid4

    store = JsonStateStore(tmp_path / "state.json")
    step_count = 60
    steps = [Step(task_id=str(uuid4()), order=i, kind="tool_call") for i in range(step_count)]

    errors: list[BaseException] = []

    def _save(step) -> None:
        try:
            store.save_step(step)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_save, args=(step,)) for step in steps]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    assert not errors
    # The file must always be valid, complete JSON -- never truncated or
    # interleaved from two concurrent writers.
    text = (tmp_path / "state.json").read_text(encoding="utf-8")
    parsed = json.loads(text)
    assert len(parsed["steps"]) == step_count
