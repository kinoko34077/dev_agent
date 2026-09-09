"""Typed, JSON-compatible facade for Controller checkpoint state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from ..domain.protocol import Task


class RuntimeState(dict[str, Any]):
    """Mutable runtime state with explicit checkpoint round-trip boundaries.

    The Controller still accepts mapping-style access for compatibility with
    existing checkpoints.  New callers can use the typed properties below and
    can never accidentally retain the caller's mutable checkpoint object.
    """

    @classmethod
    def initial(cls, task: Task, *, now: float) -> "RuntimeState":
        return cls(
            messages=[{"role": "user", "content": task.objective}],
            tool_results=[],
            model_calls=0,
            tool_calls=0,
            input_tokens_used=0,
            output_tokens_used=0,
            cost_used=0.0,
            retries_used=0,
            next_step_order=0,
            pending_tool_calls=[],
            active_step=None,
            active_request_id=None,
            deadline_epoch=now + task.limits.max_wall_time_seconds,
        )

    @classmethod
    def from_checkpoint(cls, value: Mapping[str, Any]) -> "RuntimeState":
        if not isinstance(value, Mapping):
            raise TypeError("runtime checkpoint state must be an object")
        return cls(deepcopy(dict(value)))

    def to_checkpoint(self) -> dict[str, Any]:
        return deepcopy(dict(self))

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self["messages"]

    @property
    def pending_tool_calls(self) -> list[dict[str, Any]]:
        return self["pending_tool_calls"]

    @property
    def active_step(self) -> dict[str, Any] | None:
        return self.get("active_step")

    @active_step.setter
    def active_step(self, value: dict[str, Any] | None) -> None:
        self["active_step"] = value

    @property
    def deadline_epoch(self) -> float:
        return float(self["deadline_epoch"])

    @property
    def active_request_id(self) -> str | None:
        value = self.get("active_request_id")
        return value if isinstance(value, str) and value.strip() else None

    @active_request_id.setter
    def active_request_id(self, value: str | None) -> None:
        self["active_request_id"] = value
