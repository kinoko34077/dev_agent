"""Small atomic JSON store used by alpha0 before the SQLite store in Phase 3."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain.protocol import Event, Step, Task, ToolResult


class JsonStateStore:
    """Persist the minimum task trace in one replaceable JSON document."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = {
            "tasks": {},
            "steps": {},
            "tool_results": {},
            "events": [],
            "checkpoints": [],
        }
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"state store cannot be read: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("state store root must be an object")
        for key in self._data:
            if key in value:
                self._data[key] = value[key]

    def _flush(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._data, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def save_task(self, task: Task) -> None:
        self._data["tasks"][task.task_id] = task.to_dict()
        self._flush()

    def save_step(self, step: Step) -> None:
        self._data["steps"][step.step_id] = step.to_dict()
        self._flush()

    def save_tool_result(self, result: ToolResult) -> None:
        self._data["tool_results"][result.call_id] = result.to_dict()
        self._flush()

    def append_event(self, event: Event) -> None:
        self._data["events"].append(event.to_dict())
        self._flush()

    def checkpoint(self, *, task_id: str, step_id: str, phase: str) -> None:
        self._data["checkpoints"].append({"task_id": task_id, "step_id": step_id, "phase": phase})
        self._flush()

    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._data))

    def load_task(self, task_id: str) -> Task | None:
        value = self._data["tasks"].get(task_id)
        return Task.from_dict(value) if value else None

    def get_idempotent(self, key: str) -> ToolResult | None:
        value = self._data.setdefault("idempotency", {}).get(key)
        return ToolResult.from_dict(value) if value else None

    def save_idempotent(self, key: str, result: ToolResult) -> None:
        self._data.setdefault("idempotency", {})[key] = result.to_dict()
        self._flush()
