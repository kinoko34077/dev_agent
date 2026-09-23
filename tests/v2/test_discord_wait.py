from __future__ import annotations

from datetime import datetime, timezone
from time import time

import pytest

from src.dev_agent.discord.wait import (
    USER_DELAY_REASON,
    UserDelayRequest,
    defer_user_delay,
    parse_user_delay,
)
from src.dev_agent.domain.protocol import Task, TaskStatus
from src.dev_agent.scheduler.queue import DurableQueue


def test_parse_user_delay_accepts_bounded_japanese_and_english_requests() -> None:
    assert parse_user_delay("20秒待ってから返事して") == 20
    assert parse_user_delay("wait 10 seconds before replying") == 10
    assert parse_user_delay("1分後に続けて") == 60


@pytest.mark.parametrize(
    "text",
    ["すぐ返事して", "0秒待って", "-1 seconds", "wait 3601 seconds"],
)
def test_parse_user_delay_rejects_ambiguous_or_out_of_bounds_requests(text: str) -> None:
    assert parse_user_delay(text) is None


def test_defer_user_delay_uses_existing_durable_queue_and_metadata(tmp_path) -> None:
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    task = Task(objective="reply after a wait")
    now_epoch = time()
    queue.enqueue(task.task_id, run_at=now_epoch)
    claimed = queue.claim("worker", now=now_epoch, lease_seconds=30.0)

    request = UserDelayRequest(seconds=20, source="20秒待ってから返事して")
    wake_epoch = defer_user_delay(
        task,
        request,
        queue,
        worker_id="worker",
        state_version=claimed.state_version,
        now_epoch=now_epoch,
    )

    assert wake_epoch == now_epoch + 20
    assert task.status is TaskStatus.WAITING_DEPENDENCY
    assert task.metadata["wait_reason"] == USER_DELAY_REASON
    assert task.metadata["wait_until_epoch"] == wake_epoch
    parked = queue.snapshot(task.task_id)
    assert parked.state == "waiting"
    assert parked.wake_reason == USER_DELAY_REASON
    assert parked.wake_at == datetime.fromtimestamp(wake_epoch, timezone.utc)
    stored_wake_epoch = parked.wake_at.timestamp()
    assert queue.wake_due(now=stored_wake_epoch - 0.1, reason=USER_DELAY_REASON) == 0
    assert queue.wake_due(now=stored_wake_epoch + 0.1, reason=USER_DELAY_REASON) == 1
    assert queue.snapshot(task.task_id).state == "queued"
    queue.close()


def test_user_delay_request_is_bounded_and_immutable() -> None:
    request = UserDelayRequest(seconds=20, source="20秒")
    with pytest.raises((AttributeError, TypeError)):
        request.seconds = 21  # type: ignore[misc]


def test_queued_user_delay_wakes_once_without_a_worker_lease(tmp_path) -> None:
    import uuid

    queue = DurableQueue(tmp_path / "queue.sqlite3")
    task_id = str(uuid.uuid4())
    queue.enqueue(task_id)
    item = queue.defer_queued_until(task_id, wake_at=200.0, reason=USER_DELAY_REASON)
    assert item.state == "waiting"
    assert queue.wake_due(now=199.9, reason=USER_DELAY_REASON) == 0
    assert queue.wake_due(now=200.0, reason=USER_DELAY_REASON) == 1
    assert queue.wake_due(now=201.0, reason=USER_DELAY_REASON) == 0
    queue.close()
