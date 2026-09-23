"""Deterministic, durable user-requested delay handling for Discord input."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from typing import Final

from ..domain.protocol import Task, TaskStatus
from ..scheduler.queue import DurableQueue


USER_DELAY_REASON: Final[str] = "user_delay"
DEFAULT_MAX_DELAY_SECONDS: Final[int] = 3600

_DURATION_RE = re.compile(
    r"(?<!\d)(?P<amount>\d{1,4})\s*"
    r"(?P<unit>秒|分|seconds?|secs?|s|minutes?|mins?|m)(?![a-z])",
    re.IGNORECASE,
)
_WAIT_MARKERS = ("待って", "待機", "経過", "後", "wait", "after")


@dataclass(frozen=True)
class UserDelayRequest:
    """A validated finite delay extracted from a user request."""

    seconds: int
    source: str

    def __post_init__(self) -> None:
        if isinstance(self.seconds, bool) or not isinstance(self.seconds, int):
            raise ValueError("seconds must be an integer")
        if self.seconds <= 0 or self.seconds > DEFAULT_MAX_DELAY_SECONDS:
            raise ValueError("seconds must be between 1 and 3600")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be non-empty text")


def parse_user_delay(text: str, *, max_seconds: int = DEFAULT_MAX_DELAY_SECONDS) -> int | None:
    """Return an explicit bounded delay, or ``None`` for ordinary text."""

    if not isinstance(text, str) or not text.strip():
        return None
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, int) or max_seconds <= 0:
        raise ValueError("max_seconds must be a positive integer")
    normalized = text.strip()
    if not any(marker in normalized.casefold() for marker in _WAIT_MARKERS):
        return None
    match = _DURATION_RE.search(normalized)
    if match is None:
        return None
    amount = int(match.group("amount"))
    unit = match.group("unit").casefold()
    multiplier = 60 if unit in {"分", "minute", "minutes", "min", "mins", "m"} else 1
    seconds = amount * multiplier
    if seconds <= 0 or seconds > max_seconds:
        return None
    return seconds


def defer_user_delay(
    task: Task,
    request: UserDelayRequest,
    queue: DurableQueue,
    *,
    worker_id: str,
    state_version: int,
    now_epoch: float,
) -> float:
    """Park a claimed task through the existing durable queue boundary."""

    if not isinstance(task, Task):
        raise TypeError("task must be a Task")
    if not isinstance(request, UserDelayRequest):
        raise TypeError("request must be a UserDelayRequest")
    if not isinstance(queue, DurableQueue):
        raise TypeError("queue must be a DurableQueue")
    if not isinstance(now_epoch, (int, float)) or isinstance(now_epoch, bool) or not math.isfinite(now_epoch):
        raise ValueError("now_epoch must be a finite number")
    wake_epoch = float(now_epoch) + request.seconds
    if not math.isfinite(wake_epoch):
        raise ValueError("wake time must be finite")
    queue.defer_until(
        task.task_id,
        worker_id=worker_id,
        state_version=state_version,
        wake_at=datetime.fromtimestamp(wake_epoch, timezone.utc),
        reason=USER_DELAY_REASON,
    )
    task.status = TaskStatus.WAITING_DEPENDENCY
    task.metadata["wait_reason"] = USER_DELAY_REASON
    task.metadata["wait_until_epoch"] = wake_epoch
    task.metadata["user_delay_seconds"] = request.seconds
    return wake_epoch


__all__ = [
    "DEFAULT_MAX_DELAY_SECONDS",
    "USER_DELAY_REASON",
    "UserDelayRequest",
    "defer_user_delay",
    "parse_user_delay",
]
